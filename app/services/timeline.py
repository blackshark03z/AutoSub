import json
from pathlib import Path
from uuid import uuid4

from jsonschema import validate

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.paths import ensure_dir
from app.db.session import session_scope
from app.domain.models import TimelineRevision
from app.providers.asr.base import ASRSegment


def build_timeline(project_id: str, source_duration_ms: int, asr_segments: list[ASRSegment]) -> dict:
    settings = get_settings()
    timeline_segments = []
    for index, segment in enumerate(asr_segments, start=1):
        start_ms = max(0, int(segment.start * 1000))
        end_ms = min(source_duration_ms, max(start_ms + 1, int(segment.end * 1000)))
        issues = []
        status = "draft"
        if not segment.text.strip() or (segment.no_speech_prob is not None and segment.no_speech_prob >= 0.6):
            issues.append("suspected_no_speech_or_music")
            status = "review_needed"
        if segment.avg_logprob is not None and segment.avg_logprob < -1.2:
            issues.append("low_asr_confidence")
            status = "review_needed"
        timeline_segments.append(
            {
                "id": f"seg_{index:04d}",
                "ordinal": index,
                "chapter_id": "ch_001",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_text": segment.text,
                "translated_text": "",
                "spoken_text": "",
                "subtitle_text": "",
                "enabled": bool(segment.text.strip()),
                "speaker_id": "narrator",
                "voice_profile_id": None,
                "timing_policy": "fit_slot",
                "status": status,
                "active_tts_generation_id": None,
                "issues": issues,
                "locks": {"timing": False, "spoken_text": False, "subtitle_text": False, "voice": False},
                "qa": {
                    "transcript_approved": False,
                    "content_approved": False,
                    "voice_approved": False,
                    "timing_approved": False,
                },
            }
        )
    return {
        "schema_version": 1,
        "project_id": project_id,
        "source_duration_ms": source_duration_ms,
        "source_language": "zh",
        "target_language": "en",
        "target_locale": "en-US",
        "market_profile_id": "roblox_en_us",
        "transformation_mode": "transformative_edit",
        "active_revision_id": None,
        "chapters": [{"id": "ch_001", "title": "Vertical Slice", "start_ms": 0, "end_ms": source_duration_ms}],
        "segments": timeline_segments,
        "masks": [
            {
                "id": "mask_bottom",
                "x_norm": 0.05,
                "y_norm": 0.76,
                "width_norm": 0.9,
                "height_norm": 0.21,
                "style": "blur_dark",
                "mode": "always",
                "opacity": 1.0,
                "intervals": [],
            }
        ],
    }


def validate_timeline(timeline: dict) -> None:
    settings = get_settings()
    schema = json.loads((settings.root / "schemas" / "timeline.schema.json").read_text(encoding="utf-8"))
    validate(instance=timeline, schema=schema)
    last_end = -1
    for segment in timeline["segments"]:
        if segment["end_ms"] <= segment["start_ms"]:
            raise ValueError("Timeline segment must have positive duration")
        if segment["start_ms"] < last_end:
            raise ValueError("Timeline segments overlap")
        if segment["end_ms"] > timeline["source_duration_ms"]:
            raise ValueError("Segment exceeds source duration")
        last_end = segment["end_ms"]
    _validate_tts_units(timeline)


def _validate_tts_units(timeline: dict) -> None:
    if "tts_units" not in timeline:
        return
    segments_by_id = {segment["id"]: segment for segment in timeline["segments"]}
    assigned: list[str] = []
    last_end = -1
    for unit in timeline["tts_units"]:
        members = []
        for segment_id in unit["segment_ids"]:
            segment = segments_by_id.get(segment_id)
            if segment is None:
                raise ValueError("TTS synthesis unit references an unknown subtitle segment")
            members.append(segment)
            assigned.append(segment_id)
        if unit["start_ms"] != members[0]["start_ms"] or unit["end_ms"] != members[-1]["end_ms"]:
            raise ValueError("TTS synthesis unit timing does not match its member segments")
        if unit["start_ms"] < last_end:
            raise ValueError("TTS synthesis units overlap")
        if unit["end_ms"] > timeline["source_duration_ms"]:
            raise ValueError("TTS synthesis unit exceeds source duration")
        expected_text = " ".join(member["spoken_text"].strip() for member in members).strip()
        if unit["spoken_text"] != expected_text:
            raise ValueError("TTS synthesis unit spoken text is stale")
        last_end = unit["end_ms"]
    if len(assigned) != len(set(assigned)):
        raise ValueError("Subtitle segment belongs to more than one TTS synthesis unit")
    expected = {
        segment["id"]
        for segment in timeline["segments"]
        if segment.get("enabled", True)
        and segment.get("spoken_text", "").strip()
        and segment.get("status") != "review_needed"
        and not segment.get("issues")
    }
    if set(assigned) != expected:
        raise ValueError("TTS synthesis units do not cover every eligible spoken segment exactly once")


def save_timeline_revision(project_id: str, timeline: dict) -> dict:
    if timeline.get("project_id") != project_id:
        raise ValueError("Timeline project does not match route project")
    validate_timeline(timeline)
    revision_id = f"tlrev_{uuid4().hex[:12]}"
    timeline["active_revision_id"] = revision_id
    settings = get_settings()
    timeline_dir = ensure_dir(settings.data_dir / "projects" / project_id / "timeline" / "revisions")
    path = timeline_dir / f"{revision_id}.json"
    path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    digest = sha256_file(path)
    with session_scope() as session:
        session.add(TimelineRevision(project_id=project_id, revision_id=revision_id, path=str(path), sha256=digest))
    return {"revision_id": revision_id, "path": str(path), "sha256": digest, "timeline": timeline}


def load_latest_timeline(project_id: str) -> dict:
    with session_scope() as session:
        revision = (
            session.query(TimelineRevision)
            .filter(TimelineRevision.project_id == project_id)
            .order_by(TimelineRevision.created_at.desc(), TimelineRevision.id.desc())
            .first()
        )
        if revision is None:
            raise FileNotFoundError("Timeline revision not found")
        path = Path(revision.path)
    return json.loads(path.read_text(encoding="utf-8"))


def split_segment(timeline: dict, segment_id: str, split_ms: int) -> dict:
    segments = timeline["segments"]
    index = _find_segment_index(segments, segment_id)
    original = segments[index]
    if not (original["start_ms"] < split_ms < original["end_ms"]):
        raise ValueError("Split point must be inside segment")
    left = dict(original)
    right = dict(original)
    left["end_ms"] = split_ms
    right["id"] = f"{original['id']}_b"
    right["start_ms"] = split_ms
    segments[index : index + 1] = [left, right]
    _renumber(segments)
    return timeline


def merge_segments(timeline: dict, first_id: str, second_id: str) -> dict:
    segments = timeline["segments"]
    first_index = _find_segment_index(segments, first_id)
    if first_index + 1 >= len(segments):
        raise ValueError("Segments must be adjacent")
    second = segments[first_index + 1]
    if second["id"] != second_id:
        raise ValueError("Segments must be adjacent")
    first = segments[first_index]
    merged = dict(first)
    merged["end_ms"] = second["end_ms"]
    merged["source_text"] = (first["source_text"] + " " + second["source_text"]).strip()
    merged["issues"] = sorted(set(first.get("issues", []) + second.get("issues", [])))
    segments[first_index : first_index + 2] = [merged]
    _renumber(segments)
    return timeline


def disable_segment(timeline: dict, segment_id: str) -> dict:
    for segment in timeline["segments"]:
        if segment["id"] == segment_id:
            segment["enabled"] = False
            segment["status"] = "rejected"
            return timeline
    raise ValueError("Segment not found")


def _find_segment_index(segments: list[dict], segment_id: str) -> int:
    for index, segment in enumerate(segments):
        if segment["id"] == segment_id:
            return index
    raise ValueError("Segment not found")


def _renumber(segments: list[dict]) -> None:
    for index, segment in enumerate(segments, start=1):
        segment["ordinal"] = index
