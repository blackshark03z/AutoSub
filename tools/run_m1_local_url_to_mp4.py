"""M1 one-job entrypoint: local Bilibili URL -> completed en-US subtitled MP4.

One human command begins with a source URL and, in a single job folder, automatically:
  1. resolves/validates the public Bilibili source and downloads the bounded first
     ``--end-seconds`` (default 360s = 6 min) of the selected part once;
  2. probes the real media, computes SHA-256, binds a stable job identity/manifest;
  3. runs real chunked Chinese hardcoded-subtitle OCR (checkpoint/resume);
  4. localizes to en-US with the offline HY-MT2 llama.cpp model;
  5. replaces the source caption with a tight per-cue solid-black mask and an
     equivalent-size en-US overlay (protected logo/AI badge kept, audio stream-copied);
  6. finalizes the manifest with the final MP4 hash and a truthful terminal status.

Reuses the accepted longpilot drivers/modules (``tools/run_longpilot_*.py`` and the
``app.services`` primitives). No manual transfer between stages.

Usage:
    python tools/run_m1_local_url_to_mp4.py --url https://b23.tv/gK7gvDO \
        --job-dir scratch/m1_job [--end-seconds 360] [--resume] [--skip-download]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.core.media import media_summary  # noqa: E402
from app.core.redaction import canonical_url, redact_text, redact_url  # noqa: E402
from app.services.ingestion_contract import (  # noqa: E402
    IngestionHandoffRecord,
    attach_and_validate_local_media,
    create_or_reuse_ingestion_job,
)

ALLOWED_HOSTS = {"b23.tv", "www.b23.tv", "bilibili.com", "www.bilibili.com", "m.bilibili.com"}
DEFAULT_OCR_PYTHON = Path(r"D:\AutoSub\scratch\ocr_env\Scripts\python.exe")
MAX_DOWNLOAD_ATTEMPTS = 2
MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm"}
INPUT_IDENTITY_FILE = "input_identity.json"
IDENTITY_FIELDS = ("requested_host", "canonical_id", "part_index", "end_seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact_cmd(cmd: list[str]) -> str:
    """Render argv for logging with every URL's sensitive query params masked."""
    return " ".join(redact_url(str(c)) if "://" in str(c) else str(c) for c in cmd)


def _run(cmd: list[str], *, timeout: int | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    print(f"$ {_redact_cmd(cmd)}", flush=True)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout, env=env)


def validate_source_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit(f"URL_REJECTED: unsupported scheme '{parsed.scheme}'")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise SystemExit(f"URL_REJECTED: host '{host}' not in allowlist {sorted(ALLOWED_HOSTS)}")
    return host


def ytdlp_bin() -> str:
    for candidate in (shutil.which("yt-dlp"),
                      r"C:\Users\ADMIN\AppData\Local\Programs\Python\Python311\Scripts\yt-dlp.exe"):
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("Y_OUT_DLP_UNAVAILABLE: yt-dlp not found")


_BV_ID = re.compile(r"(BV[0-9A-Za-z]+)")


def _canonical_id_from(data: dict, entry: dict, url: str) -> str | None:
    for candidate in (entry.get("id"), data.get("id")):
        if candidate:
            return str(candidate)
    for candidate in (entry.get("webpage_url"), entry.get("url"), data.get("webpage_url"),
                      data.get("original_url"), url):
        match = _BV_ID.search(candidate or "")
        if match:
            return match.group(1)
    return None


def resolve_source(url: str, part_index: int) -> dict:
    proc = _run([ytdlp_bin(), "--no-warnings", "--dump-single-json",
                 "--flat-playlist", "--playlist-items", str(part_index), url], timeout=180)
    if proc.returncode != 0:
        raise SystemExit(f"RESOLVE_FAILED: {redact_text(proc.stderr.strip()[:400])}")
    data = json.loads(proc.stdout)
    if data.get("entries") is not None:
        entries = [e for e in (data.get("entries") or []) if e]
        if not entries:
            raise SystemExit("RESOLVE_FAILED: no parts resolved")
        entry = entries[0]
    else:
        entry = data
    canonical_id = _canonical_id_from(data, entry, url)
    if not canonical_id:
        raise SystemExit("RESOLVE_FAILED: no canonical video id resolved")
    webpage = entry.get("webpage_url") or data.get("webpage_url") or url
    return {
        "canonical_id": canonical_id,
        "title": entry.get("title") or data.get("title"),
        "duration_seconds": entry.get("duration") or data.get("duration"),
        "webpage_url_canonical": canonical_url(webpage),
        "webpage_url_redacted": redact_url(webpage),
        "part_index": part_index,
    }


def _find_existing_media(source_dir: Path) -> Path | None:
    existing = sorted(p for p in source_dir.glob("source.*") if p.suffix.lower() in MEDIA_SUFFIXES)
    return existing[0] if existing else None


def load_input_identity(job_dir: Path) -> dict | None:
    path = job_dir / INPUT_IDENTITY_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def bind_input_identity(job_dir: Path, identity: dict, media: Path, *, allow_create: bool) -> dict:
    """Bind the requested URL/part/section to the on-disk media, or fail closed.

    A job directory is immutable input: if it already holds media, the requested
    canonical id, part index, bounded section and requested host must match the
    recorded identity AND the media hash must still match. Media with no recorded
    identity can never be adopted (``allow_create`` is only true right after a fresh
    download), so a changed URL/part/section cannot silently attach stale media.
    """
    path = job_dir / INPUT_IDENTITY_FILE
    source_sha = sha256_file(media)
    size_bytes = media.stat().st_size
    if path.exists():
        recorded = load_input_identity(job_dir)
        if recorded is None:
            raise SystemExit("IMMUTABLE_JOB_INPUT_MISMATCH: unreadable input identity")
        for field in IDENTITY_FIELDS:
            if recorded.get(field) != identity.get(field):
                raise SystemExit(
                    f"IMMUTABLE_JOB_INPUT_MISMATCH: {field} {recorded.get(field)!r} != {identity.get(field)!r}"
                )
        if recorded.get("source_sha256") and recorded["source_sha256"] != source_sha:
            raise SystemExit(
                "IMMUTABLE_JOB_INPUT_MISMATCH: source_sha256 "
                f"{recorded.get('source_sha256')} != {source_sha}"
            )
        print(f"input identity verified {recorded.get('canonical_id')} p{recorded.get('part_index')} "
              f"section<= {recorded.get('end_seconds')}s")
        return recorded
    if not allow_create:
        raise SystemExit(
            "IMMUTABLE_JOB_INPUT_MISMATCH: existing media has no recorded input identity"
        )
    record = {**identity, "source_sha256": source_sha, "size_bytes": size_bytes}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"input identity bound {record['canonical_id']} p{record['part_index']} "
          f"section<= {record['end_seconds']}s sha={source_sha[:16]}")
    return record


def download_bounded(url: str, part_index: int, end_seconds: int, out_dir: Path,
                     max_transfer_mib: int, timeout_s: int) -> Path:
    source_dir = out_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    outtmpl = str(source_dir / "source.%(ext)s")
    section = f"*0:00:00-0:{end_seconds // 60:02d}:{end_seconds % 60:02d}"
    cmd = [
        ytdlp_bin(), "--no-warnings", "--no-playlist", "--playlist-items", str(part_index),
        "--download-sections", section, "--force-keyframes-at-cuts",
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "--max-filesize", f"{max_transfer_mib}M",
        "--socket-timeout", "30", "--retries", "3", "--fragment-retries", "3",
        "-o", outtmpl, url,
    ]
    last_err = ""
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        print(f"download attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS}")
        proc = _run(cmd, timeout=timeout_s)
        if proc.returncode == 0:
            media = _find_existing_media(source_dir)
            if media is not None:
                return media
            last_err = "no media file produced"
        else:
            last_err = redact_text(proc.stderr.strip()[-400:])
    raise SystemExit(f"DOWNLOAD_FAILED: {last_err}")


def probe_and_validate(media: Path) -> dict:
    summary = media_summary(media)
    video = summary.get("video") or {}
    if not video.get("width") or not video.get("height"):
        raise SystemExit("MEDIA_INVALID: no decodable video stream")
    dec = _run(["ffmpeg", "-v", "error", "-i", str(media), "-f", "null", "-"], timeout=900)
    decode_errors = dec.stderr.strip()
    return {"summary": summary, "decode_errors": decode_errors,
            "media_sha256": sha256_file(media), "size_bytes": media.stat().st_size}


def audio_packet_md5(media: Path) -> str:
    proc = _run(["ffmpeg", "-v", "error", "-i", str(media), "-map", "0:a", "-c", "copy",
                 "-f", "md5", "-"], timeout=600)
    return proc.stdout.strip()


def run_stage(tool: str, args: list[str], env: dict, timeout: int) -> int:
    proc = _run([sys.executable, str(REPO / "tools" / tool), *args], timeout=timeout, env=env)
    if proc.stdout:
        print(proc.stdout, flush=True)
    if proc.returncode != 0 and proc.stderr:
        print(proc.stderr, file=sys.stderr, flush=True)
    return proc.returncode


def git_state() -> dict:
    """Record the exact executed commit plus dirty state so acceptance is bound to it."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=str(REPO), capture_output=True,
                                text=True, encoding="utf-8").stdout.strip()
        dirty_files = [line for line in status.splitlines() if line.strip()]
        return {"head": head or "UNKNOWN", "dirty": bool(dirty_files), "dirty_files": dirty_files}
    except Exception:
        return {"head": "UNKNOWN", "dirty": None, "dirty_files": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--end-seconds", type=int, default=360)
    parser.add_argument("--part", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-after-chunk", type=int, default=None)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--skip-translate", action="store_true")
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument("--max-transfer-mib", type=int, default=200)
    parser.add_argument("--download-timeout-s", type=int, default=900)
    parser.add_argument("--stage-timeout-s", type=int, default=3600)
    parser.add_argument("--ocr-python", default=None)
    args = parser.parse_args()

    started = time.time()
    host = validate_source_url(args.url)
    job_dir = Path(args.job_dir).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)

    git = git_state()
    manifest: dict = {
        "wp": "WP-M1-001",
        "implementation_head": git["head"],
        "implementation_dirty": git["dirty"],
        "implementation_dirty_files": git["dirty_files"],
        "input_url_redacted": redact_url(args.url),
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "end_seconds": args.end_seconds,
        "status": "STARTED",
    }

    resolution = resolve_source(args.url, args.part)
    manifest["source_resolution"] = resolution
    print(f"resolved canonical_id={resolution['canonical_id']} title={resolution['title']!r} "
          f"duration={resolution['duration_seconds']}")

    identity = {
        "requested_host": host,
        "canonical_id": resolution["canonical_id"],
        "part_index": args.part,
        "end_seconds": args.end_seconds,
        "url_redacted": redact_url(args.url),
    }
    source_dir = job_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    existing = _find_existing_media(source_dir)
    if existing is not None:
        media = existing
        downloaded_now = False
    elif args.skip_download:
        raise SystemExit("SKIP_DOWNLOAD_NO_MEDIA: no existing source media to reuse")
    else:
        media = download_bounded(args.url, args.part, args.end_seconds, job_dir,
                                 args.max_transfer_mib, args.download_timeout_s)
        downloaded_now = True
    print(f"media={media}")

    probe = probe_and_validate(media)
    recorded_identity = bind_input_identity(job_dir, identity, media, allow_create=downloaded_now)
    manifest["input_identity"] = recorded_identity
    manifest["media"] = {
        "path": str(media),
        "sha256": probe["media_sha256"],
        "size_bytes": probe["size_bytes"],
        "summary": probe["summary"],
        "decode_errors": probe["decode_errors"],
    }
    if probe["decode_errors"]:
        manifest["status"] = "FAILED"
        manifest["failure"] = "DECODE_ERRORS"
        (job_dir / "m1_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        print("DECODE_ERRORS", file=sys.stderr)
        return 4

    handoff = IngestionHandoffRecord(
        source_id=f"bilibili:{resolution['canonical_id']}",
        source_url_redacted=redact_url(args.url),
        canonical_id=resolution["canonical_id"],
        platform="bilibili",
        part_or_episode_id=str(args.part),
        selected_item_index=args.part,
        discovery_status="RESOLVED",
    )
    handoff = attach_and_validate_local_media(handoff, media, is_live_media=True)
    job = create_or_reuse_ingestion_job(handoff, jobs_registry={})
    manifest["ingestion"] = handoff.to_dict()
    manifest["job"] = job
    print(f"job_id={job['job_id']} ingestion_status={handoff.ingestion_status}")

    env = dict(os.environ)
    ocr_python = Path(args.ocr_python) if args.ocr_python else DEFAULT_OCR_PYTHON
    if ocr_python.exists():
        env["AUTOSUB_OCR_PYTHON"] = str(ocr_python)

    out_dir = job_dir
    rc = 0
    if not args.skip_ocr:
        ocr_args = ["--source", str(media), "--out", str(out_dir)]
        if args.resume:
            ocr_args.append("--resume")
        if args.stop_after_chunk is not None:
            ocr_args += ["--stop-after-chunk", str(args.stop_after_chunk)]
        rc = run_stage("run_longpilot_ocr.py", ocr_args, env, args.stage_timeout_s)
        if rc != 0:
            manifest["status"] = "FAILED"
            manifest["failure"] = f"OCR_STAGE_EXIT_{rc}"
            (job_dir / "m1_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
            return rc
        if args.stop_after_chunk is not None:
            manifest["status"] = "PARTIAL_INTERRUPTED"
            manifest["note"] = "stopped after chunk for resume demonstration"
            (job_dir / "m1_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
            print("PARTIAL_INTERRUPTED")
            return 0
        manifest["ocr_meta"] = json.loads((out_dir / "ocr_meta.json").read_text(encoding="utf-8"))

    if not args.skip_translate:
        rc = run_stage("run_longpilot_translate.py", ["--out", str(out_dir)], env, args.stage_timeout_s)
        if rc != 0:
            manifest["status"] = "FAILED"
            manifest["failure"] = f"TRANSLATE_STAGE_EXIT_{rc}"
            (job_dir / "m1_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
            return rc

    if not args.skip_render:
        rc = run_stage("run_longpilot_render.py", ["--out", str(out_dir), "--language", "en-US"],
                       env, args.stage_timeout_s)
        if rc != 0:
            manifest["status"] = "FAILED"
            manifest["failure"] = f"RENDER_STAGE_EXIT_{rc}"
            (job_dir / "m1_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
            return rc

    mp4 = out_dir / "longpilot_en-US.mp4"
    if not mp4.exists():
        manifest["status"] = "FAILED"
        manifest["failure"] = "NO_OUTPUT_MP4"
        (job_dir / "m1_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        print("NO_OUTPUT_MP4", file=sys.stderr)
        return 5

    render_report = json.loads((out_dir / "render_report_en-US.json").read_text(encoding="utf-8"))
    manifest["render"] = {
        "cue_count": render_report.get("cue_count"),
        "statuses": render_report.get("post_clamp_statuses"),
        "clamped": render_report.get("clamped"),
        "unrecoverable": render_report.get("unrecoverable"),
        "mask_padding_px": render_report.get("mask_padding_px"),
        "fit_budget": render_report.get("fit_budget"),
        "safe_text_width": render_report.get("safe_text_width"),
        "max_mask_width_px": render_report.get("max_mask_width_px"),
        "ass": render_report.get("ass"),
    }
    manifest["output"] = {
        "mp4_path": str(mp4),
        "mp4_sha256": sha256_file(mp4),
        "mp4_bytes": mp4.stat().st_size,
        "summary": media_summary(mp4),
        "audio_packet_md5": audio_packet_md5(mp4),
    }

    rc = run_stage("run_longpilot_finalize.py",
                   ["--source", str(media), "--out", str(out_dir),
                    "--duration-ms", str(int(round(float(probe["summary"]["duration_seconds"]) * 1000)))],
                   env, 300)
    manifest["finalize_exit"] = rc

    statuses = render_report.get("post_clamp_statuses") or {}
    blocking = {k: v for k, v in statuses.items() if k in {"NEEDS_REVIEW", "SUBTITLE_REGION_UNCONFIRMED",
                                                           "PROTECTED_ROI_INTERSECTION"}}
    manifest["status"] = "DONE" if not blocking else "NEEDS_REVIEW"
    manifest["blocking_layouts"] = blocking
    manifest["elapsed_seconds"] = round(time.time() - started, 1)
    (job_dir / "m1_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    print(f"M1_STATUS={manifest['status']}")
    print(f"output_mp4={mp4}")
    print(f"output_sha256={manifest['output']['mp4_sha256']}")
    print(f"cue_count={render_report.get('cue_count')} statuses={statuses}")
    print(f"elapsed_seconds={manifest['elapsed_seconds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
