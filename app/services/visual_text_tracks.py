from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from statistics import median
from typing import Any


@dataclass(frozen=True)
class TextObservation:
    observation_id: str
    time_ms: int
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]
    provider: str
    model: str
    frame_id: str | None = None


@dataclass
class TextTrack:
    track_id: str
    observations: list[TextObservation] = field(default_factory=list)
    canonical_text: str = ""
    confidence: float = 0.0
    start_ms: int = 0
    end_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "canonical_text": self.canonical_text,
            "confidence": round(self.confidence, 6),
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "observation_count": len(self.observations),
            "observations": [
                {
                    "observation_id": item.observation_id,
                    "time_ms": item.time_ms,
                    "text": item.text,
                    "confidence": item.confidence,
                    "bbox": list(item.bbox),
                    "provider": item.provider,
                    "model": item.model,
                    "frame_id": item.frame_id,
                }
                for item in self.observations
            ],
        }


def normalize_text_identity(text: str) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[，,。.!！?？:：;；'\"“”‘’()（）\[\]【】]", "", value)
    return value


def bbox_iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    lx0, ly0, lx1, ly1 = left
    rx0, ry0, rx1, ry1 = right
    ix0, iy0 = max(lx0, rx0), max(ly0, ry0)
    ix1, iy1 = min(lx1, rx1), min(ly1, ry1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    left_area = max(0, lx1 - lx0) * max(0, ly1 - ly0)
    right_area = max(0, rx1 - rx0) * max(0, ry1 - ry0)
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _text_similarity(left: str, right: str) -> float:
    left_n = normalize_text_identity(left)
    right_n = normalize_text_identity(right)
    if not left_n or not right_n:
        return 0.0
    if left_n == right_n:
        return 1.0
    return SequenceMatcher(a=left_n, b=right_n).ratio()


def _track_match_score(
    track: TextTrack,
    observation: TextObservation,
    *,
    max_gap_ms: int,
    min_iou: float,
    min_text_similarity: float,
) -> float | None:
    last = track.observations[-1]
    gap = observation.time_ms - last.time_ms
    if gap < 0 or gap > max_gap_ms:
        return None
    overlap = bbox_iou(last.bbox, observation.bbox)
    if overlap < min_iou:
        return None
    similarity = _text_similarity(track.canonical_text or last.text, observation.text)
    if similarity < min_text_similarity:
        return None
    return overlap * 0.55 + similarity * 0.45 - (gap / max(max_gap_ms, 1)) * 0.05


def _refresh_track(track: TextTrack) -> None:
    if not track.observations:
        return
    grouped: dict[str, list[TextObservation]] = {}
    for item in track.observations:
        key = normalize_text_identity(item.text)
        if key:
            grouped.setdefault(key, []).append(item)
    if grouped:
        winning = max(
            grouped.values(),
            key=lambda items: (len(items), sum(max(0.0, item.confidence) for item in items)),
        )
        representative = max(winning, key=lambda item: item.confidence)
        track.canonical_text = representative.text.strip()
    else:
        track.canonical_text = track.observations[-1].text.strip()
    track.confidence = float(median(item.confidence for item in track.observations))
    track.start_ms = min(item.time_ms for item in track.observations)
    track.end_ms = max(item.time_ms for item in track.observations)


def build_text_tracks(
    observations: list[TextObservation],
    *,
    max_gap_ms: int = 500,
    min_iou: float = 0.35,
    min_text_similarity: float = 0.82,
) -> list[TextTrack]:
    tracks: list[TextTrack] = []
    for observation in sorted(observations, key=lambda item: (item.time_ms, item.observation_id)):
        if not normalize_text_identity(observation.text):
            continue
        candidates: list[tuple[float, TextTrack]] = []
        for track in tracks:
            score = _track_match_score(
                track,
                observation,
                max_gap_ms=max_gap_ms,
                min_iou=min_iou,
                min_text_similarity=min_text_similarity,
            )
            if score is not None:
                candidates.append((score, track))
        if candidates:
            _, selected = max(candidates, key=lambda pair: pair[0])
            selected.observations.append(observation)
            _refresh_track(selected)
            continue
        track = TextTrack(track_id=f"vtt_{len(tracks) + 1:06d}", observations=[observation])
        _refresh_track(track)
        tracks.append(track)
    return tracks
