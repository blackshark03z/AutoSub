from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from app.core.media import media_summary
from app.services.source_caption_translation import _cleanup_sampled_frames, _sample_frames
from app.services.visual_ocr_evidence import build_visual_text_evidence
from app.services.visual_recognition_refinement import refine_uncertain_visual_text_evidence

OCRBatchRunner = Callable[[list[Path]], dict[str, Any]]


ADAPTIVE_VISUAL_SIGNATURE_WIDTH = 160
ADAPTIVE_VISUAL_SIGNATURE_HEIGHT = 90
ADAPTIVE_VISUAL_TILE_WIDTH = 16
ADAPTIVE_VISUAL_TILE_HEIGHT = 10
ADAPTIVE_VISUAL_REUSE_THRESHOLD = 0.018
ADAPTIVE_VISUAL_MAX_REUSE_SECONDS = 2.0


def _adaptive_visual_signature(path: Path) -> np.ndarray | None:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    return cv2.resize(
        image,
        (ADAPTIVE_VISUAL_SIGNATURE_WIDTH, ADAPTIVE_VISUAL_SIGNATURE_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )


def _adaptive_visual_distance(left: np.ndarray | None, right: np.ndarray | None) -> float:
    if left is None or right is None or left.shape != right.shape:
        return 1.0
    delta = cv2.absdiff(left, right).astype(np.float32) / 255.0
    tile_scores: list[float] = []
    for top in range(0, delta.shape[0], ADAPTIVE_VISUAL_TILE_HEIGHT):
        for left_x in range(0, delta.shape[1], ADAPTIVE_VISUAL_TILE_WIDTH):
            tile = delta[
                top : top + ADAPTIVE_VISUAL_TILE_HEIGHT,
                left_x : left_x + ADAPTIVE_VISUAL_TILE_WIDTH,
            ]
            if tile.size:
                tile_scores.append(float(tile.mean()))
    if not tile_scores:
        return 1.0
    tile_scores.sort()
    percentile_index = min(len(tile_scores) - 1, round((len(tile_scores) - 1) * 0.96))
    return tile_scores[percentile_index]


def _select_adaptive_ocr_frames(
    frames: list[dict[str, Any]],
    *,
    sample_seconds: float,
) -> tuple[list[dict[str, Any]], list[int], list[float]]:
    if not frames:
        return [], [], []
    selected: list[dict[str, Any]] = []
    representative_indexes: list[int] = []
    distances: list[float] = []
    representative_signature: np.ndarray | None = None
    representative_time = -1.0
    representative_index = -1
    max_reuse_seconds = max(sample_seconds, ADAPTIVE_VISUAL_MAX_REUSE_SECONDS)
    for frame in frames:
        signature = _adaptive_visual_signature(Path(frame["path"]))
        time_s = float(frame["time"])
        distance = _adaptive_visual_distance(representative_signature, signature)
        force_probe = (
            representative_index < 0
            or time_s - representative_time >= max_reuse_seconds
            or distance > ADAPTIVE_VISUAL_REUSE_THRESHOLD
        )
        if force_probe:
            representative_index = len(selected)
            representative_signature = signature
            representative_time = time_s
            selected.append(frame)
        representative_indexes.append(representative_index)
        distances.append(round(distance, 6))
    return selected, representative_indexes, distances


def _expand_adaptive_ocr_payload(
    payload: dict[str, Any],
    *,
    frames: list[dict[str, Any]],
    representative_indexes: list[int],
) -> dict[str, Any]:
    selected_frames = list(payload.get("frames") or []) if isinstance(payload, dict) else []
    if representative_indexes and max(representative_indexes) >= len(selected_frames):
        raise ValueError("adaptive whole-frame OCR representative mapping is invalid")
    expanded: list[dict[str, Any]] = []
    for frame, representative_index in zip(frames, representative_indexes):
        representative = dict(selected_frames[representative_index])
        representative["name"] = Path(frame["path"]).name
        expanded.append(representative)
    return {**payload, "frames": expanded}


def analyze_whole_frame_visual_text(
    source_path: Path,
    run_directory: Path,
    *,
    ocr_runner: OCRBatchRunner,
    provider: str,
    model: str,
    sample_seconds: float = 0.5,
    max_gap_ms: int = 900,
    evidence_filename: str = "visual_text_evidence.json",
    time_windows_ms: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    if sample_seconds <= 0:
        raise ValueError("sample_seconds must be positive")
    media = media_summary(source_path)
    duration = float(media.get("duration_seconds") or 0)
    if duration <= 0:
        raise ValueError("source video duration is unavailable")

    frame_dir = run_directory / "work" / "visual_localization_frames"
    all_frames = _sample_frames(source_path, frame_dir, duration, sample_seconds)
    frames = all_frames
    normalized_windows: list[tuple[int, int]] = []
    if time_windows_ms:
        for start_ms, end_ms in time_windows_ms:
            start = max(0, int(start_ms))
            end = max(start, int(end_ms))
            normalized_windows.append((start, end))
        frames = [
            frame for frame in all_frames
            if any(start <= round(float(frame["time"]) * 1000) <= end for start, end in normalized_windows)
        ]
        if not frames:
            raise ValueError("targeted visual OCR window selected no sampled frames")
    try:
        adaptive_frames, representative_indexes, adaptive_distances = _select_adaptive_ocr_frames(
            frames,
            sample_seconds=sample_seconds,
        )
        paths = [Path(item["path"]) for item in adaptive_frames]
        payload = ocr_runner(paths)
        raw_provider_frames = list(payload.get("frames") or []) if isinstance(payload, dict) else []
        if len(raw_provider_frames) != len(adaptive_frames):
            raise ValueError("whole-frame OCR returned a mismatched representative frame count")
        for sampled, returned in zip(adaptive_frames, raw_provider_frames):
            expected_name = Path(sampled["path"]).name
            returned_name = str(returned.get("name") or expected_name)
            if Path(returned_name).name != expected_name:
                raise ValueError("whole-frame OCR frame identity mismatch")
        payload = _expand_adaptive_ocr_payload(
            payload,
            frames=frames,
            representative_indexes=representative_indexes,
        )
        provider_frames = list(payload.get("frames") or []) if isinstance(payload, dict) else []
        if len(provider_frames) != len(frames):
            raise ValueError("whole-frame OCR returned a mismatched frame count")

        # Provider result names are normalized to our authoritative local frame
        # names; media time always comes from the sampling manifest, never an
        # inference-duration field returned by a provider.
        normalized_frames: list[dict[str, Any]] = []
        frame_times_ms: dict[str, int] = {}
        for sampled, returned in zip(frames, provider_frames):
            name = Path(sampled["path"]).name
            returned_name = str(returned.get("name") or name)
            if Path(returned_name).name != name:
                raise ValueError("whole-frame OCR frame identity mismatch")
            normalized = dict(returned)
            normalized["name"] = name
            normalized_frames.append(normalized)
            frame_times_ms[name] = round(float(sampled["time"]) * 1000)

        effective_provider = str(payload.get("provider") or provider) if isinstance(payload, dict) else provider
        effective_model = str(payload.get("model_version") or model) if isinstance(payload, dict) else model
        evidence = build_visual_text_evidence(
            {"frames": normalized_frames},
            frame_times_ms=frame_times_ms,
            provider=effective_provider,
            model=effective_model,
            max_gap_ms=max_gap_ms,
        )
        evidence = refine_uncertain_visual_text_evidence(
            evidence,
            frame_paths={Path(item["path"]).name: Path(item["path"]) for item in frames},
            ocr_runner=ocr_runner,
            work_dir=run_directory / "work" / "visual_recognition_refinement",
        )
        evidence.update({
            "source_path": str(source_path.resolve()),
            "source_duration_seconds": duration,
            "sample_seconds": sample_seconds,
            "sampled_frame_count": len(frames),
            "full_sampled_frame_count": len(all_frames),
            "targeted_time_windows_ms": [[start, end] for start, end in normalized_windows],
            "ocr_frame_count": len(adaptive_frames),
            "ocr_frame_reuse_count": max(0, len(frames) - len(adaptive_frames)),
            "ocr_frame_reuse_ratio": round(
                max(0, len(frames) - len(adaptive_frames)) / max(1, len(frames)),
                6,
            ),
            "adaptive_visual_max_reuse_seconds": ADAPTIVE_VISUAL_MAX_REUSE_SECONDS,
            "adaptive_visual_reuse_threshold": ADAPTIVE_VISUAL_REUSE_THRESHOLD,
            "adaptive_visual_max_observed_distance": max(adaptive_distances, default=0.0),
            "provider_runtime": payload.get("runtime") if isinstance(payload, dict) else None,
            "provider_model_version": payload.get("model_version") if isinstance(payload, dict) else None,
        })
        filename = Path(str(evidence_filename or "visual_text_evidence.json")).name
        output = run_directory / "visual" / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return evidence
    finally:
        _cleanup_sampled_frames(all_frames, frame_dir)
