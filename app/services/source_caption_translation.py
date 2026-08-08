from __future__ import annotations

import base64
import csv
import json
import math
import re
import statistics
import tempfile
import shutil
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import cv2
from PIL import ImageFont

from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.services.audio import extract_asr_audio
from app.services.clean_subtitle_render import (
    detect_source_subtitle_bbox,
    english_layout_for_interval,
    interval_stats,
    read_frame,
    review_times,
    stabilize_layouts,
    subtitle_plate_stats,
)
from app.services.ocr_runtime import OCRRuntimeError, is_cjk_text, run_ocr_on_images
from app.services.caption_analysis_runtime import CaptionAnalysisError, CaptionAnalysisProgress
from app.services.caption_overrides import (
    HUMAN_REVIEWED_CAPTION_PROVENANCE,
    CaptionOverrideValidationError,
    load_matching_caption_override,
)
from app.providers.translation.gemini import (
    GeminiCaptionTranslationError,
    GeminiCaptionTranslator,
    GeminiModelDiscoveryResult,
    GeminiMultimodalCaptionResolver,
    discover_gemini_models,
    load_gemini_translation_config,
)


SOURCE_CAPTION_MODE = "source_caption_ocr_translation"
SOURCE_CAPTION_GEMINI_MODE = "source_caption_gemini_translation"
SOURCE_CAPTION_HUMAN_REVIEW_MODE = "source_caption_gemini_translation_with_human_review"
SOURCE_CAPTION_MODES = {
    SOURCE_CAPTION_MODE,
    SOURCE_CAPTION_GEMINI_MODE,
    SOURCE_CAPTION_HUMAN_REVIEW_MODE,
}
SOURCE_CAPTION_EVIDENCE_FILENAME = "source_caption_gemini_translation.json"
LOCAL_AUDIO_MODE = "local_audio_transcription"
SOURCE_CAPTION_BLOCK_MESSAGE = (
    "Không thể đọc và dịch phụ đề có sẵn trong video này. "
    "Video chưa được xuất để tránh tạo kết quả sai."
)
MIN_OCR_CONFIDENCE = 0.75
DEFAULT_SAMPLE_SECONDS = 0.5
REPLACEMENT_MASK_PREROLL_SECONDS = 0.75
REPLACEMENT_MASK_POSTROLL_SECONDS = 0.35
REPLACEMENT_TEXT_PREROLL_SECONDS = 0.25
SOURCE_CAPTION_COVERAGE_SAMPLE_SECONDS = 0.25
SOURCE_CAPTION_COVERAGE_GAP_SECONDS = 0.6
SOURCE_CAPTION_COVERAGE_EXTENSION_SECONDS = 1.25
SOURCE_CAPTION_COVERAGE_MIN_UNCOVERED_SECONDS = 0.12
VISUAL_REPLACEMENT_SIGNATURE_DISTANCE = 0.30
VISUAL_REPLACEMENT_MAX_GROUP_SECONDS = 6.0
OCR_BATCH_SIZE = 16
PROVIDER_BATCH_TIMEOUT_SECONDS = 180.0
TRANSLATION_FUTURE_POLL_SECONDS = 5.0
FFMPEG_EXTRACTION_TIMEOUT_SECONDS = 300.0


class SourceCaptionUnavailableError(ValueError):
    pass


def _load_prevalidated_source_caption_evidence(
    run_directory: Path,
    *,
    source_video_sha256: str,
    width: int,
    height: int,
    duration: float,
) -> dict[str, Any] | None:
    evidence_path = run_directory / "subtitles" / SOURCE_CAPTION_EVIDENCE_FILENAME
    if not evidence_path.is_file():
        return None
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE) from exc
    if str(evidence.get("source_video_sha256") or "").lower() != str(source_video_sha256).lower():
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    mode = str(evidence.get("mode") or evidence.get("subtitle_provenance") or "").strip()
    if mode not in {SOURCE_CAPTION_GEMINI_MODE, SOURCE_CAPTION_HUMAN_REVIEW_MODE}:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    benchmark = evidence.get("caption_interval_benchmark")
    if not isinstance(benchmark, dict) or benchmark.get("status") != "PASS":
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    intervals = evidence.get("intervals")
    translations = evidence.get("translations")
    if not isinstance(intervals, list) or not isinstance(translations, list) or not intervals:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    if len(intervals) != len(translations):
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    translation_by_id = {
        str(item.get("id") or ""): item
        for item in translations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    cues: list[dict[str, Any]] = []
    for index, interval in enumerate(intervals, start=1):
        if not isinstance(interval, dict):
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        interval_id = str(interval.get("id") or f"OCR_{index:04d}").strip()
        translation = translation_by_id.get(interval_id)
        if translation is None:
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        text = str(translation.get("translated_text") or "").strip()
        start_time = _safe_float(interval.get("start_time"))
        end_time = _safe_float(interval.get("end_time"))
        bbox = interval.get("source_bbox")
        if not text or start_time is None or end_time is None or start_time < 0 or end_time <= start_time or end_time > duration + 0.5:
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        if not _valid_source_bbox(bbox, width=width, height=height):
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        cues.append({
            "cue_id": interval_id,
            "start_ms": round(start_time * 1000),
            "end_ms": round(end_time * 1000),
            "source_text": str(translation.get("source_text") or interval.get("source_text") or "").strip(),
            "text": text,
            "source_bbox": bbox,
            "source_interval": {
                "start_time": start_time,
                "end_time": end_time,
            },
            "line_count": int(interval.get("line_count") or 1),
            "ocr_confidence": interval.get("ocr_confidence"),
            "needs_pixel_refresh": True,
            "geometry_source": "prevalidated_evidence",
        })
    metadata = {
        key: value
        for key, value in evidence.items()
        if key not in {"intervals", "translations", "benchmark_rows"}
    }
    metadata["mode"] = mode
    metadata["subtitle_provenance"] = mode
    provider_usage = metadata.get("provider_usage") if isinstance(metadata.get("provider_usage"), dict) else {}
    metadata["provider_usage"] = {
        "request_count": int(provider_usage.get("request_count") or 0),
        "retry_count": int(provider_usage.get("retry_count") or 0),
        "input_tokens": int(provider_usage.get("input_tokens") or 0),
        "output_tokens": int(provider_usage.get("output_tokens") or 0),
        "cache_hits": int(provider_usage.get("cache_hits") or 0),
        "cache_misses": int(provider_usage.get("cache_misses") or 0),
        "request_ids": list(provider_usage.get("request_ids") or []),
    }
    metadata["external_calls"] = metadata["provider_usage"]["request_count"]
    metadata["prevalidated_evidence_reused"] = True
    return {"cues": cues, "metadata": metadata, "evidence_path": str(evidence_path)}


def create_source_caption_translation(
    source_path: Path,
    run_directory: Path,
    *,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    ocr_config_path: Path | None = None,
    translation_config_path: Path | None = None,
    translator: GeminiCaptionTranslator | None = None,
    resolver: GeminiMultimodalCaptionResolver | None = None,
    model_discovery: GeminiModelDiscoveryResult | None = None,
    progress: CaptionAnalysisProgress | None = None,
    analysis_only: bool = False,
) -> dict[str, Any]:
    progress = progress or CaptionAnalysisProgress(run_directory)
    progress.update(force=True, worker_state="active", analysis_stage="media_probe", current_item_id="source_media")
    media = media_summary(source_path)
    width = int(media["video"].get("width") or 0)
    height = int(media["video"].get("height") or 0)
    duration = float(media.get("duration_seconds") or 0)
    if width <= 0 or height <= 0 or duration <= 0:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    source_video_sha256 = sha256_file(source_path)
    cached_evidence = _load_prevalidated_source_caption_evidence(
        run_directory,
        source_video_sha256=source_video_sha256,
        width=width,
        height=height,
        duration=duration,
    )
    if cached_evidence is not None:
        return cached_evidence
    frame_dir = run_directory / "work" / "source_caption_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    sampled_total = max(1, math.ceil(duration / sample_seconds))
    progress.update(force=True, analysis_stage="frame_extraction", sampled_frames_total=sampled_total, current_item_id="ffmpeg_frame_extraction")
    frames = _sample_frames(source_path, frame_dir, duration, sample_seconds, progress=progress)
    dense_frame_dir = run_directory / "work" / "source_caption_dense_frames"
    dense_frames: list[dict[str, Any]] = []
    try:
        ocr_payload: dict[str, Any] | None = None
        visual_tracks: list[dict[str, Any]] = []
        progress.update(force=True, analysis_stage="visual_detection", sampled_frames_total=len(frames), current_item_id="frame_00000")
        selected, upper_selected = select_replacement_caption_regions(
            frames,
            width=width,
            height=height,
            progress=progress,
        )
        if selected is not None:
            visual_tracks.append(selected)
        if upper_selected is not None:
            visual_tracks.append(upper_selected)
        if visual_tracks:
            selected = _combine_visual_caption_tracks(visual_tracks, width=width, height=height)
        if selected is None:
            try:
                ocr_payload = _run_ocr_batches(
                    frames,
                    config_path=ocr_config_path,
                    progress=progress,
                )
            except OCRRuntimeError as exc:
                raise CaptionAnalysisError("CAPTION_OCR_RUNTIME_FAILED", str(exc)) from exc
            candidates = _ocr_candidates(frames, ocr_payload, width=width, height=height)
            selected = select_replacement_caption_track(frames, candidates, width=width, height=height)
            selected = selected or select_caption_track(candidates, width=width, height=height)
        if selected is None:
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        dense_step = sample_seconds if selected.get("selection_policy") == "pixel_visible_embedded_caption_zone" else min(sample_seconds, 0.125)
        dense_crop: tuple[int, int, int, int] | None = None
        dense_strategy = "representative_visual_caption_crops"
        intervals: list[dict[str, Any]] = []
        if selected.get("selection_policy") == "pixel_visible_embedded_caption_zone":
            dense_frame_dir.mkdir(parents=True, exist_ok=True)
            for track_index, track in enumerate(selected.get("visual_tracks") or [selected], start=1):
                dense_frames.extend(
                    _sample_representative_visual_caption_crops(
                        source_path,
                        dense_frame_dir,
                        track.get("detections", []),
                        width=width,
                        height=height,
                        duration=duration,
                        sample_seconds=sample_seconds,
                        name_prefix=f"visual_caption_{track_index:02d}",
                        track_name=str(track.get("track_name") or f"track_{track_index:02d}"),
                        progress=progress,
                    )
                )
            if dense_frames:
                try:
                    dense_payload = _run_ocr_batches(
                        dense_frames,
                        config_path=ocr_config_path,
                        progress=progress,
                    )
                except OCRRuntimeError as exc:
                    raise CaptionAnalysisError("CAPTION_OCR_RUNTIME_FAILED", str(exc)) from exc
                ocr_payload = ocr_payload or dense_payload
                dense_candidates = _ocr_candidates(dense_frames, dense_payload, width=width, height=height)
                intervals = build_visual_caption_intervals_from_representative_ocr(
                    dense_frames,
                    dense_candidates,
                    sample_seconds=sample_seconds,
                    duration_seconds=duration,
                    width=width,
                    height=height,
                )
                intervals = sorted(
                    intervals,
                    key=lambda item: (
                        float(item["start_time"]),
                        int((item.get("source_bbox") or {}).get("top_y") or 0),
                        str(item.get("source_text") or ""),
                    ),
                )
        if not intervals:
            dense_strategy = "dense_caption_crop"
            dense_crop = _dense_caption_crop(selected["region"], width=width, height=height)
            dense_frame_dir.mkdir(parents=True, exist_ok=True)
            dense_frames = _sample_frames(
                source_path,
                dense_frame_dir,
                duration,
                dense_step,
                crop=dense_crop,
                progress=progress,
            )
            try:
                dense_payload = _run_ocr_batches(
                    dense_frames,
                    config_path=ocr_config_path,
                    progress=progress,
                )
            except OCRRuntimeError as exc:
                raise CaptionAnalysisError("CAPTION_OCR_RUNTIME_FAILED", str(exc)) from exc
            ocr_payload = ocr_payload or dense_payload
            dense_candidates = _ocr_candidates(dense_frames, dense_payload, width=width, height=height)
            target_detections = _target_region_detections(
                dense_candidates,
                selected,
                height=height,
            )
            intervals = build_caption_intervals(
                target_detections,
                sample_seconds=dense_step,
                duration_seconds=duration,
                width=width,
                height=height,
            )
        if not intervals:
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        for index, interval in enumerate(intervals, start=1):
            interval["id"] = f"OCR_{index:04d}"
            interval["previous_source_text"] = intervals[index - 2]["source_text"] if index > 1 else ""
            interval["next_source_text"] = intervals[index]["source_text"] if index < len(intervals) else ""
        if analysis_only:
            progress.update(force=True, analysis_stage="track_creation", current_item_id="diagnostic_tracks")
            return {
                "tracks": intervals,
                "metadata": {
                    "sample_count": len(frames),
                    "dense_sample_count": len(dense_frames),
                    "caption_interval_count": len(intervals),
                    "provider_usage": {"request_count": 0, "retry_count": 0},
                    "analysis_only": True,
                },
            }
        overrides: dict[str, dict[str, Any]] = {}
        try:
            for interval in intervals:
                try:
                    override = load_matching_caption_override(
                        source_path=source_path,
                        source_video_sha256=source_video_sha256,
                        interval=interval,
                    )
                except CaptionOverrideValidationError as exc:
                    if selected.get("selection_policy") == "pixel_visible_embedded_caption_zone" and _is_stale_caption_override_error(exc):
                        continue
                    raise
                if override is not None:
                    overrides[str(interval["id"])] = override
        except CaptionOverrideValidationError as exc:
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE) from exc
        override_ids = set(overrides)
        blocked_ids = {
            str(item["id"])
            for item in intervals
            if str(item["id"]) not in override_ids
            and (
            not item.get("ocr_gate_pass", False)
            or float(item.get("ocr_confidence") or 0) < MIN_OCR_CONFIDENCE
            or float(item.get("agreement") or 0) < 0.6
            )
        }
        blocked_intervals = [item for item in intervals if str(item["id"]) in blocked_ids]
        eligible_intervals = [
            item
            for item in intervals
            if str(item["id"]) not in blocked_ids and str(item["id"]) not in override_ids
        ]
        if not eligible_intervals and not blocked_intervals:
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        gemini_config = load_gemini_translation_config(translation_config_path)
        discovery = model_discovery or discover_gemini_models(gemini_config)
        if not discovery.free_tier_verified or not discovery.selected_model:
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        gemini_config = replace(gemini_config, model=str(discovery.selected_model))
        caption_translator = translator or GeminiCaptionTranslator(gemini_config)
        multimodal_resolver = resolver or GeminiMultimodalCaptionResolver(gemini_config)
        caption_requests = []
        for interval in eligible_intervals:
            caption_requests.append({
                "id": str(interval["id"]),
                "source_text": interval["source_text"],
                "previous_source_text": interval.get("previous_source_text") or "",
                "next_source_text": interval.get("next_source_text") or "",
                "mode": "text_only",
            })
        blocked_requests = _blocked_interval_requests(
            source_path,
            blocked_intervals,
            width=width,
            height=height,
        )
        translated_by_id: dict[str, dict[str, Any]] = {}
        benchmark_rows: list[dict[str, Any]] = []
        for interval_id, override in overrides.items():
            translated_by_id[interval_id] = {
                "translated_text": override["approved_english"],
                "corrected_source_text": override["corrected_chinese"],
                "runtime": HUMAN_REVIEWED_CAPTION_PROVENANCE,
                "model": None,
                "override_record_hash": override["record_sha256"],
            }
            benchmark_rows.append(
                {
                    "interval_id": interval_id,
                    "mode": HUMAN_REVIEWED_CAPTION_PROVENANCE,
                    "status": "PASS",
                    "provider": "human_review",
                    "model": "",
                    "source_text": override["corrected_chinese"],
                    "translated_text": override["approved_english"],
                    "benchmark_scope": "1/31",
                    "gemini_verdict": "REJECTED",
                    "human_review_verdict": "PASS",
                    "unresolved_characters": 0,
                }
            )
        provider_usage = {
            "request_count": 0,
            "retry_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "request_ids": [],
        }
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="caption-translation")
        try:
            futures = []
            if caption_requests:
                futures.append(
                    executor.submit(_resolve_text_only_requests, caption_translator, caption_requests)
                )
            if blocked_requests:
                futures.append(
                    executor.submit(_resolve_multimodal_requests, multimodal_resolver, blocked_requests)
                )
            for index, future in enumerate(futures, start=1):
                item_id = f"translation_batch_{index:02d}"
                progress.update(force=True, analysis_stage="translation_resolution", current_item_id=item_id)
                resolved_batch, batch_usage, batch_rows = _await_translation_batch_with_heartbeat(
                    future,
                    progress=progress,
                    current_item_id=item_id,
                )
                translated_by_id.update(resolved_batch)
                benchmark_rows.extend(batch_rows)
                provider_usage["request_count"] += batch_usage["request_count"]
                provider_usage["retry_count"] += batch_usage["retry_count"]
                provider_usage["input_tokens"] += batch_usage["input_tokens"]
                provider_usage["output_tokens"] += batch_usage["output_tokens"]
                provider_usage["cache_hits"] += batch_usage["cache_hits"]
                provider_usage["cache_misses"] += batch_usage["cache_misses"]
                provider_usage["request_ids"].extend(batch_usage["request_ids"])
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        translated = []
        for interval in intervals:
            translated_item = translated_by_id.get(interval["id"])
            if translated_item is None:
                raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
            translated.append(
                {
                    "id": interval["id"],
                    "translated_text": translated_item["translated_text"],
                    "runtime": translated_item["runtime"],
                    "model": translated_item["model"],
                    "source_text": translated_item.get("corrected_source_text", interval["source_text"]),
                    "override_record_hash": translated_item.get("override_record_hash"),
                }
            )
        _validate_translations(intervals, translated)
        cues = []
        for index, (interval, translation) in enumerate(zip(intervals, translated), start=1):
            cues.append({
                "cue_id": f"OCR_{index:04d}",
                "start_ms": round(interval["start_time"] * 1000),
                "end_ms": round(interval["end_time"] * 1000),
                "source_text": translation["source_text"],
                "text": translation["translated_text"],
                "source_bbox": interval["source_bbox"],
                "source_interval": {
                    "start_time": interval["start_time"],
                    "end_time": interval["end_time"],
                },
                "line_count": interval["line_count"],
                "ocr_confidence": interval["ocr_confidence"],
                "needs_pixel_refresh": True,
                "geometry_source": "source_caption_analysis",
            })
        benchmark_csv_path = _write_task41h_full_benchmark_csv(benchmark_rows)
        overall_mode = SOURCE_CAPTION_HUMAN_REVIEW_MODE if overrides else SOURCE_CAPTION_GEMINI_MODE
        metadata = {
            "schema_version": 1,
            "mode": overall_mode,
            "subtitle_provenance": overall_mode,
            "ocr_provider": (ocr_payload or {}).get("runtime", "paddleocr"),
            "ocr_model": (ocr_payload or {}).get("model_version", "paddleocr-2.10.0"),
            "translation_provider": "gemini",
            "translation_model": gemini_config.model,
            "automatic_caption_count": len(intervals) - len(overrides),
            "human_reviewed_caption_count": len(overrides),
            "human_reviewed_interval_ids": sorted(overrides),
            "override_record_hashes": sorted(
                override["record_sha256"] for override in overrides.values()
            ),
            "operator_notice": (
                f"{len(overrides)} phụ đề đã dùng nội dung được người vận hành xác minh."
                if overrides
                else None
            ),
            "caption_region": selected["region"],
            "caption_region_normalized": selected["region_normalized"],
            "caption_interval_count": len(cues),
            "caption_interval_benchmark": {
                "total_intervals": len(intervals),
                "eligible_intervals": len(eligible_intervals),
                "blocked_intervals": len(blocked_intervals),
                "resolved_intervals": len(translated),
                "status": "PASS" if len(translated) == len(intervals) else "BLOCKED",
                "selected_model": discovery.selected_model,
                "selected_reason": discovery.selected_reason,
                "free_tier_verified": discovery.free_tier_verified,
                "benchmark_csv": str(benchmark_csv_path),
            },
            "ocr_confidence": {
                "minimum": round(min(item["ocr_confidence"] for item in intervals), 6),
                "median": round(statistics.median(item["ocr_confidence"] for item in intervals), 6),
            },
            "hud_rejection": selected["rejection_summary"],
            "sample_seconds": sample_seconds,
            "sample_count": len(frames),
            "dense_sample_seconds": dense_step,
            "dense_sample_count": len(dense_frames),
            "dense_caption_strategy": dense_strategy,
            "dense_caption_crop": (
                {
                    "left_x": dense_crop[0],
                    "top_y": dense_crop[1],
                    "right_x": dense_crop[2] - 1,
                    "bottom_y": dense_crop[3] - 1,
                }
                if dense_crop is not None
                else None
            ),
            "provider_usage": provider_usage,
            "external_calls": provider_usage["request_count"],
            "media_uploads": 0,
            "downloads": 0,
        }
        evidence = {
            **metadata,
            "intervals": intervals,
            "translations": translated,
            "benchmark_rows": benchmark_rows,
        }
        evidence_path = run_directory / "subtitles" / "source_caption_gemini_translation.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        progress.update(force=True, analysis_stage="track_creation", current_item_id="translated_tracks")
        return {"cues": cues, "metadata": metadata, "evidence_path": str(evidence_path)}
    finally:
        _cleanup_sampled_frames(frames, frame_dir)
        _cleanup_sampled_frames(dense_frames, dense_frame_dir)


def _sample_frames(
    source_path: Path,
    frame_dir: Path,
    duration: float,
    step: float,
    *,
    crop: tuple[int, int, int, int] | None = None,
    progress: CaptionAnalysisProgress | None = None,
) -> list[dict[str, Any]]:
    # Sequential FFmpeg decoding avoids hundreds of expensive random seeks on
    # long GOP video, which was the dominant Task 42 analyzer bottleneck.
    return _sample_frames_with_ffmpeg(
        source_path,
        frame_dir,
        duration,
        step,
        crop=crop,
        progress=progress,
    )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valid_source_bbox(value: Any, *, width: int, height: int) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        left = int(value["left_x"])
        top = int(value["top_y"])
        right = int(value["right_x"])
        bottom = int(value["bottom_y"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= left < right < width and 0 <= top < bottom < height


def _sample_frames_with_ffmpeg(
    source_path: Path,
    frame_dir: Path,
    duration: float,
    step: float,
    *,
    crop: tuple[int, int, int, int] | None = None,
    progress: CaptionAnalysisProgress | None = None,
) -> list[dict[str, Any]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    frame_dir.mkdir(parents=True, exist_ok=True)
    for existing in frame_dir.glob("frame_*.jpg"):
        existing.unlink(missing_ok=True)
    filters = [f"fps={max(1.0, round(1.0 / max(step, 0.001), 6))}"]
    if crop is not None:
        left, top, right, bottom = crop
        filters.insert(0, f"crop={max(1, right - left)}:{max(1, bottom - top)}:{max(0, left)}:{max(0, top)}")
    output_pattern = frame_dir / "frame_%05d.jpg"
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vf",
        ",".join(filters),
        "-q:v",
        "2",
        str(output_pattern),
    ]
    started = time.monotonic()
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        while process.poll() is None:
            if progress is not None:
                progress.heartbeat("ffmpeg_frame_extraction", force=True)
            if time.monotonic() - started > FFMPEG_EXTRACTION_TIMEOUT_SECONDS:
                process.terminate()
                process.wait(timeout=10)
                raise CaptionAnalysisError("CAPTION_FRAME_EXTRACTION_TIMEOUT", "Frame extraction exceeded its bounded timeout")
            time.sleep(2)
        stderr = process.stderr.read() if process.stderr is not None else ""
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, command, stderr=stderr)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE) from exc
    output_files = sorted(frame_dir.glob("frame_*.jpg"))
    if not output_files:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    count = max(1, math.ceil(duration / step))
    frames = []
    for index, path in enumerate(output_files[:count]):
        time_s = min(index * step, max(0.0, duration - 0.02))
        frames.append(
            {
                "index": len(frames),
                "time": round(time_s, 3),
                "path": path,
                "origin_x": 0 if crop is None else crop[0],
                "origin_y": 0 if crop is None else crop[1],
            }
        )
    if not frames:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    return frames


def _cleanup_sampled_frames(frames: list[dict[str, Any]], frame_dir: Path) -> None:
    for frame in frames:
        frame["path"].unlink(missing_ok=True)
    if frame_dir.exists() and not any(frame_dir.iterdir()):
        frame_dir.rmdir()


def _run_ocr_batches(
    frames: list[dict[str, Any]],
    *,
    config_path: Path | None,
    progress: CaptionAnalysisProgress,
) -> dict[str, Any]:
    batches = [frames[index : index + OCR_BATCH_SIZE] for index in range(0, len(frames), OCR_BATCH_SIZE)]
    snapshot = progress.snapshot()
    completed_before = int(snapshot.get("ocr_batches_completed") or 0)
    total_before = int(snapshot.get("ocr_batches_total") or 0)
    progress.update(
        force=True,
        analysis_stage="ocr",
        ocr_batches_total=total_before + len(batches),
        current_item_id="ocr_batch_0001" if batches else None,
    )
    merged_frames: list[dict[str, Any]] = []
    runtime = "paddleocr"
    model_version = "paddleocr-2.10.0"
    for index, batch in enumerate(batches, start=1):
        progress.heartbeat(f"ocr_batch_{index:04d}", force=True)
        payload = run_ocr_on_images(
            [item["path"] for item in batch],
            config_path=config_path,
            heartbeat_callback=lambda: progress.heartbeat(f"ocr_batch_{index:04d}", force=True),
        )
        payload_frames = payload.get("frames") if isinstance(payload, dict) else None
        if not isinstance(payload_frames, list) or len(payload_frames) != len(batch):
            raise CaptionAnalysisError("CAPTION_OCR_BATCH_CONTRACT_ERROR", f"OCR batch {index} returned an invalid frame count")
        merged_frames.extend(payload_frames)
        runtime = str(payload.get("runtime") or runtime)
        model_version = str(payload.get("model_version") or model_version)
        progress.update(
            force=True,
            ocr_batches_completed=completed_before + index,
            current_item_id=f"ocr_batch_{index:04d}",
        )
    return {
        "ok": True,
        "frames": merged_frames,
        "runtime": runtime,
        "model_version": model_version,
    }


def _await_translation_batch_with_heartbeat(
    future: Any,
    *,
    progress: CaptionAnalysisProgress,
    current_item_id: str,
    timeout_seconds: float = PROVIDER_BATCH_TIMEOUT_SECONDS,
    poll_seconds: float = TRANSLATION_FUTURE_POLL_SECONDS,
    monotonic: Any = time.monotonic,
) -> Any:
    deadline = monotonic() + timeout_seconds
    while True:
        remaining_seconds = deadline - monotonic()
        if remaining_seconds <= 0:
            future.cancel()
            raise CaptionAnalysisError("CAPTION_TRANSLATION_BATCH_TIMEOUT", "Caption translation batch timed out")
        try:
            return future.result(timeout=min(poll_seconds, remaining_seconds))
        except FutureTimeoutError as exc:
            progress.update(
                force=True,
                analysis_stage="translation_resolution",
                current_item_id=current_item_id,
            )
            if monotonic() >= deadline:
                future.cancel()
                raise CaptionAnalysisError("CAPTION_TRANSLATION_BATCH_TIMEOUT", "Caption translation batch timed out") from exc


def _resolve_text_only_requests(
    translator: GeminiCaptionTranslator,
    caption_requests: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    result = translator.translate(caption_requests)
    resolved: dict[str, dict[str, Any]] = {}
    benchmark_rows: list[dict[str, Any]] = []
    for request, translation in zip(caption_requests, result.translations):
        interval_id = str(request["id"])
        resolved[interval_id] = {
            "translated_text": str(translation["english"]),
            "runtime": "gemini",
            "model": result.model,
        }
        benchmark_rows.append(
            {
                "interval_id": interval_id,
                "mode": "text_only",
                "status": "PASS",
                "provider": translator.provider_name,
                "model": result.model,
                "source_text": request["source_text"],
                "translated_text": translation["english"],
                "benchmark_scope": "27/31",
            }
        )
    return resolved, {
        "request_count": result.request_count,
        "retry_count": result.retry_count,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cache_hits": result.cache_hits,
        "cache_misses": result.cache_misses,
        "request_ids": result.request_ids,
    }, benchmark_rows


def _resolve_multimodal_requests(
    resolver: GeminiMultimodalCaptionResolver,
    blocked_requests: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    resolved: dict[str, dict[str, Any]] = {}
    benchmark_rows: list[dict[str, Any]] = []
    request_count = retry_count = input_tokens = output_tokens = cache_hits = cache_misses = 0
    request_ids: list[str] = []
    executor = ThreadPoolExecutor(max_workers=max(1, min(4, len(blocked_requests))), thread_name_prefix="caption-multimodal")
    try:
        futures = {
            executor.submit(resolver.resolve, request): (index, request)
            for index, request in enumerate(blocked_requests)
        }
        ordered = []
        for future, (index, request) in futures.items():
            try:
                result = future.result(timeout=PROVIDER_BATCH_TIMEOUT_SECONDS)
            except FutureTimeoutError as exc:
                for outstanding in futures:
                    outstanding.cancel()
                raise CaptionAnalysisError("CAPTION_MULTIMODAL_ITEM_TIMEOUT", f"Multimodal item {request.get('id')} timed out") from exc
            ordered.append((index, request, result))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    for index, request, result in sorted(ordered, key=lambda item: item[0]):
        interval = result.intervals[0]
        interval_id = str(interval["id"])
        resolved[interval_id] = {
            "translated_text": interval["english"],
            "runtime": "gemini_multimodal",
            "model": result.model,
        }
        benchmark_rows.append(
            {
                "interval_id": interval_id,
                "mode": "multimodal",
                "status": "PASS",
                "provider": resolver.provider_name,
                "model": result.model,
                "source_text": request["source_chinese"],
                "translated_text": interval["english"],
                "benchmark_scope": "4/31",
            }
        )
        request_count += result.request_count
        retry_count += result.retry_count
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        cache_hits += result.cache_hits
        cache_misses += result.cache_misses
        request_ids.extend(result.request_ids)
    return resolved, {
        "request_count": request_count,
        "retry_count": retry_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "request_ids": request_ids,
    }, benchmark_rows


def _blocked_interval_requests(
    source_path: Path,
    intervals: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    return [
        _build_multimodal_interval_request(source_path, interval, width=width, height=height)
        for interval in intervals
    ]


def _build_multimodal_interval_request(
    source_path: Path,
    interval: dict[str, Any],
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    interval_id = str(interval.get("id") or "")
    bbox = interval.get("source_bbox") or {}
    if not isinstance(bbox, dict):
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    sample_start = float(interval["start_time"])
    sample_end = float(interval["end_time"])
    sample_mid = round((sample_start + sample_end) / 2, 3)
    crop_box = _expanded_caption_bbox(bbox, width=width, height=height)
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    visual_crops = []
    cap = cv2.VideoCapture(str(source_path))
    try:
        use_cv2_capture = cap.isOpened()
        for label, time_s in (("start", sample_start), ("mid", sample_mid), ("end", sample_end)):
            if use_cv2_capture:
                try:
                    frame = read_frame(cap, time_s, width, height)
                except RuntimeError:
                    frame = _read_frame_with_ffmpeg(source_path, time_s, width=width, height=height)
            else:
                frame = _read_frame_with_ffmpeg(source_path, time_s, width=width, height=height)
            crop = frame[crop_box[1] : crop_box[3] + 1, crop_box[0] : crop_box[2] + 1]
            crop = _upscale_caption_crop(crop)
            ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if not ok:
                raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
            visual_crops.append(
                {
                    "label": label,
                    "time": round(time_s, 3),
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(encoded.tobytes()).decode("ascii"),
                }
            )
    finally:
        cap.release()
    audio_dir = Path(tempfile.mkdtemp(prefix=f"task41h_{interval_id}_", dir=str(Path(tempfile.gettempdir()))))
    audio_path = audio_dir / "context.wav"
    audio_start = max(0.0, sample_start - 0.25)
    audio_duration = min(3.0, max(1.0, (sample_end - sample_start) + 0.75))
    try:
        extract_asr_audio(source_path, audio_path, start_seconds=audio_start, duration_seconds=audio_duration)
        audio_data = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    except Exception as exc:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE) from exc
    finally:
        audio_path.unlink(missing_ok=True)
        audio_dir.rmdir()
    previous_text = str(interval.get("previous_source_text") or "")
    next_text = str(interval.get("next_source_text") or "")
    source_text = str(interval.get("source_text") or "")
    candidate_variants = [
        str(item.get("text") or "").strip()
        for item in interval.get("ocr_candidates", [])
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    prompt = (
        f"Resolve caption interval {interval_id}. "
        f"Current OCR hypothesis: {source_text}. "
        f"OCR candidate variants: {json.dumps(candidate_variants, ensure_ascii=False)}. "
        f"Previous Chinese context: {previous_text or 'none'}. "
        f"Next Chinese context: {next_text or 'none'}. "
        "Use all three caption-only crops, the short local audio, and context to "
        "recover the exact natural Chinese subtitle and its concise en-US meaning. "
        "Return exactly one interval mapping."
    )
    return {
        "id": interval_id,
        "prompt": prompt,
        "source_chinese": source_text,
        "previous_source_chinese": previous_text,
        "next_source_chinese": next_text,
        "source_interval": {
            "start_time": sample_start,
            "end_time": sample_end,
        },
        "source_bbox": bbox,
        "visual_crops": visual_crops,
        "audio": {
            "data": audio_data,
            "mime_type": "audio/wav",
            "start_seconds": audio_start,
            "duration_seconds": audio_duration,
        },
    }


def _read_frame_with_ffmpeg(
    source_path: Path,
    time_s: float,
    *,
    width: int,
    height: int,
) -> Any:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    frame_dir = Path(tempfile.mkdtemp(prefix="task41h_frame_", dir=str(Path(tempfile.gettempdir()))))
    frame_path = frame_dir / "frame.jpg"
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-ss",
        f"{max(0.0, time_s):.3f}",
        "-frames:v",
        "1",
    ]
    if width > 0 and height > 0:
        command.extend(["-vf", f"scale={width}:{height}"])
    command.append(str(frame_path))
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        frame = cv2.imread(str(frame_path))
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE) from exc
    finally:
        frame_path.unlink(missing_ok=True)
        frame_dir.rmdir()
    if frame is None:
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    return frame


def _upscale_caption_crop(crop: Any) -> Any:
    crop_height, crop_width = crop.shape[:2]
    if crop_height <= 0 or crop_width <= 0 or crop_height >= 192:
        return crop
    scale = min(3.0, 192.0 / crop_height, 3072.0 / crop_width)
    if scale <= 1.0:
        return crop
    return cv2.resize(
        crop,
        (round(crop_width * scale), round(crop_height * scale)),
        interpolation=cv2.INTER_CUBIC,
    )


def _expanded_caption_bbox(bbox: dict[str, Any], *, width: int, height: int) -> tuple[int, int, int, int]:
    left = max(0, int(bbox.get("left_x") or 0))
    top = max(0, int(bbox.get("top_y") or 0))
    right = min(width - 1, int(bbox.get("right_x") or width - 1))
    bottom = min(height - 1, int(bbox.get("bottom_y") or height - 1))
    pad_x = max(12, round((right - left + 1) * 0.12))
    pad_y = max(8, round((bottom - top + 1) * 0.18))
    return (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width - 1, right + pad_x),
        min(height - 1, bottom + pad_y),
    )


def _write_task41h_full_benchmark_csv(rows: list[dict[str, Any]]) -> Path:
    benchmark_path = Path(tempfile.gettempdir()) / "task41h_full_benchmark.csv"
    benchmark_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "interval_id",
        "mode",
        "status",
        "provider",
        "model",
        "source_text",
        "translated_text",
        "benchmark_scope",
        "gemini_verdict",
        "human_review_verdict",
        "unresolved_characters",
    ]
    with benchmark_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: str(item.get("interval_id") or "")):
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    return benchmark_path


def _dense_caption_crop(
    region: dict[str, int],
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    vertical_padding = max(round(height * 0.055), region["bottom_y"] - region["top_y"] + 1)
    top = max(0, region["top_y"] - vertical_padding)
    bottom = min(height, region["bottom_y"] + vertical_padding + 1)
    return 0, top, width, bottom


def _upper_caption_crop(*, width: int, height: int) -> tuple[int, int, int, int]:
    return (
        max(0, round(width * 0.12)),
        max(0, round(height * 0.035)),
        min(width - 1, round(width * 0.88)),
        min(height - 1, round(height * 0.18)),
    )


def _target_region_detections(
    candidates: list[dict[str, Any]],
    selected: dict[str, Any],
    *,
    height: int,
) -> list[dict[str, Any]]:
    selected_center_y = statistics.median(item["center_y"] for item in selected["detections"])
    selected_height = statistics.median(item["height"] for item in selected["detections"])
    return [
        item
        for item in candidates
        if abs(item["center_y"] - selected_center_y) <= height * 0.045
        and selected_height * 0.65 <= item["height"] <= selected_height * 1.55
    ]


def _ocr_candidates(
    frames: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    results = payload.get("frames") if isinstance(payload.get("frames"), list) else []
    if len(results) != len(frames):
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
    candidates = []
    for sampled, result in zip(frames, results):
        for item in result.get("items", []):
            text = normalize_source_text(str(item.get("text") or ""))
            confidence = float(item.get("confidence") or 0)
            bbox = _ocr_bbox(item.get("box"))
            if not text or not is_cjk_text(text) or confidence < MIN_OCR_CONFIDENCE or bbox is None:
                continue
            origin_x = int(sampled.get("origin_x") or 0)
            origin_y = int(sampled.get("origin_y") or 0)
            if origin_x or origin_y:
                bbox = {
                    "left_x": bbox["left_x"] + origin_x,
                    "top_y": bbox["top_y"] + origin_y,
                    "right_x": bbox["right_x"] + origin_x,
                    "bottom_y": bbox["bottom_y"] + origin_y,
                }
            bbox_width = bbox["right_x"] - bbox["left_x"] + 1
            bbox_height = bbox["bottom_y"] - bbox["top_y"] + 1
            if bbox_width < width * 0.04 or bbox_height < height * 0.022:
                continue
            candidates.append({
                "time": sampled["time"],
                "text": text,
                "confidence": confidence,
                "bbox": bbox,
                "center_x": (bbox["left_x"] + bbox["right_x"]) / 2,
                "center_y": (bbox["top_y"] + bbox["bottom_y"]) / 2,
                "height": bbox_height,
                "unresolved_solid_glyph": _has_solid_caption_glyph(
                    sampled["path"],
                    {
                        "left_x": bbox["left_x"] - origin_x,
                        "top_y": bbox["top_y"] - origin_y,
                        "right_x": bbox["right_x"] - origin_x,
                        "bottom_y": bbox["bottom_y"] - origin_y,
                    },
                ),
            })
    return candidates


def select_caption_track(
    detections: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> dict[str, Any] | None:
    clusters: list[list[dict[str, Any]]] = []
    for detection in detections:
        match = None
        for cluster in clusters:
            center_x = statistics.median(item["center_x"] for item in cluster)
            center_y = statistics.median(item["center_y"] for item in cluster)
            text_height = statistics.median(item["height"] for item in cluster)
            if (
                abs(detection["center_x"] - center_x) <= width * 0.18
                and abs(detection["center_y"] - center_y) <= height * 0.08
                and abs(detection["height"] - text_height) <= height * 0.035
            ):
                match = cluster
                break
        (match if match is not None else clusters.append([]) or clusters[-1]).append(detection)

    eligible = []
    rejected_static = 0
    rejected_sparse = 0
    for cluster in clusters:
        counts = Counter(item["text"] for item in cluster)
        static_ratio = max(counts.values()) / len(cluster)
        if len(cluster) < 3 or len(counts) < 2:
            rejected_sparse += 1
            continue
        if static_ratio > 0.82:
            rejected_static += 1
            continue
        median_height = statistics.median(item["height"] for item in cluster)
        # Reject compact counters and UI labels before they can outvote a
        # sparse but real subtitle track.
        if median_height < height * 0.035:
            rejected_sparse += 1
            continue
        score = len(counts) * 2 + min(len(cluster), 80) / 20 - static_ratio * 3
        eligible.append({
            "detections": cluster,
            "unique_texts": len(counts),
            "static_ratio": static_ratio,
            "median_height": median_height,
            "score": score,
        })
    if not eligible:
        return None
    minimum_height = min(item["median_height"] for item in eligible)
    # Commentary captions use one consistent text scale. Large stylized game labels
    # may change often, but they are not allowed to outvote the caption track merely
    # by appearing in more frames.
    typographic_peers = [item for item in eligible if item["median_height"] <= minimum_height * 1.4]
    selected = max(typographic_peers, key=lambda item: item["score"])
    region = _robust_bbox([item["bbox"] for item in selected["detections"]])
    selected["region"] = region
    selected["region_normalized"] = {
        "left": round(region["left_x"] / width, 6),
        "top": round(region["top_y"] / height, 6),
        "right": round(region["right_x"] / width, 6),
        "bottom": round(region["bottom_y"] / height, 6),
    }
    selected["rejection_summary"] = {
        "candidate_clusters": len(clusters),
        "static_hud_clusters_rejected": rejected_static,
        "sparse_noise_clusters_rejected": rejected_sparse,
        "oversized_hud_clusters_rejected": len(eligible) - len(typographic_peers),
        "selected_unique_texts": selected["unique_texts"],
    }
    return selected


def select_replacement_caption_track(
    frames: list[dict[str, Any]],
    detections: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> dict[str, Any] | None:
    """Prefer the pixel-visible embedded subtitle zone over creator overlays.

    The OCR pass can see multiple CJK text tracks.  For replacement mode, the
    authoritative target is the visible subtitle-like glyph band that must be
    erased and reused for English, not whichever OCR cluster happens to score
    highest globally.
    """
    visual_detections = _visual_replacement_caption_detections(frames, width=width, height=height)
    if len(visual_detections) < 3:
        return None
    matched = []
    for detection in detections:
        for visual in visual_detections:
            if abs(float(detection["time"]) - float(visual["time"])) > DEFAULT_SAMPLE_SECONDS * 0.75:
                continue
            if _boxes_overlap_for_replacement(detection["bbox"], visual["bbox"], width=width, height=height):
                matched.append(detection)
                break
    if len(matched) < 3:
        return None
    selected = select_caption_track(matched, width=width, height=height)
    if selected is None:
        return None
    selected["replacement_visual_region"] = _robust_bbox([item["bbox"] for item in visual_detections])
    selected["replacement_visual_detection_count"] = len(visual_detections)
    selected["selection_policy"] = "pixel_visible_embedded_caption_zone"
    return selected


def select_replacement_caption_region(
    frames: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> dict[str, Any] | None:
    visual_detections = _visual_replacement_caption_detections(frames, width=width, height=height)
    if len(visual_detections) < 3:
        return None
    region = _robust_bbox([item["bbox"] for item in visual_detections])
    if region is None:
        return None
    center_y = statistics.median(item["center_y"] for item in visual_detections)
    text_height = statistics.median(item["height"] for item in visual_detections)
    if center_y < height * 0.55 or text_height < height * 0.025:
        return None
    return {
        "detections": visual_detections,
        "region": region,
        "region_normalized": {
            "left": round(region["left_x"] / width, 6),
            "top": round(region["top_y"] / height, 6),
            "right": round(region["right_x"] / width, 6),
            "bottom": round(region["bottom_y"] / height, 6),
        },
        "unique_texts": None,
        "static_ratio": None,
        "median_height": text_height,
        "score": len(visual_detections),
        "replacement_visual_region": region,
        "replacement_visual_detection_count": len(visual_detections),
        "selection_policy": "pixel_visible_embedded_caption_zone",
        "rejection_summary": {
            "candidate_clusters": 1,
            "static_hud_clusters_rejected": 0,
            "sparse_noise_clusters_rejected": 0,
            "oversized_hud_clusters_rejected": 0,
            "selected_unique_texts": None,
            "pixel_visible_replacement_detections": len(visual_detections),
        },
        "track_name": "lower_embedded_caption",
    }


def select_replacement_caption_regions(
    frames: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    progress: CaptionAnalysisProgress | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    lower_detections: list[dict[str, Any]] = []
    upper_detections: list[dict[str, Any]] = []
    total = len(frames)
    if progress is not None:
        progress.update(force=True, sampled_frames_total=total, sampled_frames_completed=0)
    for index, sampled in enumerate(frames, start=1):
        path = sampled.get("path")
        if path:
            frame = cv2.imread(str(path))
            if frame is not None:
                for target, detector in (
                    (lower_detections, detect_source_subtitle_bbox),
                    (upper_detections, _detect_upper_caption_bbox),
                ):
                    bbox, detail = detector(frame, output_width=width, output_height=height)
                    if bbox:
                        target.append(
                            {
                                "time": sampled["time"],
                                "path": path,
                                "bbox": bbox,
                                "center_x": (bbox["left_x"] + bbox["right_x"]) / 2,
                                "center_y": (bbox["top_y"] + bbox["bottom_y"]) / 2,
                                "height": bbox["bottom_y"] - bbox["top_y"] + 1,
                                "component_count": detail.get("component_count", 0),
                                "pixel_count": detail.get("pixel_count", 0),
                                "glyph_signature": _caption_glyph_signature(frame, bbox),
                            }
                        )
        if progress is not None:
            progress.update(
                force=index == total or index % 8 == 0,
                sampled_frames_completed=index,
                current_item_id=f"frame_{index:05d}",
            )
    return (
        _caption_region_from_detections(lower_detections, width=width, height=height, upper=False),
        _caption_region_from_detections(upper_detections, width=width, height=height, upper=True),
    )


def _caption_region_from_detections(
    detections: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    upper: bool,
) -> dict[str, Any] | None:
    if len(detections) < 3:
        return None
    region = _robust_bbox([item["bbox"] for item in detections])
    if region is None:
        return None
    center_y = statistics.median(item["center_y"] for item in detections)
    text_height = statistics.median(item["height"] for item in detections)
    if upper:
        if center_y > height * 0.22 or text_height < height * 0.018:
            return None
    elif center_y < height * 0.55 or text_height < height * 0.025:
        return None
    track_name = "upper_overlay_caption" if upper else "lower_embedded_caption"
    return {
        "detections": detections,
        "region": region,
        "region_normalized": {
            "left": round(region["left_x"] / width, 6),
            "top": round(region["top_y"] / height, 6),
            "right": round(region["right_x"] / width, 6),
            "bottom": round(region["bottom_y"] / height, 6),
        },
        "unique_texts": None,
        "static_ratio": None,
        "median_height": text_height,
        "score": len(detections),
        "replacement_visual_region": region,
        "replacement_visual_detection_count": len(detections),
        "selection_policy": "pixel_visible_embedded_caption_zone",
        "rejection_summary": {
            "candidate_clusters": 1,
            "static_hud_clusters_rejected": 0,
            "sparse_noise_clusters_rejected": 0,
            "oversized_hud_clusters_rejected": 0,
            "selected_unique_texts": None,
            "pixel_visible_replacement_detections": len(detections),
        },
        "track_name": track_name,
    }


def select_upper_replacement_caption_region(
    frames: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> dict[str, Any] | None:
    visual_detections = _upper_replacement_caption_detections(frames, width=width, height=height)
    if len(visual_detections) < 3:
        return None
    region = _robust_bbox([item["bbox"] for item in visual_detections])
    if region is None:
        return None
    center_y = statistics.median(item["center_y"] for item in visual_detections)
    text_height = statistics.median(item["height"] for item in visual_detections)
    if center_y > height * 0.22 or text_height < height * 0.018:
        return None
    return {
        "detections": visual_detections,
        "region": region,
        "region_normalized": {
            "left": round(region["left_x"] / width, 6),
            "top": round(region["top_y"] / height, 6),
            "right": round(region["right_x"] / width, 6),
            "bottom": round(region["bottom_y"] / height, 6),
        },
        "unique_texts": None,
        "static_ratio": None,
        "median_height": text_height,
        "score": len(visual_detections),
        "replacement_visual_region": region,
        "replacement_visual_detection_count": len(visual_detections),
        "selection_policy": "pixel_visible_embedded_caption_zone",
        "rejection_summary": {
            "candidate_clusters": 1,
            "static_hud_clusters_rejected": 0,
            "sparse_noise_clusters_rejected": 0,
            "oversized_hud_clusters_rejected": 0,
            "selected_unique_texts": None,
            "pixel_visible_upper_replacement_detections": len(visual_detections),
        },
        "track_name": "upper_overlay_caption",
    }


def _combine_visual_caption_tracks(
    tracks: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    if len(tracks) == 1:
        selected = dict(tracks[0])
        selected["visual_tracks"] = tracks
        return selected
    region = _union_bbox([track["region"] for track in tracks])
    if region is None:
        return dict(tracks[0])
    detections = [item for track in tracks for item in track.get("detections", [])]
    return {
        "detections": detections,
        "visual_tracks": tracks,
        "region": region,
        "region_normalized": {
            "left": round(region["left_x"] / width, 6),
            "top": round(region["top_y"] / height, 6),
            "right": round(region["right_x"] / width, 6),
            "bottom": round(region["bottom_y"] / height, 6),
        },
        "unique_texts": None,
        "static_ratio": None,
        "median_height": statistics.median(track["median_height"] for track in tracks),
        "score": sum(len(track.get("detections", [])) for track in tracks),
        "replacement_visual_region": region,
        "replacement_visual_detection_count": len(detections),
        "selection_policy": "pixel_visible_embedded_caption_zone",
        "rejection_summary": {
            "candidate_clusters": len(tracks),
            "static_hud_clusters_rejected": 0,
            "sparse_noise_clusters_rejected": 0,
            "oversized_hud_clusters_rejected": 0,
            "selected_unique_texts": None,
            "pixel_visible_replacement_detections": len(detections),
            "track_names": [str(track.get("track_name") or "") for track in tracks],
        },
        "track_name": "multi_track_visible_caption",
    }


def _visual_replacement_caption_detections(
    frames: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    visual = []
    for sampled in frames:
        path = sampled.get("path")
        if not path:
            continue
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        bbox, detail = detect_source_subtitle_bbox(frame, output_width=width, output_height=height)
        if not bbox:
            continue
        visual.append(
            {
                "time": sampled["time"],
                "bbox": bbox,
                "center_x": (bbox["left_x"] + bbox["right_x"]) / 2,
                "center_y": (bbox["top_y"] + bbox["bottom_y"]) / 2,
                "height": bbox["bottom_y"] - bbox["top_y"] + 1,
                "component_count": detail.get("component_count", 0),
                "pixel_count": detail.get("pixel_count", 0),
                "glyph_signature": _caption_glyph_signature(frame, bbox),
            }
        )
    return visual


def _upper_replacement_caption_detections(
    frames: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    visual = []
    for sampled in frames:
        path = sampled.get("path")
        if not path:
            continue
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        bbox, detail = _detect_upper_caption_bbox(frame, output_width=width, output_height=height)
        if not bbox:
            continue
        visual.append(
            {
                "time": sampled["time"],
                "bbox": bbox,
                "center_x": (bbox["left_x"] + bbox["right_x"]) / 2,
                "center_y": (bbox["top_y"] + bbox["bottom_y"]) / 2,
                "height": bbox["bottom_y"] - bbox["top_y"] + 1,
                "component_count": detail.get("component_count", 0),
                "pixel_count": detail.get("pixel_count", 0),
                "glyph_signature": _caption_glyph_signature(frame, bbox),
            }
        )
    return visual


def _detect_upper_caption_bbox(frame_bgr: Any, *, output_width: int, output_height: int) -> tuple[dict[str, int] | None, dict[str, int]]:
    detection_frame = cv2.resize(frame_bgr, (1280, 720))
    roi_y0, roi_y1 = 34, 138
    roi_x0, roi_x1 = 190, 1090
    roi = detection_frame[roi_y0:roi_y1, roi_x0:roi_x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bright = cv2.inRange(hsv, (0, 0, 168), (180, 120, 255))
    edges = cv2.Canny(gray, 60, 180)
    mask = cv2.bitwise_and(bright, cv2.dilate(edges, None, iterations=1))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    num, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    components = []
    for label in range(1, num):
        x, y, w, h, area = stats[label]
        abs_x = int(x + roi_x0)
        abs_y = int(y + roi_y0)
        if area < 8 or h < 3 or abs_y < 42 or abs_y > 132:
            continue
        components.append((abs_x, abs_y, int(w), int(h), int(area)))
    if not components:
        return None, {"component_count": 0, "pixel_count": int(mask.sum() // 255)}
    scale_x = output_width / 1280
    scale_y = output_height / 720
    bbox = {
        "left_x": round(min(x for x, _, _, _, _ in components) * scale_x),
        "top_y": round(min(y for _, y, _, _, _ in components) * scale_y),
        "right_x": round(max((x + w - 1) for x, _, w, _, _ in components) * scale_x),
        "bottom_y": round(max((y + h - 1) for _, y, _, h, _ in components) * scale_y),
    }
    box_width = bbox["right_x"] - bbox["left_x"] + 1
    box_height = bbox["bottom_y"] - bbox["top_y"] + 1
    if box_width < max(90, round(output_width * 0.045)) or box_height < max(18, round(output_height * 0.018)):
        return None, {
            "component_count": len(components),
            "pixel_count": int(mask.sum() // 255),
            "rejected": "bbox_too_small",
        }
    return bbox, {"component_count": len(components), "pixel_count": int(mask.sum() // 255)}


def _sample_representative_visual_caption_crops(
    source_path: Path,
    frame_dir: Path,
    detections: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    duration: float,
    sample_seconds: float,
    name_prefix: str = "visual_caption",
    track_name: str = "visual_caption",
    progress: CaptionAnalysisProgress | None = None,
) -> list[dict[str, Any]]:
    groups = _visual_replacement_caption_groups(
        detections,
        sample_seconds=sample_seconds,
        width=width,
        height=height,
    )
    if not groups:
        return []
    completed_before = int((progress.snapshot() if progress is not None else {}).get("dense_crops_completed") or 0)
    total_before = int((progress.snapshot() if progress is not None else {}).get("dense_crops_total") or 0)
    if progress is not None:
        progress.update(
            force=True,
            analysis_stage="dense_crop_generation",
            dense_crops_total=total_before + len(groups),
            current_item_id=f"{name_prefix}_0001",
        )
    frame_dir.mkdir(parents=True, exist_ok=True)
    representative_frames: list[dict[str, Any]] = []
    cap = cv2.VideoCapture(str(source_path))
    try:
        use_cv2_capture = cap.isOpened()
        for index, group in enumerate(groups, start=1):
            group_bbox = _union_bbox([item["bbox"] for item in group])
            if group_bbox is None:
                raise CaptionAnalysisError("CAPTION_CROP_BBOX_MISSING", f"Missing crop bbox for {name_prefix}_{index:04d}")
            representative = group[len(group) // 2]
            time_s = float(representative["time"])
            sampled_path = representative.get("path")
            frame = cv2.imread(str(sampled_path)) if sampled_path else None
            if frame is None and use_cv2_capture:
                try:
                    frame = read_frame(cap, time_s, width, height)
                except RuntimeError:
                    frame = _read_frame_with_ffmpeg(source_path, time_s, width=width, height=height)
            elif frame is None:
                frame = _read_frame_with_ffmpeg(source_path, time_s, width=width, height=height)
            prefer_ocr_bbox = track_name == "upper_overlay_caption"
            crop_box = _upper_caption_crop(width=width, height=height) if prefer_ocr_bbox else _expanded_caption_bbox(group_bbox, width=width, height=height)
            left, top, right, bottom = crop_box
            crop = frame[top : bottom + 1, left : right + 1]
            if crop.size == 0:
                raise CaptionAnalysisError("CAPTION_CROP_EMPTY", f"Empty crop for {name_prefix}_{index:04d}")
            path = frame_dir / f"{name_prefix}_{index:04d}.jpg"
            if not cv2.imwrite(str(path), crop, [cv2.IMWRITE_JPEG_QUALITY, 92]):
                raise CaptionAnalysisError("CAPTION_CROP_WRITE_FAILED", f"Unable to write {name_prefix}_{index:04d}")
            representative_frames.append(
                {
                    "index": len(representative_frames),
                    "time": round(time_s, 3),
                    "path": path,
                    "origin_x": left,
                    "origin_y": top,
                    "visual_group_start_time": round(max(0.0, float(group[0]["time"]) - sample_seconds / 2), 3),
                    "visual_group_end_time": round(min(duration, float(group[-1]["time"]) + sample_seconds / 2), 3),
                    "visual_group_bbox": group_bbox,
                    "candidate_match_bbox": {
                        "left_x": left,
                        "top_y": top,
                        "right_x": right,
                        "bottom_y": bottom,
                    },
                    "prefer_ocr_bbox": prefer_ocr_bbox,
                    "visual_group_sample_count": len(group),
                    "visual_track_name": track_name,
                }
            )
            if progress is not None:
                progress.update(
                    force=index == len(groups) or index % 4 == 0,
                    dense_crops_completed=completed_before + index,
                    current_item_id=f"{name_prefix}_{index:04d}",
                )
    finally:
        cap.release()
    return representative_frames


def _visual_replacement_caption_groups(
    detections: list[dict[str, Any]],
    *,
    sample_seconds: float,
    width: int,
    height: int,
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    max_gap = max(sample_seconds * 2.2, 0.85)
    for detection in sorted(detections, key=lambda item: float(item["time"])):
        if not groups:
            groups.append([detection])
            continue
        previous = groups[-1][-1]
        group_start = float(groups[-1][0]["time"])
        time_gap = float(detection["time"]) - float(previous["time"])
        signature_distance = _caption_signature_distance(
            str(previous.get("glyph_signature") or ""),
            str(detection.get("glyph_signature") or ""),
        )
        bbox_overlap = _bbox_overlap_ratio(previous["bbox"], detection["bbox"])
        if (
            time_gap > max_gap
            or float(detection["time"]) - group_start > VISUAL_REPLACEMENT_MAX_GROUP_SECONDS
            or signature_distance > VISUAL_REPLACEMENT_SIGNATURE_DISTANCE
            or bbox_overlap < 0.35
        ):
            groups.append([detection])
        else:
            groups[-1].append(detection)
    return groups


def build_visual_caption_intervals_from_representative_ocr(
    frames: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    sample_seconds: float,
    duration_seconds: float,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    intervals = []
    for frame in frames:
        group_bbox = frame.get("visual_group_bbox")
        if not isinstance(group_bbox, dict):
            continue
        match_bbox = frame.get("candidate_match_bbox") if isinstance(frame.get("candidate_match_bbox"), dict) else group_bbox
        frame_candidates = [
            candidate
            for candidate in candidates
            if abs(float(candidate["time"]) - float(frame["time"])) <= 0.001
            and _candidate_matches_visual_group(candidate, match_bbox, width=width, height=height)
        ]
        if not frame_candidates:
            continue
        best = max(
            frame_candidates,
            key=lambda item: (
                _bbox_overlap_ratio(item["bbox"], match_bbox),
                float(item.get("confidence") or 0),
                _bbox_area(item["bbox"]),
            ),
        )
        source_bbox = best["bbox"] if frame.get("prefer_ocr_bbox") else (_union_bbox([group_bbox, best["bbox"]]) or group_bbox)
        start = _safe_float(frame.get("visual_group_start_time"))
        end = _safe_float(frame.get("visual_group_end_time"))
        if start is None or end is None or end <= start:
            continue
        candidate_counts = Counter(_source_text_key(item["text"]) for item in frame_candidates)
        intervals.append(
            {
                "start_time": round(max(0.0, start), 3),
                "end_time": round(min(duration_seconds, end), 3),
                "source_text": best["text"],
                "source_bbox": source_bbox,
                "source_bbox_normalized": {
                    "left": round(source_bbox["left_x"] / width, 6),
                    "top": round(source_bbox["top_y"] / height, 6),
                    "right": round(source_bbox["right_x"] / width, 6),
                    "bottom": round(source_bbox["bottom_y"] / height, 6),
                },
                "line_count": 2 if (source_bbox["bottom_y"] - source_bbox["top_y"] + 1) > height * 0.075 else 1,
                "ocr_confidence": round(float(best.get("confidence") or 0), 6),
                "agreement": 1.0,
                "sample_count": int(frame.get("visual_group_sample_count") or 1),
                "ocr_candidates": [
                    {
                        "text": text,
                        "count": count,
                        "median_confidence": round(
                            statistics.median(
                                float(item.get("confidence") or 0)
                                for item in frame_candidates
                                if _source_text_key(item["text"]) == text
                            ),
                            6,
                        ),
                    }
                    for text, count in candidate_counts.most_common()
                ],
                "unresolved_solid_glyph": bool(best.get("unresolved_solid_glyph"))
                and not bool(frame.get("prefer_ocr_bbox")),
                "ocr_gate_pass": float(best.get("confidence") or 0) >= MIN_OCR_CONFIDENCE
                and (
                    not bool(best.get("unresolved_solid_glyph"))
                    or bool(frame.get("prefer_ocr_bbox"))
                ),
            }
        )
    return _merge_duplicate_intervals(intervals, sample_seconds)


def _candidate_matches_visual_group(
    candidate: dict[str, Any],
    group_bbox: dict[str, int],
    *,
    width: int,
    height: int,
) -> bool:
    overlap = _bbox_overlap_ratio(candidate["bbox"], group_bbox)
    if overlap >= 0.18:
        return True
    pad_x = max(24, round(width * 0.025))
    pad_y = max(16, round(height * 0.018))
    expanded = {
        "left_x": max(0, int(group_bbox["left_x"]) - pad_x),
        "top_y": max(0, int(group_bbox["top_y"]) - pad_y),
        "right_x": min(width - 1, int(group_bbox["right_x"]) + pad_x),
        "bottom_y": min(height - 1, int(group_bbox["bottom_y"]) + pad_y),
    }
    center_x = (int(candidate["bbox"]["left_x"]) + int(candidate["bbox"]["right_x"])) / 2
    center_y = (int(candidate["bbox"]["top_y"]) + int(candidate["bbox"]["bottom_y"])) / 2
    return expanded["left_x"] <= center_x <= expanded["right_x"] and expanded["top_y"] <= center_y <= expanded["bottom_y"]


def _caption_glyph_signature(frame: Any, bbox: dict[str, int]) -> str:
    if frame is None:
        return ""
    height, width = frame.shape[:2]
    left = max(0, int(bbox["left_x"]))
    top = max(0, int(bbox["top_y"]))
    right = min(width - 1, int(bbox["right_x"]))
    bottom = min(height - 1, int(bbox["bottom_y"]))
    if right <= left or bottom <= top:
        return ""
    crop = frame[top : bottom + 1, left : right + 1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    bright = cv2.inRange(hsv, (0, 0, 170), (180, 130, 255))
    edges = cv2.Canny(gray, 60, 180)
    mask = cv2.bitwise_or(bright, cv2.dilate(edges, None, iterations=1))
    small = cv2.resize(mask, (48, 12), interpolation=cv2.INTER_AREA)
    bits = (small > 40).astype("uint8").flatten()
    return "".join("1" if value else "0" for value in bits)


def _caption_signature_distance(left: str, right: str) -> float:
    if not left or not right or len(left) != len(right):
        return 1.0
    return sum(a != b for a, b in zip(left, right)) / len(left)


def _bbox_area(box: dict[str, Any]) -> int:
    return max(0, int(box["right_x"]) - int(box["left_x"]) + 1) * max(
        0,
        int(box["bottom_y"]) - int(box["top_y"]) + 1,
    )


def _bbox_overlap_ratio(first: dict[str, Any], second: dict[str, Any]) -> float:
    overlap_left = max(int(first["left_x"]), int(second["left_x"]))
    overlap_top = max(int(first["top_y"]), int(second["top_y"]))
    overlap_right = min(int(first["right_x"]), int(second["right_x"]))
    overlap_bottom = min(int(first["bottom_y"]), int(second["bottom_y"]))
    if overlap_right < overlap_left or overlap_bottom < overlap_top:
        return 0.0
    overlap_area = (overlap_right - overlap_left + 1) * (overlap_bottom - overlap_top + 1)
    return overlap_area / max(1, min(_bbox_area(first), _bbox_area(second)))


def _boxes_overlap_for_replacement(
    detected: dict[str, int],
    visual: dict[str, int],
    *,
    width: int,
    height: int,
) -> bool:
    expanded = {
        "left_x": max(0, int(visual["left_x"]) - max(16, round(width * 0.015))),
        "top_y": max(0, int(visual["top_y"]) - max(10, round(height * 0.012))),
        "right_x": min(width - 1, int(visual["right_x"]) + max(16, round(width * 0.015))),
        "bottom_y": min(height - 1, int(visual["bottom_y"]) + max(10, round(height * 0.012))),
    }
    center_x = (int(detected["left_x"]) + int(detected["right_x"])) / 2
    center_y = (int(detected["top_y"]) + int(detected["bottom_y"])) / 2
    if expanded["left_x"] <= center_x <= expanded["right_x"] and expanded["top_y"] <= center_y <= expanded["bottom_y"]:
        return True
    overlap_left = max(int(detected["left_x"]), expanded["left_x"])
    overlap_top = max(int(detected["top_y"]), expanded["top_y"])
    overlap_right = min(int(detected["right_x"]), expanded["right_x"])
    overlap_bottom = min(int(detected["bottom_y"]), expanded["bottom_y"])
    if overlap_right < overlap_left or overlap_bottom < overlap_top:
        return False
    overlap_area = (overlap_right - overlap_left + 1) * (overlap_bottom - overlap_top + 1)
    detected_area = max(1, (int(detected["right_x"]) - int(detected["left_x"]) + 1) * (int(detected["bottom_y"]) - int(detected["top_y"]) + 1))
    return overlap_area / detected_area >= 0.35


def build_caption_intervals(
    detections: list[dict[str, Any]],
    *,
    sample_seconds: float,
    duration_seconds: float,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for detection in sorted(detections, key=lambda item: item["time"]):
        previous = groups[-1][-1] if groups else None
        text_continuation = (
            previous is not None
            and _intervals_are_ocr_variants(
                {
                    "source_text": previous["text"],
                    "source_bbox": previous["bbox"],
                },
                {
                    "source_text": detection["text"],
                    "source_bbox": detection["bbox"],
                },
            )
        )
        if (
            not groups
            or detection["time"] - groups[-1][-1]["time"] > sample_seconds * 2.2
            or (
                _source_text_key(detection["text"]) != _source_text_key(groups[-1][-1]["text"])
                and not text_continuation
            )
        ):
            groups.append([detection])
        else:
            groups[-1].append(detection)
    intervals = []
    for group in groups:
        if len(group) < 2:
            continue
        text_counts = Counter(_source_text_key(item["text"]) for item in group)
        source_text, agreement = text_counts.most_common(1)[0]
        variant_consistent = all(
            _intervals_are_ocr_variants(
                {"source_text": group[0]["text"], "source_bbox": group[0]["bbox"]},
                {"source_text": item["text"], "source_bbox": item["bbox"]},
            )
            for item in group[1:]
        )
        if agreement / len(group) < 0.6 and not variant_consistent:
            continue
        matching_items = [
            item for item in group
            if _source_text_key(item["text"]) == source_text
        ]
        bbox = _robust_bbox([item["bbox"] for item in (matching_items or group)])
        start = max(0.0, group[0]["time"] - sample_seconds / 2)
        end = min(duration_seconds, group[-1]["time"] + sample_seconds / 2)
        if end - start < sample_seconds:
            continue
        intervals.append({
            "start_time": round(start, 3),
            "end_time": round(end, 3),
            "source_text": source_text,
            "source_bbox": bbox,
            "source_bbox_normalized": {
                "left": round(bbox["left_x"] / width, 6),
                "top": round(bbox["top_y"] / height, 6),
                "right": round(bbox["right_x"] / width, 6),
                "bottom": round(bbox["bottom_y"] / height, 6),
            },
            "line_count": 2 if (bbox["bottom_y"] - bbox["top_y"] + 1) > height * 0.075 else 1,
            "ocr_confidence": round(statistics.median(item["confidence"] for item in group), 6),
            "agreement": round(max(agreement / len(group), 1.0 if variant_consistent else 0.0), 6),
            "sample_count": len(group),
            "ocr_candidates": [
                {
                    "text": text,
                    "count": count,
                    "median_confidence": round(
                        statistics.median(
                            item["confidence"]
                            for item in group
                            if _source_text_key(item["text"]) == text
                        ),
                        6,
                    ),
                }
                for text, count in text_counts.most_common()
            ],
            "unresolved_solid_glyph": (
                sum(bool(item.get("unresolved_solid_glyph")) for item in group) >= max(2, math.ceil(len(group) * 0.5))
            ),
            "ocr_gate_pass": (
                agreement / len(group) >= 0.6
                and sum(bool(item.get("unresolved_solid_glyph")) for item in group)
                < max(2, math.ceil(len(group) * 0.5))
            ),
        })
    return _merge_duplicate_intervals(intervals, sample_seconds)


def build_source_caption_render_plan(
    source_path: Path,
    cues: list[dict[str, Any]],
    *,
    font_path: Path,
    subtitle_provenance: str = SOURCE_CAPTION_MODE,
    reviewed_coverage_exclusions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    media = media_summary(source_path)
    width = int(media["video"].get("width") or 1280)
    height = int(media["video"].get("height") or 720)
    duration = float(media.get("duration_seconds") or 0)
    font_size = max(36, round(height * 0.041))
    font = ImageFont.truetype(str(font_path), font_size)
    pad_x = max(12, round(width * 0.00625))
    pad_y = max(8, round(height * 0.0074))
    intervals = []
    layouts = []
    cap = cv2.VideoCapture(str(source_path))
    try:
        source_video_sha256 = sha256_file(source_path) if source_path.exists() else None
        coverage_records: list[dict[str, Any]] = []
        coverage_adjustments: dict[str, dict[str, float]] = {}
        requires_pixel_coverage = False
        for cue in cues:
            bbox = cue.get("source_bbox")
            if not isinstance(bbox, dict):
                continue
            bbox_center_y = (int(bbox["top_y"]) + int(bbox["bottom_y"])) / 2
            if _cue_requires_visual_geometry_refresh(cue, bbox_center_y=bbox_center_y, height=height):
                requires_pixel_coverage = True
                break
        if requires_pixel_coverage:
            coverage_windows = _source_caption_pixel_coverage_windows(
                cap,
                width=width,
                height=height,
                duration=duration,
            )
            coverage_adjustments, coverage_records, uncovered_coverage = _source_caption_coverage_adjustments(
                cues,
                coverage_windows,
                source_video_sha256=source_video_sha256,
                reviewed_coverage_exclusions=reviewed_coverage_exclusions,
            )
            if uncovered_coverage:
                raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        for cue in cues:
            bbox = cue.get("source_bbox")
            source_interval = cue.get("source_interval") or {}
            if not isinstance(bbox, dict):
                raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
            start = float(source_interval.get("start_time", int(cue["start_ms"]) / 1000))
            end = float(source_interval.get("end_time", int(cue["end_ms"]) / 1000))
            adjusted_span = coverage_adjustments.get(str(cue.get("cue_id") or cue.get("id") or ""))
            if adjusted_span:
                start = float(adjusted_span["start_time"])
                end = float(adjusted_span["end_time"])
            mask_start = max(0.0, start - REPLACEMENT_MASK_PREROLL_SECONDS)
            mask_end = min(duration, end + REPLACEMENT_MASK_POSTROLL_SECONDS) if duration > 0 else end + REPLACEMENT_MASK_POSTROLL_SECONDS
            cue_id = str(cue["cue_id"])
            bbox_center_y = (int(bbox["top_y"]) + int(bbox["bottom_y"])) / 2
            refresh_geometry = _cue_requires_visual_geometry_refresh(cue, bbox_center_y=bbox_center_y, height=height)
            visual_bbox = (
                _visual_replacement_bbox_for_cue(cap, start, end, width=width, height=height)
                if refresh_geometry
                else None
            )
            if refresh_geometry and visual_bbox is None and bbox_center_y < height * 0.55:
                raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
            layout_bbox = visual_bbox or bbox
            layout_interval = _source_caption_mask_interval(
                f"{cue_id}_replacement",
                layout_bbox,
                start=mask_start,
                end=mask_end,
                pad_x=pad_x,
                pad_y=pad_y,
                width=width,
                height=height,
                line_count=int(cue.get("line_count") or 1),
                role="pixel_replacement_zone" if visual_bbox else "stored_source_zone",
            )
            layout = english_layout_for_interval(
                layout_interval,
                str(cue.get("resolved_text") or cue.get("translation_text") or cue.get("text") or ""),
                font,
                output_width=width,
                output_height=height,
                cue_start=max(0.0, start - REPLACEMENT_TEXT_PREROLL_SECONDS),
                cue_end=end,
            )
            plate = layout["plate"]
            union_x = min(layout_interval["x"], int(plate["x"]))
            union_y = min(layout_interval["y"], int(plate["y"]))
            union_right = max(layout_interval["x"] + layout_interval["width"] - 1, int(plate["right_x"]))
            union_bottom = max(layout_interval["y"] + layout_interval["height"] - 1, int(plate["bottom_y"]))
            union = {
                "x": union_x,
                "y": union_y,
                "width": union_right - union_x + 1,
                "height": union_bottom - union_y + 1,
                "right_x": union_right,
                "bottom_y": union_bottom,
            }
            # One compact source-zone rectangle prevents the eraser and text plate
            # from appearing as two offset overlays in the rendered video.
            layout_interval.update({key: union[key] for key in ("x", "y", "width", "height")})
            layout_interval["right_x"] = union["right_x"]
            layout_interval["bottom_y"] = union["bottom_y"]
            layout_interval["geometry_refresh"] = bool(visual_bbox)
            layout_interval["geometry_source"] = "pixel_visible_embedded_caption_zone" if visual_bbox else str(cue.get("geometry_source") or "stored_source_zone")
            intervals.append(layout_interval)
            if visual_bbox and not cue.get("needs_pixel_refresh") and _source_caption_boxes_are_distinct(bbox, visual_bbox, height=height):
                intervals.append(
                    _source_caption_mask_interval(
                        f"{cue_id}_stored_source",
                        bbox,
                        start=mask_start,
                        end=mask_end,
                        pad_x=pad_x,
                        pad_y=pad_y,
                        width=width,
                        height=height,
                        line_count=int(cue.get("line_count") or 1),
                        role="stored_source_zone",
                    )
                )
            layout["plate"] = union
            layout["plate_area_frame_percent"] = round(
                union["width"] * union["height"] * 100 / (width * height),
                4,
            )
            layout["segment_id"] = cue_id
            layout["geometry_refresh"] = bool(visual_bbox)
            layout["geometry_source"] = "pixel_visible_embedded_caption_zone" if visual_bbox else str(cue.get("geometry_source") or "stored_source_zone")
            layout["source_interval_id"] = layout_interval["segment_id"]
            layout["replacement_role"] = layout_interval["replacement_role"]
            layouts.append(layout)
    finally:
        cap.release()
    layouts = stabilize_layouts(layouts)
    return {
        "media": media,
        "source_video_sha256": source_video_sha256,
        "output_width": width,
        "output_height": height,
        "font_size": font_size,
        "intervals": intervals,
        "layouts": layouts,
        "source_caption_coverage": coverage_records,
        "interval_stats": interval_stats(
            intervals,
            output_width=width,
            output_height=height,
            output_duration_seconds=duration,
        ),
        "plate_stats": subtitle_plate_stats(layouts, output_width=width, output_height=height),
        "review_times": review_times(intervals, output_duration_seconds=duration),
        "subtitle_provenance": subtitle_provenance,
    }


def _visual_replacement_bbox_for_cue(
    cap: cv2.VideoCapture,
    start: float,
    end: float,
    *,
    width: int,
    height: int,
) -> dict[str, int] | None:
    if not cap.isOpened():
        return None
    sample_end = max(start, end - 0.033)
    sample_times = [
        max(0.0, start),
        max(0.0, (start + end) / 2),
        max(0.0, sample_end),
    ]
    boxes = []
    for time_s in sample_times:
        try:
            frame = read_frame(cap, time_s, width, height)
        except RuntimeError:
            continue
        bbox, _ = detect_source_subtitle_bbox(frame, output_width=width, output_height=height)
        if bbox:
            boxes.append(bbox)
    return _union_bbox(boxes)


def _cue_requires_visual_geometry_refresh(cue: dict[str, Any], *, bbox_center_y: float, height: int) -> bool:
    if bool(cue.get("needs_pixel_refresh")):
        return True
    return bbox_center_y >= height * 0.55


def _source_caption_mask_interval(
    segment_id: str,
    bbox: dict[str, Any],
    *,
    start: float,
    end: float,
    pad_x: int,
    pad_y: int,
    width: int,
    height: int,
    line_count: int,
    role: str,
) -> dict[str, Any]:
    x = max(0, int(bbox["left_x"]) - pad_x)
    y = max(0, int(bbox["top_y"]) - pad_y)
    right = min(width - 1, int(bbox["right_x"]) + pad_x)
    bottom = min(height - 1, int(bbox["bottom_y"]) + pad_y)
    return {
        "segment_id": segment_id,
        "start_time": round(start, 3),
        "end_time": round(max(start, end), 3),
        "x": x,
        "y": y,
        "width": right - x + 1,
        "height": bottom - y + 1,
        "right_x": right,
        "bottom_y": bottom,
        "line_count": line_count,
        "padding": {"vertical": pad_y, "horizontal": pad_x},
        "source_bbox": bbox,
        "source_only": True,
        "replacement_role": role,
    }


def _source_caption_boxes_are_distinct(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    height: int,
) -> bool:
    first_center_y = (int(first["top_y"]) + int(first["bottom_y"])) / 2
    second_center_y = (int(second["top_y"]) + int(second["bottom_y"])) / 2
    return abs(first_center_y - second_center_y) > height * 0.18


def normalize_source_text(text: str) -> str:
    value = re.sub(r"\s+", "", text).strip()
    return value.strip("|_~")


def _is_stale_caption_override_error(exc: CaptionOverrideValidationError) -> bool:
    message = str(exc).lower()
    return (
        "start time mismatch" in message
        or "end time mismatch" in message
        or "region fingerprint mismatch" in message
    )


def _source_text_key(text: str) -> str:
    return normalize_source_text(text).rstrip(".,!?;:。，！？；：…")


def _validate_translations(intervals: list[dict[str, Any]], translations: list[dict[str, Any]]) -> None:
    outputs = []
    for interval, item in zip(intervals, translations):
        source = interval["source_text"]
        output = str(item.get("translated_text") or "").strip()
        if not output or is_cjk_text(output) or len(output) > max(80, len(source) * 12):
            raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)
        outputs.append(output.lower())
    if len(outputs) >= 4 and Counter(outputs).most_common(1)[0][1] > max(3, len(outputs) // 3):
        raise SourceCaptionUnavailableError(SOURCE_CAPTION_BLOCK_MESSAGE)


def _merge_duplicate_intervals(intervals: list[dict[str, Any]], gap: float) -> list[dict[str, Any]]:
    merged = []
    for item in intervals:
        previous = merged[-1] if merged else None
        bridge_gap = max(gap, 0.25)
        if (
            previous
            and item["start_time"] - previous["end_time"] <= bridge_gap
            and _intervals_are_ocr_variants(previous, item)
        ):
            if item["ocr_confidence"] > previous["ocr_confidence"]:
                previous["source_text"] = item["source_text"]
            previous["end_time"] = item["end_time"]
            previous["source_bbox"] = _robust_bbox([previous["source_bbox"], item["source_bbox"]])
            previous["sample_count"] += item["sample_count"]
            previous["ocr_confidence"] = round(
                max(previous["ocr_confidence"], item["ocr_confidence"]),
                6,
            )
        else:
            merged.append(dict(item))
    return merged


def _source_caption_pixel_coverage_windows(
    cap: cv2.VideoCapture,
    *,
    width: int,
    height: int,
    duration: float,
    sample_seconds: float = SOURCE_CAPTION_COVERAGE_SAMPLE_SECONDS,
) -> list[dict[str, Any]]:
    if not cap.isOpened() or duration <= 0:
        return []
    detections: list[dict[str, Any]] = []
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    except AttributeError:
        fps = 0.0
    if fps > 0 and hasattr(cap, "grab") and hasattr(cap, "read"):
        sample_frame_step = max(1, round(fps * sample_seconds))
        final_frame = max(0, round(duration * fps))
        target_frame = 0
        current_frame = 0
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        while target_frame <= final_frame:
            while current_frame < target_frame:
                if not cap.grab():
                    target_frame = final_frame + 1
                    break
                current_frame += 1
            if target_frame > final_frame:
                break
            ok, frame = cap.read()
            if not ok:
                break
            current_frame += 1
            time_s = target_frame / fps
            if width and height:
                frame = cv2.resize(frame, (width, height))
            bbox, detail = detect_source_subtitle_bbox(frame, output_width=width, output_height=height)
            if bbox:
                detections.append({
                    "time": round(time_s, 3),
                    "bbox": bbox,
                    "detail": detail,
                })
            target_frame += sample_frame_step
    else:
        time_s = 0.0
        while time_s <= duration + 0.001:
            try:
                frame = read_frame(cap, time_s, width, height)
            except RuntimeError:
                break
            bbox, detail = detect_source_subtitle_bbox(frame, output_width=width, output_height=height)
            if bbox:
                detections.append({
                    "time": round(time_s, 3),
                    "bbox": bbox,
                    "detail": detail,
                })
            time_s += sample_seconds

    groups: list[list[dict[str, Any]]] = []
    for detection in detections:
        if not groups or detection["time"] - groups[-1][-1]["time"] > SOURCE_CAPTION_COVERAGE_GAP_SECONDS:
            groups.append([detection])
        else:
            groups[-1].append(detection)

    windows: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        bbox = _union_bbox([item["bbox"] for item in group])
        if not bbox:
            continue
        start = max(0.0, float(group[0]["time"]) - sample_seconds / 2)
        end = min(duration, float(group[-1]["time"]) + sample_seconds / 2)
        if end <= start:
            continue
        windows.append({
            "window_id": f"caption_active_{index:04d}",
            "start_time": round(start, 3),
            "end_time": round(end, 3),
            "representative_times": [
                round(start, 3),
                round((start + end) / 2, 3),
                round(end, 3),
            ],
            "source_bbox": bbox,
            "pixel_confidence": round(min(1.0, len(group) / 3), 6),
            "sample_count": len(group),
            "schema_version": "source_caption_pixel_coverage_v1",
        })
    return windows


def _cue_source_span(cue: dict[str, Any]) -> tuple[float, float]:
    source_interval = cue.get("source_interval") or {}
    start = float(source_interval.get("start_time", int(cue["start_ms"]) / 1000))
    end = float(source_interval.get("end_time", int(cue["end_ms"]) / 1000))
    return start, end


def _merge_time_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if not merged or start > merged[-1][1] + SOURCE_CAPTION_COVERAGE_MIN_UNCOVERED_SECONDS:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _validate_coverage_exclusion(exc: dict[str, Any], source_video_sha256: str | None, window: dict[str, Any]) -> bool:
    import json
    import hashlib
    if not source_video_sha256 or exc.get("source_video_sha256") != source_video_sha256:
        return False
    if exc.get("classification") not in ("HUD_NON_CAPTION", "USERNAME_HUD", "REVIEWED_TRANSITION_GAP"):
        return False
    if exc.get("provenance") != "human_reviewed_from_pixels":
        return False
    if exc.get("authority") != "TECH_LEAD_APPROVED":
        return False
    if exc.get("window_id") != window.get("window_id"):
        return False
    exc_start = float(exc.get("start_time", -1.0))
    exc_end = float(exc.get("end_time", -1.0))
    if exc_start < float(window["start_time"]) - 0.1 or exc_end > float(window["end_time"]) + 0.1:
        return False
    expected_hash = exc.get("record_hash")
    if not expected_hash:
        return False
    record_without_hash = dict(exc)
    del record_without_hash["record_hash"]
    encoded = json.dumps(record_without_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != expected_hash:
        return False
    return True


def _source_caption_coverage_adjustments(
    cues: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    *,
    source_video_sha256: str | None,
    reviewed_coverage_exclusions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, float]], list[dict[str, Any]], list[dict[str, Any]]]:
    adjustments: dict[str, dict[str, float]] = {}
    records: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []
    cue_spans = []
    for cue in cues:
        cue_id = str(cue.get("cue_id") or cue.get("id") or "")
        if not cue_id:
            continue
        start, end = _cue_source_span(cue)
        cue_spans.append({"cue_id": cue_id, "start_time": start, "end_time": end})

    for window in windows:
        window_start = float(window["start_time"])
        window_end = float(window["end_time"])
        matches = [
            span
            for span in cue_spans
            if span["end_time"] >= window_start - SOURCE_CAPTION_COVERAGE_EXTENSION_SECONDS
            and span["start_time"] <= window_end + SOURCE_CAPTION_COVERAGE_EXTENSION_SECONDS
        ]
        valid_excs = []
        for exc in (reviewed_coverage_exclusions or []):
            if _validate_coverage_exclusion(exc, source_video_sha256, window):
                valid_excs.append(exc)

        if not matches and not valid_excs:
            record = {
                "source_video_sha256": source_video_sha256,
                "caption_active_window": {"start_time": window_start, "end_time": window_end},
                "representative_frame_timestamps": window["representative_times"],
                "full_frame_bbox": window["source_bbox"],
                "pixel_confidence": window["pixel_confidence"],
                "matched_cue_id": None,
                "match_reason": "no_cue_within_temporal_extension_limit",
                "mapping_status": "missing",
                "mask_interval": None,
                "english_interval": None,
                "coverage_verdict": "FAIL_UNCOVERED_SOURCE_CAPTION_WINDOW",
                "schema_algorithm_version": window["schema_version"],
            }
            records.append(record)
            uncovered.append(record)
            continue

        if matches:
            first = min(matches, key=lambda item: item["start_time"])
            last = max(matches, key=lambda item: item["end_time"])
            first_adj = adjustments.setdefault(
                first["cue_id"],
                {"start_time": first["start_time"], "end_time": first["end_time"]},
            )
            first_adj["start_time"] = min(first_adj["start_time"], window_start)
            last_adj = adjustments.setdefault(
                last["cue_id"],
                {"start_time": last["start_time"], "end_time": last["end_time"]},
            )
            last_adj["end_time"] = max(last_adj["end_time"], window_end)

        adjusted_spans = []
        for match in matches:
            adjusted = adjustments.get(match["cue_id"], match)
            adjusted_spans.append((
                max(window_start, float(adjusted["start_time"])),
                min(window_end, float(adjusted["end_time"])),
            ))
        for exc in valid_excs:
            adjusted_spans.append((
                max(window_start, float(exc["start_time"])),
                min(window_end, float(exc["end_time"])),
            ))

        merged = _merge_time_spans(adjusted_spans)
        cursor = window_start
        gaps = []
        for start, end in merged:
            if start - cursor > SOURCE_CAPTION_COVERAGE_MIN_UNCOVERED_SECONDS:
                gaps.append({"start_time": round(cursor, 3), "end_time": round(start, 3)})
            cursor = max(cursor, end)
        if window_end - cursor > SOURCE_CAPTION_COVERAGE_MIN_UNCOVERED_SECONDS:
            gaps.append({"start_time": round(cursor, 3), "end_time": round(window_end, 3)})

        verdict = "PASS_MATCHED_SOURCE_CUE" if matches else "PASS_REVIEWED_EXCLUSION"
        if gaps:
            verdict = "FAIL_UNCOVERED_SOURCE_CAPTION_GAP"
        
        record = {
            "source_video_sha256": source_video_sha256,
            "caption_active_window": {"start_time": window_start, "end_time": window_end},
            "representative_frame_timestamps": window["representative_times"],
            "full_frame_bbox": window["source_bbox"],
            "pixel_confidence": window["pixel_confidence"],
            "matched_cue_id": [match["cue_id"] for match in matches] if matches else None,
            "match_reason": "temporal_overlap_or_bounded_edge_extension" if matches else "reviewed_exclusion",
            "mapping_status": "configured" if not gaps else "partial",
            "mask_interval": {"start_time": round(window_start, 3), "end_time": round(window_end, 3)} if matches else None,
            "english_interval": {"start_time": round(window_start, 3), "end_time": round(window_end, 3)} if matches else None,
            "coverage_verdict": verdict,
            "uncovered_gaps": gaps,
            "schema_algorithm_version": window["schema_version"],
        }
        records.append(record)
        if gaps:
            uncovered.append(record)

    return adjustments, records, uncovered


def _intervals_are_ocr_variants(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _source_text_key(left["source_text"]) == _source_text_key(right["source_text"]):
        return True
    left_text = normalize_source_text(left["source_text"])
    right_text = normalize_source_text(right["source_text"])
    if not left_text or not right_text:
        return False
    left_box = left["source_bbox"]
    right_box = right["source_bbox"]
    overlap_left = max(left_box["left_x"], right_box["left_x"])
    overlap_top = max(left_box["top_y"], right_box["top_y"])
    overlap_right = min(left_box["right_x"], right_box["right_x"])
    overlap_bottom = min(left_box["bottom_y"], right_box["bottom_y"])
    if overlap_right < overlap_left or overlap_bottom < overlap_top:
        return False
    overlap_area = (overlap_right - overlap_left + 1) * (overlap_bottom - overlap_top + 1)
    left_area = (left_box["right_x"] - left_box["left_x"] + 1) * (left_box["bottom_y"] - left_box["top_y"] + 1)
    right_area = (right_box["right_x"] - right_box["left_x"] + 1) * (right_box["bottom_y"] - right_box["top_y"] + 1)
    if overlap_area / max(1, min(left_area, right_area)) < 0.65:
        return False
    similarity = SequenceMatcher(None, left_text, right_text).ratio()
    if similarity >= 0.72:
        return True
    if max(len(left_text), len(right_text)) <= 6 and similarity >= 0.66:
        return len(set(left_text) & set(right_text)) >= 2
    return False


def _ocr_bbox(box: Any) -> dict[str, int] | None:
    if not isinstance(box, list) or len(box) < 4:
        return None
    try:
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
    except (TypeError, ValueError, IndexError):
        return None
    return {
        "left_x": round(min(xs)),
        "top_y": round(min(ys)),
        "right_x": round(max(xs)),
        "bottom_y": round(max(ys)),
    }


def _has_solid_caption_glyph(path: Path, bbox: dict[str, int]) -> bool:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return False
    left = max(0, bbox["left_x"])
    top = max(0, bbox["top_y"])
    right = min(image.shape[1] - 1, bbox["right_x"])
    bottom = min(image.shape[0] - 1, bbox["bottom_y"])
    if right <= left or bottom <= top:
        return False
    crop = image[top : bottom + 1, left : right + 1]
    _, binary = cv2.threshold(crop, 235, 255, cv2.THRESH_BINARY)
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    line_height = crop.shape[0]
    for component in range(1, count):
        _, _, width, height, area = stats[component]
        if (
            line_height * 0.4 <= height <= line_height * 1.05
            and height * 0.55 <= width <= height * 1.2
            and area / max(1, width * height) >= 0.78
        ):
            return True
    return False


def _robust_bbox(boxes: list[dict[str, int]]) -> dict[str, int]:
    return {
        "left_x": round(statistics.median(box["left_x"] for box in boxes)),
        "top_y": round(statistics.median(box["top_y"] for box in boxes)),
        "right_x": round(statistics.median(box["right_x"] for box in boxes)),
        "bottom_y": round(statistics.median(box["bottom_y"] for box in boxes)),
    }


def _union_bbox(boxes: list[dict[str, int]]) -> dict[str, int] | None:
    if not boxes:
        return None
    return {
        "left_x": min(box["left_x"] for box in boxes),
        "top_y": min(box["top_y"] for box in boxes),
        "right_x": max(box["right_x"] for box in boxes),
        "bottom_y": max(box["bottom_y"] for box in boxes),
    }
