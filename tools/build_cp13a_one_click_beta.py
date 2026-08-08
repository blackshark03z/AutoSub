from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.preflight import PACKAGE_SAFETY_RESERVE_BYTES, storage_preflight


CP12B_ZIP = ROOT / "release" / "CP12B" / "tool_auto_sub_windows_full_portable_cp12b.zip"
CP12B_SHA = "9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0"
ACCEPTED_MP4 = ROOT / "data" / "projects" / "vertical_slice_cp07" / "renders" / "cp08e2_decoupled_suppression_english_plate_720p.mp4"
ACCEPTED_SHA = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"
RELEASE_DIR = ROOT / "release" / "CP13A"
INSTALLER = RELEASE_DIR / "ToolAutoSubBetaSetup_CP13A.exe"
RELEASE_ID = "CP13A_WINDOWS_ONE_CLICK_EXTERNAL_BETA"
FFMPEG_ROOT = Path(os.environ.get("CP13A_FFMPEG_ROOT", r"C:\Users\ADMIN\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def copy_required_ffmpeg(temp_payload: Path) -> dict:
    ffmpeg_zip = temp_payload / "ffmpeg_runtime_cp13a.zip"
    required = [
        FFMPEG_ROOT / "bin" / "ffmpeg.exe",
        FFMPEG_ROOT / "bin" / "ffprobe.exe",
        FFMPEG_ROOT / "LICENSE",
        FFMPEG_ROOT / "README.txt",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"CP13A_BLOCKED_FFMPEG_RUNTIME_MISSING: {missing}")
    with zipfile.ZipFile(ffmpeg_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in required:
            if source.name in {"ffmpeg.exe", "ffprobe.exe"}:
                arcname = f"bin/{source.name}"
            else:
                arcname = source.name
            archive.write(source, arcname)
    return {
        "source": str(FFMPEG_ROOT),
        "zip_name": ffmpeg_zip.name,
        "zip_size": ffmpeg_zip.stat().st_size,
        "zip_sha256": sha256_file(ffmpeg_zip),
        "ffmpeg_sha256": sha256_file(FFMPEG_ROOT / "bin" / "ffmpeg.exe"),
        "ffprobe_sha256": sha256_file(FFMPEG_ROOT / "bin" / "ffprobe.exe"),
        "version": subprocess.check_output([str(FFMPEG_ROOT / "bin" / "ffmpeg.exe"), "-version"], text=True, stderr=subprocess.DEVNULL).splitlines()[0],
    }


def build_overlay(temp_payload: Path) -> dict:
    overlay_zip = temp_payload / "cp13a_app_overlay.zip"
    include_roots = [ROOT / "app", ROOT / "alembic"]
    include_files = [ROOT / "tools" / "ocr_runtime_worker.py", ROOT / "tools" / "storage_preflight.py"]
    with zipfile.ZipFile(overlay_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for folder in include_roots:
            for path in sorted(folder.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, path.relative_to(ROOT).as_posix())
        for path in include_files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    return {"zip_name": overlay_zip.name, "zip_size": overlay_zip.stat().st_size, "zip_sha256": sha256_file(overlay_zip)}


def copy_installer_scripts(temp_payload: Path) -> None:
    source = ROOT / "packaging" / "cp13a"
    shutil.copy2(source / "install.cmd", temp_payload / "install.cmd")
    shutil.copy2(source / "install.ps1", temp_payload / "install.ps1")
    launcher_zip = temp_payload / "cp13a_launcher.zip"
    with zipfile.ZipFile(launcher_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in source.rglob("*"):
            if path.is_file() and path.name not in {"install.cmd", "install.ps1"}:
                archive.write(path, path.relative_to(source).as_posix())


def write_sed(temp_payload: Path, sed_path: Path) -> None:
    files = sorted(path for path in temp_payload.rglob("*") if path.is_file())
    source_sections: dict[Path, list[Path]] = {}
    for path in files:
        source_sections.setdefault(path.parent, []).append(path)
    file_lines = [f"FILE{idx}={path.name}" for idx, path in enumerate(files)]
    source_lines = []
    section_lines = []
    for idx, (folder, folder_files) in enumerate(source_sections.items()):
        source_lines.append(f"SourceFiles{idx}={folder}")
        section_lines.append(f"[SourceFiles{idx}]")
        for path in folder_files:
            section_lines.append(f"{path.name}=")
        section_lines.append("")
    sed = "\n".join(
        [
            "[Version]",
            "Class=IEXPRESS",
            "SEDVersion=3",
            "",
            "[Options]",
            "PackagePurpose=InstallApp",
            "ShowInstallProgramWindow=1",
            "HideExtractAnimation=0",
            "UseLongFileName=1",
            "InsideCompressed=0",
            "CAB_FixedSize=0",
            "CAB_ResvCodeSigning=0",
            "RebootMode=N",
            "InstallPrompt=Install Tool Auto Sub Beta CP13A?",
            "DisplayLicense=",
            "FinishMessage=Tool Auto Sub Beta installer finished.",
            f"TargetName={INSTALLER}",
            "FriendlyName=Tool Auto Sub Beta CP13A",
            "AppLaunched=install.cmd",
            "PostInstallCmd=<None>",
            "AdminQuietInstCmd=install.cmd",
            "UserQuietInstCmd=install.cmd",
            "SourceFiles=SourceFiles",
            *file_lines,
            "",
            "[Strings]",
            "",
            "[SourceFiles]",
            *source_lines,
            "",
            *section_lines,
        ]
    )
    sed_path.write_text(sed, encoding="utf-8")


def run_iexpress(sed_path: Path) -> dict:
    iexpress = shutil.which("iexpress.exe")
    if not iexpress:
        raise RuntimeError("CP13A_BLOCKED_INSTALLER_TOOLCHAIN: iexpress.exe not found")
    version = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", "(Get-Command iexpress.exe).Version.ToString()"],
        text=True,
    ).strip()
    completed = subprocess.run([iexpress, "/N", "/Q", str(sed_path)], cwd=ROOT, text=True, capture_output=True, check=False)
    if completed.returncode != 0 or not INSTALLER.exists():
        raise RuntimeError(f"CP13A_BLOCKED_INSTALLER_TOOLCHAIN: IExpress failed: {completed.stderr or completed.stdout}")
    return {"tool": "iexpress.exe", "path": iexpress, "version": version}


def wait_health(url: str, seconds: int = 120) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(url, timeout=2) as response:
                home_ok = response.status == 200
            if payload.get("status") == "ok" and home_ok:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def validate_installer() -> dict:
    validation_root = Path(tempfile.mkdtemp(prefix="cp13a_validation_"))
    install_dir = validation_root / "Install With Spaces" / "ToolAutoSubBeta"
    user_dir = validation_root / "User Data With Spaces"
    start_menu = validation_root / "Start Menu"
    desktop = validation_root / "Desktop"
    steps: list[dict] = []
    env = os.environ.copy()
    env.update(
        {
            "CP13A_INSTALL_SILENT": "1",
            "CP13A_CREATE_DESKTOP_SHORTCUT": "1",
            "CP13A_INSTALL_DIR": str(install_dir),
            "CP13A_USERDATA_DIR": str(user_dir),
            "CP13A_START_MENU_DIR": str(start_menu),
            "CP13A_DESKTOP_DIR": str(desktop),
            "CP13A_NO_BROWSER": "1",
            "CP13A_NO_EXPLORER": "1",
            "CP13A_HEADLESS": "1",
        }
    )

    def tail(value: str | None, limit: int = 4000) -> str:
        return (value or "")[-limit:]

    def run_step(name: str, command: list[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        print(f"CP13A validation step: {name}", flush=True)
        started = time.time()
        log_path = validation_root / f"{name}.log"
        try:
            with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
                completed = subprocess.run(
                    command,
                    cwd=cwd or ROOT,
                    env=env,
                    text=True,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            steps.append(
                {
                    "name": name,
                    "status": "TIMEOUT",
                    "timeout_seconds": timeout,
                    "duration_seconds": round(time.time() - started, 3),
                    "log_path": str(log_path),
                    "log_tail": tail(log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""),
                }
            )
            raise RuntimeError(f"{name} timed out after {timeout} seconds") from exc
        log_tail = tail(log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "")
        steps.append(
            {
                "name": name,
                "returncode": completed.returncode,
                "duration_seconds": round(time.time() - started, 3),
                "log_path": str(log_path),
                "log_tail": log_tail,
            }
        )
        if completed.returncode != 0:
            raise RuntimeError(f"{name} returned {completed.returncode}: {log_tail}")
        return completed

    def stop_validation_processes() -> None:
        escaped = str(validation_root).replace("'", "''")
        script = (
            f"$root='{escaped}'; "
            "Get-CimInstance Win32_Process | Where-Object { "
            "($_.CommandLine -and $_.CommandLine.Contains($root)) -or ($_.ExecutablePath -and $_.ExecutablePath.Contains($root)) "
            "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], text=True, capture_output=True, timeout=30, check=False)

    try:
        run_step("install", [str(INSTALLER), "/Q"], 900)
        run_step(
            "launch",
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(install_dir / "beta" / "ToolAutoSubBeta.ps1")],
            240,
            cwd=install_dir,
        )
        state = json.loads((user_dir / "runtime_state.json").read_text(encoding="utf-8-sig"))
        url = state["url"]
        if not wait_health(url):
            raise RuntimeError("health check did not become ready")
        run_step(
            "second_launch_idempotency",
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(install_dir / "beta" / "ToolAutoSubBeta.ps1")],
            90,
            cwd=install_dir,
        )
        run_step(
            "diagnostics",
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(install_dir / "beta" / "CollectDiagnostics.ps1")],
            120,
            cwd=install_dir,
        )
        run_step(
            "stop",
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(install_dir / "beta" / "ToolAutoSubBeta.ps1"), "-Action", "Stop"],
            90,
            cwd=install_dir,
        )
        run_step(
            "uninstall",
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(install_dir / "beta" / "UninstallToolAutoSubBeta.ps1")],
            120,
            cwd=install_dir,
        )
        return {
            "status": "PASS",
            "validation_root": str(validation_root),
            "steps": steps,
            "install_dir_removed": not install_dir.exists(),
            "user_data_preserved": user_dir.exists(),
            "start_menu_shortcut": (start_menu / "Tool Auto Sub Beta.lnk").exists() is False,
            "desktop_shortcut_removed": (desktop / "Tool Auto Sub Beta.lnk").exists() is False,
            "simple_ui_url": url,
            "provider_calls": {"gemini": 0, "elevenlabs": 0, "youtube": 0},
            "no_browser_opened_by_validation": True,
        }
    except Exception as exc:
        stop_validation_processes()
        return {"status": "FAIL", "validation_root": str(validation_root), "error": str(exc), "steps": steps}


def main() -> None:
    if sha256_file(CP12B_ZIP) != CP12B_SHA:
        raise SystemExit("CP12B protected hash mismatch")
    if sha256_file(ACCEPTED_MP4) != ACCEPTED_SHA:
        raise SystemExit("Accepted MP4 protected hash mismatch")

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    cp12b_size = CP12B_ZIP.stat().st_size
    ffmpeg_size = sum(path.stat().st_size for path in [FFMPEG_ROOT / "bin" / "ffmpeg.exe", FFMPEG_ROOT / "bin" / "ffprobe.exe", FFMPEG_ROOT / "LICENSE", FFMPEG_ROOT / "README.txt"])
    projected = cp12b_size + ffmpeg_size + 250 * 1024 * 1024
    gate = storage_preflight("package", ROOT, projected_workspace_bytes=projected, safety_reserve_bytes=PACKAGE_SAFETY_RESERVE_BYTES)
    if not gate["passed"]:
        raise SystemExit(json.dumps(gate, indent=2))

    with tempfile.TemporaryDirectory(prefix="cp13a_build_") as temp_name:
        temp_root = Path(temp_name)
        payload = temp_root / "payload"
        payload.mkdir()
        shutil.copy2(CP12B_ZIP, payload / CP12B_ZIP.name)
        copy_installer_scripts(payload)
        ffmpeg_inventory = copy_required_ffmpeg(payload)
        overlay_inventory = build_overlay(payload)
        sed_path = temp_root / "cp13a.sed"
        write_sed(payload, sed_path)
        toolchain = run_iexpress(sed_path)

    installer_hash = sha256_file(INSTALLER)
    validation = validate_installer()
    if validation["status"] != "PASS":
        raise SystemExit(json.dumps(validation, indent=2))

    manifest = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "application_version": "CP13A",
        "build_commit": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip(),
        "installer_filename": INSTALLER.name,
        "installer_size": INSTALLER.stat().st_size,
        "installer_sha256": installer_hash,
        "bundled_backend_version": "0.2.0",
        "simple_ui_asset_version": "cp13a",
        "operator_ui_asset_version": "cp09c",
        "database_schema_version": "0009_subtitle_tracks",
        "target_windows_architecture": "x64",
        "installation_mode": "per-user-no-admin",
        "default_install_path": "%LOCALAPPDATA%\\Programs\\ToolAutoSubBeta",
        "default_user_data_path": "%LOCALAPPDATA%\\ToolAutoSubBeta\\data",
        "default_log_path": "%LOCALAPPDATA%\\ToolAutoSubBeta\\logs",
        "default_diagnostics_path": "%LOCALAPPDATA%\\ToolAutoSubBeta\\diagnostics",
        "previous_portable_baseline": {"release": "CP12B", "sha256": CP12B_SHA},
        "bundled_runtime_inventory": {
            "cp12b_zip": {"path": str(CP12B_ZIP), "sha256": CP12B_SHA, "size": cp12b_size},
            "ffmpeg": ffmpeg_inventory,
            "app_overlay": overlay_inventory,
            "installer_toolchain": toolchain,
        },
        "provider_disabled_state": {"gemini": "disabled", "elevenlabs": "disabled", "youtube": "disabled", "upload": "disabled", "publish": "disabled"},
        "build_storage_preflight_result": gate,
        "validation_summary": validation,
        "protected_hashes": {"cp12b_zip": CP12B_SHA, "accepted_mp4": ACCEPTED_SHA},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(RELEASE_DIR / "RELEASE_MANIFEST.json", manifest)
    (RELEASE_DIR / "RELEASE_NOTES.md").write_text(
        "\n".join(
            [
                "# CP13A One-Click External Beta",
                "",
                "Release ID: `CP13A_WINDOWS_ONE_CLICK_EXTERNAL_BETA`",
                "",
                "This release wraps the CP12B portable baseline in a per-user Windows installer.",
                "Normal users launch the Simple UI from Start Menu; Operator UI remains advanced/troubleshooting only.",
                "Gemini, ElevenLabs, upload and publish are disabled.",
                "Uninstall preserves user projects and database by default.",
                "",
                "External-machine friend beta remains pending until tested outside this development machine.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copy2(ROOT / "packaging" / "cp13a" / "BETA_TEST_5_MINUTES.txt", RELEASE_DIR / "BETA_TEST_5_MINUTES.txt")
    sums = {
        INSTALLER.name: installer_hash,
        "RELEASE_MANIFEST.json": sha256_file(RELEASE_DIR / "RELEASE_MANIFEST.json"),
        "RELEASE_NOTES.md": sha256_file(RELEASE_DIR / "RELEASE_NOTES.md"),
        "BETA_TEST_5_MINUTES.txt": sha256_file(RELEASE_DIR / "BETA_TEST_5_MINUTES.txt"),
    }
    (RELEASE_DIR / "SHA256SUMS.txt").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())), encoding="utf-8")
    print(json.dumps({"verdict": "CP13A_ONE_CLICK_EXTERNAL_BETA_PACKAGE_MACHINE_PASS", "installer": str(INSTALLER), "sha256": installer_hash}, indent=2))


if __name__ == "__main__":
    main()
