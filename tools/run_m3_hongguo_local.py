"""M3 Phase B-1: one Hongguo local episode -> full en-US subtitled MP4.

This entrypoint is deliberately local-file only. It never resolves or downloads a
URL, never impersonates URL ingestion, and never writes to the Owner's source folder.
It binds HONGGUO_LOCAL_FILE + series + episode + source SHA-256 identity, then runs
the accepted M2 OCR -> offline en-US HY-MT2 -> adaptive union ASS mask -> MP4 stages.

Usage:
    python tools/run_m3_hongguo_local.py \
        --source "D:\\...\\[title]_Tap_001.mp4" \
        --series-id 7690432295777340441 --episode 001 \
        --title "隐世魔尊，女帝带娃找上门来" \
        --expected-sha256 070c8e...b186 \
        --job-dir data/m3_hongguo_job --resume
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
from typing import Any

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.core.media import media_summary  # noqa: E402
from app.services.hongguo_local_intake import (  # noqa: E402
    INPUT_MODE,
    PROVIDER,
    LocalSourceError,
    bind_local_input_identity,
    create_hongguo_local_handoff,
    probe_local_media,
    resolve_local_media,
    sha256_file,
)
from app.services.ingestion_contract import create_or_reuse_ingestion_job  # noqa: E402

DEFAULT_OCR_PYTHON = Path(r"D:\AutoSub\scratch\ocr_env\Scripts\python.exe")
FONT = Path(r"C:\Windows\Fonts\arial.ttf")
EVIDENCE_TIMES = (3.0, 5.0, 25.0, 50.0, 80.0)


def _run(cmd: list[str], *, timeout: int | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def git_state() -> tuple[str, list[str]]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()
    dirty = [line for line in subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True,
        text=True, encoding="utf-8").stdout.splitlines() if line.strip()]
    return head, dirty


def run_stage(tool: str, args: list[str], env: dict, timeout: int) -> int:
    proc = _run([sys.executable, str(REPO / "tools" / tool), *args], timeout=timeout, env=env)
    if proc.stdout:
        print(proc.stdout, flush=True)
    if proc.returncode != 0 and proc.stderr:
        print(proc.stderr, file=sys.stderr, flush=True)
    return proc.returncode


def audio_packet_md5(media: Path) -> str:
    proc = _run(["ffmpeg", "-v", "error", "-i", str(media), "-map", "0:a", "-c", "copy",
                 "-f", "md5", "-"], timeout=600)
    return proc.stdout.strip()


def extract_frame(media: Path, seconds: float, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}",
                    "-i", str(media), "-frames:v", "1", str(output)], check=True)


def contains(outer: dict[str, int], inner: dict[str, int]) -> bool:
    return (outer["left_x"] <= inner["left_x"] and outer["top_y"] <= inner["top_y"]
            and outer["right_x"] >= inner["right_x"] and outer["bottom_y"] >= inner["bottom_y"])


def intersects(a: dict[str, int], b: dict[str, int]) -> bool:
    return not (a["right_x"] < b["left_x"] or b["right_x"] < a["left_x"]
                or a["bottom_y"] < b["top_y"] or b["bottom_y"] < a["top_y"])


def _ass_seconds(value: str) -> float:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def ass_active_events(ass_path: Path, at_seconds: float) -> list[str]:
    active = []
    for line in ass_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            continue
        start, end = _ass_seconds(fields[1]), _ass_seconds(fields[2])
        if start <= at_seconds <= end:
            active.append(fields[9])
    return active


def build_cue_audit(job_dir: Path, render_report: dict[str, Any],
                    translations: dict[str, Any], protected_rois: dict[str, dict[str, int]]) -> dict[str, Any]:
    by_id = {str(r["cue_id"]): r for r in translations.get("results", [])}
    entries = []
    blocking = []
    for layout in render_report.get("cue_windows", []):
        cue_id = str(layout["cue_id"])
        mask = layout.get("mask")
        src = layout.get("source_bbox")
        text_rect = None
        if layout.get("text_width") and layout.get("text_height"):
            left = int(layout["center_x"]) - int(layout["text_width"]) // 2
            top = int(layout["anchor_y"]) - int(layout["text_height"]) // 2
            text_rect = {"left_x": left, "top_y": top,
                         "right_x": left + int(layout["text_width"]) - 1,
                         "bottom_y": top + int(layout["text_height"]) - 1}
        roi_hits = []
        if mask:
            mask_rect = {"left_x": mask["x"], "top_y": mask["y"],
                         "right_x": mask["right_x"], "bottom_y": mask["bottom_y"]}
            roi_hits = [name for name, roi in protected_rois.items() if intersects(mask_rect, roi)]
        checks = {
            "status": layout.get("status"),
            "has_mask": bool(mask),
            "covers_source": bool(mask and src and contains(
                {"left_x": mask["x"], "top_y": mask["y"], "right_x": mask["right_x"],
                 "bottom_y": mask["bottom_y"]}, src)),
            "covers_english": bool(mask and text_rect and contains(
                {"left_x": mask["x"], "top_y": mask["y"], "right_x": mask["right_x"],
                 "bottom_y": mask["bottom_y"]}, text_rect)),
            "non_empty_text": bool(layout.get("text")),
            "roi_hits": roi_hits,
        }
        if layout.get("status") != "PASS" or not all(
                checks[k] for k in ("has_mask", "covers_source", "covers_english", "non_empty_text")) or roi_hits:
            blocking.append(cue_id)
        entries.append({
            "cue_id": cue_id, "start": layout.get("start"), "end": layout.get("end"),
            "source_text": by_id.get(cue_id, {}).get("source_text"),
            "translation": layout.get("text"),
            "font_size": layout.get("font_size"),
            "source_char_height": layout.get("source_char_height"),
            "equivalent_size_ratio": layout.get("equivalent_size_ratio"),
            "mask": mask, "source_bbox": src, "text_rect": text_rect,
            "checks": checks,
        })
    ass_path = Path(render_report["ass"])
    windows = sorted((float(e["start"]), float(e["end"])) for e in entries)
    gaps = [(prev[1], nxt[0]) for prev, nxt in zip(windows, windows[1:]) if nxt[0] > prev[1] + 0.2]
    probe = None
    if gaps:
        gap_start, gap_end = max(gaps, key=lambda g: g[1] - g[0])
        probe = round((gap_start + gap_end) / 2.0, 3)
    active_at_gap = ass_active_events(ass_path, probe) if probe is not None else []
    return {
        "cue_count": len(entries),
        "statuses": render_report.get("statuses"),
        "blocking_cues": blocking,
        "gap_probe_seconds": probe,
        "gap_active_events": active_at_gap,
        "gap_is_clear": not active_at_gap,
        "gap_note": "no inter-cue gap >= 0.20s" if probe is None else None,
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--skip-translate", action="store_true")
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument("--stage-timeout-s", type=int, default=3600)
    parser.add_argument("--ocr-python", default=None)
    args = parser.parse_args()

    started = time.time()
    if re.match(r"^[a-z]+://", args.source, re.IGNORECASE):
        raise SystemExit("LOCAL_INPUT_REQUIRED: URL ingestion is not authorized for M3 Phase B-1")
    original = resolve_local_media(args.source)
    job_dir = Path(args.job_dir).resolve()
    source_dir = job_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = job_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    original_sha = sha256_file(original)
    if original_sha.lower() != args.expected_sha256.lower():
        raise SystemExit(f"SOURCE_HASH_MISMATCH: expected {args.expected_sha256.lower()} but original is {original_sha}")
    probe = probe_local_media(original)

    destination = source_dir / f"source{original.suffix.lower()}"
    if destination.exists() and sha256_file(destination) == original_sha:
        copied = False
        print(f"source copy reused sha={original_sha[:16]}")
    else:
        temp = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copy2(original, temp)
        os.replace(temp, destination)
        copied = True
        if sha256_file(destination) != original_sha:
            raise SystemExit("SOURCE_COPY_HASH_MISMATCH: copied media differs from original")
        print(f"source copied sha={original_sha[:16]}")
    if sha256_file(original) != original_sha:
        raise SystemExit("SOURCE_MUTATED: original source hash changed during intake")

    handoff = create_hongguo_local_handoff(
        destination, series_id=args.series_id, episode=args.episode, title=args.title,
        expected_sha256=original_sha, summary=probe)
    code_head, code_dirty_paths = git_state()
    identity = {
        "provider": PROVIDER,
        "input_mode": INPUT_MODE,
        "url_ingest": False,
        "series_id": str(args.series_id),
        "episode": str(args.episode),
        "title": args.title,
        "interval": {"start_ms": 0, "end_ms": int(round(float(probe["duration_seconds"]) * 1000))},
        "source_sha256": original_sha,
        "duration_ms": int(round(float(probe["duration_seconds"]) * 1000)),
        "width": int(probe["video"]["width"]),
        "height": int(probe["video"]["height"]),
        "code_head": code_head,
        "code_dirty": bool(code_dirty_paths),
        "code_dirty_paths": code_dirty_paths,
        "config": {
            "target_locale": "en-US",
            "secondary_locale": "vi-VN",
            "translation_model_id": "tencent/Hy-MT2-1.8B-Q4_K_M",
            "mask_model": "adaptive_union_source_and_english",
            "protected_rois": {},
        },
        "origin_path": str(original),
        "source_copy_path": str(destination),
        "ocr_engine_version": "RapidOCR_PP-OCRv5_ONNX_v1.4.4",
        "sampling_policy_id": "sample_fps=2.0_step_ms=500",
    }
    computed_identity = identity
    identity = bind_local_input_identity(job_dir, computed_identity, allow_create=True)
    identity.update({
        "code_head": code_head, "code_dirty": bool(code_dirty_paths),
        "code_dirty_paths": code_dirty_paths,
        "interval": computed_identity["interval"],
        "config": computed_identity["config"],
    })
    (job_dir / "local_input_identity.json").write_text(
        json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    job = create_or_reuse_ingestion_job(handoff, jobs_registry={})

    manifest: dict[str, Any] = {
        "wp": "WP-M3-001",
        "phase": "B-1",
        "provider": PROVIDER,
        "input_mode": INPUT_MODE,
        "url_ingest": False,
        "series_id": str(args.series_id),
        "episode": str(args.episode),
        "series_title": args.title,
        "origin_path": str(original),
        "origin_sha256_before": original_sha,
        "source_copy_path": str(destination),
        "source_copy_sha256": sha256_file(destination),
        "source_copy_created_this_run": copied,
        "media": {"summary": probe, "sha256": original_sha,
                  "size_bytes": destination.stat().st_size},
        "input_identity": identity,
        "ingestion": handoff.to_dict(),
        "job": job,
        "protected_rois": {},
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "STARTED",
    }
    (job_dir / "m3_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                              encoding="utf-8")

    env = dict(os.environ)
    ocr_python = Path(args.ocr_python) if args.ocr_python else DEFAULT_OCR_PYTHON
    if ocr_python.exists():
        env["AUTOSUB_OCR_PYTHON"] = str(ocr_python)

    if not args.skip_ocr:
        ocr_args = ["--source", str(destination), "--out", str(job_dir), "--resume"]
        rc = run_stage("run_longpilot_ocr.py", ocr_args, env, args.stage_timeout_s)
        if rc != 0:
            manifest["status"] = "FAILED"
            manifest["failure"] = f"OCR_STAGE_EXIT_{rc}"
            (job_dir / "m3_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
            return rc
    ocr_meta = json.loads((job_dir / "ocr_meta.json").read_text(encoding="utf-8"))
    master_meta = json.loads((job_dir / "master_cues.json").read_text(encoding="utf-8"))
    manifest["ocr"] = ocr_meta
    if int(ocr_meta.get("cue_count") or 0) <= 0:
        manifest["status"] = "FAILED"
        manifest["failure"] = "NO_CAPTIONS_DETECTED"
        (job_dir / "m3_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        return 6

    if not args.skip_translate:
        rc = run_stage("run_longpilot_translate.py", ["--out", str(job_dir)], env, args.stage_timeout_s)
        if rc != 0:
            manifest["status"] = "FAILED"
            manifest["failure"] = f"TRANSLATE_STAGE_EXIT_{rc}"
            (job_dir / "m3_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
            return rc
    translations = json.loads((job_dir / "translations_en-US.json").read_text(encoding="utf-8"))
    translate_meta = json.loads((job_dir / "translate_meta.json").read_text(encoding="utf-8"))
    markdown_cues = [str(r.get("cue_id")) for r in translations.get("results", [])
                     if "**" in str(r.get("translation") or "") or "`" in str(r.get("translation") or "")]
    manifest["translation"] = {
        "model_sha256": translate_meta.get("model_sha256"),
        "cue_count": translations.get("cue_count"),
        "translated_count": translations.get("translated_count"),
        "l3_cache_key": translations.get("l3_cache_key"),
        "statuses": {r["status"]: sum(1 for x in translations.get("results", []) if x["status"] == r["status"])
                     for r in translations.get("results", [])},
        "hygiene_markdown_cues": markdown_cues,
        "hygiene_cleanup_applied_at_render": True,
    }
    ffprobe_ms = int(round(float(probe["duration_seconds"]) * 1000))
    ocr_ms = int(master_meta.get("duration_ms") or 0)
    manifest["duration_sources"] = {
        "ffprobe_ms": ffprobe_ms,
        "ocr_master_ms": ocr_ms,
        "delta_ms": ffprobe_ms - ocr_ms,
    }

    roi_path = job_dir / "protected_rois.json"
    roi_path.write_text("{}", encoding="utf-8")
    if not args.skip_render:
        rc = run_stage("run_longpilot_render.py",
                       ["--out", str(job_dir), "--language", "en-US",
                        "--protected-rois-json", str(roi_path)], env, args.stage_timeout_s)
        if rc != 0:
            manifest["status"] = "FAILED"
            manifest["failure"] = f"RENDER_STAGE_EXIT_{rc}"
            (job_dir / "m3_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
            return rc
    render = json.loads((job_dir / "render_report_en-US.json").read_text(encoding="utf-8"))
    manifest["render"] = {
        "cue_count": render.get("cue_count"), "statuses": render.get("statuses"),
        "mask_model": render.get("mask_model"), "mask_padding_px": render.get("mask_padding_px"),
        "protected_rois": render.get("protected_rois"), "safe_text_width": render.get("safe_text_width"),
        "max_mask_width_px": render.get("max_mask_width_px"), "ass": render.get("ass"),
        "output": render.get("output"),
    }

    mp4 = Path(render["output"])
    if not mp4.exists():
        manifest["status"] = "FAILED"
        manifest["failure"] = "NO_OUTPUT_MP4"
        (job_dir / "m3_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        return 5

    rc = run_stage("run_longpilot_finalize.py",
                   ["--source", str(destination), "--out", str(job_dir),
                    "--duration-ms", str(int(round(float(probe["duration_seconds"]) * 1000)))],
                   env, 300)
    manifest["finalize_exit"] = rc

    output_summary = media_summary(mp4)
    source_audio = audio_packet_md5(destination)
    output_audio = audio_packet_md5(mp4)
    manifest["output"] = {
        "mp4_path": str(mp4), "mp4_sha256": sha256_file(mp4), "mp4_bytes": mp4.stat().st_size,
        "summary": output_summary, "audio_packet_md5": output_audio,
    }
    manifest["source_audio_packet_md5"] = source_audio
    manifest["audio_packet_md5_equal"] = source_audio == output_audio

    for seconds in EVIDENCE_TIMES:
        tag = f"{seconds:g}s".replace(".", "_")
        extract_frame(destination, seconds, evidence_dir / "source_frames" / f"source_{tag}.png")
        extract_frame(mp4, seconds, evidence_dir / "after_frames" / f"after_{tag}.png")

    cue_audit = build_cue_audit(job_dir, render, translations, manifest["protected_rois"])
    (job_dir / "cue_audit.json").write_text(json.dumps(cue_audit, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    manifest["cue_audit"] = {
        "cue_count": cue_audit["cue_count"], "blocking_cues": cue_audit["blocking_cues"],
        "gap_is_clear": cue_audit["gap_is_clear"], "gap_active_events": cue_audit["gap_active_events"],
    }

    duration_delta = abs(float(output_summary["duration_seconds"]) - float(probe["duration_seconds"]))
    blocking_statuses = {k: v for k, v in (render.get("statuses") or {}).items()
                         if k in {"NEEDS_REVIEW", "SUBTITLE_REGION_UNCONFIRMED", "PROTECTED_ROI_INTERSECTION"}}
    all_translated = (translations.get("translated_count") == translations.get("cue_count")
                      == ocr_meta.get("cue_count"))
    complete = (not blocking_statuses and not cue_audit["blocking_cues"] and cue_audit["gap_is_clear"]
                and all_translated and manifest["audio_packet_md5_equal"] and duration_delta <= 1.0)
    manifest["status"] = "DONE" if complete else "NEEDS_REVIEW"
    manifest["blocking_layouts"] = blocking_statuses
    manifest["duration_delta_seconds"] = round(duration_delta, 3)
    manifest["all_cues_translated"] = all_translated
    manifest["elapsed_seconds"] = round(time.time() - started, 1)
    (job_dir / "m3_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    print(f"M3_STATUS={manifest['status']}")
    print(f"output_mp4={mp4}")
    print(f"output_sha256={manifest['output']['mp4_sha256']}")
    print(f"cue_count={render.get('cue_count')} statuses={render.get('statuses')}")
    print(f"audio_equal={manifest['audio_packet_md5_equal']} duration_delta={duration_delta:.3f}")
    print(f"elapsed_seconds={manifest['elapsed_seconds']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LocalSourceError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(4)
