"""Long-pilot OCR driver: chunked RapidOCR once -> immutable E1 master, with resume.

Processes a continuous source clip in ~60s chunks (bounded overlap used only for
detection context), runs RapidOCR PP-OCRv5 ONNX per chunk, deduplicates cues in the
overlap via ``merge_chunk_cues``, and binds the result into one immutable E1
``SourceZhMasterCueSet`` persisted to the L1 cache.

Every chunk is checkpointed atomically (``ChunkCheckpointStore``); an interrupted or
re-run invocation resumes only the chunks that are not yet ``OCR_DONE`` and reuses the
already-detected chunks, so a source is OCR'd just once per chunk.

Usage:
    python tools/run_longpilot_ocr.py --source ... --out ... [--stop-after-chunk N] [--resume]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import cv2  # noqa: E402

from app.services.experimental_cue_ocr_tracking import (  # noqa: E402
    ExperimentalCueOCRTracker,
    TrackedCue,
)
from app.services.longpilot_chunking import (  # noqa: E402
    CheckpointError,
    ChunkCheckpointStore,
    MasterVersion,
    merge_chunk_cues,
    plan_chunks,
)
from app.services.multimarket_cache import MultiMarketCacheStore, compute_l1_key  # noqa: E402
from app.services.multimarket_contracts import (  # noqa: E402
    SourceZhCue,
    SourceZhMasterCueSet,
    SpatialBoundingBox,
)

OCR_ENGINE_VERSION = "RapidOCR_PP-OCRv5_ONNX_v1.4.4"
SAMPLING_POLICY_ID = "sample_fps=2.0_step_ms=500"
ROI_FILTER_VERSION = "roi_dialogue_band_v1.3"
DECODE_VERSION = "ffmpeg9.0.1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_video(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {"fps": fps, "total_frames": total, "width": width, "height": height,
            "duration_ms": int(round(total / fps * 1000))}


def _box(b: dict[str, int]) -> SpatialBoundingBox:
    return SpatialBoundingBox(left_x=int(b["left_x"]), top_y=int(b["top_y"]),
                              right_x=int(b["right_x"]), bottom_y=int(b["bottom_y"]),
                              width=int(b["width"]), height=int(b["height"]))


def tracked_to_dict(cue: TrackedCue) -> dict:
    return {"cue_id": cue.cue_id, "start_ms": int(cue.start_ms), "end_ms": int(cue.end_ms),
            "text": cue.zh_text, "zh_text": cue.zh_text, "confidence": float(cue.confidence),
            "bbox": dict(cue.bbox), "plate_box": dict(cue.plate_box), "frame_count": int(cue.frame_count)}


def sample_frames(cap: cv2.VideoCapture, fps: float, start_s: float, end_s: float, sample_fps: float):
    step = max(1, int(round(fps / sample_fps)))
    start_frame = int(round(start_s * fps))
    end_frame = int(round(end_s * fps))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for f_idx in range(start_frame, min(total, end_frame + 1), step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()
        if not ret:
            break
        frames.append((f_idx / fps, frame))
    return frames


def ocr_chunk(tracker, cap, fps, window, sample_fps, *, frame_width, frame_height):
    start_s = window.decode_start_ms / 1000.0
    end_s = window.decode_end_ms / 1000.0
    frames = sample_frames(cap, fps, start_s, end_s, sample_fps)
    outputs = tracker.run_ocr_on_frames(frames) if frames else []
    cues = tracker.aggregate_temporal_cues(
        outputs,
        frame_interval_s=1.0 / sample_fps,
        frame_width=frame_width,
        frame_height=frame_height,
    )
    return [tracked_to_dict(c) for c in cues]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sample-fps", type=float, default=2.0)
    parser.add_argument("--chunk-ms", type=int, default=60_000)
    parser.add_argument("--overlap-ms", type=int, default=2_000)
    parser.add_argument("--stop-after-chunk", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    source = Path(args.source)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = out_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    probe = probe_video(source)
    video_sha = sha256_file(source)
    print(f"source={source}")
    print(f"source_sha256={video_sha}")
    print(f"duration_ms={probe['duration_ms']} {probe['width']}x{probe['height']} fps={probe['fps']}")

    windows = plan_chunks(probe["duration_ms"], chunk_ms=args.chunk_ms, overlap_ms=args.overlap_ms)
    print(f"chunks={len(windows)}")
    master_version = MasterVersion(
        source_video_sha256=video_sha, video_width=probe["width"], video_height=probe["height"],
        fps=probe["fps"], duration_ms=probe["duration_ms"], ocr_engine_version=OCR_ENGINE_VERSION,
        sampling_policy_id=SAMPLING_POLICY_ID, roi_filter_version=ROI_FILTER_VERSION,
        decode_version=DECODE_VERSION,
    )
    print(f"master_version_id={master_version.version_id}")

    ckpt_path = out_dir / "checkpoint.json"
    store = ChunkCheckpointStore(ckpt_path, master=master_version)
    try:
        store.load()
    except CheckpointError as exc:  # fail closed on corrupt/mismatched checkpoint
        print(f"CHECKPOINT_FAIL_CLOSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    resume = store.resume_plan(len(windows), min_status="OCR_DONE")
    print(f"resume_plan completed={resume['completed']} pending={resume['pending']}")

    tracker = ExperimentalCueOCRTracker()
    if not tracker.is_available:
        print("FATAL: OCR runtime unavailable", file=sys.stderr)
        return 2
    print(f"ocr_python={tracker.ocr_python}")

    cap = cv2.VideoCapture(str(source))
    processed_this_run = []
    try:
        for window in windows:
            rec = store.record(window.index, window.start_ms, window.end_ms)
            chunk_file = chunks_dir / f"chunk_{window.index:02d}.json"
            if store.is_complete(window.index, min_status="OCR_DONE") and chunk_file.exists():
                print(f"  chunk {window.index:02d} SKIP (cached, {rec.cue_count} cues)")
                continue
            t0 = time.time()
            cues = ocr_chunk(tracker, cap, probe["fps"], window, args.sample_fps,
                             frame_width=probe["width"], frame_height=probe["height"])
            chunk_file.write_text(json.dumps({"window": window.to_dict(), "cues": cues},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
            store.mark(window.index, status="OCR_DONE", cue_count=len(cues),
                       notes=f"decode=[{window.decode_start_ms},{window.decode_end_ms}]ms")
            store.save_atomic()
            processed_this_run.append(window.index)
            print(f"  chunk {window.index:02d} OCR_DONE cues={len(cues)} in {time.time() - t0:.1f}s")
            if args.stop_after_chunk is not None and window.index >= args.stop_after_chunk:
                print(f"STOP_AFTER_CHUNK={args.stop_after_chunk} (interruption simulated)")
                break
    finally:
        cap.release()

    if args.stop_after_chunk is not None:
        print(f"partial run complete; processed={processed_this_run}")
        return 0

    # All chunks done -> merge and bind immutable master.
    chunk_cues = []
    for window in windows:
        data = json.loads((chunks_dir / f"chunk_{window.index:02d}.json").read_text(encoding="utf-8"))
        chunk_cues.append(data["cues"])
    merged = merge_chunk_cues(chunk_cues)
    print(f"merged cue_count={merged['cue_count']} duplicates_removed={merged['duplicates_removed']} "
          f"boundary_merges={merged['boundary_merges']}")

    master_cues = []
    for c in merged["cues"]:
        provenance = "chunked_merge:" + hashlib.sha256(
            f"{c['cue_id']}|{c['text']}|{video_sha[:16]}".encode("utf-8")).hexdigest()[:24]
        master_cues.append(SourceZhCue(
            cue_id=c["cue_id"], start_ms=int(c["start_ms"]), end_ms=int(c["end_ms"]),
            raw_ocr_text=c["text"], ocr_confidence=float(c["confidence"]),
            raw_bbox=_box(c["bbox"]), plate_box=_box(c["plate_box"]),
            frame_count=int(c["frame_count"]), source_frame_provenance=provenance))
    master = SourceZhMasterCueSet(
        source_video_sha256=video_sha, video_width=probe["width"], video_height=probe["height"],
        fps=probe["fps"], total_frames=probe["total_frames"], duration_ms=probe["duration_ms"],
        ocr_engine_version=OCR_ENGINE_VERSION, sampling_policy_id=SAMPLING_POLICY_ID,
        roi_filter_version=ROI_FILTER_VERSION, cues=tuple(master_cues), source_video_path=str(source))
    (out_dir / "master_cues.json").write_text(master.to_json(), encoding="utf-8")
    print(f"master_extraction_hash={master.extraction_hash}")

    l1_key = compute_l1_key(source_video_sha256=video_sha, video_width=probe["width"],
                            video_height=probe["height"], ocr_engine_version=OCR_ENGINE_VERSION,
                            sampling_policy_id=SAMPLING_POLICY_ID, roi_filter_version=ROI_FILTER_VERSION)
    cache = MultiMarketCacheStore(out_dir / "multimarket_cache.db")
    cache.put_l1_extraction(l1_key, master)
    loaded = cache.get_l1_extraction(l1_key)
    print(f"l1_key={l1_key} roundtrip_ok={loaded is not None and loaded.extraction_hash == master.extraction_hash}")

    # Raw OCR artifact consumable by tools/run_e2_dynamic_mask.py
    detected = []
    for c in merged["cues"]:
        d = dict(c)
        d["text_height"] = int(d["bbox"]["height"])
        detected.append(d)
    (out_dir / "ocr_audit_results.json").write_text(json.dumps({
        "video_path": str(source), "source_video_sha256": video_sha,
        "total_duration_s": probe["duration_ms"] / 1000.0, "sample_interval_s": round(1.0 / args.sample_fps, 3),
        "ocr_engine_version": OCR_ENGINE_VERSION, "sampling_policy_id": SAMPLING_POLICY_ID,
        "roi_filter_version": ROI_FILTER_VERSION, "total_cues_detected": len(detected),
        "detected_cues": detected}, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = {"source_video_sha256": video_sha, "master_extraction_hash": master.extraction_hash,
            "l1_cache_key": l1_key, "cue_count": merged["cue_count"],
            "duplicates_removed": merged["duplicates_removed"], "boundary_merges": merged["boundary_merges"],
            "chunk_count": len(windows), "master_version_id": master_version.version_id,
            "processed_this_run": processed_this_run}
    (out_dir / "ocr_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
