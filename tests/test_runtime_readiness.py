import json
from pathlib import Path

import pytest

from app.services import runtime_readiness as readiness


def _install_fake_ready_runtime(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TOOL_AUTO_SUB_RUNTIME_ROOT", str(tmp_path / "machine-runtime"))
    binary = readiness.managed_autosubs_binary(tmp_path)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"autosubs fixture")
    readiness._write_json(
        readiness._autosubs_probe_record(tmp_path),
        {"binary_sha256": readiness._sha256_file(binary), "model": "small"},
    )
    packages = readiness.runtime_root(tmp_path) / "translation" / "packages"
    model = packages / readiness.ARGOS_MODEL_ID
    (model / "model").mkdir(parents=True)
    (model / "model" / "model.bin").write_bytes(b"model")
    (model / "metadata.json").write_text(
        json.dumps({"package_version": "1.9", "argos_version": "1.9.0", "from_code": "zh", "to_code": "en"}),
        encoding="utf-8",
    )
    python_path = readiness.runtime_root(tmp_path) / "translation" / "venv" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"python fixture")
    readiness._write_json(
        readiness.managed_translation_config_path(tmp_path),
        {"python_path": str(python_path), "packages_root": str(packages), "model_id": readiness.ARGOS_MODEL_ID},
    )
    return binary


def test_clean_runtime_root_reports_all_required_dependencies_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_AUTO_SUB_RUNTIME_ROOT", str(tmp_path / "clean-runtime"))

    status = readiness.runtime_readiness(tmp_path)

    assert status["status"] == "not_ready"
    assert status["autosubs_runtime"]["state"] == "missing"
    assert status["autosubs_small_model"]["state"] == "missing"
    assert status["argos_runtime"]["state"] == "missing"
    assert status["argos_zh_en_model"]["state"] == "missing"


def test_prepare_clean_runtime_uses_managed_steps_and_reports_progress(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_AUTO_SUB_RUNTIME_ROOT", str(tmp_path / "clean-runtime"))
    events = []

    def fake_download(root):
        binary = readiness.managed_autosubs_binary(root)
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"downloaded autosubs")
        return binary

    def fake_prepare_translation(root):
        _install_fake_ready_runtime(monkeypatch, root)

    monkeypatch.setattr(readiness, "_download_autosubs", fake_download)
    monkeypatch.setattr(readiness, "_validate_autosubs_binary", lambda _binary: None)
    monkeypatch.setattr(readiness, "_probe_autosubs_small", lambda _binary: None)
    monkeypatch.setattr(readiness, "_prepare_translation", fake_prepare_translation)
    monkeypatch.setattr(readiness, "_run_argos_import_probe", lambda *_args: None)
    monkeypatch.setattr(readiness, "_run_translation_probe", lambda *_args: None)

    status = readiness.ensure_product_runtime_ready(tmp_path, progress=lambda state, message: events.append((state, message)))

    assert status["status"] == "ready"
    assert [state for state, _message in events] == [
        "checking_runtime",
        "downloading_autosubs",
        "preparing_autosubs_model",
        "preparing_translation",
        "runtime_ready",
    ]


def test_cached_runtime_is_revalidated_without_downloading_or_preparing(monkeypatch, tmp_path):
    _install_fake_ready_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(readiness, "_validate_autosubs_binary", lambda _binary: None)
    monkeypatch.setattr(readiness, "_probe_autosubs_small", lambda _binary: None)
    monkeypatch.setattr(readiness, "_run_argos_import_probe", lambda *_args: None)
    monkeypatch.setattr(readiness, "_run_translation_probe", lambda *_args: None)
    monkeypatch.setattr(readiness, "_download_autosubs", lambda _root: pytest.fail("cached executable must not redownload"))
    monkeypatch.setattr(readiness, "_prepare_translation", lambda _root: pytest.fail("cached translation runtime must not prepare again"))

    events = []
    status = readiness.ensure_product_runtime_ready(tmp_path, progress=lambda state, _message: events.append(state))

    assert status["status"] == "ready"
    assert status["autosubs_small_model"]["state"] == "ready"
    assert status["argos_zh_en_model"]["state"] == "ready"
    assert events == ["checking_runtime", "runtime_ready"]


def test_invalid_executable_is_distinguished_from_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_AUTO_SUB_RUNTIME_ROOT", str(tmp_path / "machine-runtime"))
    binary = readiness.managed_autosubs_binary(tmp_path)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"wrong version")
    monkeypatch.setattr(readiness, "_validate_autosubs_binary", lambda _binary: (_ for _ in ()).throw(readiness.RuntimeReadinessError("wrong AutoSubs version")))

    status = readiness.runtime_readiness(tmp_path)

    assert status["autosubs_runtime"]["state"] == "invalid"
    assert status["autosubs_small_model"]["state"] == "missing"


def test_corrupt_autosubs_download_is_removed_and_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_AUTO_SUB_RUNTIME_ROOT", str(tmp_path / "machine-runtime"))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            if hasattr(self, "sent"):
                return b""
            self.sent = True
            return b"corrupt"

    monkeypatch.setattr(readiness.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(readiness.RuntimeReadinessError, match="integrity"):
        readiness._download_autosubs(tmp_path)

    assert not readiness.managed_autosubs_binary(tmp_path).exists()
    assert not readiness.managed_autosubs_binary(tmp_path).with_suffix(".exe.partial").exists()


def test_controlled_failure_is_limited_to_isolated_test_processes(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_AUTO_SUB_TEST_RUNTIME_FAILURE", "1")
    monkeypatch.delenv("TOOL_AUTO_SUB_TESTING", raising=False)
    readiness._raise_controlled_test_failure()

    monkeypatch.setenv("TOOL_AUTO_SUB_TESTING", "1")
    with pytest.raises(readiness.RuntimeReadinessError, match="could not complete"):
        readiness._raise_controlled_test_failure()
