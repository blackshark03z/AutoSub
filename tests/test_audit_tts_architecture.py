from pathlib import Path
from uuid import uuid4

import pytest

from app.db.session import init_db, session_scope
from app.domain.models import Project
from app.providers.asr.fake import FakeASRProvider
from app.providers.tts.fake import FakeTTSProvider
from app.services.timeline import build_timeline, validate_timeline
from app.services.tts_generation import generate_tts_for_timeline
from app.services.tts_units import attach_tts_synthesis_units, build_tts_synthesis_units


def _fragmented_timeline() -> dict:
    timeline = build_timeline("proj_grouping", 12_000, FakeASRProvider().transcribe(Path("unused.wav")))
    timeline["segments"] = []
    for index in range(8):
        timeline["segments"].append(
            {
                "id": f"seg_{index + 1:04d}",
                "ordinal": index + 1,
                "chapter_id": "ch_001",
                "start_ms": index * 1_400,
                "end_ms": index * 1_400 + 1_200,
                "source_text": f"source {index}",
                "translated_text": f"translated {index}",
                "spoken_text": f"spoken fragment {index}.",
                "subtitle_text": f"subtitle {index}",
                "enabled": True,
                "speaker_id": "narrator",
                "voice_profile_id": None,
                "timing_policy": "fit_slot",
                "status": "draft",
                "active_tts_generation_id": None,
                "issues": [],
                "locks": {"timing": False, "spoken_text": False, "subtitle_text": False, "voice": False},
                "qa": {
                    "transcript_approved": False,
                    "content_approved": False,
                    "voice_approved": False,
                    "timing_approved": False,
                },
            }
        )
    return timeline


def test_subtitle_fragments_are_grouped_into_independent_tts_units():
    timeline = _fragmented_timeline()
    original_timing = [(segment["start_ms"], segment["end_ms"]) for segment in timeline["segments"]]
    units = attach_tts_synthesis_units(timeline, target_max_ms=5_000)
    assert len(units) < len(timeline["segments"])
    assert [segment_id for unit in units for segment_id in unit["segment_ids"]] == [
        segment["id"] for segment in timeline["segments"]
    ]
    assert [(segment["start_ms"], segment["end_ms"]) for segment in timeline["segments"]] == original_timing
    validate_timeline(timeline)


def test_grouping_respects_voice_and_manual_lock_boundaries():
    timeline = _fragmented_timeline()
    timeline["segments"][2]["locks"]["spoken_text"] = True
    timeline["segments"][5]["voice_profile_id"] = "alternate"
    units = build_tts_synthesis_units(timeline)
    locked = next(unit for unit in units if "seg_0003" in unit["segment_ids"])
    alternate = next(unit for unit in units if "seg_0006" in unit["segment_ids"])
    assert locked["segment_ids"] == ["seg_0003"]
    assert alternate["segment_ids"] == ["seg_0006"]


def test_timeline_rejects_stale_or_duplicate_tts_unit_membership():
    timeline = _fragmented_timeline()
    attach_tts_synthesis_units(timeline)
    timeline["tts_units"][0]["spoken_text"] = "stale"
    with pytest.raises(ValueError, match="stale"):
        validate_timeline(timeline)


def test_grouped_generation_binds_units_without_changing_subtitle_timing():
    timeline = _fragmented_timeline()
    timing = [(segment["start_ms"], segment["end_ms"]) for segment in timeline["segments"]]
    project_id = f"proj_group_bind_{uuid4().hex[:8]}"
    timeline["project_id"] = project_id
    for segment in timeline["segments"]:
        segment["spoken_text"] = f"{project_id} {segment['spoken_text']}"
    init_db()
    with session_scope() as session:
        session.add(Project(project_id=project_id, title=project_id))
    generations = generate_tts_for_timeline(project_id, timeline, FakeTTSProvider(), "fake_voice")
    assert len(generations) == len(timeline["tts_units"])
    assert all(unit["active_tts_generation_id"] for unit in timeline["tts_units"])
    assert [(segment["start_ms"], segment["end_ms"]) for segment in timeline["segments"]] == timing
    validate_timeline(timeline)
