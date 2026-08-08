from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.services.ocr_runtime import OCRRuntimeError, discover_ocr_runtime_config_path, get_ocr_runtime_status, is_cjk_text, load_ocr_runtime_config, run_ocr_on_image, run_ocr_on_images


def _write_config(tmp_path: Path, python_path: Path | None = None) -> Path:
    runtime = tmp_path / "runtime"
    python = python_path or runtime / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    payload = {
        "runtime_root": str(runtime),
        "python_path": str(python),
        "model_root": str(runtime / "models"),
        "temp_root": str(runtime / "tmp"),
        "log_root": str(runtime / "logs"),
        "timeout_seconds": 1,
    }
    for model_name in ("ch_det", "ch_rec", "ch_cls"):
        model_dir = runtime / "models" / model_name
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "inference.pdmodel").write_bytes(b"fixture")
        (model_dir / "inference.pdiparams").write_bytes(b"fixture")
    path = tmp_path / "ocr_runtime_config.local.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _configure_root(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("TOOL_AUTO_SUB_ROOT", str(root))
    operator = root / "operator"
    operator.mkdir(parents=True, exist_ok=True)
    (root / "input").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (operator / "run_config.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"path": "input/source.mp4", "expected_sha256": None},
                "market_profile_id": "test",
                "source_language": "zh",
                "target_language": "en",
                "target_locale": "en-US",
                "content_mode": "dialogue_subtitles_only",
                "audio_policy": "reference",
                "preview": "720p",
                "final": "source_or_720p",
                "auto_upload": False,
                "push": False,
                "provider_calls_enabled": False,
                "upload_publish_enabled": False,
                "non_dialogue_localization_enabled": False,
                "translation": {"provider": "disabled", "config_path": "config/translation_config.env", "key_file": None},
                "tts": {"provider": "disabled", "key_file": None, "allow_real_paid_calls": False},
                "hardware": {"gpu": "auto", "whisper_device": "disabled_by_default", "free_disk_required_gb": 1},
                "subtitle": {"font_path": r"C:\\Windows\\Fonts\\arial.ttf"},
                "ocr_runtime": {"path": str(root / "addons" / "ocr_runtime" / "runtime"), "required_for_closed_loop_cleanup": True},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (operator / "source_provenance.json").write_text(json.dumps({"schema_version": 1, "source_sha256": "test"}), encoding="utf-8")
    from app.core.config import get_settings

    get_settings.cache_clear()


def test_runtime_config_parsing_and_external_python_validation(tmp_path):
    config = load_ocr_runtime_config(_write_config(tmp_path))
    assert str(config.python_path).startswith(str(config.runtime_root))


def test_release_relative_runtime_config_resolves_from_portable_root(tmp_path):
    root = tmp_path / "portable"
    runtime = root / "addons" / "ocr_runtime" / "runtime"
    python = runtime / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    config = root / "operator" / "ocr_runtime_config.local.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "runtime_root": r"addons\ocr_runtime\runtime",
        "python_path": r"addons\ocr_runtime\runtime\.venv\Scripts\python.exe",
        "model_root": r"addons\ocr_runtime\runtime\models",
        "temp_root": r"addons\ocr_runtime\runtime\tmp",
        "log_root": r"addons\ocr_runtime\runtime\logs",
    }), encoding="utf-8")
    loaded = load_ocr_runtime_config(config)
    assert loaded.runtime_root == runtime.resolve()
    assert loaded.python_path == python.resolve()


def test_runtime_config_rejects_python_outside_runtime(tmp_path):
    outside = tmp_path / "python.exe"
    outside.write_text("", encoding="utf-8")
    with pytest.raises(OCRRuntimeError, match="inside the runtime root"):
        load_ocr_runtime_config(_write_config(tmp_path, outside))


def test_cjk_unicode_classification():
    assert is_cjk_text("太阳已经落下")
    assert not is_cjk_text("English only?!")


def test_path_traversal_and_allowlist_rejection(tmp_path):
    config_path = _write_config(tmp_path)
    outside = tmp_path.parent / "outside.png"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(OCRRuntimeError, match="outside approved roots"):
        run_ocr_on_image(outside, config_path)


def test_malformed_ocr_json_is_controlled(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path)
    image = tmp_path / "runtime" / "smoke.png"
    image.write_text("x", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = "not-json"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(OCRRuntimeError, match="malformed JSON"):
        run_ocr_on_image(image, config_path)


def test_timeout_is_controlled(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path)
    image = tmp_path / "runtime" / "smoke.png"
    image.write_text("x", encoding="utf-8")

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ocr", timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    with pytest.raises(OCRRuntimeError, match="timed out"):
        run_ocr_on_image(image, config_path)


def test_long_ocr_worker_emits_bounded_heartbeat(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path)
    image = tmp_path / "runtime" / "smoke.png"
    image.write_text("x", encoding="utf-8")
    heartbeats = []

    class Process:
        returncode = 0

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("ocr", timeout=timeout)
            return json.dumps({"ok": True, "frames": [{"items": []}]}), ""

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process())
    payload = run_ocr_on_images([image], config_path, heartbeat_callback=lambda: heartbeats.append(True))

    assert payload["ok"] is True
    assert heartbeats == [True]


def test_adapter_redacts_paths_and_does_not_require_global_python(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path)
    image = tmp_path / "runtime" / "smoke.png"
    image.write_text("x", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = json.dumps({"ok": True, "image_path": "secret", "model_root": "secret", "items": []})
        stderr = ""

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    payload = run_ocr_on_image(image, config_path)
    assert payload == {"ok": True, "items": []}
    assert str(tmp_path / "runtime") in calls[0][0]


def test_local_addon_config_is_preferred_over_external_fallback(monkeypatch, tmp_path):
    root = tmp_path / "app"
    local_runtime = root / "addons" / "ocr_runtime" / "runtime"
    local_python = local_runtime / ".venv" / "Scripts" / "python.exe"
    local_python.parent.mkdir(parents=True, exist_ok=True)
    local_python.write_text("", encoding="utf-8")
    local_config = root / "addons" / "ocr_runtime" / "operator" / "ocr_runtime_config.local.json"
    local_config.parent.mkdir(parents=True, exist_ok=True)
    local_config.write_text(json.dumps({
        "runtime_root": str(local_runtime),
        "python_path": str(local_python),
        "model_root": str(local_runtime / "models"),
        "temp_root": str(local_runtime / "tmp"),
        "log_root": str(local_runtime / "logs"),
        "timeout_seconds": 1,
    }), encoding="utf-8")
    external_root = tmp_path / "external_runtime"
    external_config = external_root / "operator" / "ocr_runtime_config.local.json"
    external_config.parent.mkdir(parents=True, exist_ok=True)
    external_python = external_root / ".venv" / "Scripts" / "python.exe"
    external_python.parent.mkdir(parents=True, exist_ok=True)
    external_python.write_text("", encoding="utf-8")
    external_config.write_text(json.dumps({
        "runtime_root": str(external_root),
        "python_path": str(external_python),
        "model_root": str(external_root / "models"),
        "temp_root": str(external_root / "tmp"),
        "log_root": str(external_root / "logs"),
        "timeout_seconds": 1,
    }), encoding="utf-8")
    _configure_root(monkeypatch, root)
    monkeypatch.setenv("TOOL_AUTO_SUB_OCR_RUNTIME_CONFIG", str(external_config))
    monkeypatch.setenv("TOOL_AUTO_SUB_OCR_RUNTIME_ROOT", str(external_root))
    discovered = discover_ocr_runtime_config_path()
    assert discovered == local_config.resolve()
    status = get_ocr_runtime_status()
    assert status["status"] == "missing"
    assert status["configured_path"] == str(local_config.resolve())


def test_health_reports_ocr_runtime_status(monkeypatch, tmp_path):
    root = tmp_path / "app"
    _configure_root(monkeypatch, root)
    from app.api.routes import health

    payload = health()
    assert payload["status"] == "ok"
    assert payload["ocr_runtime"]["status"] == "missing"
    assert "actionable_fix_message" in payload["ocr_runtime"]
