import json
from pathlib import Path

import pytest

from app.db.session import init_db
from app.services import simple_workflow
from tests.test_cp10b_simple_workflow import configure_test_root
from app.services.localization_run_config import (
    COMPUTE_AUTO,
    COMPUTE_CLOUD_NO_OVERAGE,
    FULL_LOCALIZATION_VOICE,
    FULL_VISUAL_LOCALIZATION,
    SUBTITLE_LOCALIZATION,
    LocalizationRunConfigError,
    build_localization_run_snapshot,
    normalize_localization_v2_settings,
    settings_compatible_for_output,
)


def test_default_localization_settings_are_safe_and_subtitle_only() -> None:
    settings = normalize_localization_v2_settings({})
    assert settings["localization_mode"] == SUBTITLE_LOCALIZATION
    assert settings["compute_policy"] == COMPUTE_AUTO
    assert settings["tts_provider"] is None
    assert settings["voice_id"] is None
    assert settings["voice_speed"] is None


def test_voice_mode_gets_real_kokoro_defaults() -> None:
    settings = normalize_localization_v2_settings({"localization_mode": FULL_LOCALIZATION_VOICE})
    assert settings["tts_provider"] == "kokoro_local"
    assert settings["voice_id"] == "bm_george"
    assert settings["voice_speed"] == 1.0


def test_voice_speed_is_bounded() -> None:
    with pytest.raises(LocalizationRunConfigError):
        normalize_localization_v2_settings({"localization_mode": FULL_LOCALIZATION_VOICE, "voice_speed": 1.5})


def test_compute_policy_is_validated() -> None:
    settings = normalize_localization_v2_settings({"compute_policy": COMPUTE_CLOUD_NO_OVERAGE})
    assert settings["compute_policy"] == COMPUTE_CLOUD_NO_OVERAGE
    with pytest.raises(LocalizationRunConfigError):
        normalize_localization_v2_settings({"compute_policy": "whatever_is_fast"})


def test_snapshot_identity_changes_for_output_affecting_voice_configuration() -> None:
    one = build_localization_run_snapshot({
        "localization_mode": FULL_LOCALIZATION_VOICE,
        "voice_id": "bm_george",
        "voice_speed": 1.0,
    })
    two = build_localization_run_snapshot({
        "localization_mode": FULL_LOCALIZATION_VOICE,
        "voice_id": "bf_emma",
        "voice_speed": 1.0,
    })
    three = build_localization_run_snapshot({
        "localization_mode": FULL_LOCALIZATION_VOICE,
        "voice_id": "bm_george",
        "voice_speed": 1.1,
    })
    assert one.sha256 != two.sha256
    assert one.sha256 != three.sha256
    assert json.loads(one.canonical_json)["voice_id"] == "bm_george"


def test_output_reuse_rejects_different_modes_or_voice() -> None:
    subtitle = {"localization_mode": SUBTITLE_LOCALIZATION}
    visual = {"localization_mode": FULL_VISUAL_LOCALIZATION}
    voice_one = {"localization_mode": FULL_LOCALIZATION_VOICE, "voice_id": "bm_george", "voice_speed": 1.0}
    voice_two = {"localization_mode": FULL_LOCALIZATION_VOICE, "voice_id": "bf_emma", "voice_speed": 1.0}
    assert settings_compatible_for_output(subtitle, dict(subtitle)) is True
    assert settings_compatible_for_output(subtitle, visual) is False
    assert settings_compatible_for_output(voice_one, voice_two) is False


def test_non_voice_mode_discards_stale_voice_settings() -> None:
    normalized = normalize_localization_v2_settings({
        "localization_mode": SUBTITLE_LOCALIZATION,
        "tts_provider": "kokoro_local",
        "voice_id": "bf_emma",
        "voice_speed": 1.1,
    })
    assert normalized["tts_provider"] is None
    assert normalized["voice_id"] is None
    assert normalized["voice_speed"] is None


def test_unknown_localization_mode_is_rejected() -> None:
    with pytest.raises(LocalizationRunConfigError):
        normalize_localization_v2_settings({"localization_mode": "magic_full_video"})


def test_public_run_creation_allows_stable_full_modes_without_experimental_guard(monkeypatch, tmp_path: Path) -> None:
    configure_test_root(monkeypatch, tmp_path)
    init_db()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-a-real-video-but-hashable")
    monkeypatch.delenv("AUTOSUB_ENABLE_EXPERIMENTAL_LOCALIZATION_V2", raising=False)
    monkeypatch.setattr(simple_workflow, "_source_metadata", lambda path, source_hash=None: {
        "path": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": source_hash,
        "duration_seconds": 1.0,
        "resolution": {"width": 160, "height": 90},
        "media": {},
    })
    for mode in (FULL_VISUAL_LOCALIZATION, FULL_LOCALIZATION_VOICE):
        created = simple_workflow.create_or_reuse_run(str(source), settings={"localization_mode": mode})
        assert created["settings"]["localization_mode"] == mode
        assert created["internal_state"] == "selected"
        assert created["output"]["path"] is None
