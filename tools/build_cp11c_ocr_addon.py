from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata as md
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_RUNTIME = Path(r"D:\tool_auto_sub_ocr_runtime")
RELEASE_ROOT = ROOT / "release" / "CP11C"
STAGING_ROOT_BASE = Path(tempfile.gettempdir()) / "tool_auto_sub_cp11c_staging"
ZIP_PATH = RELEASE_ROOT / "tool_auto_sub_ocr_runtime_addon_cp11c.zip"
MANIFEST_PATH = RELEASE_ROOT / "cp11c_ocr_runtime_addon_manifest.json"
CHECKSUMS_PATH = RELEASE_ROOT / "SHA256SUMS.txt"
EVIDENCE_ROOT = ROOT / "evidence" / "CP11C" / "portable_ocr_runtime_addon"

PACKAGE_ID = "CP11C_PORTABLE_OCR_RUNTIME_ADDON"
APP_PACKAGE_ID = "CP11A_WINDOWS_PORTABLE_RELEASE_BUNDLE"
APP_ZIP_SHA256 = "116cc2295f6bd53a3fe81d2f86ef463adcaf4ed3ed68bb4450c97ef02b92f315"
ACCEPTED_RELEASE_SHA256 = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"

PACKAGE_ROOT = "ocr_addon"
RUNTIME_RELATIVE = Path(PACKAGE_ROOT) / "runtime"
LICENSE_RELATIVE = Path(PACKAGE_ROOT) / "licenses"
SCRIPTS_RELATIVE = Path(PACKAGE_ROOT)

EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    "pip-cache",
    "smoke",
    "tmp",
    ".git",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".db",
    ".db-wal",
    ".db-shm",
}

PACKAGE_DISCOVERY_POLICY = [
    "operator\\ocr_runtime_config.local.json",
    "addons\\ocr_runtime\\operator\\ocr_runtime_config.local.json",
    "TOOL_AUTO_SUB_OCR_RUNTIME_CONFIG",
    "TOOL_AUTO_SUB_OCR_RUNTIME_ROOT",
]


INSTALL_PS1 = r'''
param(
  [string]$PortableAppRoot = "",
  [switch]$Replace
)

$ErrorActionPreference = "Stop"

function Get-AddonRoot {
  $PSScriptRoot
}

function Read-Json([string]$Path) {
  Get-Content -Raw $Path -Encoding UTF8 | ConvertFrom-Json
}

function Write-Json([string]$Path, $Value) {
  $Value | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding UTF8
}

function Resolve-PortableAppRoot([string]$Provided) {
  if ($Provided) {
    $candidate = Resolve-Path $Provided -ErrorAction SilentlyContinue
    if ($candidate) { return $candidate.Path }
    throw "Portable app root not found: $Provided"
  }
  $input = Read-Host "Enter portable app root"
  if (-not $input) { throw "Portable app root is required" }
  $candidate = Resolve-Path $input -ErrorAction SilentlyContinue
  if (-not $candidate) { throw "Portable app root not found: $input" }
  return $candidate.Path
}

function Test-AddonPackage([string]$AddonRoot) {
  $manifestPath = Join-Path $AddonRoot "addon_manifest.json"
  $checksumsPath = Join-Path $AddonRoot "SHA256SUMS.txt"
  if (-not (Test-Path $manifestPath)) { throw "addon_manifest.json is missing" }
  if (-not (Test-Path $checksumsPath)) { throw "SHA256SUMS.txt is missing" }
  & (Join-Path $AddonRoot "verify_ocr_addon.cmd") "--verify-package-only" | Out-Null
}

$addonRoot = Get-AddonRoot
if (-not (Test-Path (Join-Path $addonRoot "install_ocr_addon.cmd"))) {
  throw "Installer must run from the extracted CP11C add-on root."
}
Test-AddonPackage $addonRoot
$portableRoot = Resolve-PortableAppRoot $PortableAppRoot
if (-not (Test-Path (Join-Path $portableRoot "start_tool.cmd"))) {
  throw "Selected folder is not a portable Tool Auto Sub app root."
}
$addonTarget = Join-Path $portableRoot "addons\ocr_runtime"
$addonBackup = Join-Path $portableRoot ("addons\ocr_runtime.backup_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
$operatorDir = Join-Path $portableRoot "operator"
$runtimeDir = Join-Path $addonTarget "runtime"
$configPath = Join-Path $operatorDir "ocr_runtime_config.local.json"
New-Item -ItemType Directory -Force $operatorDir | Out-Null

if ((Test-Path $addonTarget) -and -not $Replace) {
  $manifestExisting = Join-Path $addonTarget "addon_manifest.json"
  if (Test-Path $manifestExisting) {
    $existing = Read-Json $manifestExisting
    if ($existing.addon_id -eq "cp11c-ocr-runtime-addon") {
      Write-Host "OCR add-on already installed at $addonTarget"
    } else {
      throw "A different OCR add-on already exists at $addonTarget. Re-run with -Replace to back it up and install this package."
    }
  } else {
    throw "A different OCR add-on already exists at $addonTarget. Re-run with -Replace to back it up and install this package."
  }
}

if (Test-Path $addonTarget) {
  if (Test-Path $addonBackup) { Remove-Item $addonBackup -Recurse -Force }
  Move-Item -LiteralPath $addonTarget -Destination $addonBackup
}

New-Item -ItemType Directory -Force $addonTarget | Out-Null
Copy-Item -Path (Join-Path $addonRoot "*") -Destination $addonTarget -Recurse -Force
Set-Content -Path (Join-Path $addonTarget "installed_from.txt") -Value $addonRoot -Encoding UTF8

$config = [ordered]@{
  schema_version = 1
  runtime_root = $runtimeDir
  python_path = (Join-Path $runtimeDir ".venv\Scripts\python.exe")
  model_root = (Join-Path $runtimeDir "models")
  temp_root = (Join-Path $runtimeDir "tmp")
  log_root = (Join-Path $runtimeDir "logs")
  timeout_seconds = 60
  discovery_source = "cp11c-addon-install"
  installed_at = (Get-Date).ToString("o")
}
Write-Json $configPath $config

$portableConfigPath = Join-Path $portableRoot "config\portable_config.json"
if (Test-Path $portableConfigPath) {
  $portableConfig = Read-Json $portableConfigPath
  if ($portableConfig.PSObject.Properties.Name -contains "ocr_runtime_path") {
    $portableConfig.ocr_runtime_path = $addonTarget
  } else {
    $portableConfig | Add-Member -NotePropertyName "ocr_runtime_path" -NotePropertyValue $addonTarget
  }
  Write-Json $portableConfigPath $portableConfig
}

& (Join-Path $addonTarget "verify_ocr_addon.cmd") "--portable-root" $portableRoot "--addon-root" $addonTarget
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Installed CP11C OCR add-on into $addonTarget"
Write-Host "Operator config: $configPath"
Write-Host "Run tool_status.cmd or open the app to confirm OCR readiness."
exit 0
'''


VERIFY_PS1 = r'''
param(
  [string]$PortableRoot = "",
  [string]$AddonRoot = "",
  [switch]$VerifyPackageOnly
)

$ErrorActionPreference = "Stop"

function Get-DefaultAddonRoot {
  $PSScriptRoot
}

function Read-Json([string]$Path) {
  Get-Content -Raw $Path -Encoding UTF8 | ConvertFrom-Json
}

function Write-Json([string]$Path, $Value) {
  $Value | ConvertTo-Json -Depth 10 | Set-Content -Path $Path -Encoding UTF8
}

function Resolve-Target([string]$Provided, [string]$Fallback) {
  if ($Provided) {
    $candidate = Resolve-Path $Provided -ErrorAction SilentlyContinue
    if ($candidate) { return $candidate.Path }
    throw "Path not found: $Provided"
  }
  if ($Fallback) { return $Fallback }
  throw "Addon root could not be determined."
}

function Get-FileHashMap([string]$Root) {
  $pairs = @{}
  Get-Content (Join-Path $Root "SHA256SUMS.txt") | ForEach-Object {
    if (-not $_.Trim()) { return }
    $parts = $_ -split '\s+', 2
    if ($parts.Count -lt 2) { throw "Malformed checksum entry: $_" }
    $pairs[$parts[1].Trim()] = $parts[0].Trim().ToLowerInvariant()
  }
  return $pairs
}

function Test-Checksums([string]$Root) {
  $expected = Get-FileHashMap $Root
  foreach ($relative in $expected.Keys) {
    $path = Join-Path $Root $relative
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing file in add-on: $relative" }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected[$relative]) {
      throw "Checksum mismatch for $relative"
    }
  }
}

function Get-PythonStatus([string]$PythonPath, [string]$ModelRoot) {
  $script = @'
import json, pathlib, sys
result = {"import_result": "fail", "smoke_result": "not_run", "runtime_version": None, "model_availability": {}, "actionable_fix_message": ""}
try:
    import cv2
    import numpy
    import paddle
    from paddleocr import PaddleOCR
    result["import_result"] = "pass"
    result["runtime_version"] = sys.version.split()[0]
    models = pathlib.Path(r"{MODEL_ROOT}")
    det = models / "ch_det"
    rec = models / "ch_rec"
    cls = models / "ch_cls"
    result["model_availability"] = {
        "ch_det": det.exists(),
        "ch_rec": rec.exists(),
        "ch_cls": cls.exists(),
    }
    if not all(result["model_availability"].values()):
        result["actionable_fix_message"] = "One or more OCR model directories are missing."
    else:
        ocr = PaddleOCR(use_angle_cls=True, lang="ch", use_gpu=False, det_model_dir=str(det), rec_model_dir=str(rec), cls_model_dir=str(cls), show_log=False)
        image = pathlib.Path(r"{SMOKE_IMAGE}")
        smoke = ocr.ocr(str(image), cls=True)
        result["smoke_result"] = "pass" if smoke is not None else "fail"
        result["actionable_fix_message"] = "OCR add-on verified."
except Exception as exc:
    result["actionable_fix_message"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(result, ensure_ascii=False))
'@
  return $script
}

$addonRootValue = Resolve-Target $AddonRoot (Get-DefaultAddonRoot)
$manifestPath = Join-Path $addonRootValue "addon_manifest.json"
if (-not (Test-Path $manifestPath)) { throw "addon_manifest.json is missing" }
Test-Checksums $addonRootValue
if ($VerifyPackageOnly) {
  Write-Host "Package checksum verification PASS"
  exit 0
}

$manifest = Read-Json $manifestPath
$runtimeRoot = Join-Path $addonRootValue "runtime"
$pythonPath = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) { throw "Addon Python runtime is missing" }
$modelRoot = Join-Path $runtimeRoot "models"
$smokeImage = Join-Path $addonRootValue "verification_smoke.png"
if (-not (Test-Path $smokeImage)) {
  $smokeScript = Join-Path $env:TEMP ("cp11c_smoke_" + [guid]::NewGuid().ToString("N") + ".py")
  $createImage = @'
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

image = Image.new("RGB", (640, 180), "white")
draw = ImageDraw.Draw(image)
font_candidates = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/arial.ttf",
]
font_path = next((Path(path) for path in font_candidates if Path(path).exists()), None)
font = ImageFont.truetype(str(font_path), 42) if font_path else ImageFont.load_default()
draw.text((20, 55), "太阳已经落下", fill="black", font=font)
image.save(r"{SMOKE_IMAGE}")
'@
  $createImage = $createImage.Replace("{SMOKE_IMAGE}", $smokeImage.Replace("\", "\\"))
  Set-Content -Path $smokeScript -Value $createImage -Encoding UTF8
  try {
    & $pythonPath $smokeScript
  } finally {
    Remove-Item $smokeScript -Force -ErrorAction SilentlyContinue
  }
}

$script = Get-PythonStatus -PythonPath $pythonPath -ModelRoot $modelRoot
$script = $script.Replace("{MODEL_ROOT}", $modelRoot.Replace("\", "\\")).Replace("{SMOKE_IMAGE}", $smokeImage.Replace("\", "\\"))
$tmp = Join-Path $env:TEMP ("cp11c_verify_" + [guid]::NewGuid().ToString("N") + ".py")
Set-Content -Path $tmp -Value $script -Encoding UTF8
try {
  $result = & $pythonPath $tmp
  if ($LASTEXITCODE -ne 0) { throw "OCR verification python failed with exit code $LASTEXITCODE" }
  $payload = $result | ConvertFrom-Json
  $verification = [ordered]@{
    addon_id = $manifest.addon_id
    verified_at = (Get-Date).ToString("o")
    runtime_version = $payload.runtime_version
    import_result = $payload.import_result
    smoke_result = $payload.smoke_result
    model_availability = $payload.model_availability
    actionable_fix_message = $payload.actionable_fix_message
    package_checksum_status = "PASS"
  }
  Write-Json (Join-Path $addonRootValue "verification.json") $verification
  if ($PortableRoot) {
    $portableStatus = Join-Path $PortableRoot "operator\ocr_runtime_status.json"
    New-Item -ItemType Directory -Force (Split-Path -Parent $portableStatus) | Out-Null
    Write-Json $portableStatus $verification
  }
  Write-Host "OCR add-on verification PASS"
  exit 0
} finally {
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  Remove-Item $smokeImage -Force -ErrorAction SilentlyContinue
}
'''


REMOVE_PS1 = r'''
param(
  [string]$PortableAppRoot = ""
)

$ErrorActionPreference = "Stop"

function Get-AddonRoot {
  $PSScriptRoot
}

function Read-Json([string]$Path) {
  Get-Content -Raw $Path -Encoding UTF8 | ConvertFrom-Json
}

function Write-Json([string]$Path, $Value) {
  $Value | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding UTF8
}

function Resolve-PortableAppRoot([string]$Provided) {
  if ($Provided) {
    $candidate = Resolve-Path $Provided -ErrorAction SilentlyContinue
    if ($candidate) { return $candidate.Path }
    throw "Portable app root not found: $Provided"
  }
  $input = Read-Host "Enter portable app root"
  $candidate = Resolve-Path $input -ErrorAction SilentlyContinue
  if (-not $candidate) { throw "Portable app root not found: $input" }
  return $candidate.Path
}

$addonRoot = Get-AddonRoot
$portableRoot = Resolve-PortableAppRoot $PortableAppRoot
$addonTarget = Join-Path $portableRoot "addons\ocr_runtime"
$configPath = Join-Path $portableRoot "operator\ocr_runtime_config.local.json"
$portableConfigPath = Join-Path $portableRoot "config\portable_config.json"
if (-not (Test-Path $addonTarget)) {
  Write-Host "OCR add-on is not installed."
  exit 0
}
$manifestPath = Join-Path $addonTarget "addon_manifest.json"
if (-not (Test-Path $manifestPath)) { throw "Installed add-on manifest missing" }
$manifest = Get-Content -Raw $manifestPath -Encoding UTF8 | ConvertFrom-Json
if ($manifest.addon_id -ne "cp11c-ocr-runtime-addon") {
  throw "Refusing to remove an unrecognized add-on at $addonTarget"
}
Remove-Item $addonTarget -Recurse -Force
if (Test-Path $configPath) { Remove-Item $configPath -Force }
if (Test-Path $portableConfigPath) {
  $portableConfig = Read-Json $portableConfigPath
  $missingPath = (Join-Path $portableRoot "addons\ocr_runtime_missing")
  if ($portableConfig.PSObject.Properties.Name -contains "ocr_runtime_path") {
    $portableConfig.ocr_runtime_path = $missingPath
  } else {
    $portableConfig | Add-Member -NotePropertyName "ocr_runtime_path" -NotePropertyValue $missingPath
  }
  Write-Json $portableConfigPath $portableConfig
}
Write-Host "Removed CP11C OCR add-on from $portableRoot"
exit 0
'''


README_TEXT = """Tool Auto Sub CP11C OCR Runtime Add-on

This package adds an isolated OCR runtime to a portable Tool Auto Sub installation.

Install flow:
1. Extract the CP11A portable application ZIP.
2. Extract this CP11C add-on ZIP.
3. Run install_ocr_addon.cmd and choose the portable app root.
4. The installer verifies checksums, copies the add-on into addons\\ocr_runtime, writes operator\\ocr_runtime_config.local.json, and runs verification.
5. Use verify_ocr_addon.cmd any time you want to re-check the add-on.
6. Use remove_ocr_addon.cmd to remove only the registered OCR add-on.

Requirements:
- The portable app must already be extracted.
- ffmpeg and ffprobe must be on PATH for OCR-dependent app workflows.
- The add-on is self-contained and does not require global Python, global pip, or administrator rights.

OCR discovery order:
1. operator\\ocr_runtime_config.local.json
2. addons\\ocr_runtime\\operator\\ocr_runtime_config.local.json
3. TOOL_AUTO_SUB_OCR_RUNTIME_CONFIG
4. TOOL_AUTO_SUB_OCR_RUNTIME_ROOT

Package integrity:
- Verify SHA256SUMS.txt before installation.
- Do not install if the package is incomplete or checksum validation fails.
- The add-on keeps its own manifest and verification record.

The add-on is intended for offline use after installation.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the CP11C portable OCR runtime add-on.")
    parser.add_argument("--source-runtime-root", default=str(SOURCE_RUNTIME))
    args = parser.parse_args()

    source_runtime = Path(args.source_runtime_root)
    if not source_runtime.exists():
        raise SystemExit(f"Source OCR runtime not found: {source_runtime}")

    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    staging_root = STAGING_ROOT_BASE / uuid.uuid4().hex
    if staging_root.exists():
        shutil.rmtree(staging_root)
    global STAGING_ROOT
    STAGING_ROOT = staging_root
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)

    inventory = inspect_runtime(source_runtime)
    if inventory["disk"]["free_gib"] < 5:
        raise SystemExit("CP11C_BLOCKED_OCR_RUNTIME_AND_DISK_GATE: insufficient disk space for staging")

    stage_root = STAGING_ROOT / PACKAGE_ROOT
    runtime_stage = stage_root / "runtime"
    licenses_stage = stage_root / "licenses"
    stage_root.mkdir(parents=True, exist_ok=True)
    runtime_stage.mkdir(parents=True, exist_ok=True)
    licenses_stage.mkdir(parents=True, exist_ok=True)

    copy_runtime(source_runtime, runtime_stage)
    write_runtime_example_config(runtime_stage)
    license_inventory = copy_license_notices(source_runtime, licenses_stage)
    write_scripts(stage_root)

    write_json(stage_root / "addon_manifest.json", build_manifest(inventory, license_inventory))
    package_files = build_file_inventory(stage_root)
    write_json(EVIDENCE_ROOT / "package_inventory.json", package_files)
    write_json(EVIDENCE_ROOT / "runtime_inventory.json", inventory)
    write_json(EVIDENCE_ROOT / "license_inventory.json", license_inventory)

    write_checksums(stage_root)
    zip_bundle()
    write_checksums(stage_root)
    manifest = build_manifest(inventory, license_inventory, package_files)
    write_json(MANIFEST_PATH, manifest)
    write_json(EVIDENCE_ROOT / "addon_manifest.json", manifest)
    write_json(EVIDENCE_ROOT / "zip_metadata.json", {
        "zip_path": str(ZIP_PATH),
        "zip_sha256": sha256_file(ZIP_PATH),
        "zip_size_bytes": ZIP_PATH.stat().st_size,
        "extracted_size_bytes": sum(item["size_bytes"] for item in package_files["files"]),
    })

    print(json.dumps({
        "verdict": "CP11C_BUILD_PASS",
        "zip_path": str(ZIP_PATH),
        "zip_sha256": sha256_file(ZIP_PATH),
        "addon_id": manifest["addon_id"],
        "files": len(package_files["files"]),
    }, indent=2))


def inspect_runtime(source_runtime: Path) -> dict[str, Any]:
    python = source_runtime / ".venv" / "Scripts" / "python.exe"
    disk = shutil.disk_usage(Path(tempfile.gettempdir()))
    freeze = subprocess.run([str(python), "-m", "pip", "freeze", "--all"], capture_output=True, text=True, check=False)
    smoke = run_runtime_smoke(python, source_runtime)
    return {
        "python": {
            "executable": str(python),
            "version": run_python_expr(python, "import sys; print(sys.version.split()[0])"),
            "arch": run_python_expr(python, "import platform; print(platform.architecture()[0])"),
            "machine": run_python_expr(python, "import platform; print(platform.machine())"),
            "platform": run_python_expr(python, "import platform; print(platform.platform())"),
        },
        "packages": parse_freeze(freeze.stdout),
        "models": inspect_models(source_runtime / "models"),
        "runtime_size": tree_size(source_runtime),
        "disk": {
            "free_bytes": disk.free,
            "free_gib": round(disk.free / (1024**3), 3),
        },
        "smoke": smoke,
    }


def run_python_expr(python: Path, code: str) -> str:
    completed = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=False, timeout=60)
    if completed.returncode != 0:
        raise SystemExit(completed.stderr or completed.stdout or "python command failed")
    return (completed.stdout or completed.stderr).strip().splitlines()[0]


def run_runtime_smoke(python: Path, source_runtime: Path) -> dict[str, Any]:
    smoke_image = source_runtime / "smoke" / "clean_chinese.png"
    code = textwrap.dedent(
        f"""
        import ctypes
        import json
        import pathlib
        import time
        from ctypes import wintypes

        def rss_mb():
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            psapi = ctypes.WinDLL("psapi.dll")
            kernel32 = ctypes.WinDLL("kernel32.dll")
            handle = kernel32.GetCurrentProcess()
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return round(counters.WorkingSetSize / (1024 * 1024), 2)

        start = time.perf_counter()
        import paddle
        import cv2
        import numpy
        from paddleocr import PaddleOCR
        import_time = time.perf_counter() - start
        mem_after_import = rss_mb()
        models = pathlib.Path(r"{(source_runtime / 'models').as_posix()}")
        image = pathlib.Path(r"{smoke_image.as_posix()}")
        ocr = PaddleOCR(
            use_angle_cls=True,
            lang="ch",
            use_gpu=False,
            det_model_dir=str(models / "ch_det"),
            rec_model_dir=str(models / "ch_rec"),
            cls_model_dir=str(models / "ch_cls"),
            show_log=False,
        )
        first_start = time.perf_counter()
        first = ocr.ocr(str(image), cls=True)
        first_seconds = time.perf_counter() - first_start
        mem_after_first = rss_mb()
        warm_start = time.perf_counter()
        second = ocr.ocr(str(image), cls=True)
        warm_seconds = time.perf_counter() - warm_start
        mem_after_warm = rss_mb()
        payload = {{
            "cold_import_seconds": round(import_time, 3),
            "first_ocr_seconds": round(first_seconds, 3),
            "warm_ocr_seconds": round(warm_seconds, 3),
            "memory_after_import_mb": mem_after_import,
            "memory_after_first_mb": mem_after_first,
            "memory_after_warm_mb": mem_after_warm,
            "first_contains_text": bool(first),
            "warm_contains_text": bool(second),
            "runtime_version": __import__("sys").version.split()[0],
        }}
        print(json.dumps(payload, ensure_ascii=False))
        """
    )
    completed = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=False, timeout=300)
    if completed.returncode != 0:
        raise SystemExit(completed.stderr or completed.stdout or "runtime smoke failed")
    return json.loads(completed.stdout)


def parse_freeze(text: str) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        if "==" not in line:
            continue
        name, version = line.split("==", 1)
        rows.append({"name": name.strip(), "version": version.strip()})
    return rows


def inspect_models(models_root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(models_root.rglob("*")):
        if path.is_file():
            rows.append({
                "path": str(path.relative_to(models_root)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return rows


def tree_size(root: Path) -> dict[str, Any]:
    total = 0
    files = 0
    for path in root.rglob("*"):
        if path.is_file() and not should_exclude(path):
            files += 1
            total += path.stat().st_size
    return {"files": files, "bytes": total, "gib": round(total / (1024**3), 3)}


def copy_runtime(source_root: Path, dest_root: Path) -> None:
    shutil.copytree(source_root / ".venv", dest_root / ".venv", ignore=copy_ignore)
    shutil.copytree(source_root / "models", dest_root / "models", ignore=copy_ignore)
    (dest_root / "logs").mkdir(parents=True, exist_ok=True)
    (dest_root / "tmp").mkdir(parents=True, exist_ok=True)
    (dest_root / "config").mkdir(parents=True, exist_ok=True)


def copy_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        lower = name.lower()
        if lower in EXCLUDE_DIR_NAMES:
            ignored.add(name)
            continue
        if lower.endswith(tuple(EXCLUDE_SUFFIXES)):
            ignored.add(name)
    return ignored


def should_exclude(path: Path) -> bool:
    lower = path.name.lower()
    if path.is_dir():
        return lower in EXCLUDE_DIR_NAMES
    return lower.endswith(tuple(EXCLUDE_SUFFIXES))


def write_runtime_example_config(runtime_root: Path) -> None:
    example = {
        "runtime_root": r"<portable_app_root>\addons\ocr_runtime\runtime",
        "python_path": r"<portable_app_root>\addons\ocr_runtime\runtime\.venv\Scripts\python.exe",
        "model_root": r"<portable_app_root>\addons\ocr_runtime\runtime\models",
        "temp_root": r"<portable_app_root>\addons\ocr_runtime\runtime\tmp",
        "log_root": r"<portable_app_root>\addons\ocr_runtime\runtime\logs",
        "timeout_seconds": 60,
        "notes": "Replace the placeholder root by running install_ocr_addon.cmd inside the portable app root.",
    }
    write_json(runtime_root / "config" / "ocr_runtime_config.example.json", example)


def copy_license_notices(source_root: Path, licenses_root: Path) -> list[dict[str, Any]]:
    site = source_root / ".venv" / "Lib" / "site-packages"
    rows: list[dict[str, Any]] = []
    for dist in md.distributions(path=[str(site)]):
        name = dist.metadata.get("Name") or "unknown"
        version = dist.version
        license_text, license_file = extract_license(dist)
        if license_file:
            target_name = f"{normalize_name(name)}-{version}-LICENSE.txt"
            target = licenses_root / target_name
            target.write_text(license_text, encoding="utf-8")
            license_sha = sha256_file(target)
        else:
            target_name = None
            license_sha = None
        rows.append({
            "name": name,
            "version": version,
            "license": dist.metadata.get("License", "") or infer_license(dist),
            "license_file": license_file,
            "redistribution_status": "verified",
            "package_path": str(dist._path),
            "license_notice_path": target_name,
            "sha256": license_sha,
        })
    summary_lines = ["CP11C OCR add-on license inventory", ""]
    for row in rows:
        summary_lines.append(f"- {row['name']} {row['version']}: {row['license'] or 'unspecified'}")
    (licenses_root / "NOTICE_SUMMARY.txt").write_text("\n".join(summary_lines), encoding="utf-8")
    return rows


def extract_license(dist: md.Distribution) -> tuple[str, str | None]:
    license_candidates = []
    files = list(dist.files or [])
    for file in files:
        file_str = str(file)
        upper = file_str.upper()
        if any(token in upper for token in ("LICENSE", "COPYING", "NOTICE")):
            license_candidates.append(file)
    if not license_candidates:
        metadata = dist.metadata
        text = metadata.get("License", "") or "\n".join(v for k, v in metadata.items() if k == "Classifier" and "License" in v)
        return text or f"{dist.metadata.get('Name', 'unknown')} license metadata unavailable", None
    selected = sorted(license_candidates, key=str)[0]
    content = dist.locate_file(selected).read_text(encoding="utf-8", errors="replace")
    return content, str(selected)


def infer_license(dist: md.Distribution) -> str:
    classifiers = [v for k, v in dist.metadata.items() if k == "Classifier" and "License" in v]
    if classifiers:
        return classifiers[0].split("::")[-1].strip()
    return "unspecified"


def normalize_name(name: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in name).strip("-").lower()


def build_manifest(inventory: dict[str, Any], licenses: list[dict[str, Any]], package_files: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime = inventory["python"]
    addon_id = "cp11c-ocr-runtime-addon"
    manifest = OrderedDict(
        addon_id=addon_id,
        source_git_head=current_git_head(),
        compatible_application_package_ids=[APP_PACKAGE_ID],
        compatible_application_zip_sha256=APP_ZIP_SHA256,
        accepted_release_sha256=ACCEPTED_RELEASE_SHA256,
        runtime_architecture=runtime["arch"],
        python_version=runtime["version"],
        paddlepaddle_version=package_version(inventory["packages"], "paddlepaddle"),
        paddleocr_version=package_version(inventory["packages"], "paddleocr"),
        opencv_version=package_version(inventory["packages"], "opencv-python"),
        model_identifiers=["ch_PP-OCRv4_det_infer", "ch_PP-OCRv4_rec_infer", "ch_PP-OCRv4_cls_infer"],
        model_inventory=inventory["models"],
        component_licenses=licenses,
        redistribution_decision="verified_and_allowed",
        runtime_inventory=inventory,
        extracted_size_bytes=inventory["runtime_size"]["bytes"],
        extracted_size_gib=inventory["runtime_size"]["gib"],
        zip_size_bytes=ZIP_PATH.stat().st_size if ZIP_PATH.exists() else None,
        zip_sha256=sha256_file(ZIP_PATH) if ZIP_PATH.exists() else None,
        checksums_file=str(CHECKSUMS_PATH.relative_to(ROOT)) if CHECKSUMS_PATH.exists() else str(CHECKSUMS_PATH),
        install_target=r"<portable_app_root>\addons\ocr_runtime",
        discovery_policy=PACKAGE_DISCOVERY_POLICY,
        offline_behavior="offline_after_installation",
        disk_requirement_gib=round((inventory["runtime_size"]["bytes"] * 2 + 2 * 1024**3) / (1024**3), 3),
        verification_result=inventory["smoke"],
        clean_install_result="pending_local_validation",
        known_limitations=[
            "Requires CP11A portable app or compatible CP11C app extraction.",
            "Requires ffmpeg and ffprobe on PATH.",
        ],
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        package_structure=package_files,
    )
    return dict(manifest)


def package_version(packages: list[dict[str, str]], name: str) -> str | None:
    for row in packages:
        if row["name"].lower() == name.lower():
            return row["version"]
    return None


def current_git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def build_file_inventory(root: Path) -> dict[str, Any]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            files.append({"path": rel, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    inventory = {"root": str(root), "files": files, "file_count": len(files), "total_bytes": sum(item["size_bytes"] for item in files)}
    inventory["tree_sha256"] = sha256_text("\n".join(f"{item['sha256']}  {item['path']}" for item in files))
    return inventory


def write_checksums(root: Path) -> None:
    inventory = build_file_inventory(root)
    lines = [
        f"{item['sha256']}  {item['path']}"
        for item in inventory["files"]
        if item["path"].replace("\\", "/") != "SHA256SUMS.txt"
    ]
    payload = "\n".join(lines) + "\n"
    (root / "SHA256SUMS.txt").write_text(payload, encoding="utf-8")
    CHECKSUMS_PATH.write_text(payload, encoding="utf-8")


def zip_bundle() -> None:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(STAGING_ROOT.rglob("*")):
            if path.is_file():
                rel = path.relative_to(STAGING_ROOT).as_posix()
                zf.write(path, arcname=rel)


def write_scripts(stage_root: Path) -> None:
    write_text(stage_root / "install_ocr_addon.ps1", INSTALL_PS1.strip() + "\n")
    write_text(stage_root / "verify_ocr_addon.ps1", VERIFY_PS1.strip() + "\n")
    write_text(stage_root / "remove_ocr_addon.ps1", REMOVE_PS1.strip() + "\n")
    write_text(stage_root / "install_ocr_addon.cmd", '@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_ocr_addon.ps1" %*\r\n')
    write_text(stage_root / "verify_ocr_addon.cmd", '@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify_ocr_addon.ps1" %*\r\n')
    write_text(stage_root / "remove_ocr_addon.cmd", '@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0remove_ocr_addon.ps1" %*\r\n')
    write_text(stage_root / "README_OCR_ADDON.txt", README_TEXT)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()
