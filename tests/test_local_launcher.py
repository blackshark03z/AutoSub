import importlib.util
import re
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path("tools/local_launcher.py")
SPEC = importlib.util.spec_from_file_location("local_launcher", MODULE_PATH)
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


class FakeProcess:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


def make_project_root(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "operator").mkdir()
    (tmp_path / "app" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "operator" / "run_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "run_app.ps1").write_text("", encoding="utf-8")
    return tmp_path


def test_launcher_entry_is_single_obvious_double_click_command():
    command = Path("Run AutoSub.cmd").read_text(encoding="utf-8")
    assert "tools\\local_launcher.py" in command
    assert '--project-root "%CD%"' in command
    assert "pause" in command
    assert "uvicorn" not in command


def test_resolve_project_root_requires_prepared_project(tmp_path):
    with pytest.raises(launcher.LaunchError, match="prepared AutoSub"):
        launcher.resolve_project_root(tmp_path)


def test_existing_verified_autosub_is_reused_without_starting(monkeypatch, tmp_path):
    root = make_project_root(tmp_path)
    opened = []
    monkeypatch.setattr(launcher, "autosub_is_ready", lambda: True)
    monkeypatch.setattr(launcher, "start_server", lambda *_args: pytest.fail("must not start a duplicate server"))

    result = launcher.launch(root, open_browser=lambda url, new: opened.append((url, new)) or True)

    assert result.started_new_server is False
    assert opened == [(launcher.UI_URL, 2)]


def test_unrelated_port_occupant_is_not_killed_or_replaced(monkeypatch, tmp_path):
    root = make_project_root(tmp_path)
    monkeypatch.setattr(launcher, "autosub_is_ready", lambda: False)
    monkeypatch.setattr(launcher, "port_is_in_use", lambda: True)
    monkeypatch.setattr(launcher, "start_server", lambda *_args: pytest.fail("must not replace another process"))

    with pytest.raises(launcher.LaunchError, match="did not close it"):
        launcher.launch(root)


def test_new_server_is_opened_only_after_verified_readiness(monkeypatch, tmp_path):
    root = make_project_root(tmp_path)
    readiness = iter((False, False, True))
    opened = []
    monkeypatch.setattr(launcher, "autosub_is_ready", lambda: next(readiness))
    monkeypatch.setattr(launcher, "port_is_in_use", lambda: False)
    monkeypatch.setattr(launcher, "start_server", lambda *_args: FakeProcess())
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)

    result = launcher.launch(root, timeout_seconds=1, open_browser=lambda url, new: opened.append((url, new)) or True)

    assert result.started_new_server is True
    assert opened == [(launcher.UI_URL, 2)]


def test_failed_server_keeps_actionable_log_pointer(monkeypatch, tmp_path):
    log_path = tmp_path / "autosub-launcher.log"
    monkeypatch.setattr(launcher, "autosub_is_ready", lambda: False)

    with pytest.raises(launcher.LaunchError, match=re.escape(str(log_path))):
        launcher.wait_for_readiness(FakeProcess(exit_code=9), log_path, timeout_seconds=1)
