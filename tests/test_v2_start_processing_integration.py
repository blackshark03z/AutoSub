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


def test_full_voice_repairs_display_projection_after_narration_alignment(monkeypatch, tmp_path: Path) -> None:
    configure_test_root(monkeypatch, tmp_path)
    init_db()
    monkeypatch.setenv("AUTOSUB_ENABLE_EXPERIMENTAL_LOCALIZATION_V2", "1")
    source = tmp_path / "voice-post-narration-projection.mp4"
    _make_tiny_video(source)
    _install_common_fakes(monkeypatch, source)
    semantic = {
        "subtitle_provenance": "user_import",
        "cues": [{
            "cue_id": "cue-1",
            "start_ms": 50,
            "end_ms": 950,
            "source_text": "他们会见我",
            "translation_text": "They will meet me.",
            "resolved_text": "They will meet me.",
            "subtitle_text": "They will meet me.",
            "spoken_text": "They will meet me.",
        }],
    }
    monkeypatch.setattr(workflow, "resolved_cues", lambda _run_id: semantic)
    monkeypatch.setattr(workflow, "_prepare_full_visual_runtime", lambda *_args, **_kwargs: SimpleNamespace(provider_name="fake", model="fake"))
    monkeypatch.setattr(workflow, "execute_full_visual_localization", _fake_visual_pipeline)

    def fake_narration(*, run_directory, voice_id, base_speed, resolved, **_kwargs):
        audio_dir = Path(run_directory) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        mix = audio_dir / "narration_mix.wav"
        mix.write_bytes(b"kokoro-post-narration")
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
        cue = {
            **resolved["cues"][0],
            "start_ms": 50,
            "end_ms": 850,
            "source_start_ms": 50,
            "source_end_ms": 950,
            "timing_provenance": "kokoro_token_alignment",
        }
        return {"resolved": {**resolved, "cues": [cue]}, "mix_path": mix, "manifest": manifest}

    monkeypatch.setattr(workflow, "generate_kokoro_narration", fake_narration)
    quality_calls = 0

    def fake_bounded_quality(resolved, _requested, *, source_duration_ms, allow_split):
        nonlocal quality_calls
        quality_calls += 1
        if quality_calls == 2:
            evaluation = {
                "status": "FAIL",
                "eligible": False,
                "violations": [{"cue_id": "cue-1", "code": "reading_speed", "value": 22.5, "limit": 20.0}],
            }
            return resolved, {"eligible": False, "actions": [], "evaluation": evaluation}
        evaluation = {"status": "PASS", "eligible": True, "violations": []}
        return resolved, {"eligible": True, "actions": [], "evaluation": evaluation}

    monkeypatch.setattr(workflow, "_bounded_quality_repair", fake_bounded_quality)
    monkeypatch.setattr(workflow, "ensure_gemini_caption_ready", lambda: {"selected_model": "fake-model"})

    class FakeProjectionTranslator:
        provider_name = "fake_projection"

        def translate(self, requests):
            return SimpleNamespace(
                translations=[{"id": item["id"], "english": "They'll meet me."} for item in requests],
                model="fake-model",
                request_count=1,
                retry_count=0,
                cache_hits=0,
                cache_misses=1,
            )

    monkeypatch.setattr(workflow, "GeminiCaptionTranslator", lambda _config: FakeProjectionTranslator())
    created = workflow.create_or_reuse_run(str(source), settings={
        "localization_mode": "full_localization_voice",
        "caption_mode": "external_audio_transcription",
        "target_language": "English",
        "translation_policy": "quality_auto",
        "subtitle_quality_profile": "english_v2",
        "tts_provider": "kokoro_local",
        "voice_id": "bm_george",
        "voice_speed": 1.0,
    })
    completed = workflow.start_processing(created["run_id"])
    assert completed["internal_state"] == "completed"
    run_dir = Path(completed["run_directory"])
    resolved = json.loads((run_dir / "subtitles" / "resolved_active_track.json").read_text(encoding="utf-8"))
    cue = resolved["cues"][0]
    assert cue["translation_text"] == "They will meet me."
    assert cue["spoken_text"] == "They will meet me."
    assert cue["subtitle_text"] == "They'll meet me."
    assert cue["resolved_text"] == "They'll meet me."
    evidence = json.loads((run_dir / "subtitles" / "post_narration_subtitle_projection_repair_evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "PASS"
    assert evidence["phase"] == "post_narration_alignment"
    assert evidence["repaired_cue_count"] == 1
    assert evidence["narration_audio_preserved"] is True
    assert evidence["spoken_text_preserved"] is True


def test_full_voice_render_resume_reuses_parent_narration_without_regeneration(monkeypatch, tmp_path: Path) -> None:
    configure_test_root(monkeypatch, tmp_path)
    init_db()
    monkeypatch.setenv("AUTOSUB_ENABLE_EXPERIMENTAL_LOCALIZATION_V2", "1")
    source = tmp_path / "voice-resume-source.mp4"
    _make_tiny_video(source)
    _install_common_fakes(monkeypatch, source)
    monkeypatch.setattr(workflow, "_prepare_full_visual_runtime", lambda *_args, **_kwargs: SimpleNamespace(provider_name="fake", model="fake"))
    monkeypatch.setattr(workflow, "execute_full_visual_localization", _fake_visual_pipeline)
    narration_calls = []

    def fake_parent_narration(*, run_directory, voice_id, base_speed, resolved, **_kwargs):
        narration_calls.append("parent")
        audio_dir = Path(run_directory) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        mix = audio_dir / "narration_mix.wav"
        mix.write_bytes(b"parent-kokoro-mix")
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
            "generations": [],
        }
        (audio_dir / "narration_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        cue = {
            **resolved["cues"][0],
            "source_start_ms": resolved["cues"][0]["start_ms"],
            "source_end_ms": resolved["cues"][0]["end_ms"],
            "narration_token_start_ms": 80,
            "narration_token_end_ms": 650,
            "start_ms": 80,
            "end_ms": 650,
            "timing_provenance": "kokoro_token_alignment",
        }
        retimed = {**resolved, "cues": [cue], "narration_timing": {"aligned_cue_count": 1}}
        return {"resolved": retimed, "mix_path": mix, "manifest": manifest}

    monkeypatch.setattr(workflow, "generate_kokoro_narration", fake_parent_narration)
    monkeypatch.setattr(
        workflow,
        "_bounded_subtitle_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parent render interrupted")),
    )
    settings = {
        "localization_mode": "full_localization_voice",
        "caption_mode": "external_audio_transcription",
        "target_language": "English",
        "tts_provider": "kokoro_local",
        "voice_id": "bm_george",
        "voice_speed": 1.0,
    }
    parent = workflow.create_or_reuse_run(str(source), settings=settings)
    with pytest.raises(ValueError):
        workflow.start_processing(parent["run_id"])
    failed_parent = workflow.get_run(parent["run_id"])
    assert failed_parent["failure_category"] == "render_failed"
    parent_dir = Path(failed_parent["run_directory"])
    parent_manifest = json.loads((parent_dir / "audio" / "narration_manifest.json").read_text(encoding="utf-8"))
    parent_mix_hash = parent_manifest["mix_sha256"]
    assert narration_calls == ["parent"]

    _install_common_fakes(monkeypatch, source)
    monkeypatch.setattr(workflow, "_prepare_full_visual_runtime", lambda *_args, **_kwargs: SimpleNamespace(provider_name="fake", model="fake"))
    monkeypatch.setattr(workflow, "execute_full_visual_localization", _fake_visual_pipeline)
    monkeypatch.setattr(
        workflow,
        "generate_kokoro_narration",
        lambda **_kwargs: pytest.fail("render-only Full Voice retry must reuse parent narration"),
    )
    child = workflow.create_or_reuse_run(
        str(source),
        settings=settings,
        retry_parent_run_id=parent["run_id"],
    )
    completed = workflow.start_processing(child["run_id"])
    assert completed["internal_state"] == "completed"
    assert completed["result_eligible"] is True
    child_dir = Path(completed["run_directory"])
    child_manifest = json.loads((child_dir / "audio" / "narration_manifest.json").read_text(encoding="utf-8"))
    assert child_manifest["mix_sha256"] == parent_mix_hash
    assert Path(child_manifest["mix_path"]).resolve() == (child_dir / "audio" / "narration_mix.wav").resolve()
    assert child_manifest["resume_checkpoint"] == {
        "kind": "parent_narration_artifact",
        "parent_run_id": parent["run_id"],
        "source_mix_sha256": parent_mix_hash,
    }
    assert child_manifest["acceptance_binding"]["run_id"] == child["run_id"]
    child_resolved = json.loads((child_dir / "subtitles" / "resolved_active_track.json").read_text(encoding="utf-8"))
    assert child_resolved["run_id"] == child["run_id"]
    assert child_resolved["resume_checkpoint"]["kind"] == "render_only"
    assert child_resolved["resume_checkpoint"]["parent_run_id"] == parent["run_id"]
    assert child_resolved["cues"][0]["timing_provenance"] == "kokoro_token_alignment"
