import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services import asr_models, local_transcription
from app.services.subtitle_tracks import SubtitleContentUnavailableError
from tests.test_cp10b_simple_workflow import _make_tiny_video, _with_client, configure_test_root


def _configure_root(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TOOL_AUTO_SUB_ROOT", str(tmp_path))
    monkeypatch.setenv("TOOL_AUTO_SUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOOL_AUTO_SUB_DB_PATH", str(tmp_path / "data" / "app.db"))
    operator = tmp_path / "operator"
    operator.mkdir(parents=True)
    (operator / "run_config.json").write_text(
        json.dumps(
            {
                "source": {"path": "input/source.mp4"},
                "subtitle": {"font_path": "C:/Windows/Fonts/arial.ttf"},
                "hardware": {"whisper_model": "tiny"},
            }
        ),
        encoding="utf-8",
    )
    get_settings.cache_clear()
    return tmp_path


def _write_model(root: Path, model_name: str, *, valid_metadata: bool = True) -> Path:
    model_dir = root / "models" / f"faster-whisper-{model_name}"
    model_dir.mkdir(parents=True)
    for filename in asr_models.ASR_MODEL_REQUIRED_FILES:
        (model_dir / filename).write_text(filename, encoding="utf-8")
    metadata = {
        "model_name": model_name,
        "model_id": f"Systran/faster-whisper-{model_name}",
        "snapshot": asr_models.SIMPLE_UI_MODEL_SNAPSHOT if valid_metadata else "wrong",
        "local_files_only": True,
    }
    (model_dir / "MODEL_METADATA.json").write_text(json.dumps(metadata), encoding="utf-8")
    return model_dir


def test_legacy_simple_ui_model_setting_is_normalized(monkeypatch, tmp_path):
    root = _configure_root(monkeypatch, tmp_path)
    small = _write_model(root, "small")
    _write_model(root, "tiny")

    settings = asr_models.normalize_simple_ui_settings(
        {"asr_model": "tiny", "whisper_model": "base", "asr": {"model": "tiny"}}
    )

    assert settings["asr_model"] == "small"
    assert settings["asr_model_path"] == str(small.resolve())
    assert settings["asr_model_policy"] == "simple_ui_quality_model"
    assert "whisper_model" not in settings
    assert "model" not in settings["asr"]
    assert asr_models.resolve_simple_ui_model_path() == small.resolve()


def test_environment_and_discovery_cannot_override_simple_ui(monkeypatch, tmp_path):
    root = _configure_root(monkeypatch, tmp_path)
    _write_model(root, "tiny")
    small = _write_model(root, "small")
    monkeypatch.setenv("TOOL_AUTO_SUB_ASR_MODEL_NAME", "tiny")
    monkeypatch.setenv("TOOL_AUTO_SUB_ASR_MODEL_DIR", str(root / "models" / "faster-whisper-tiny"))

    assert asr_models.normalize_simple_ui_model_name("tiny") == "small"
    assert asr_models.resolve_simple_ui_model_path() == small.resolve()


def test_missing_or_mismatched_small_fails_closed(monkeypatch, tmp_path):
    root = _configure_root(monkeypatch, tmp_path)
    _write_model(root, "tiny")
    with pytest.raises(FileNotFoundError, match="faster-whisper-small"):
        asr_models.resolve_simple_ui_model_path()

    _write_model(root, "small", valid_metadata=False)
    with pytest.raises(FileNotFoundError, match="identity mismatch"):
        asr_models.resolve_simple_ui_model_path()


def test_provider_is_created_with_explicit_small_path(monkeypatch, tmp_path):
    root = _configure_root(monkeypatch, tmp_path)
    small = _write_model(root, "small")
    captured = {}

    class FakeWhisperProvider:
        def __init__(self, model_name, device, compute_type, local_files_only):
            captured.update(
                {
                    "model_name": model_name,
                    "device": device,
                    "compute_type": compute_type,
                    "local_files_only": local_files_only,
                }
            )

    monkeypatch.setattr(local_transcription, "FasterWhisperASRProvider", FakeWhisperProvider)
    local_transcription.create_local_asr_provider(small)

    assert Path(captured["model_name"]) == small
    assert captured["device"] == "cpu"
    assert captured["compute_type"] == "int8"
    assert captured["local_files_only"] is True


def test_loaded_model_mismatch_is_rejected(tmp_path):
    expected = tmp_path / "faster-whisper-small"
    expected.mkdir()

    class MismatchedProvider:
        model_name = str(tmp_path / "faster-whisper-tiny")
        last_metadata = {}

    with pytest.raises(SubtitleContentUnavailableError, match="mô hình phiên âm chất lượng"):
        local_transcription._assert_loaded_model(MismatchedProvider(), expected)


def test_builder_packages_only_explicit_small_policy():
    builder = Path("tools/build_cp13a1_complete_payload_hotfix.py").read_text(encoding="utf-8")
    assert "ASR_MODEL_NAME = SIMPLE_UI_MODEL_NAME" in builder
    assert 'ASR_LICENSE_SOURCE = ROOT / "licenses" / "faster-whisper-small-MIT.txt"' in builder
    assert 'foreach ($legacyModel in @("faster-whisper-tiny","faster-whisper-base"))' in builder
    assert 'for legacy_model in ("faster-whisper-tiny", "faster-whisper-base")' in builder


def test_simple_run_metadata_ignores_client_tiny_override(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    source = tmp_path / "client-override.mp4"
    _make_tiny_video(source)

    async def run(client):
        response = await client.post(
            "/api/simple/runs",
            json={
                "source_path": str(source),
                "settings": {
                    "asr_model": "tiny",
                    "whisper_model": "base",
                    "asr": {"model": "tiny"},
                },
            },
        )
        assert response.status_code == 200
        settings = response.json()["run"]["settings"]
        assert settings["asr_model"] == "small"
        assert settings["asr_model_source"] == "bundled"
        assert settings["asr_model_policy"] == "simple_ui_quality_model"
        assert "whisper_model" not in settings
        assert "model" not in settings["asr"]

    asyncio.run(_with_client(run))
