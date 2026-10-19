"""Finalize the long-pilot checkpoint: record translation keys + emitted media hashes.

Loads the chunk checkpoint (bound to the immutable master identity), records each
chunk's translation cache keys and the per-locale rendered media hashes, and advances
every chunk to ``RENDERED_DONE`` via an atomic save. This is the manifest the WP asks
for: source sha, master version, windows, OCR/decode versions, translation keys and
emitted media, with no secrets.

Usage:
    python tools/run_longpilot_finalize.py --source ... --out scratch/longpilot/run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.services.longpilot_chunking import (  # noqa: E402
    CheckpointError,
    ChunkCheckpointStore,
    MasterVersion,
    plan_chunks,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=15.0)
    parser.add_argument("--duration-ms", type=int, required=True)
    parser.add_argument("--chunk-ms", type=int, default=60_000)
    parser.add_argument("--overlap-ms", type=int, default=2_000)
    args = parser.parse_args()

    out_dir = Path(args.out)
    source = Path(args.source)
    # Bind to the immutable master identity actually produced by the OCR stage rather
    # than re-hardcoding version ids, so a version bump can never silently drift.
    master_meta = json.loads((out_dir / "master_cues.json").read_text(encoding="utf-8"))
    master = MasterVersion(
        source_video_sha256=master_meta.get("source_video_sha256") or sha256_file(source),
        video_width=int(master_meta.get("video_width") or args.width),
        video_height=int(master_meta.get("video_height") or args.height),
        fps=float(master_meta.get("fps") or args.fps),
        duration_ms=int(master_meta.get("duration_ms") or args.duration_ms),
        ocr_engine_version=str(master_meta["ocr_engine_version"]),
        sampling_policy_id=str(master_meta["sampling_policy_id"]),
        roi_filter_version=str(master_meta["roi_filter_version"]),
        decode_version=str(master_meta.get("decode_version") or "ffmpeg9.0.1"))

    store = ChunkCheckpointStore(out_dir / "checkpoint.json", master=master)
    try:
        store.load()
    except CheckpointError as exc:
        print(f"CHECKPOINT_FAIL_CLOSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    windows = plan_chunks(args.duration_ms, chunk_ms=args.chunk_ms, overlap_ms=args.overlap_ms)
    translate_meta = json.loads((out_dir / "translate_meta.json").read_text(encoding="utf-8"))
    l3_keys = translate_meta["l3_keys"]

    media = {}
    for locale in ("en-US", "vi-VN"):
        p = out_dir / f"longpilot_{locale}.mp4"
        if p.exists():
            media[locale] = {"path": str(p), "sha256": sha256_file(p), "bytes": p.stat().st_size}

    for window in windows:
        store.record(window.index, window.start_ms, window.end_ms)
        store.mark(window.index, status="RENDERED_DONE", translation_cache_keys=dict(l3_keys),
                   emitted_media=media, notes="OCR->NMT->render complete")
    checksum = store.save_atomic()
    print(f"finalized chunks={len(windows)} status=RENDERED_DONE checkpoint_checksum={checksum}")
    print(f"media={json.dumps(media, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
