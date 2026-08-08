from pathlib import Path

import pytest

from app.providers.asr.fake import FakeASRProvider
from app.services.audio import extract_asr_audio
from app.services.timeline import build_timeline, disable_segment, merge_segments, split_segment, validate_timeline


def test_fake_asr_builds_schema_valid_timeline(monkeypatch):
    segments = FakeASRProvider().transcribe(Path("unused.wav"))
    timeline = build_timeline("proj_test", 90000, segments)
    validate_timeline(timeline)
    assert len(timeline["segments"]) == 3
    assert timeline["segments"][0]["source_text"]


def test_music_only_fixture_is_review_needed():
    segments = FakeASRProvider(music_only=True).transcribe(Path("music.wav"))
    timeline = build_timeline("proj_music", 10000, segments)
    segment = timeline["segments"][0]
    assert segment["status"] == "review_needed"
    assert "suspected_no_speech_or_music" in segment["issues"]
    assert segment["enabled"] is False


def test_split_merge_disable_operations():
    timeline = build_timeline("proj_ops", 90000, FakeASRProvider().transcribe(Path("unused.wav")))
    timeline = split_segment(timeline, "seg_0001", 2000)
    assert len(timeline["segments"]) == 4
    timeline = merge_segments(timeline, "seg_0001", "seg_0001_b")
    assert len(timeline["segments"]) == 3
    timeline = disable_segment(timeline, "seg_0002")
    assert timeline["segments"][1]["enabled"] is False
    validate_timeline(timeline)


def test_extract_audio_smoke():
    source = Path("input/source.mp4")
    if not source.exists():
        pytest.skip("source video not present")
    output = Path("data/test_tmp/source_asr_test.wav")
    extract_asr_audio(source, output, duration_seconds=1.0)
    assert output.exists()
    assert output.stat().st_size > 0
