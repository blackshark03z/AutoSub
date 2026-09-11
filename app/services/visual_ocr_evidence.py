from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.visual_text_tracks import TextObservation, build_text_tracks

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _polygon_bbox(poly: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(poly, (list, tuple)) or len(poly) < 3:
        return None
    points: list[tuple[float, float]] = []
    for point in poly:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            points.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            return None
    left = int(round(min(point[0] for point in points)))
    top = int(round(min(point[1] for point in points)))
    right = int(round(max(point[0] for point in points)))
    bottom = int(round(max(point[1] for point in points)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def observations_from_ocr_frames(
    frames: list[dict[str, Any]],
    *,
    frame_times_ms: dict[str, int],
    provider: str,
    model: str,
    minimum_confidence: float = 0.20,
) -> list[TextObservation]:
    observations: list[TextObservation] = []
    for frame in frames:
        name = str(frame.get("name") or "").strip()
        if not name or name not in frame_times_ms:
            raise ValueError(f"Missing authoritative video timestamp for OCR frame: {name or '<unnamed>'}")
        time_ms = int(frame_times_ms[name])
        if time_ms < 0:
            raise ValueError("frame video timestamp cannot be negative")
        for ordinal, item in enumerate(list(frame.get("texts") or []), start=1):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                confidence = float(item.get("score"))
            except (TypeError, ValueError):
                continue
            if confidence < minimum_confidence or confidence > 1.0:
                continue
            bbox = _polygon_bbox(item.get("poly"))
            if bbox is None:
                continue
            observations.append(TextObservation(
                observation_id=f"{name}:{ordinal:04d}",
                time_ms=time_ms,
                text=text,
                confidence=confidence,
                bbox=bbox,
                provider=provider,
                model=model,
                frame_id=name,
            ))
    return observations


def classify_visual_track_text(text: str) -> dict[str, str]:
    value = str(text or "").strip()
    if not value:
        return {"action": "ignore", "reason": "empty"}
    if _CJK_RE.search(value):
        return {"action": "localize", "reason": "source_cjk_semantic_candidate"}
    if re.fullmatch(r"[\d\s.,:%+\-/()]+", value):
        return {"action": "preserve", "reason": "numeric_or_symbol_only"}
    return {"action": "preserve", "reason": "non_source_script_or_identifier_candidate"}


def classify_visual_track(track: Any) -> dict[str, str]:
    classification = classify_visual_track_text(str(getattr(track, "canonical_text", "") or ""))
    if classification.get("action") != "localize":
        return classification
    observations = list(getattr(track, "observations", []) or [])
    if len(observations) != 1:
        return classification
    bbox = getattr(observations[0], "bbox", None)
    if not isinstance(bbox, tuple) or len(bbox) != 4:
        return classification
    width = max(0, int(bbox[2]) - int(bbox[0]))
    height = max(0, int(bbox[3]) - int(bbox[1]))
    text = str(getattr(track, "canonical_text", "") or "")
    cjk_count = len(_CJK_RE.findall(text))
    if width <= 40 and height <= 28 and cjk_count <= 4:
        return {"action": "ignore", "reason": "transient_microtext_single_observation"}
    return classification


def build_visual_text_evidence(
    ocr_payload: dict[str, Any],
    *,
    frame_times_ms: dict[str, int],
    provider: str,
    model: str,
    max_gap_ms: int = 750,
) -> dict[str, Any]:
    frames = list(ocr_payload.get("frames") or [])
    observations = observations_from_ocr_frames(
        frames,
        frame_times_ms=frame_times_ms,
        provider=provider,
        model=model,
    )
    tracks = build_text_tracks(observations, max_gap_ms=max_gap_ms)
    serialized_tracks: list[dict[str, Any]] = []
    localizable_count = 0
    for track in tracks:
        classification = classify_visual_track(track)
        value = track.as_dict()
        value["classification"] = classification
        serialized_tracks.append(value)
        if classification["action"] == "localize":
            localizable_count += 1
    return {
        "schema_version": 1,
        "provider": provider,
        "model": model,
        "frame_count": len(frames),
        "observation_count": len(observations),
        "track_count": len(tracks),
        "localizable_track_count": localizable_count,
        "tracks": serialized_tracks,
    }


def write_visual_text_evidence(
    path: Path,
    ocr_payload: dict[str, Any],
    *,
    frame_times_ms: dict[str, int],
    provider: str,
    model: str,
) -> dict[str, Any]:
    evidence = build_visual_text_evidence(
        ocr_payload,
        frame_times_ms=frame_times_ms,
        provider=provider,
        model=model,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence
