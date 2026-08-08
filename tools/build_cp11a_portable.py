from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import venv
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = ROOT / "release" / "CP11A"
BUILD_ROOT = RELEASE_ROOT / "build"
STAGING_ROOT = BUILD_ROOT / "tool_auto_sub_windows_portable_cp11a"
ZIP_PATH = RELEASE_ROOT / "tool_auto_sub_windows_portable_cp11a.zip"
MANIFEST_PATH = RELEASE_ROOT / "cp11a_windows_portable_release_manifest.json"
CHECKSUMS_PATH = RELEASE_ROOT / "SHA256SUMS.txt"
EVIDENCE_ROOT = ROOT / "evidence" / "CP11A" / "windows_portable_release_bundle"

PACKAGE_ID = "CP11A_WINDOWS_PORTABLE_RELEASE_BUNDLE"
CP10C_RELEASE_CANDIDATE_ID = "CP10C_SIMPLE_UI_RELEASE_CANDIDATE"
CP10C_HEAD = "c3a3dcf"
ACCEPTED_RELEASE_HASH = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"
BACKEND_VERSION = "0.2.0"
SIMPLE_FRONTEND_VERSION = "cp10b"
OPERATOR_FRONTEND_VERSION = "cp09c"
DB_SCHEMA = "0008_simple_workflow_runs"

INCLUDE_DIRS = ["app", "alembic"]
INCLUDE_FILES = [
    "alembic.ini",
    "pyproject.toml",
    "requirements-portable.lock.txt",
    "README.md",
    "CONTRIBUTING.md",
]
EXCLUDED_CATEGORIES = [
    ".git metadata",
    "provider secrets and .env files",
    "development databases and WAL/SHM files",
    "source media and accepted release media",
    "evidence, checkpoints, test caches, renders and project runtime data",
    "OCR model/runtime files",
    "browser profiles and private logs",
]
TEXT_SECRET_PATTERNS = {
    "google_api_key_like": re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    "openai_key_like": re.compile(r"\bsk-(?:proj-|live-)?[A-Za-z0-9_\-]{40,}\b"),
    "dotenv_secret_assignment": re.compile(r"(?m)^\s*(?:API_KEY|GEMINI_API_KEY|ELEVENLABS_API_KEY|XI_API_KEY)\s*=\s*[\"']?[^#\s\"']{16,}"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the CP11A Windows portable bundle.")
    parser.add_argument("--skip-venv", action="store_true", help="Reuse an existing staging runtime.")
    args = parser.parse_args()
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    inventory = runtime_inventory()
    write_json(EVIDENCE_ROOT / "runtime_inventory.json", inventory)
    if inventory["disk"]["free_gib"] < 3:
        raise SystemExit("CP11A_BLOCKED_RUNTIME_OR_DISK_GATE: less than 3 GiB free for safe staging")
    reset_staging()
    copy_production_files()
    write_portable_config()
    write_packaged_runtime_manifest()
    write_launchers()
    write_quick_start()
    if not args.skip_venv:
        create_isolated_runtime()
    verify_runtime_imports()
    package_inventory = build_file_inventory(STAGING_ROOT)
    write_json(EVIDENCE_ROOT / "include_exclude_inventory.json", package_inventory)
    scan = secret_scan(STAGING_ROOT)
    write_json(EVIDENCE_ROOT / "secret_scan.json", scan)
    if scan["findings"]:
        raise SystemExit("CP11A_BLOCKED_RUNTIME_OR_DISK_GATE: secret-like content found in staging")
    zip_bundle()
    extracted_size = sum(item["size_bytes"] for item in package_inventory["files"])
    manifest = build_manifest(package_inventory, inventory, extracted_size)
    write_json(MANIFEST_PATH, manifest)
    write_checksums(manifest)
    write_json(EVIDENCE_ROOT / "zip_metadata.json", {
        "zip_path": str(ZIP_PATH),
        "zip_sha256": sha256_file(ZIP_PATH),
        "zip_size_bytes": ZIP_PATH.stat().st_size,
        "extracted_size_bytes": extracted_size,
    })
    print(json.dumps({
        "verdict": "CP11A_PACKAGE_BUILD_PASS",
        "zip_path": str(ZIP_PATH),
        "zip_sha256": sha256_file(ZIP_PATH),
        "files": len(package_inventory["files"]),
        "extracted_size_bytes": extracted_size,
    }, indent=2))


def runtime_inventory() -> dict:
    python = Path(sys.executable).resolve()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    ocr_root = Path(r"D:\tool_auto_sub_ocr_runtime")
    pip_freeze = subprocess.run([str(python), "-m", "pip", "freeze", "--all"], capture_output=True, text=True, check=False)
    disk = shutil.disk_usage(ROOT)
    return {
        "python": {
            "executable": str(python),
            "version": sys.version.split()[0],
            "project_local_virtualenv_exists": any((ROOT / name).exists() for name in [".venv", "venv"]),
        },
        "dependencies": {
            "source": "requirements-portable.lock.txt into release-local venv; global site-packages disabled",
            "pip_freeze_available": pip_freeze.returncode == 0,
        },
        "ffmpeg": {"path": ffmpeg, "version": version_line(ffmpeg) if ffmpeg else None},
        "ffprobe": {"path": ffprobe, "version": version_line(ffprobe) if ffprobe else None},
        "ocr_runtime": {
            "path": str(ocr_root),
            "exists": ocr_root.exists(),
            "policy": "external dependency only; models are not bundled",
        },
        "node_npm_runtime_required": False,
        "browser_required": "default local browser only for opening UI; not bundled",
        "disk": {"free_bytes": disk.free, "free_gib": round(disk.free / (1024**3), 3)},
    }


def version_line(executable: str | None) -> str | None:
    if not executable:
        return None
    completed = subprocess.run([executable, "-version"], capture_output=True, text=True, check=False)
    first = (completed.stdout or completed.stderr).splitlines()
    return first[0] if first else None


def reset_staging() -> None:
    if BUILD_ROOT.exists():
        shutil.rmtree(BUILD_ROOT)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    STAGING_ROOT.mkdir(parents=True)
    for child in ["runtime", "config", "data", "logs", "release"]:
        (STAGING_ROOT / child).mkdir(parents=True, exist_ok=True)


def copy_production_files() -> None:
    for directory in INCLUDE_DIRS:
        shutil.copytree(ROOT / directory, STAGING_ROOT / directory, ignore=ignore_dev_files)
    for file_name in INCLUDE_FILES:
        source = ROOT / file_name
        destination = STAGING_ROOT / file_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    safe_operator = STAGING_ROOT / "operator"
    safe_operator.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "templates" / "overnight_inputs" / "source_provenance.example.json", safe_operator / "source_provenance.json")
    (safe_operator / "run_config.json").write_text(json.dumps(safe_run_config(), indent=2), encoding="utf-8")


def ignore_dev_files(_dir: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        lower = name.lower()
        if lower in {"__pycache__", ".pytest_cache"} or lower.endswith((".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log")):
            ignored.add(name)
    return ignored


def safe_run_config() -> dict:
    return {
        "schema_version": 1,
        "source": {"path": "input\\source.mp4", "expected_sha256": None},
        "market_profile_id": "portable_default",
        "source_language": "zh",
        "target_language": "en",
        "target_locale": "en-US",
        "content_mode": "dialogue_subtitles_only",
        "audio_policy": "simple_reference_copy_by_default",
        "preview": "720p",
        "final": "source_or_720p",
        "auto_upload": False,
        "push": False,
        "provider_calls_enabled": False,
        "upload_publish_enabled": False,
        "non_dialogue_localization_enabled": False,
        "translation": {"provider": "disabled", "config_path": "config\\translation_config.env", "key_file": None},
        "tts": {"provider": "disabled", "key_file": None, "allow_real_paid_calls": False},
        "hardware": {"gpu": "auto", "whisper_device": "disabled_by_default", "free_disk_required_gb": 1},
        "subtitle": {"font_path": r"C:\Windows\Fonts\arial.ttf"},
        "ocr_runtime": {"path": r"D:\tool_auto_sub_ocr_runtime", "required_for_closed_loop_cleanup": True},
    }


def write_portable_config() -> None:
    payload = {
        "schema_version": 1,
        "bind": "127.0.0.1",
        "port": 8173,
        "build_commit": CP10C_HEAD,
        "release_candidate": CP10C_RELEASE_CANDIDATE_ID,
        "package_checkpoint": "CP11A",
        "provider_calls_enabled": False,
        "upload_publish_enabled": False,
        "data_root": "data",
        "logs_root": "logs",
        "ocr_runtime_path": r"D:\tool_auto_sub_ocr_runtime",
    }
    write_json(STAGING_ROOT / "config" / "portable_config.json", payload)
    (STAGING_ROOT / "config" / "translation_config.env.example").write_text(
        "# Provider calls are disabled by default in CP11A portable.\n",
        encoding="utf-8",
    )


def write_packaged_runtime_manifest() -> None:
    write_json(STAGING_ROOT / "release" / "cp11a_portable_runtime_manifest.json", {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "package_checkpoint": "CP11A",
        "frozen_cp10c_head": CP10C_HEAD,
        "cp10c_release_candidate_id": CP10C_RELEASE_CANDIDATE_ID,
        "backend_version": BACKEND_VERSION,
        "simple_frontend_version": SIMPLE_FRONTEND_VERSION,
        "operator_frontend_version": OPERATOR_FRONTEND_VERSION,
        "database_schema": DB_SCHEMA,
        "accepted_release_artifact_hash": ACCEPTED_RELEASE_HASH,
        "provider_defaults": {"gemini": "disabled", "elevenlabs": "disabled", "youtube": "disabled"},
        "source_handling_policy": "reference source by default; no media bundled",
        "run_isolation_policy": "data/projects/<project_id>/runs/<run_id>",
        "external_dependencies": {
            "ffmpeg": "required on PATH",
            "ffprobe": "required on PATH",
            "ocr_runtime": r"D:\tool_auto_sub_ocr_runtime",
        },
    })


def write_launchers() -> None:
    scripts = STAGING_ROOT / "release" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ["start_tool.cmd", "stop_tool.cmd", "tool_status.cmd", "open_operator_ui.cmd"]:
        ps1 = name.replace(".cmd", ".ps1")
        (STAGING_ROOT / name).write_text(f'@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release\\scripts\\{ps1}" %*\r\n', encoding="utf-8")
    (scripts / "common.ps1").write_text(COMMON_PS1, encoding="utf-8")
    (scripts / "start_tool.ps1").write_text(START_PS1, encoding="utf-8")
    (scripts / "stop_tool.ps1").write_text(STOP_PS1, encoding="utf-8")
    (scripts / "tool_status.ps1").write_text(STATUS_PS1, encoding="utf-8")
    (scripts / "open_operator_ui.ps1").write_text(OPEN_OPERATOR_PS1, encoding="utf-8")


def write_quick_start() -> None:
    (STAGING_ROOT / "README_FIRST.txt").write_text(
        "\n".join([
            "Tool Auto Sub portable release CP11A",
            "",
            "1. Extract this folder anywhere writable, such as D:\\Tool Auto Sub CP11A.",
            "2. Double-click start_tool.cmd to start the local app.",
            "3. The Simple UI opens at http://127.0.0.1:<port>/.",
            "4. Use open_operator_ui.cmd for the Operator UI.",
            "5. Use tool_status.cmd to inspect health and stop_tool.cmd to stop only this bundle.",
            "",
            "Provider, upload and publish calls are disabled by default. No API keys are included.",
            "OCR closed-loop cleanup requires an external runtime at D:\\tool_auto_sub_ocr_runtime or a configured equivalent.",
        ]),
        encoding="utf-8",
    )


def create_isolated_runtime() -> None:
    venv_dir = STAGING_ROOT / "runtime" / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=False, clear=True).create(venv_dir)
    python = venv_dir / "Scripts" / "python.exe"
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(STAGING_ROOT / "requirements-portable.lock.txt")], check=True)


def verify_runtime_imports() -> None:
    python = STAGING_ROOT / "runtime" / "venv" / "Scripts" / "python.exe"
    code = "import fastapi, uvicorn, sqlalchemy, alembic, pydantic, httpx, jsonschema; print('imports-ok')"
    completed = subprocess.run([str(python), "-c", code], cwd=STAGING_ROOT, capture_output=True, text=True, check=False)
    write_json(EVIDENCE_ROOT / "isolated_runtime_imports.json", {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "python": str(python),
    })
    if completed.returncode != 0:
        raise SystemExit("CP11A_BLOCKED_RUNTIME_OR_DISK_GATE: isolated runtime imports failed")


def build_file_inventory(root: Path) -> dict:
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        files.append({"path": rel, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {"root": str(root), "files": files, "excluded_categories": EXCLUDED_CATEGORIES}


def secret_scan(root: Path) -> dict:
    findings = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.suffix.lower() not in {".py", ".ps1", ".cmd", ".txt", ".json", ".md", ".ini", ".css", ".js", ".html"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, pattern in TEXT_SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append({"path": path.relative_to(root).as_posix(), "pattern": name})
    return {"status": "PASS" if not findings else "FAIL", "findings": findings}


def zip_bundle() -> None:
    files = [p for p in STAGING_ROOT.rglob("*") if p.is_file()]
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(STAGING_ROOT).as_posix())


def build_manifest(package_inventory: dict, inventory: dict, extracted_size: int) -> dict:
    zip_sha = sha256_file(ZIP_PATH)
    return {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "package_checkpoint": "CP11A",
        "source_git_head": current_git_head(),
        "frozen_cp10c_head": CP10C_HEAD,
        "cp10c_release_candidate_id": CP10C_RELEASE_CANDIDATE_ID,
        "build_versions": {
            "backend": BACKEND_VERSION,
            "simple_ui": SIMPLE_FRONTEND_VERSION,
            "operator_ui": OPERATOR_FRONTEND_VERSION,
            "database_schema": DB_SCHEMA,
        },
        "packaged_file_inventory": package_inventory["files"],
        "excluded_content_categories": EXCLUDED_CATEGORIES,
        "zip": {"path": str(ZIP_PATH), "sha256": zip_sha, "size_bytes": ZIP_PATH.stat().st_size},
        "extracted_size_bytes": extracted_size,
        "runtime_strategy": "release-local Python venv; no global site-packages; system ffmpeg/ffprobe checked by launcher",
        "python": inventory["python"],
        "ffmpeg": inventory["ffmpeg"],
        "ffprobe": inventory["ffprobe"],
        "ocr_runtime_policy": inventory["ocr_runtime"],
        "default_bind_address": "127.0.0.1",
        "default_port": 8173,
        "default_data_root": "data",
        "provider_defaults": {"gemini": "disabled", "elevenlabs": "disabled", "youtube": "disabled"},
        "source_handling_policy": "reference source by default; explicit save-copy only",
        "run_isolation_policy": "data/projects/<project_id>/runs/<run_id>",
        "accepted_release_artifact_hash": ACCEPTED_RELEASE_HASH,
        "test_result": "pending full validation",
        "cold_start_result": "pending validation",
        "known_limitations": [
            "OCR runtime is external and not bundled.",
            "Provider calls are disabled by default.",
            "CP11A package is a portable ZIP, not a system installer.",
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def write_checksums(manifest: dict) -> None:
    lines = [f"{manifest['zip']['sha256']}  {ZIP_PATH.name}"]
    for item in manifest["packaged_file_inventory"]:
        lines.append(f"{item['sha256']}  staging/{item['path']}")
    CHECKSUMS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def current_git_head() -> str:
    completed = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


COMMON_PS1 = r'''$ErrorActionPreference = "Stop"
function Get-BundleRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
function Read-PortableConfig {
  $root = Get-BundleRoot
  $configPath = Join-Path $root "config\portable_config.json"
  return Get-Content -Raw $configPath | ConvertFrom-Json
}
function Get-StatePath {
  $root = Get-BundleRoot
  return Join-Path $root "runtime\tool_state.json"
}
function Read-State {
  $statePath = Get-StatePath
  if (Test-Path $statePath) { return Get-Content -Raw $statePath | ConvertFrom-Json }
  return $null
}
function Test-PortOpen([string]$HostName, [int]$Port) {
  try {
    $client = New-Object Net.Sockets.TcpClient
    $async = $client.BeginConnect($HostName, $Port, $null, $null)
    if ($async.AsyncWaitHandle.WaitOne(250, $false)) {
      $client.EndConnect($async)
      $client.Close()
      return $true
    }
    $client.Close()
  } catch {}
  return $false
}
function Get-ProcessCommandLine([int]$ProcessIdValue) {
  try {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessIdValue"
    return $proc.CommandLine
  } catch {
    return $null
  }
}
function Test-OwnedProcess([int]$ProcessIdValue) {
  $root = Get-BundleRoot
  $cmd = Get-ProcessCommandLine $ProcessIdValue
  return ($cmd -and $cmd.Contains($root))
}
function Get-ListeningPid([string]$HostName, [int]$Port) {
  try {
    $connection = Get-NetTCPConnection -LocalAddress $HostName -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($connection) { return [int]$connection.OwningProcess }
  } catch {}
  return $null
}
'''


START_PS1 = r'''. "$PSScriptRoot\common.ps1"
$root = Get-BundleRoot
$config = Read-PortableConfig
$statePath = Get-StatePath
$logs = Join-Path $root "logs"
$data = Join-Path $root "data"
New-Item -ItemType Directory -Force $logs,$data,(Join-Path $root "runtime") | Out-Null
$state = Read-State
if ($state -and $state.pid) {
  $proc = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
  if ($proc -and (Test-OwnedProcess ([int]$state.pid))) {
    Write-Host "Tool Auto Sub is already running at $($state.url)"
    Start-Process $state.url
    exit 0
  }
}
$bind = [string]$config.bind
$port = [int]$config.port
if (Test-PortOpen $bind $port) {
  $found = $false
  foreach ($candidate in 8174..8199) {
    if (-not (Test-PortOpen $bind $candidate)) {
      Write-Host "Port $port is busy. Starting this portable bundle on safe alternate port $candidate."
      $port = $candidate
      $found = $true
      break
    }
  }
  if (-not $found) {
    Write-Host "Startup failed: ports 8173-8199 are occupied. No unrelated process was stopped."
    exit 2
  }
}
$python = Join-Path $root "runtime\venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  Write-Host "Startup failed: portable Python runtime is missing at $python"
  exit 3
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Startup failed: ffmpeg is not available on PATH. Install ffmpeg or add it to PATH, then start again."
  exit 4
}
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
  Write-Host "Startup failed: ffprobe is not available on PATH. Install ffmpeg/ffprobe or add it to PATH, then start again."
  exit 5
}
$ocrPath = [string]$config.ocr_runtime_path
$ocrAvailable = Test-Path $ocrPath
if (-not $ocrAvailable) {
  Write-Host "OCR runtime not found at $ocrPath. Simple UI can start; OCR cleanup features will show a dependency message."
}
$env:TOOL_AUTO_SUB_ROOT = $root
$env:TOOL_AUTO_SUB_DB_PATH = Join-Path $data "app.db"
$env:TOOL_AUTO_SUB_BUILD_COMMIT = [string]$config.build_commit
$env:TOOL_AUTO_SUB_PROVIDER_CALLS_ENABLED = "0"
$env:TOOL_AUTO_SUB_UPLOAD_PUBLISH_ENABLED = "0"
$outLog = Join-Path $logs "backend.out.log"
$errLog = Join-Path $logs "backend.err.log"
$args = @("-m","uvicorn","app.main:app","--host",$bind,"--port",[string]$port)
$proc = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $root -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WindowStyle Hidden -PassThru
$url = "http://$bind`:$port/"
$health = "http://$bind`:$port/api/health"
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $response = Invoke-RestMethod -Uri $health -TimeoutSec 1
    if ($response.status -eq "ok") { $ready = $true; break }
  } catch {}
}
if (-not $ready) {
  Write-Host "Startup failed: backend did not become healthy. See $errLog"
  if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
  exit 6
}
$listenerPid = Get-ListeningPid $bind $port
if ($listenerPid -and (Test-OwnedProcess $listenerPid)) {
  $ownedPid = $listenerPid
} else {
  $ownedPid = $proc.Id
}
$statePayload = [ordered]@{
  pid = $ownedPid
  launcher_pid = $proc.Id
  url = $url
  bind = $bind
  port = $port
  data_dir = $data
  log_dir = $logs
  build_commit = [string]$config.build_commit
  backend_version = "0.2.0"
  simple_ui = "cp10b"
  operator_ui = "cp09c"
  database_schema = "0008_simple_workflow_runs"
  ocr_runtime_available = $ocrAvailable
  started_at = (Get-Date).ToString("o")
}
$statePayload | ConvertTo-Json | Set-Content -Encoding UTF8 $statePath
Write-Host "Tool Auto Sub is running at $url"
Start-Process $url
'''


STOP_PS1 = r'''. "$PSScriptRoot\common.ps1"
$statePath = Get-StatePath
$state = Read-State
if (-not $state -or -not $state.pid) {
  Write-Host "Tool Auto Sub is stopped. No PID state exists."
  exit 0
}
$pidValue = [int]$state.pid
$proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
if (-not $proc) {
  Remove-Item $statePath -Force -ErrorAction SilentlyContinue
  Write-Host "Removed stale PID state. Tool Auto Sub is stopped."
  exit 0
}
if (-not (Test-OwnedProcess $pidValue)) {
  Write-Host "Refusing to stop PID $pidValue because it was not started from this bundle."
  exit 2
}
Stop-Process -Id $pidValue
if ($state.launcher_pid) {
  $launcherPid = [int]$state.launcher_pid
  if ($launcherPid -ne $pidValue) {
    $launcherProc = Get-Process -Id $launcherPid -ErrorAction SilentlyContinue
    if ($launcherProc -and (Test-OwnedProcess $launcherPid)) {
      Stop-Process -Id $launcherPid -ErrorAction SilentlyContinue
    }
  }
}
Remove-Item $statePath -Force -ErrorAction SilentlyContinue
Write-Host "Stopped Tool Auto Sub PID $pidValue."
'''


STATUS_PS1 = r'''. "$PSScriptRoot\common.ps1"
$root = Get-BundleRoot
$state = Read-State
if (-not $state -or -not $state.pid) {
  Write-Host "Status: stopped"
  Write-Host "Data: $(Join-Path $root 'data')"
  Write-Host "Logs: $(Join-Path $root 'logs')"
  exit 0
}
$pidValue = [int]$state.pid
$proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
if ($proc -and (Test-OwnedProcess $pidValue)) {
  Write-Host "Status: running"
  Write-Host "PID: $pidValue"
  Write-Host "URL: $($state.url)"
  Write-Host "Build: $($state.build_commit)"
  Write-Host "Backend: $($state.backend_version)"
  Write-Host "Simple UI: $($state.simple_ui)"
  Write-Host "Operator UI: $($state.operator_ui)"
  Write-Host "Database schema: $($state.database_schema)"
  Write-Host "Data: $($state.data_dir)"
  Write-Host "Logs: $($state.log_dir)"
  Write-Host "OCR runtime available: $($state.ocr_runtime_available)"
} else {
  Write-Host "Status: stopped (stale PID state)"
  Remove-Item (Get-StatePath) -Force -ErrorAction SilentlyContinue
}
'''


OPEN_OPERATOR_PS1 = r'''. "$PSScriptRoot\common.ps1"
$state = Read-State
if (-not $state -or -not $state.url) {
  Write-Host "Tool Auto Sub is not running. Start it with start_tool.cmd first."
  exit 1
}
$operator = "$($state.url.TrimEnd('/'))/operator/"
Write-Host "Opening $operator"
Start-Process $operator
'''


if __name__ == "__main__":
    main()
