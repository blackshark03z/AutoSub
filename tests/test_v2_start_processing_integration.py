from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.simple_workflow as workflow
from app.core.hashing import sha256_file
from app.providers.translation.gemini import GeminiCaptionTranslationError
from app.db.session import init_db
from tests.test_cp10b_simple_workflow import _make_tiny_video, configure_test_root


def _resolved() -> dict:
    return {
        "subtitle_provenance": "user_import",
        "cues": [
            {
                "cue_id": "cue-1",
                "start_ms": 50,
                "end_ms": 700,
                "source_text": "你好",
                "translation_text": "Hello",
                "resolved_text": "Hello",
                "subtitle_text": "Hello",
                "spoken_text": "Hello",
            }
        ],
    }


def _write_ass(run_dir: Path) -> None:
    path = run_dir / "subtitles" / "dialogue_subtitles_en.ass"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.05,0:00:00.70,Default,,0,0,0,,Hello\n",
        encoding="utf-8",
    )


def _install_common_fakes(monkeypatch, source: Path):
    monkeypatch.setattr(workflow, "active_track_provenance", lambda _run_id: "user_import")
    monkeypatch.setattr(workflow, "resolved_cues", lambda _run_id: _resolved())

    def fake_subtitle_process(row, resolved, *, output_override=None, audio_override=None):
        run_dir = Path(row.run_directory)
        _write_ass(run_dir)
        destination = Path(output_override) if output_override is not None else run_dir / "output" / "final_video.mp4"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        with destination.open("ab") as handle:
            handle.write(b"autosub-subtitle-stage")
        return destination

    monkeypatch.setattr(workflow, "_bounded_subtitle_process", fake_subtitle_process)


def _fake_visual_pipeline(source_path, run_directory, output_path, **_kwargs):
    run_dir = Path(run_directory)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(source_path), output)
    with output.open("ab") as handle:
        handle.write(b"autosub-full-visual")
    visual = run_dir / "visual" / "visual_quality_evidence.json"
    visual.parent.mkdir(parents=True, exist_ok=True)
    visual.write_text(json.dumps({
        "status": "PASS",
        "eligible": True,
        "failure_count": 0,
        "residual_source_track_count": 0,
    }), encoding="utf-8")
    return {"quality": {"status": "PASS", "eligible": True}, "output_path": output}


def test_full_visual_start_processing_composes_and_accepts_only_bound_qc(monkeypatch, tmp_path: Path) -> None:
    configure_test_root(monkeypatch, tmp_path)
    init_db()
    monkeypatch.setenv("AUTOSUB_ENABLE_EXPERIMENTAL_LOCALIZATION_V2", "1")
    source = tmp_path / "visual-source.mp4"
    _make_tiny_video(source)
    _install_common_fakes(monkeypatch, source)
    monkeypatch.setattr(workflow, "_prepare_full_visual_runtime", lambda *_args, **_kwargs: SimpleNamespace(provider_name="fake", model="fake"))
    monkeypatch.setattr(workflow, "execute_full_visual_localization", _fake_visual_pipeline)

    created = workflow.create_or_reuse_run(str(source), settings={
        "localization_mode": "full_visual_localization",
        "caption_mode": "external_audio_transcription",
        "target_language": "English",
    })
    completed = workflow.start_processing(created["run_id"])
    assert completed["internal_state"] == "completed"
    assert completed["result_eligible"] is True
    run_dir = Path(completed["run_directory"])
    quality = json.loads((run_dir / "visual" / "visual_quality_evidence.json").read_text(encoding="utf-8"))
    assert quality["acceptance_binding"]["run_id"] == created["run_id"]
    assert quality["acceptance_binding"]["output_sha256"] == sha256_file(Path(completed["output"]["path"]))


def test_full_visual_gemini_failure_keeps_true_phase_and_provider_telemetry(monkeypatch, tmp_path: Path) -> None:
    configure_test_root(monkeypatch, tmp_path)
    init_db()
    monkeypatch.setenv("AUTOSUB_ENABLE_EXPERIMENTAL_LOCALIZATION_V2", "1")
    source = tmp_path / "visual-provider-failure.mp4"
    _make_tiny_video(source)
    _install_common_fakes(monkeypatch, source)
    monkeypatch.setattr(workflow, "_prepare_full_visual_runtime", lambda *_args, **_kwargs: SimpleNamespace(provider_name="gemini", model="fake"))

    def fail_visual_pipeline(*_args, **_kwargs):
        raise GeminiCaptionTranslationError(
            "GEMINI_CAPTION_REPEATED_GENERIC_OUTPUT",
            "Gemini repeated the same translation across unrelated captions.",
            request_count=2,
            retry_count=1,
            failed_caption_ids=["vtt_1", "vtt_2"],
        )

    monkeypatch.setattr(workflow, "execute_full_visual_localization", fail_visual_pipeline)
    created = workflow.create_or_reuse_run(str(source), settings={
        "localization_mode": "full_visual_localization",
        "caption_mode": "external_audio_transcription",
        "target_language": "English",
    })

    with pytest.raises(ValueError, match="Gemini"):
        workflow.start_processing(created["run_id"])

    failed = workflow.get_run(created["run_id"])
    assert failed["internal_state"] == "failed"
    assert failed["phase"] == "Localize visual text"
    assert failed["failure_category"] == "GEMINI_CAPTION_REPEATED_GENERIC_OUTPUT"
    assert failed["failure_category"] != "render_failed"
    assert failed["failure_detail"] == {
        "code": "GEMINI_CAPTION_REPEATED_GENERIC_OUTPUT",
        "message": "Gemini repeated the same translation across unrelated captions.",
    }
    assert failed["provider_calls"]["gemini"] == 2
    failure = json.loads((Path(failed["run_directory"]) / "logs" / "processing_failure.json").read_text(encoding="utf-8"))
    assert failure["provider"] == "gemini"
    assert failure["provider_usage"] == {"request_count": 2, "retry_count": 1}


def test_full_voice_start_processing_binds_kokoro_mix_and_visual_qc(monkeypatch, tmp_path: Path) -> None:
    configure_test_root(monkeypatch, tmp_path)
    init_db()
    monkeypatch.setenv("AUTOSUB_ENABLE_EXPERIMENTAL_LOCALIZATION_V2", "1")
    source = tmp_path / "voice-source.mp4"
    _make_tiny_video(source)
    _install_common_fakes(monkeypatch, source)
    monkeypatch.setattr(workflow, "_prepare_full_visual_runtime", lambda *_args, **_kwargs: SimpleNamespace(provider_name="fake", model="fake"))
    monkeypatch.setattr(workflow, "execute_full_visual_localization", _fake_visual_pipeline)

    def fake_narration(*, run_directory, voice_id, base_speed, resolved, **_kwargs):
        audio_dir = Path(run_directory) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        mix = audio_dir / "narration_mix.wav"
        mix.write_bytes(b"kokoro-mix")
        manifest = {
            "schema_version": 1,
            "status": "PASS",
            "provider": "kokoro_local",
            "model": "hexgrad/Kokoro-82M",
            "voice_id": voice_id,
            "base_speed": base_speed,
            "mix_path": str(mix.resolve()),
            "mix_sha256": sha256_file(mix),
            "expected_retimed_cue_count": 1,
            "retimed_cue_count": 1,
        }
        (audio_dir / "narration_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        retimed = {**resolved, "cues": [{**resolved["cues"][0], "timing_provenance": "kokoro_token_alignment"}]}
        return {"resolved": retimed, "mix_path": mix, "manifest": manifest}

    monkeypatch.setattr(workflow, "generate_kokoro_narration", fake_narration)

    created = workflow.create_or_reuse_run(str(source), settings={
        "localization_mode": "full_localization_voice",
        "caption_mode": "external_audio_transcription",
        "target_language": "English",
        "tts_provider": "kokoro_local",
        "voice_id": "bm_george",
        "voice_speed": 1.0,
    })
    completed = workflow.start_processing(created["run_id"])
    assert completed["internal_state"] == "completed"
    assert completed["result_eligible"] is True
    run_dir = Path(completed["run_directory"])
    narration = json.loads((run_dir / "audio" / "narration_manifest.json").read_text(encoding="utf-8"))
    assert narration["acceptance_binding"]["run_id"] == created["run_id"]
    assert narration["acceptance_binding"]["output_sha256"] == sha256_file(Path(completed["output"]["path"]))
    assert narration["voice_id"] == "bm_george"
