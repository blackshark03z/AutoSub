import wave
from pathlib import Path

import pytest

from app.services.narration_pipeline import (
    NarrationFitError,
    allocate_narration_timing_slack,
    build_narration_timeline,
    evaluate_narration_fit,
    retime_resolved_cues_to_narration,
    write_narration_mix,
)
from app.services.tts_units import attach_tts_synthesis_units


def _write_wav(path: Path, duration_ms: int, sample_rate: int = 24_000) -> None:
    frames = round(duration_ms * sample_rate / 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x01" * frames)


def _generation(generation_id: str, path: Path, duration_ms: int, tokens=None):
    return {
        "generation_id": generation_id,
        "artifact_path": str(path),
        "alignment": {
            "duration_seconds": duration_ms / 1000,
            "sample_rate": 24000,
            "tokens": tokens or [],
        },
    }


def test_tts_unit_records_character_spans_for_each_source_segment() -> None:
    resolved = {"cues": [
        {"cue_id": "a", "start_ms": 0, "end_ms": 1500, "resolved_text": "Hello there."},
        {"cue_id": "b", "start_ms": 1700, "end_ms": 3200, "resolved_text": "Second line."},
    ]}
    timeline = build_narration_timeline(resolved, source_duration_ms=5000)
    units = attach_tts_synthesis_units(timeline)
    assert len(units) == 1
    assert units[0]["spoken_text"] == "Hello there. Second line."
    assert units[0]["segment_text_spans"] == [
        {"segment_id": "a", "source_start": 0, "source_end": 12},
        {"segment_id": "b", "source_start": 13, "source_end": 25},
    ]


def test_token_alignment_retimes_each_cue_to_generated_narration() -> None:
    resolved = {"cues": [
        {"cue_id": "a", "start_ms": 1000, "end_ms": 2200, "resolved_text": "Hello there."},
        {"cue_id": "b", "start_ms": 2300, "end_ms": 4000, "resolved_text": "Second line."},
    ]}
    timeline = build_narration_timeline(resolved, source_duration_ms=5000)
    unit = attach_tts_synthesis_units(timeline)[0]
    unit["active_tts_generation_id"] = "g1"
    generation = _generation("g1", Path("unused.wav"), 2500, tokens=[
        {"text": "Hello", "source_start": 0, "source_end": 5, "start": 0.10, "end": 0.55},
        {"text": "there.", "source_start": 6, "source_end": 12, "start": 0.55, "end": 1.00},
        {"text": "Second", "source_start": 13, "source_end": 19, "start": 1.15, "end": 1.70},
        {"text": "line.", "source_start": 20, "source_end": 25, "start": 1.70, "end": 2.25},
    ])
    retimed = retime_resolved_cues_to_narration(resolved, timeline, [generation])
    assert retimed["cues"][0]["start_ms"] == 1100
    assert retimed["cues"][0]["end_ms"] == 2000
    assert retimed["cues"][1]["start_ms"] == 2150
    assert retimed["cues"][1]["end_ms"] == 3250
    assert retimed["cues"][1]["timing_provenance"] == "kokoro_token_alignment"
    assert retimed["cues"][0]["narration_token_start_ms"] == 1100
    assert retimed["cues"][1]["narration_token_end_ms"] == 3250
    assert retimed["narration_timing"]["aligned_cue_count"] == 2


def test_mix_streams_24khz_audio_with_silence_and_exact_source_duration(tmp_path) -> None:
    resolved = {"cues": [
        {"cue_id": "a", "start_ms": 1000, "end_ms": 2000, "resolved_text": "One."},
        {"cue_id": "b", "start_ms": 3000, "end_ms": 4000, "resolved_text": "Two."},
    ]}
    timeline = build_narration_timeline(resolved, source_duration_ms=5000)
    units = attach_tts_synthesis_units(timeline, max_gap_ms=0)
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _write_wav(first, 800)
    _write_wav(second, 700)
    units[0]["active_tts_generation_id"] = "g1"
    units[1]["active_tts_generation_id"] = "g2"
    generations = [_generation("g1", first, 800), _generation("g2", second, 700)]
    output = write_narration_mix(timeline, generations, tmp_path / "mix.wav")
    with wave.open(str(output), "rb") as audio:
        assert audio.getframerate() == 24000
        assert audio.getnchannels() == 1
        assert audio.getnframes() == 120000


def test_fit_uses_pcm_duration_when_alignment_underreports_audio(tmp_path) -> None:
    resolved = {"cues": [
        {"cue_id": "a", "start_ms": 0, "end_ms": 1000, "resolved_text": "One."},
    ]}
    timeline = build_narration_timeline(resolved, source_duration_ms=1000)
    unit = attach_tts_synthesis_units(timeline)[0]
    wav = tmp_path / "actual.wav"
    _write_wav(wav, 1010)
    unit["active_tts_generation_id"] = "g1"
    generation = _generation("g1", wav, 980)
    fit = evaluate_narration_fit(timeline, [generation])
    assert fit[0]["duration_source"] == "wav_pcm"
    assert fit[0]["tts_duration_ms"] == 1010.0
    assert fit[0]["overflow_ms"] == 10.0
    assert fit[0]["status"] == "FAIL"
    with pytest.raises(NarrationFitError):
        write_narration_mix(timeline, [generation], tmp_path / "mix.wav")


def test_fit_fails_when_clip_runs_into_next_unit(tmp_path) -> None:
    resolved = {"cues": [
        {"cue_id": "a", "start_ms": 1000, "end_ms": 1800, "resolved_text": "One."},
        {"cue_id": "b", "start_ms": 2000, "end_ms": 3000, "resolved_text": "Two."},
    ]}
    timeline = build_narration_timeline(resolved, source_duration_ms=4000)
    units = attach_tts_synthesis_units(timeline, max_gap_ms=0)
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _write_wav(first, 1200)
    _write_wav(second, 500)
    units[0]["active_tts_generation_id"] = "g1"
    units[1]["active_tts_generation_id"] = "g2"
    generations = [_generation("g1", first, 1200), _generation("g2", second, 500)]
    fit = evaluate_narration_fit(timeline, generations)
    assert fit[0]["status"] == "FAIL"
    assert fit[0]["overflow_ms"] == 200.0
    with pytest.raises(NarrationFitError):
        write_narration_mix(timeline, generations, tmp_path / "mix.wav")


def test_slack_allocator_borrows_existing_silence_without_mutating_source_timing(tmp_path) -> None:
    resolved = {"cues": [
        {"cue_id": "a", "start_ms": 1000, "end_ms": 1800, "resolved_text": "One."},
        {"cue_id": "b", "start_ms": 2500, "end_ms": 3300, "resolved_text": "Two."},
        {"cue_id": "c", "start_ms": 3600, "end_ms": 4400, "resolved_text": "Three."},
    ]}
    timeline = build_narration_timeline(resolved, source_duration_ms=4500)
    units = attach_tts_synthesis_units(timeline, max_gap_ms=0)
    paths = [tmp_path / f"u{index}.wav" for index in range(3)]
    for path, duration in zip(paths, (900, 900, 1100)):
        _write_wav(path, duration)
    generations = []
    for index, (unit, path, duration) in enumerate(zip(units, paths, (900, 900, 1100)), start=1):
        unit["active_tts_generation_id"] = f"g{index}"
        generations.append(_generation(f"g{index}", path, duration))
    before = [(unit["start_ms"], unit["end_ms"]) for unit in units]
    assert evaluate_narration_fit(timeline, generations)[-1]["overflow_ms"] == 200.0
    adjustments = allocate_narration_timing_slack(timeline, generations)
    assert adjustments
    assert [(unit["start_ms"], unit["end_ms"]) for unit in units] == before
    assert units[-1]["narration_start_ms"] == 3400
    assert evaluate_narration_fit(timeline, generations)[-1]["status"] == "PASS"
    write_narration_mix(timeline, generations, tmp_path / "slack_mix.wav")


def test_token_alignment_uses_narration_start_without_overwriting_source_anchor() -> None:
    resolved = {"cues": [{"cue_id": "a", "start_ms": 1000, "end_ms": 2000, "resolved_text": "Hello."}]}
    timeline = build_narration_timeline(resolved, source_duration_ms=3000)
    unit = attach_tts_synthesis_units(timeline)[0]
    unit["narration_start_ms"] = 800
    unit["active_tts_generation_id"] = "g1"
    generation = _generation("g1", Path("unused.wav"), 800, tokens=[
        {"text": "Hello.", "source_start": 0, "source_end": 6, "start": 0.1, "end": 0.7},
    ])
    retimed = retime_resolved_cues_to_narration(resolved, timeline, [generation])
    assert unit["start_ms"] == 1000
    assert retimed["cues"][0]["source_start_ms"] == 1000
    assert retimed["cues"][0]["start_ms"] == 900
    assert retimed["cues"][0]["end_ms"] == 1500
