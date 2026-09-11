from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import app.services.simple_workflow as workflow
from app.core.hashing import sha256_file
from app.services.localization_run_config import build_localization_run_snapshot


def _row(tmp_path: Path, mode: str, *, voice: bool = False):
    run_dir = tmp_path / "run"
    (run_dir / "output").mkdir(parents=True)
    (run_dir / "subtitles").mkdir(parents=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = run_dir / "output" / "final_video.mp4"
    output.write_bytes(b"localized-output")
    settings = {
        "target_language": "English",
        "caption_mode": "external_audio",
        "subtitle_mode": "burned_into_video",
        "localization_mode": mode,
        "translation_policy": "quality_auto",
        "subtitle_quality_profile": "english_v2",
        "compute_policy": "auto",
    }
    if voice:
        settings.update({"tts_provider": "kokoro_local", "voice_id": "bm_george", "voice_speed": 1.0})
    snapshot = build_localization_run_snapshot(settings)
    (run_dir / "run_snapshot.json").write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-test",
        "project_id": "project-test",
        "source_hash": sha256_file(source),
        "settings_sha256": snapshot.sha256,
        "settings": snapshot.settings,
        "frozen_at_start": True,
    }), encoding="utf-8")
    (run_dir / "subtitles" / "dialogue_subtitles_en.ass").write_text("Dialogue: placeholder\n", encoding="utf-8")
    return SimpleNamespace(
        run_id="run-test",
        project_id="project-test",
        run_directory=str(run_dir),
        source_path=str(source),
        source_hash=sha256_file(source),
        output_path=str(output),
        output_hash=sha256_file(output),
        requested_settings_json=json.dumps(snapshot.settings),
        source_metadata_json=json.dumps({"duration_seconds": 2.0}),
        internal_state="completed",
        failure_category=None,
    ), snapshot.settings


def _base_validation_stubs(monkeypatch):
    monkeypatch.setattr(workflow, "media_summary", lambda _path: {"video": {"width": 1280, "height": 720}, "audio": {"codec": "aac"}})
    monkeypatch.setattr(workflow, "_valid_ass_dialogue_count", lambda _path: 1)
    monkeypatch.setattr(workflow, "_subtitle_content_validation", lambda _row, _path: {"eligible": True, "cue_count": 1, "reason_code": None})


def test_subtitle_mode_does_not_require_v2_visual_or_narration_evidence(tmp_path: Path, monkeypatch) -> None:
    _base_validation_stubs(monkeypatch)
    row, _settings = _row(tmp_path, "subtitle_localization")
    result = workflow._completed_result_validation(row)
    assert result["eligible"] is True
    assert result["localization_v2_acceptance"]["mode"] == "subtitle_localization"


def test_full_visual_fails_closed_without_bound_quality_evidence(tmp_path: Path, monkeypatch) -> None:
    _base_validation_stubs(monkeypatch)
    row, _settings = _row(tmp_path, "full_visual_localization")
    result = workflow._completed_result_validation(row)
    assert result["eligible"] is False
    assert result["reason_code"] == "visual_quality_evidence_missing"


def test_full_visual_passes_only_after_quality_is_bound_to_exact_output(tmp_path: Path, monkeypatch) -> None:
    _base_validation_stubs(monkeypatch)
    row, settings = _row(tmp_path, "full_visual_localization")
    visual = Path(row.run_directory) / "visual" / "visual_quality_evidence.json"
    visual.parent.mkdir(parents=True)
    visual.write_text(json.dumps({"status": "PASS", "eligible": True, "failure_count": 0}), encoding="utf-8")
    workflow._bind_localization_v2_acceptance_evidence(row, settings)
    result = workflow._completed_result_validation(row)
    assert result["eligible"] is True

    Path(row.output_path).write_bytes(b"tampered-output")
    result = workflow._completed_result_validation(row)
    assert result["eligible"] is False
    assert result["reason_code"] == "visual_quality_binding_invalid"


def test_voice_mode_requires_exact_kokoro_mix_identity_and_visual_binding(tmp_path: Path, monkeypatch) -> None:
    _base_validation_stubs(monkeypatch)
    row, settings = _row(tmp_path, "full_localization_voice", voice=True)
    visual = Path(row.run_directory) / "visual" / "visual_quality_evidence.json"
    visual.parent.mkdir(parents=True)
    visual.write_text(json.dumps({"status": "PASS", "eligible": True, "failure_count": 0}), encoding="utf-8")

    audio_dir = Path(row.run_directory) / "audio"
    audio_dir.mkdir(parents=True)
    mix = audio_dir / "narration_mix.wav"
    mix.write_bytes(b"kokoro-pcm-fixture")
    manifest = audio_dir / "narration_manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "status": "PASS",
        "provider": "kokoro_local",
        "model": "hexgrad/Kokoro-82M",
        "voice_id": "bm_george",
        "base_speed": 1.0,
        "mix_path": str(mix.resolve()),
        "mix_sha256": sha256_file(mix),
        "expected_retimed_cue_count": 1,
        "retimed_cue_count": 1,
    }), encoding="utf-8")

    workflow._bind_localization_v2_acceptance_evidence(row, settings)
    result = workflow._completed_result_validation(row)
    assert result["eligible"] is True

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["voice_id"] = "bf_emma"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    result = workflow._completed_result_validation(row)
    assert result["eligible"] is False
    assert result["reason_code"] == "narration_identity_invalid"
