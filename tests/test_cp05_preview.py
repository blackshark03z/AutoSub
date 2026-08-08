from pathlib import Path
from uuid import uuid4

import pytest

from app.providers.asr.fake import FakeASRProvider
from app.providers.tts.fake import FakeTTSProvider
from app.providers.tts.base import TTSRequest
from app.services.preview_render import build_video_filter, evaluate_tts_fit, write_tts_mix
from app.services.subtitles import format_ass_timestamp, format_srt_timestamp, write_ass, write_srt
from app.services.timeline import build_timeline
from app.services.tts_units import attach_tts_synthesis_units


def test_srt_and_ass_are_written(tmp_path):
    timeline = build_timeline("proj_subs", 90000, FakeASRProvider().transcribe(Path("unused.wav")))
    for segment in timeline["segments"]:
        segment["subtitle_text"] = f"Subtitle {segment['id']}\nLine 2"
        segment["spoken_text"] = f"Spoken {segment['id']}"
    srt = write_srt(timeline, tmp_path / "preview.srt")
    ass = write_ass(timeline, tmp_path / "preview.ass")
    first = timeline["segments"][0]
    expected = f"{format_srt_timestamp(first['start_ms'])} --> {format_srt_timestamp(first['end_ms'])}"
    assert expected in srt.read_text(encoding="utf-8")
    assert "[Events]" in ass.read_text(encoding="utf-8")
    assert "Dialogue:" in ass.read_text(encoding="utf-8")
    assert r"\NLine 2" in ass.read_text(encoding="utf-8")


def test_timestamp_formatters():
    assert format_srt_timestamp(3_725_006) == "01:02:05,006"
    assert format_ass_timestamp(3_725_006) == "1:02:05.00"


def test_legacy_bottom_mask_is_upgraded_to_opaque_full_coverage(tmp_path):
    timeline = build_timeline("proj_mask", 5_000, FakeASRProvider().transcribe(Path("unused.wav"))[:1])
    timeline["masks"][0].update({"y_norm": 0.82, "height_norm": 0.16, "opacity": 0.82})
    expression = build_video_filter(timeline, tmp_path / "subtitle.ass")
    assert "drawbox=x=64:y=547:w=1152:h=151:color=black@1.000:t=fill" in expression


def test_tts_mix_places_audio(tmp_path):
    project_id = f"proj_mix_{uuid4().hex[:8]}"
    timeline = build_timeline(project_id, 5000, FakeASRProvider().transcribe(Path("unused.wav"))[:1])
    artifact = tmp_path / "tts.wav"
    spoken_text = "mix"
    tts_result = FakeTTSProvider().synthesize(
        TTSRequest(
            project_id=project_id,
            segment_id="seg_0001",
            text=spoken_text,
            voice_id="fake_voice",
            model="fake-tts-v1",
            previous_request_ids=[],
        ),
        artifact,
    )
    generation = {
        "generation_id": "tts_1",
        "artifact_path": str(tts_result.audio_path),
        "sha256": "unused",
    }
    timeline["segments"][0]["spoken_text"] = spoken_text
    unit = attach_tts_synthesis_units(timeline)[0]
    unit["active_tts_generation_id"] = "tts_1"
    output = write_tts_mix(timeline, [generation], tmp_path / "mix.wav")
    assert output.exists()
    assert output.stat().st_size > 1000
    assert evaluate_tts_fit(timeline, [generation])[0]["status"] == "PASS"


def test_tts_mix_blocks_unresolved_fit_failure(tmp_path):
    timeline = build_timeline("proj_fit_fail", 5_000, FakeASRProvider().transcribe(Path("unused.wav"))[:1])
    timeline["segments"][0]["end_ms"] = 1_000
    timeline["segments"][0]["spoken_text"] = "This narration is intentionally much too long for the short subtitle timing slot."
    unit = attach_tts_synthesis_units(timeline)[0]
    artifact = tmp_path / "overflow.wav"
    result = FakeTTSProvider().synthesize(
        TTSRequest(
            project_id="proj_fit_fail",
            segment_id=unit["id"],
            text=unit["spoken_text"],
            voice_id="fake_voice",
            model="fake-tts-v1",
            previous_request_ids=[],
        ),
        artifact,
    )
    generation = {"generation_id": "tts_overflow", "artifact_path": str(result.audio_path), "sha256": "unused"}
    unit["active_tts_generation_id"] = "tts_overflow"
    assert evaluate_tts_fit(timeline, [generation])[0]["status"] == "FAIL"
    with pytest.raises(ValueError, match="fit failed"):
        write_tts_mix(timeline, [generation], tmp_path / "blocked.wav")
