from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.release

from tools import build_cp13a1_complete_payload_hotfix as hotfix


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_file(path: Path, timeout: float = 10) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return path.read_text(encoding="utf-8").strip()
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {path}")


def _wait_for_port(port: int, expected_open: bool, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as client:
            client.settimeout(0.1)
            is_open = client.connect_ex(("127.0.0.1", port)) == 0
        if is_open is expected_open:
            return
        time.sleep(0.05)
    raise AssertionError(f"Port {port} did not become open={expected_open}")


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _write_lifecycle_harness(tmp_path: Path) -> tuple[Path, Path, Path]:
    install_dir = tmp_path / "Install With Spaces" / "ToolAutoSubBeta"
    beta_dir = install_dir / "beta"
    user_root = tmp_path / "user"
    beta_dir.mkdir(parents=True)
    user_root.mkdir()
    (beta_dir / "ToolAutoSubBeta.ps1").write_text(hotfix.LAUNCHER_PS1, encoding="utf-8-sig")
    listener = beta_dir / "ToolAutoSubBetaRuntime.py"
    listener.write_text(
        "\n".join(
            [
                "import os, pathlib, socket, subprocess, sys, time",
                "mode = sys.argv[1]",
                "if mode == 'parent':",
                "    pathlib.Path(sys.argv[2]).write_text(str(os.getpid()), encoding='utf-8')",
                "    subprocess.Popen([sys.executable, __file__, 'listener', sys.argv[3], sys.argv[4]])",
                "    while True: time.sleep(1)",
                "port = int(sys.argv[2])",
                "pathlib.Path(sys.argv[3]).write_text(str(os.getpid()), encoding='utf-8')",
                "server = socket.socket()",
                "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)",
                "server.bind(('127.0.0.1', port))",
                "server.listen()",
                "while True: time.sleep(1)",
            ]
        ),
        encoding="utf-8",
    )
    return install_dir, user_root, listener


def _start_listener(
    script: Path, port: int, pid_path: Path, *, lifecycle_helper: bool = True
) -> tuple[subprocess.Popen[str], int]:
    args = [sys.executable, str(script)]
    if lifecycle_helper:
        args.append("listener")
    args.extend([str(port), str(pid_path)])
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    pid = int(_wait_for_file(pid_path))
    _wait_for_port(port, True)
    return process, pid


def _run_stop(install_dir: Path, user_root: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is required for lifecycle tests")
    env = os.environ.copy()
    env.update({"CP13A_USERDATA_DIR": str(user_root), "CP13A_HEADLESS": "1"})
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(install_dir / "beta" / "ToolAutoSubBeta.ps1"),
            "-Action",
            "Stop",
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _write_state(user_root: Path, *, pid: int, launcher_pid: int, port: int) -> Path:
    state = user_root / "runtime_state.json"
    state.write_text(
        json.dumps(
            {
                "pid": pid,
                "launcher_pid": launcher_pid,
                "port": port,
                "url": f"http://127.0.0.1:{port}/",
            }
        ),
        encoding="utf-8",
    )
    return state


def test_cp13a1_required_components_cover_complete_main_payload():
    names = {item["component"] for item in hotfix.REQUIRED_COMPONENTS}
    assert {
        "main_launcher",
        "runtime_entry",
        "backend_entry_point",
        "release_local_python",
        "simple_ui_entry",
        "operator_ui_entry",
        "ffmpeg_executable",
        "migration_head",
        "diagnostics_launcher",
        "release_manifest",
        "ocr_runtime_manifest",
        "gemini_translation_config",
    }.issubset(names)
    by_component = {item["component"]: item for item in hotfix.REQUIRED_COMPONENTS}
    assert by_component["gemini_translation_config"]["relative_path"] == "operator/translation_config.env"


def test_cp13a1_lifecycle_contract_tracks_owned_runtime_and_verifies_closure():
    script = hotfix.LAUNCHER_PS1
    assert "ToolAutoSubBetaRuntime.py" in hotfix.RUNTIME_PY + script
    assert "INSTALL_ROOT = Path(__file__).resolve().parents[1]" in hotfix.RUNTIME_PY
    assert "sys.path.insert(0, str(INSTALL_ROOT))" in hotfix.RUNTIME_PY
    assert "os.chdir(INSTALL_ROOT)" in hotfix.RUNTIME_PY
    assert '$args = @("`"$runtimeEntry`"","--host",$bind,"--port",[string]$port)' in script
    assert "Get-OwnedRuntimePids" in script
    assert "Get-ListeningPid" in script
    assert "Wait-ProcessesExit" in script
    assert "stop_attempt_id=" in script
    assert "final_port_open=" in script
    assert "stop_result=FAIL" in script
    assert "stop_result=PASS" in script
    assert "Stop-Process -Id $pidValue -Force" in script
    assert "taskkill" not in script.lower()
    assert "stop-process -name python" not in script.lower()


def test_cp13a1_runtime_entry_bootstraps_install_root_with_spaces(tmp_path):
    install_dir = tmp_path / "Install With Spaces" / "ToolAutoSubBeta"
    beta_dir = install_dir / "beta"
    beta_dir.mkdir(parents=True)
    marker = install_dir / "runtime-import.txt"
    (install_dir / "uvicorn.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                f"Path({str(marker)!r}).write_text(str(Path.cwd()), encoding='utf-8')",
                "def run(*args, **kwargs): pass",
            ]
        ),
        encoding="utf-8",
    )
    runtime_entry = beta_dir / "ToolAutoSubBetaRuntime.py"
    runtime_entry.write_text(hotfix.RUNTIME_PY, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(runtime_entry), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert marker.read_text(encoding="utf-8") == str(install_dir)


def test_cp13a1_stop_closes_listener_parent_is_idempotent_and_preserves_unrelated_python(tmp_path):
    install_dir, user_root, listener_script = _write_lifecycle_harness(tmp_path)
    parent_pid_path = tmp_path / "parent.pid"
    child_pid_path = tmp_path / "child.pid"
    port = _free_port()
    unrelated_script = tmp_path / "unrelated.py"
    unrelated_script.write_text("import time\nwhile True: time.sleep(1)\n", encoding="utf-8")
    unrelated = subprocess.Popen([sys.executable, str(unrelated_script)], text=True)
    parent = subprocess.Popen(
        [
            sys.executable,
            str(listener_script),
            "parent",
            str(parent_pid_path),
            str(port),
            str(child_pid_path),
        ],
        text=True,
    )
    try:
        parent_pid = int(_wait_for_file(parent_pid_path))
        child_pid = int(_wait_for_file(child_pid_path))
        _wait_for_port(port, True)
        state = _write_state(user_root, pid=child_pid, launcher_pid=parent_pid, port=port)

        stopped = _run_stop(install_dir, user_root)
        assert stopped.returncode == 0, stopped.stderr
        parent.wait(timeout=10)
        _wait_for_port(port, False)
        assert not state.exists()
        assert unrelated.poll() is None

        second = _run_stop(install_dir, user_root)
        assert second.returncode == 0, second.stderr
        assert unrelated.poll() is None
        log = (user_root / "logs" / "launcher_bootstrap.log").read_text(encoding="utf-8-sig")
        attempts = re.findall(r"stop_attempt_id=([0-9a-f]{32}).*stop_result=PASS", log)
        assert len(set(attempts)) == 2
        assert "final_process_count=0" in log
        assert "final_port_open=False" in log
    finally:
        _terminate(parent)
        _terminate(unrelated)


def test_cp13a1_stop_cleans_stale_state_without_killing_reused_unrelated_pid(tmp_path):
    install_dir, user_root, _ = _write_lifecycle_harness(tmp_path)
    unrelated_script = tmp_path / "unrelated.py"
    unrelated_script.write_text("import time\nwhile True: time.sleep(1)\n", encoding="utf-8")
    unrelated = subprocess.Popen([sys.executable, str(unrelated_script)], text=True)
    try:
        state = _write_state(user_root, pid=unrelated.pid, launcher_pid=0, port=_free_port())
        stopped = _run_stop(install_dir, user_root)
        assert stopped.returncode == 0, stopped.stderr
        assert unrelated.poll() is None
        assert not state.exists()
        log = (user_root / "logs" / "launcher_bootstrap.log").read_text(encoding="utf-8-sig")
        assert f"identity_pid={unrelated.pid} exists=True owned=False" in log
    finally:
        _terminate(unrelated)


def test_cp13a1_stop_finds_detached_owned_listener_when_parent_is_gone(tmp_path):
    install_dir, user_root, listener_script = _write_lifecycle_harness(tmp_path)
    port = _free_port()
    listener, listener_pid = _start_listener(listener_script, port, tmp_path / "detached.pid")
    state = _write_state(user_root, pid=listener_pid, launcher_pid=999999, port=port)
    try:
        stopped = _run_stop(install_dir, user_root)
        assert stopped.returncode == 0, stopped.stderr
        listener.wait(timeout=10)
        _wait_for_port(port, False)
        assert not state.exists()
    finally:
        _terminate(listener)


def test_cp13a1_stop_fails_if_unrelated_process_still_owns_target_port(tmp_path):
    install_dir, user_root, _ = _write_lifecycle_harness(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    unrelated_listener = outside / "unrelated_listener.py"
    unrelated_listener.write_text(
        "\n".join(
            [
                "import os, pathlib, socket, sys, time",
                "port = int(sys.argv[1])",
                "pathlib.Path(sys.argv[2]).write_text(str(os.getpid()), encoding='utf-8')",
                "server = socket.socket()",
                "server.bind(('127.0.0.1', port))",
                "server.listen()",
                "while True: time.sleep(1)",
            ]
        ),
        encoding="utf-8",
    )
    port = _free_port()
    listener, _ = _start_listener(
        unrelated_listener,
        port,
        tmp_path / "unrelated-listener.pid",
        lifecycle_helper=False,
    )
    state = _write_state(user_root, pid=999998, launcher_pid=999999, port=port)
    try:
        stopped = _run_stop(install_dir, user_root)
        assert stopped.returncode != 0
        assert listener.poll() is None
        assert state.exists()
        log = (user_root / "logs" / "launcher_bootstrap.log").read_text(encoding="utf-8-sig")
        assert "final_port_open=True" in log
        assert "stop_result=FAIL" in log
    finally:
        _terminate(listener)


def test_cp13a1_stop_propagates_verified_runtime_termination_failure(tmp_path):
    install_dir, user_root, listener_script = _write_lifecycle_harness(tmp_path)
    port = _free_port()
    listener, listener_pid = _start_listener(listener_script, port, tmp_path / "owned-failure.pid")
    state = _write_state(user_root, pid=listener_pid, launcher_pid=0, port=port)
    try:
        stopped = _run_stop(
            install_dir,
            user_root,
            {
                "CP13A_LIFECYCLE_TEST_MODE": "1",
                "CP13A_STOP_TEST_SIMULATE_FAILURE": "1",
            },
        )
        assert stopped.returncode != 0
        assert listener.poll() is None
        assert state.exists()
        log = (user_root / "logs" / "launcher_bootstrap.log").read_text(encoding="utf-8-sig")
        assert "stop_method=TEST_SIMULATED_FAILURE" in log
        assert "stop_result=FAIL" in log
    finally:
        _terminate(listener)


def test_cp13a1_addons_only_payload_is_rejected(tmp_path):
    installed = tmp_path / "installed"
    _write(installed / "addons" / "ocr_runtime" / "addon_manifest.json")
    manifest = {
        "minimum_file_count": 2,
        "minimum_total_size_bytes": 2,
        "components": [
            {"component": "main_launcher", "relative_path": "ToolAutoSubBeta.cmd", "type": "file", "required": True, "sha256": None},
            {"component": "ocr_runtime_manifest", "relative_path": "addons/ocr_runtime/addon_manifest.json", "type": "file", "required": True, "sha256": None},
        ],
    }
    with pytest.raises(RuntimeError, match="main_launcher"):
        hotfix.validate_installed_tree(installed, manifest)


def test_cp13a1_installed_tree_validation_checks_hash_and_size(tmp_path):
    installed = tmp_path / "installed"
    critical = installed / "ToolAutoSubBeta.cmd"
    _write(critical, "launcher")
    digest = _sha256(critical)
    manifest = {
        "minimum_file_count": 1,
        "minimum_total_size_bytes": 1,
        "components": [
            {"component": "main_launcher", "relative_path": "ToolAutoSubBeta.cmd", "type": "file", "required": True, "sha256": digest},
        ],
    }
    assert hotfix.validate_installed_tree(installed, manifest)["passed"] is True
    critical.write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="main_launcher_hash"):
        hotfix.validate_installed_tree(installed, manifest)


def test_cp13a1_install_script_self_checks_before_shortcuts():
    script = hotfix.INSTALL_PS1
    assert "Validate-InstalledPayload" in script
    assert "installed_root_addons_only" in script
    assert "Cai dat Tool Auto Sub chua day du" in script
    assert script.index("Validate-InstalledPayload") < script.index("New-Shortcut")
    assert "cp13a1_complete_payload.zip" in script
    assert "Resolve-ShortcutTarget" in script
    assert "Get-Command $Target -CommandType Application" in script
    assert "$shortcut.TargetPath = $resolvedTarget" in script
    assert "Shortcut target cannot be resolved: $Target" in script


def test_cp13a1_overlay_bootstrap_quiet_command_propagates_to_child(tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    complete_payload = tmp_path / "cp13a1_complete_payload.zip"
    _write(complete_payload, "zip-bytes")
    manifest = {"minimum_file_count": 1, "minimum_total_size_bytes": 1, "components": []}

    hotfix.write_payload_scripts(payload, complete_payload, manifest)
    inventory = hotfix.write_installer_payload_zip(payload, tmp_path / "installer_payload.zip")

    assert inventory["zip_size"] > complete_payload.stat().st_size
    with zipfile.ZipFile(tmp_path / "installer_payload.zip") as archive:
        names = set(archive.namelist())
    assert {"install.cmd", "install.ps1", "EXPECTED_INSTALL_MANIFEST.json", "cp13a1_complete_payload.zip"} <= names
    assert 'if /I "%~1"=="/Q"' in hotfix.INSTALL_CMD
    assert '"%~dp0install.ps1" -Quiet' in hotfix.INSTALL_CMD
    assert '"%~dp0install.ps1"\n' in hotfix.INSTALL_CMD
    assert 'string childArguments = "/c " + Quote(installCmd) + (quiet ? " /Q" : "")' in hotfix.BOOTSTRAP_CS
    assert "TOOL_AUTO_SUB_CP13A1_PAYLOAD_V1" in hotfix.BOOTSTRAP_CS


def test_cp13a1_quiet_and_interactive_install_paths_remain_conditional():
    script = hotfix.INSTALL_PS1
    assert "param(" in script
    assert "[switch]$Quiet" in script
    assert '$silent = $Quiet.IsPresent -or $env:CP13A_INSTALL_SILENT -eq "1"' in script
    assert "if (-not $silent) {" in script
    assert "[System.Windows.Forms.MessageBox]::Show" in script
    assert 'if ($silent -or $env:CP13A_HEADLESS -eq "1") {' in script
    assert "CP13A_INSTALL_SILENT" not in hotfix.INSTALL_CMD


def test_cp13a1_install_attempt_logs_current_pass_and_failure_without_masking():
    script = hotfix.INSTALL_PS1
    assert '$installAttemptId = [guid]::NewGuid().ToString("N")' in script
    assert 'install_attempt_id=$installAttemptId phase=BEGIN quiet=$silent' in script
    assert 'install_attempt_id=$installAttemptId install_result=PASS' in script
    assert 'install_attempt_id=$installAttemptId install_result=FAIL' in script
    assert script.index("install_result=PASS") < script.index("} catch {")
    failure_path = script.split("} catch {", 1)[1]
    assert "install_result=FAIL" in failure_path
    assert "exit 1" in failure_path


def test_cp13a1_quiet_child_failure_is_unattended_and_logged_for_current_attempt(tmp_path):
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is required for the installer script regression test")
    script_path = tmp_path / "install.ps1"
    script_path.write_text(hotfix.INSTALL_PS1, encoding="utf-8-sig")
    user_root = tmp_path / "user"
    env = os.environ.copy()
    env.pop("CP13A_INSTALL_SILENT", None)
    env.pop("CP13A_HEADLESS", None)
    env.update(
        {
            "CP13A_INSTALL_DIR": str(tmp_path / "install"),
            "CP13A_USERDATA_DIR": str(user_root),
            "CP13A_START_MENU_DIR": str(tmp_path / "start-menu"),
            "CP13A_DESKTOP_DIR": str(tmp_path / "desktop"),
        }
    )

    completed = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path), "-Quiet"],
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode != 0
    log = (user_root / "logs" / "launcher_bootstrap.log").read_text(encoding="utf-8-sig")
    begin = re.search(r"install_attempt_id=([0-9a-f]{32}) phase=BEGIN quiet=True", log)
    assert begin
    attempt_id = begin.group(1)
    assert f"install_attempt_id={attempt_id} install_result=FAIL" in log
    assert f"install_attempt_id={attempt_id} install_result=PASS" not in log


def test_cp13a1_task27_primary_flow_is_still_packaged():
    index = (ROOT / "app" / "static" / "simple" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "app" / "static" / "simple" / "app.js").read_text(encoding="utf-8")
    assert "Tạo video có phụ đề" in index
    assert "Chưa bắt đầu" in index
    assert "Kết quả gần nhất" in app
    assert "Xem kết quả" in index
    assert 'class="step-nav"' not in index


def test_cp13a1_builder_uses_single_complete_payload_and_no_downloads():
    source = (ROOT / "tools" / "build_cp13a1_complete_payload_hotfix.py").read_text(encoding="utf-8")
    assert "cp13a1_complete_payload.zip" in source
    assert "SourceFiles0=" in source
    assert "urlretrieve" not in source
    assert "pip install" not in source
    assert "EXPECTED_INSTALL_MANIFEST.json" in source


def test_cp13a1_shortcut_resolution_accepts_bare_executable_without_filesystem_only_rejection():
    script = hotfix.INSTALL_PS1
    assert 'New-Shortcut (Join-Path $startMenu "Uninstall Tool Auto Sub Beta.lnk") "powershell.exe"' in script
    assert "Test-Path -LiteralPath $Target -PathType Leaf" in script
    assert script.index("function Resolve-ShortcutTarget") < script.index("function New-Shortcut")
    new_shortcut_body = script.split("function New-Shortcut", 1)[1].split("New-Shortcut (Join-Path $startMenu", 1)[0]
    assert "Resolve-ShortcutTarget $Target" in new_shortcut_body
    assert "Test-Path -LiteralPath $Target -PathType Leaf" not in new_shortcut_body


def test_cp13a1_uninstall_shortcut_arguments_quote_installed_script():
    script = hotfix.INSTALL_PS1
    assert 'Uninstall Tool Auto Sub Beta.lnk") "powershell.exe"' in script
    assert '-File `"$betaDir\\UninstallToolAutoSubBeta.ps1`"' in script
    assert 'Uninstall Tool Auto Sub Beta.lnk") "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$betaDir\\UninstallToolAutoSubBeta.ps1`"" $userRoot' in script
    assert '"powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$betaDir\\UninstallToolAutoSubBeta.ps1`""' in script


def test_cp13a1_beta_checklist_contains_external_machine_handoff_details():
    guide = hotfix.beta_guide(123, "abc123")
    for required in [
        "Get-FileHash -Algorithm SHA256",
        "Expected size: 123 bytes",
        "Expected SHA-256: abc123",
        "Uninstall Tool Auto Sub Beta",
        "http://127.0.0.1:8173/",
        "Diagnostics ZIP",
        "Do not enter Gemini, ElevenLabs, or YouTube keys",
        "CP12B Full Portable remains canonical",
    ]:
        assert required in guide


def test_cp13a1_release_manifest_after_build_if_present():
    manifest_path = ROOT / "release" / "CP13A1" / "RELEASE_MANIFEST.json"
    if not manifest_path.exists():
        pytest.skip("CP13A1 package has not been built yet")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    installer = ROOT / "release" / "CP13A1" / manifest["installer_filename"]
    expected = ROOT / "release" / "CP13A1" / manifest["expected_install_manifest"]
    assert manifest["release_id"] == "CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX"
    validation = manifest.get("validation_summary") or manifest.get("machine_validation") or {}
    assert validation["status"] == "PASS"
    assert manifest["expected_installed_file_count"] >= hotfix.MIN_INSTALLED_FILE_COUNT
    assert manifest["expected_installed_size_bytes"] >= hotfix.MIN_INSTALLED_SIZE_BYTES
    assert installer.exists()
    assert expected.exists()
    assert _sha256(installer) == manifest["installer_sha256"]


def test_cp13a1_protected_artifacts_unchanged():
    assert _sha256(hotfix.CP12B_ZIP) == hotfix.CP12B_SHA
    assert _sha256(hotfix.ACCEPTED_MP4) == hotfix.ACCEPTED_SHA
    assert _sha256(hotfix.CP13A_INSTALLER) == hotfix.CP13A_SHA
