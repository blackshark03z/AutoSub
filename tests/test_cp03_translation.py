from pathlib import Path
from uuid import uuid4

import pytest

from app.db.session import init_db, session_scope
from app.domain.models import Project
from app.providers.asr.fake import FakeASRProvider
from app.providers.translation.fake import FakeTranslationProvider
from app.providers.translation.manual import ManualImportTranslationProvider
from app.services.content_transform import (
    apply_translation_result,
    build_translation_request,
    transform_timeline,
    partition_translation_segments,
    validate_translation_response,
)
from app.services.timeline import build_timeline


def _project(project_id: str) -> None:
    init_db()
    with session_scope() as session:
        if session.query(Project).filter(Project.project_id == project_id).one_or_none() is None:
            session.add(Project(project_id=project_id, title=project_id))


def test_fake_translation_preserves_four_text_fields_and_uses_cache():
    project_id = f"proj_cp03_fake_{uuid4().hex[:8]}"
    _project(project_id)
    timeline = build_timeline(project_id, 90000, FakeASRProvider().transcribe(Path("unused.wav")))
    original = dict(timeline["segments"][0])
    provider = FakeTranslationProvider()
    first = transform_timeline(project_id, timeline, provider)
    assert first["cache_status"] == "miss"
    assert provider.calls == 1
    transformed = first["timeline"]["timeline"]["segments"][0]
    assert transformed["id"] == original["id"]
    assert transformed["start_ms"] == original["start_ms"]
    assert transformed["end_ms"] == original["end_ms"]
    assert transformed["enabled"] is original["enabled"]
    assert transformed["speaker_id"] == original["speaker_id"]
    assert transformed["source_text"]
    assert transformed["translated_text"]
    assert transformed["spoken_text"]
    assert transformed["subtitle_text"]
    assert transformed["translated_text"] != transformed["spoken_text"]

    second_timeline = build_timeline(project_id, 90000, FakeASRProvider().transcribe(Path("unused.wav")))
    second = transform_timeline(project_id, second_timeline, provider)
    assert second["cache_status"] == "hit"
    assert second["idempotent_reuse"] is True
    assert second["timeline"]["revision_id"] == first["timeline"]["revision_id"]
    assert provider.calls == 1


def test_duration_budget_excludes_review_needed_segments():
    timeline = build_timeline("proj_budget", 10000, FakeASRProvider(music_only=True).transcribe(Path("music.wav")))
    request = build_translation_request(timeline, "fake")
    assert request.duration_budget_ms == 0
    assert request.segments == []


def test_manual_import_response_is_validated_and_applied():
    project_id = "proj_manual"
    _project(project_id)
    timeline = build_timeline(project_id, 90000, FakeASRProvider().transcribe(Path("unused.wav")))
    response = {
        "schema_version": 1,
        "segments": [
            {
                "id": segment["id"],
                "translated_text": f"Faithful translation {segment['id']}",
                "spoken_text": f"Natural spoken line {segment['id']}.",
                "subtitle_text": f"Short subtitle {segment['id']}",
                "duration_budget_ms": segment["end_ms"] - segment["start_ms"],
            }
            for segment in timeline["segments"]
        ],
    }
    result = transform_timeline(project_id, timeline, ManualImportTranslationProvider(response))
    updated = result["timeline"]["timeline"]["segments"][0]
    assert updated["translated_text"] == "Faithful translation seg_0001"
    assert updated["spoken_text"] == "Natural spoken line seg_0001."
    assert updated["subtitle_text"] == "Short subtitle seg_0001"


def test_transform_response_requires_schema_fields():
    with pytest.raises(ValueError):
        validate_translation_response({"schema_version": 1, "segments": [{"id": "seg_0001"}]})


def test_transform_response_requires_exact_ids_and_valid_types():
    response = {
        "schema_version": 1,
        "segments": [
            {
                "id": "seg_0001",
                "translated_text": "A",
                "spoken_text": "B",
                "subtitle_text": "C",
                "duration_budget_ms": "3000",
            }
        ],
    }
    with pytest.raises(ValueError):
        validate_translation_response(response, expected_ids=["seg_0001"])


def test_apply_translation_preserves_non_text_metadata():
    timeline = build_timeline("proj_preserve", 90000, FakeASRProvider().transcribe(Path("unused.wav")))
    original = dict(timeline["segments"][0])
    response = {
        "schema_version": 1,
        "segments": [
            {
                "id": original["id"],
                "translated_text": "Translated",
                "spoken_text": "Spoken",
                "subtitle_text": "Subtitle",
                "duration_budget_ms": original["end_ms"] - original["start_ms"],
            }
        ],
    }
    updated = apply_translation_result(timeline, response, expected_ids=[original["id"]])
    segment = updated["segments"][0]
    assert segment["start_ms"] == original["start_ms"]
    assert segment["end_ms"] == original["end_ms"]
    assert segment["enabled"] == original["enabled"]
    assert segment["speaker_id"] == original["speaker_id"]


def test_translation_blocks_are_bounded_without_one_call_per_fragment():
    segments = [
        {
            "id": f"seg_{index:04d}",
            "start_ms": index * 1_000,
            "end_ms": (index + 1) * 1_000,
            "duration_budget_ms": 1_000,
            "source_text": "short source fragment",
        }
        for index in range(49)
    ]
    blocks = partition_translation_segments(segments)
    assert len(blocks) == 3
    assert all(len(block) <= 24 for block in blocks)
    assert all(sum(segment["duration_budget_ms"] for segment in block) <= 45_000 for block in blocks)
