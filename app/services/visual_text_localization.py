from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class TrackTranslator(Protocol):
    provider_name: str

    def translate(self, captions: list[dict[str, str]], **kwargs: Any) -> Any:
        ...


def _translation_memory_key(value: Any) -> str:
    return " ".join(str(value or "").split())


def _track_requests(tracks: list[dict[str, Any]]) -> list[dict[str, str]]:
    eligible = [
        track for track in tracks
        if ((track.get("classification") or {}).get("action") == "localize")
    ]
    requests: list[dict[str, str]] = []
    seen_source_keys: set[str] = set()
    for index, track in enumerate(eligible):
        track_id = str(track.get("track_id") or "").strip()
        source_text = str(track.get("canonical_text") or "").strip()
        source_key = _translation_memory_key(source_text)
        if not track_id or not source_key:
            raise ValueError("localizable TextTrack is missing identity or source text")
        # Identical accepted visual text is translated once and reused across
        # temporal/geometry tracks. This is deterministic translation-memory
        # reuse, not track identity merging: every TextTrack remains distinct.
        if source_key in seen_source_keys:
            continue
        seen_source_keys.add(source_key)
        previous = str(eligible[index - 1].get("canonical_text") or "") if index > 0 else ""
        following = str(eligible[index + 1].get("canonical_text") or "") if index + 1 < len(eligible) else ""
        requests.append({
            "id": track_id,
            "source_text": source_text,
            "previous_source_text": previous,
            "next_source_text": following,
        })
    return requests


def localize_visual_text_tracks(
    evidence: dict[str, Any],
    *,
    translator: TrackTranslator,
    target_language: str = "English",
) -> dict[str, Any]:
    tracks = [dict(track) for track in list(evidence.get("tracks") or [])]
    requests = _track_requests(tracks)
    if not requests:
        return {
            "schema_version": 1,
            "target_language": target_language,
            "translation_provider": getattr(translator, "provider_name", "unknown"),
            "translation_model": getattr(translator, "model", None),
            "translated_track_count": 0,
            "tracks": tracks,
            "provider_usage": {"request_count": 0, "retry_count": 0, "cache_hits": 0, "cache_misses": 0},
        }

    result = translator.translate(requests)
    translations = list(getattr(result, "translations", []) or [])
    if len(translations) != len(requests):
        raise ValueError("visual TextTrack translation count mismatch")
    by_source_key: dict[str, str] = {}
    for request, item in zip(requests, translations):
        item_id = str(item.get("id") or "")
        english = str(item.get("english") or "").strip()
        if item_id != request["id"] or not english:
            raise ValueError("visual TextTrack translation identity mismatch")
        by_source_key[_translation_memory_key(request["source_text"])] = english

    translated_track_count = 0
    for track in tracks:
        classification = track.get("classification") or {}
        if classification.get("action") == "localize":
            translated = by_source_key.get(_translation_memory_key(track.get("canonical_text")))
            if not translated:
                raise ValueError("localizable TextTrack has no target translation")
            track["semantic_translation"] = translated
            track["display_text"] = translated
            track["localization_state"] = "translated"
            translated_track_count += 1
        else:
            track["semantic_translation"] = None
            track["display_text"] = str(track.get("canonical_text") or "")
            track["localization_state"] = "preserved"

    return {
        "schema_version": 1,
        "target_language": target_language,
        "translation_provider": getattr(translator, "provider_name", "unknown"),
        "translation_model": getattr(result, "model", getattr(translator, "model", None)),
        "translated_track_count": translated_track_count,
        "translation_request_count": len(requests),
        "translation_reuse_count": max(0, translated_track_count - len(requests)),
        "tracks": tracks,
        "provider_usage": {
            "request_count": int(getattr(result, "request_count", 0) or 0),
            "retry_count": int(getattr(result, "retry_count", 0) or 0),
            "cache_hits": int(getattr(result, "cache_hits", 0) or 0),
            "cache_misses": int(getattr(result, "cache_misses", 0) or 0),
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "request_ids": list(getattr(result, "request_ids", []) or []),
        },
    }


def write_visual_text_localization(
    path: Path,
    evidence: dict[str, Any],
    *,
    translator: TrackTranslator,
    target_language: str = "English",
) -> dict[str, Any]:
    localized = localize_visual_text_tracks(
        evidence,
        translator=translator,
        target_language=target_language,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(localized, ensure_ascii=False, indent=2), encoding="utf-8")
    return localized
