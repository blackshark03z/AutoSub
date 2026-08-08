import hashlib


def build_tts_synthesis_units(
    timeline: dict,
    *,
    target_min_ms: int = 3_000,
    target_max_ms: int = 20_000,
    max_gap_ms: int = 1_500,
    max_characters: int = 700,
) -> list[dict]:
    """Group narration for synthesis without changing subtitle timing units."""
    units: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        units.append(_make_unit(current, len(units) + 1))
        current.clear()

    for segment in timeline["segments"]:
        if not _is_spoken(segment):
            flush()
            continue
        if not current:
            current.append(segment)
            continue
        if _must_split(current[-1], segment):
            flush()
            current.append(segment)
            continue
        candidate_span = segment["end_ms"] - current[0]["start_ms"]
        candidate_gap = segment["start_ms"] - current[-1]["end_ms"]
        candidate_chars = len(_join_spoken(current + [segment]))
        if candidate_span > target_max_ms or candidate_gap > max_gap_ms or candidate_chars > max_characters:
            flush()
        current.append(segment)
        if segment["end_ms"] - current[0]["start_ms"] >= target_min_ms and _locked_boundary(segment):
            flush()
    flush()
    return units


def attach_tts_synthesis_units(timeline: dict, **grouping_options) -> list[dict]:
    units = build_tts_synthesis_units(timeline, **grouping_options)
    timeline["tts_units"] = units
    return units


def _make_unit(segments: list[dict], ordinal: int) -> dict:
    segment_ids = [segment["id"] for segment in segments]
    identity = hashlib.sha256("\n".join(segment_ids).encode("utf-8")).hexdigest()[:12]
    first = segments[0]
    last = segments[-1]
    return {
        "id": f"ttsu_{identity}",
        "ordinal": ordinal,
        "chapter_id": first.get("chapter_id"),
        "start_ms": first["start_ms"],
        "end_ms": last["end_ms"],
        "segment_ids": segment_ids,
        "spoken_text": _join_spoken(segments),
        "speaker_id": first.get("speaker_id", "narrator"),
        "voice_profile_id": first.get("voice_profile_id"),
        "timing_policy": first.get("timing_policy", "fit_slot"),
        "active_tts_generation_id": None,
    }


def _join_spoken(segments: list[dict]) -> str:
    return " ".join(segment["spoken_text"].strip() for segment in segments if segment.get("spoken_text", "").strip())


def _is_spoken(segment: dict) -> bool:
    return bool(
        segment.get("enabled", True)
        and segment.get("spoken_text", "").strip()
        and segment.get("status") != "review_needed"
        and not segment.get("issues")
    )


def _must_split(previous: dict, current: dict) -> bool:
    compatibility_fields = ("chapter_id", "speaker_id", "voice_profile_id", "timing_policy")
    return (
        any(previous.get(field) != current.get(field) for field in compatibility_fields)
        or _locked_boundary(previous)
        or _locked_boundary(current)
    )


def _locked_boundary(segment: dict) -> bool:
    locks = segment.get("locks", {})
    return any(locks.get(name, False) for name in ("timing", "spoken_text", "voice"))
