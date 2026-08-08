from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.preflight import PACKAGE_SAFETY_RESERVE_BYTES, storage_preflight
from app.services.asr_models import (
    SIMPLE_UI_MODEL_NAME,
    model_directory_name,
    model_family_name,
    resolve_local_model_path as resolve_asr_model_path,
)


CP12B_ZIP = ROOT / "release" / "CP12B" / "tool_auto_sub_windows_full_portable_cp12b.zip"
CP12B_SHA = "9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0"
ACCEPTED_MP4 = ROOT / "data" / "projects" / "vertical_slice_cp07" / "renders" / "cp08e2_decoupled_suppression_english_plate_720p.mp4"
ACCEPTED_SHA = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"
CP13A_INSTALLER = ROOT / "release" / "CP13A" / "ToolAutoSubBetaSetup_CP13A.exe"
CP13A_SHA = "eeae0d2d0d67374691166e2bc80fc961deab47c7a258d610913283eb74ade4da"
RELEASE_DIR = ROOT / "release" / "CP13A1"
INSTALLER = RELEASE_DIR / "ToolAutoSubBetaSetup_CP13A1.exe"
EXPECTED_MANIFEST = RELEASE_DIR / "EXPECTED_INSTALL_MANIFEST.json"
RELEASE_ID = "CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX"
EVIDENCE_ROOT = ROOT / "evidence" / "CP13A1"
OLD_DEFECTIVE_INSTALLER_SHA = "e13d6a40d918b74423a457f34f9f0ac212df6e71ac3dd4075959f9621eb7fec0"
FFMPEG_ROOT = Path(os.environ.get("CP13A_FFMPEG_ROOT", r"C:\Users\ADMIN\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build"))
ASR_MODEL_NAME = SIMPLE_UI_MODEL_NAME
ASR_MODEL_ID = model_family_name(ASR_MODEL_NAME)
ASR_MODEL_SOURCE = f"https://huggingface.co/{ASR_MODEL_ID}"
ASR_MODEL_REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
ASR_REQUIREMENTS = ROOT / "requirements-asr.lock.txt"
ASR_LICENSE_SOURCE = ROOT / "licenses" / "faster-whisper-small-MIT.txt"
ASR_LICENSE_TARGET_NAME = f"{model_directory_name(ASR_MODEL_NAME)}-MIT.txt"
TRANSLATION_MODEL_ID = "translate-zh_en-1_9"
TRANSLATION_REQUIREMENTS = ROOT / "requirements-translation.lock.txt"
TRANSLATION_LICENSE_SOURCE = ROOT / "licenses" / "ARGOS_ZH_EN_NOTICE.txt"
TRANSLATION_PACKAGE_SOURCE = (
    Path(os.environ.get("CP13A_TRANSLATION_PACKAGES_ROOT", r"C:\Users\ADMIN\AppData\Local\ToolAutoSubTranslation\packages"))
    / TRANSLATION_MODEL_ID
)
SFX_PAYLOAD_MAGIC = b"TOOL_AUTO_SUB_CP13A1_PAYLOAD_V1"
UNINSTALL_REGISTRY_BASE = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
PRODUCTION_UNINSTALL_KEY_LEAF = "ToolAutoSubBeta"
VALIDATION_UNINSTALL_KEY_PREFIX = "ToolAutoSubBetaValidation_"
VALIDATION_UNINSTALL_KEY_MAX_LENGTH = 96
UNINSTALL_REGISTRY_SNAPSHOT_VALUES = (
    "DisplayName",
    "DisplayVersion",
    "InstallLocation",
    "UninstallString",
    "Publisher",
)


def validate_validation_uninstall_key_leaf(value: str) -> str:
    leaf = str(value)
    if (
        not leaf.startswith(VALIDATION_UNINSTALL_KEY_PREFIX)
        or len(leaf) > VALIDATION_UNINSTALL_KEY_MAX_LENGTH
        or re.fullmatch(r"[A-Za-z0-9._-]+", leaf) is None
    ):
        raise ValueError("Invalid validation uninstall registry key leaf")
    return leaf


def create_validation_uninstall_key_leaf() -> str:
    return validate_validation_uninstall_key_leaf(f"{VALIDATION_UNINSTALL_KEY_PREFIX}{uuid.uuid4().hex}")


def uninstall_registry_key_path(*, validation_mode: bool = False, validation_key_leaf: str | None = None) -> str:
    leaf = (
        validate_validation_uninstall_key_leaf(validation_key_leaf or "")
        if validation_mode
        else PRODUCTION_UNINSTALL_KEY_LEAF
    )
    return f"{UNINSTALL_REGISTRY_BASE}\\{leaf}"


def _registry_snapshot_for_leaf(key_leaf: str) -> dict:
    if key_leaf == PRODUCTION_UNINSTALL_KEY_LEAF:
        registry_path = uninstall_registry_key_path()
    else:
        registry_path = uninstall_registry_key_path(validation_mode=True, validation_key_leaf=key_leaf)
    script = (
        f"$path='{registry_path}'; "
        "if (Test-Path -LiteralPath $path) { "
        "$item=Get-ItemProperty -LiteralPath $path; "
        "[ordered]@{exists=$true;values=[ordered]@{"
        + ";".join(f"{name}=$item.{name}" for name in UNINSTALL_REGISTRY_SNAPSHOT_VALUES)
        + "}} | ConvertTo-Json -Compress "
        "} else { [ordered]@{exists=$false;values=[ordered]@{}} | ConvertTo-Json -Compress }"
    )
    payload = json.loads(subprocess.check_output(["powershell", "-NoProfile", "-Command", script], text=True))
    return {
        "exists": bool(payload.get("exists")),
        "values": dict(payload.get("values") or {}),
    }


def _remove_validation_registry_key(key_leaf: str) -> None:
    registry_path = uninstall_registry_key_path(validation_mode=True, validation_key_leaf=key_leaf)
    script = f"if (Test-Path -LiteralPath '{registry_path}') {{ Remove-Item -LiteralPath '{registry_path}' -Recurse -Force -ErrorAction Stop }}"
    completed = subprocess.run(["powershell", "-NoProfile", "-Command", script], text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError("Failed to remove validation uninstall registry key")


def _assert_production_registry_snapshot(expected: dict, actual: dict) -> None:
    if actual != expected:
        raise RuntimeError("Production uninstall registry snapshot changed during validation")


def validation_registry_environment(key_leaf: str) -> dict[str, str]:
    return {
        "CP13A_VALIDATION_MODE": "1",
        "CP13A_VALIDATION_UNINSTALL_KEY_LEAF": validate_validation_uninstall_key_leaf(key_leaf),
    }


def cleanup_validation_uninstall_registry_key(key_leaf: str) -> None:
    validated_leaf = validate_validation_uninstall_key_leaf(key_leaf)
    _remove_validation_registry_key(validated_leaf)
    if _registry_snapshot_for_leaf(validated_leaf)["exists"]:
        raise RuntimeError("Validation uninstall registry key remains after cleanup")

MIN_INSTALLED_FILE_COUNT = 12_000
MIN_INSTALLED_SIZE_BYTES = 1_250_000_000
INSTALL_VALIDATION_TIMEOUT_SECONDS = int(os.environ.get("CP13A_INSTALL_VALIDATION_TIMEOUT_SECONDS", "2400"))

def asr_required_components(model_name: str) -> list[dict]:
    model_dir = model_directory_name(model_name)
    license_name = f"{model_dir}-MIT.txt"
    return [
        {"component": "offline_asr_model", "relative_path": f"models/{model_dir}/model.bin", "type": "file", "critical": True},
        {"component": "offline_asr_model_metadata", "relative_path": f"models/{model_dir}/MODEL_METADATA.json", "type": "file", "critical": True},
        {"component": "offline_asr_license", "relative_path": f"licenses/{license_name}", "type": "file", "critical": True},
    ]


REQUIRED_COMPONENTS = [
    {"component": "main_launcher", "relative_path": "ToolAutoSubBeta.cmd", "type": "file", "critical": True},
    {"component": "launcher_script", "relative_path": "beta/ToolAutoSubBeta.ps1", "type": "file", "critical": True},
    {"component": "runtime_entry", "relative_path": "beta/ToolAutoSubBetaRuntime.py", "type": "file", "critical": True},
    {"component": "backend_entry_point", "relative_path": "app/main.py", "type": "file", "critical": True},
    {"component": "release_local_python", "relative_path": "runtime/venv/Scripts/python.exe", "type": "file", "critical": True},
    {"component": "offline_asr_package", "relative_path": "runtime/venv/Lib/site-packages/faster_whisper/__init__.py", "type": "file", "critical": True},
    {"component": "offline_asr_runtime", "relative_path": "runtime/venv/Lib/site-packages/ctranslate2/__init__.py", "type": "file", "critical": True},
    {"component": "subtitle_font_runtime", "relative_path": "runtime/venv/Lib/site-packages/PIL/__init__.py", "type": "file", "critical": True},
    {"component": "simple_ui_entry", "relative_path": "app/static/simple/index.html", "type": "file", "critical": True},
    {"component": "operator_ui_entry", "relative_path": "app/static/operator/index.html", "type": "file", "critical": True},
    {"component": "ffmpeg_executable", "relative_path": "ffmpeg/bin/ffmpeg.exe", "type": "file", "critical": True},
    {"component": "ffprobe_executable", "relative_path": "ffmpeg/bin/ffprobe.exe", "type": "file", "critical": True},
    {"component": "migration_head", "relative_path": "alembic/versions/0009_subtitle_tracks.py", "type": "file", "critical": True},
    {"component": "diagnostics_launcher", "relative_path": "CollectDiagnostics.cmd", "type": "file", "critical": True},
    {"component": "release_manifest", "relative_path": "release/CP13A1/INSTALL_RELEASE_MANIFEST.json", "type": "file", "critical": True},
    {"component": "ocr_runtime_manifest", "relative_path": "addons/ocr_runtime/addon_manifest.json", "type": "file", "critical": True},
    {"component": "gemini_translation_config", "relative_path": "operator/translation_config.env", "type": "file", "critical": True},
    {"component": "offline_translation_config", "relative_path": "operator/translation_runtime_config.local.json", "type": "file", "critical": True},
    {"component": "offline_translation_worker", "relative_path": "tools/offline_translation_worker.py", "type": "file", "critical": True},
    {"component": "offline_translation_package", "relative_path": f"addons/translation_runtime/packages/{TRANSLATION_MODEL_ID}/model/model.bin", "type": "file", "critical": True},
    {"component": "offline_translation_license", "relative_path": "licenses/ARGOS_ZH_EN_NOTICE.txt", "type": "file", "critical": True},
    {"component": "backend_package", "relative_path": "app", "type": "directory", "critical": True},
    {"component": "migration_directory", "relative_path": "alembic", "type": "directory", "critical": True},
    {"component": "optional_addons", "relative_path": "addons", "type": "directory", "critical": False},
] + asr_required_components(ASR_MODEL_NAME)


INSTALL_CMD = """@echo off
setlocal
if /I "%~1"=="/Q" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Quiet
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
)
set "CP13A_INSTALL_EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %CP13A_INSTALL_EXIT_CODE%
"""

ROOT_LAUNCHER_CMD = """@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0beta\\ToolAutoSubBeta.ps1"
exit /b %ERRORLEVEL%
"""

ROOT_STOP_CMD = """@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0beta\\ToolAutoSubBeta.ps1" -Action Stop
exit /b %ERRORLEVEL%
"""

ROOT_RESTART_CMD = """@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0beta\\ToolAutoSubBeta.ps1" -Action Restart
exit /b %ERRORLEVEL%
"""

ROOT_DIAGNOSTICS_CMD = """@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0beta\\CollectDiagnostics.ps1"
exit /b %ERRORLEVEL%
"""

BOOTSTRAP_CS = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;

public static class ToolAutoSubBetaSetup
{
    private static readonly byte[] Magic = Encoding.ASCII.GetBytes("TOOL_AUTO_SUB_CP13A1_PAYLOAD_V1");

    public static int Main(string[] args)
    {
        bool quiet = args.Any(arg => string.Equals(arg, "/Q", StringComparison.OrdinalIgnoreCase)
            || string.Equals(arg, "/QUIET", StringComparison.OrdinalIgnoreCase));
        string extractRoot = Path.Combine(Path.GetTempPath(), "ToolAutoSubBetaSetup_CP13A1_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(extractRoot);
        string payloadZip = Path.Combine(extractRoot, "installer_payload.zip");
        try
        {
            ExtractOverlayPayload(payloadZip);
            ZipFile.ExtractToDirectory(payloadZip, extractRoot);
            string installCmd = Path.Combine(extractRoot, "install.cmd");
            if (!File.Exists(installCmd))
            {
                throw new FileNotFoundException("Installer payload is missing install.cmd.", installCmd);
            }
            string childArguments = "/c " + Quote(installCmd) + (quiet ? " /Q" : "");
            var startInfo = new ProcessStartInfo("cmd.exe", childArguments)
            {
                UseShellExecute = false,
                CreateNoWindow = quiet,
                WorkingDirectory = extractRoot,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            using (var child = new Process())
            {
                child.StartInfo = startInfo;
                child.OutputDataReceived += (sender, eventArgs) => { if (eventArgs.Data != null) Console.Out.WriteLine(eventArgs.Data); };
                child.ErrorDataReceived += (sender, eventArgs) => { if (eventArgs.Data != null) Console.Error.WriteLine(eventArgs.Data); };
                child.Start();
                child.BeginOutputReadLine();
                child.BeginErrorReadLine();
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception exc)
        {
            Console.Error.WriteLine(exc.ToString());
            return 1;
        }
        finally
        {
            try { Directory.Delete(extractRoot, true); } catch { }
        }
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private static void ExtractOverlayPayload(string destination)
    {
        string exePath = Process.GetCurrentProcess().MainModule.FileName;
        using (var input = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            if (input.Length < Magic.Length + 8)
            {
                throw new InvalidDataException("Installer payload marker is missing.");
            }
            input.Seek(-8, SeekOrigin.End);
            byte[] lengthBytes = new byte[8];
            ReadExactly(input, lengthBytes, 0, lengthBytes.Length);
            long payloadLength = BitConverter.ToInt64(lengthBytes, 0);
            long magicOffset = input.Length - 8 - Magic.Length;
            long payloadOffset = magicOffset - payloadLength;
            if (payloadLength <= 0 || payloadOffset <= 0)
            {
                throw new InvalidDataException("Installer payload length is invalid.");
            }
            input.Seek(magicOffset, SeekOrigin.Begin);
            byte[] actualMagic = new byte[Magic.Length];
            ReadExactly(input, actualMagic, 0, actualMagic.Length);
            if (!actualMagic.SequenceEqual(Magic))
            {
                throw new InvalidDataException("Installer payload marker is invalid.");
            }
            input.Seek(payloadOffset, SeekOrigin.Begin);
            using (var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                CopyExactly(input, output, payloadLength);
            }
        }
    }

    private static void ReadExactly(Stream stream, byte[] buffer, int offset, int count)
    {
        int remaining = count;
        while (remaining > 0)
        {
            int read = stream.Read(buffer, offset, remaining);
            if (read <= 0) throw new EndOfStreamException();
            offset += read;
            remaining -= read;
        }
    }

    private static void CopyExactly(Stream input, Stream output, long byteCount)
    {
        byte[] buffer = new byte[1024 * 1024];
        long remaining = byteCount;
        while (remaining > 0)
        {
            int toRead = (int)Math.Min(buffer.Length, remaining);
            int read = input.Read(buffer, 0, toRead);
            if (read <= 0) throw new EndOfStreamException();
            output.Write(buffer, 0, read);
            remaining -= read;
        }
    }
}
'''

RUNTIME_PY = r'''from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parents[1]
os.chdir(INSTALL_ROOT)
if str(INSTALL_ROOT) not in sys.path:
    sys.path.insert(0, str(INSTALL_ROOT))

import uvicorn

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
'''

BETA_GUIDE_TEMPLATE = """Tool Auto Sub Beta CP13A1 - 5 minute external-machine checklist

Installer verification
1. Put `ToolAutoSubBetaSetup_CP13A1.exe` in a local folder on the beta Windows machine.
2. Run this PowerShell command in that folder:

   Get-FileHash -Algorithm SHA256 .\\ToolAutoSubBetaSetup_CP13A1.exe

3. Expected size: {installer_size} bytes.
4. Expected SHA-256: {installer_sha256}
5. If the hash does not match, stop and do not install.

Install
1. Double-click `ToolAutoSubBetaSetup_CP13A1.exe`.
2. Expected install path: `%LOCALAPPDATA%\\Programs\\ToolAutoSubBeta`.
3. Expected user-data path: `%LOCALAPPDATA%\\ToolAutoSubBeta`.
4. Expected Start Menu shortcuts:
   - Tool Auto Sub Beta
   - Stop Tool Auto Sub Beta
   - Restart Tool Auto Sub Beta
   - Collect Diagnostics
   - Uninstall Tool Auto Sub Beta

Launch
1. Launch Tool Auto Sub Beta from the Start Menu.
2. Open `http://127.0.0.1:8173/`.
3. Confirm the Simple UI loads.
4. Run Collect Diagnostics from the Start Menu.

Stop
1. Use the Stop Tool Auto Sub Beta Start Menu shortcut.
2. Confirm the app is no longer reachable at `http://127.0.0.1:8173/`.

Reinstall/reuse
1. Run the installer a second time.
2. Confirm the same five Start Menu shortcuts exist and are not duplicated.
3. Confirm any local project/user data remains present.

Uninstall
1. Use the Uninstall Tool Auto Sub Beta Start Menu shortcut.
2. Confirm the install path is removed.
3. Confirm the Start Menu shortcuts are removed.
4. Confirm user data is preserved unless explicitly removed by the tester.

Return this evidence
- Windows version.
- Installer hash output.
- Installer log and launcher/bootstrap log.
- List of the five Start Menu shortcuts.
- Uninstall shortcut target, arguments, and working directory.
- Diagnostics ZIP.
- Local URL result.
- Stop result.
- Reinstall result.
- Uninstall result.
- Install-root removal result.
- Shortcut removal result.
- User-data preservation result.
- Screenshot or exact error text if anything fails.

Safety
- Do not enter Gemini, ElevenLabs, or YouTube keys.
- Do not upload or publish.
- CP12B Full Portable remains canonical until this beta is separately accepted.
"""


def beta_guide(installer_size: int | str, installer_sha256: str) -> str:
    return BETA_GUIDE_TEMPLATE.format(installer_size=installer_size, installer_sha256=installer_sha256)

UNINSTALL_REGISTRY_RESOLUTION_PS1 = r'''
$uninstallRegistryBase = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
$validationMode = $env:CP13A_VALIDATION_MODE -eq "1"
if ($validationMode) {
  $validationUninstallLeaf = [string]$env:CP13A_VALIDATION_UNINSTALL_KEY_LEAF
  if (
    [string]::IsNullOrWhiteSpace($validationUninstallLeaf) -or
    $validationUninstallLeaf.Length -gt 96 -or
    -not $validationUninstallLeaf.StartsWith("ToolAutoSubBetaValidation_") -or
    $validationUninstallLeaf -notmatch '^[A-Za-z0-9._-]+$'
  ) {
    throw "Invalid validation uninstall registry key leaf"
  }
  $uninstallLeaf = $validationUninstallLeaf
} else {
  $uninstallLeaf = "ToolAutoSubBeta"
}
$uninstallKey = Join-Path $uninstallRegistryBase $uninstallLeaf
'''


INSTALL_PS1 = r'''
param(
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms

$payloadRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$installDir = if ($env:CP13A_INSTALL_DIR) { $env:CP13A_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\ToolAutoSubBeta" }
$userRoot = if ($env:CP13A_USERDATA_DIR) { $env:CP13A_USERDATA_DIR } else { Join-Path $env:LOCALAPPDATA "ToolAutoSubBeta" }
$logDir = Join-Path $userRoot "logs"
$diagDir = Join-Path $userRoot "diagnostics"
$bootstrapLog = Join-Path $logDir "launcher_bootstrap.log"
$installAttemptId = [guid]::NewGuid().ToString("N")
$silent = $Quiet.IsPresent -or $env:CP13A_INSTALL_SILENT -eq "1"
$createDesktop = $env:CP13A_CREATE_DESKTOP_SHORTCUT -eq "1"
$launchAfter = $env:CP13A_LAUNCH_AFTER_INSTALL -eq "1"
''' + UNINSTALL_REGISTRY_RESOLUTION_PS1 + r'''

New-Item -ItemType Directory -Force $installDir,$userRoot,(Join-Path $userRoot "data"),$logDir,$diagDir | Out-Null

function Write-Bootstrap($Message) {
  ("{0} {1}" -f (Get-Date).ToString("o"), $Message) | Add-Content -Path $bootstrapLog -Encoding UTF8
}

function Expand-Zip($ZipPath, $DestinationPath) {
  New-Item -ItemType Directory -Force $DestinationPath | Out-Null
  $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
  if ($tar) {
    & $tar.Source -xf $ZipPath -C $DestinationPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to extract archive with tar.exe: $ZipPath" }
    return
  }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
  try {
    foreach ($entry in $archive.Entries) {
      if (-not $entry.FullName -or $entry.FullName.EndsWith("/")) { continue }
      $target = Join-Path $DestinationPath $entry.FullName
      $targetDir = Split-Path -Parent $target
      New-Item -ItemType Directory -Force $targetDir | Out-Null
      [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
    }
  } finally {
    $archive.Dispose()
  }
}

function Get-TreeSummary($Root) {
  $files = Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue
  $size = ($files | Measure-Object Length -Sum).Sum
  if (-not $size) { $size = 0 }
  return @{ file_count = @($files).Count; total_size_bytes = [int64]$size }
}

function Validate-InstalledPayload($Root, $ManifestPath) {
  $manifest = Get-Content -Raw -Path $ManifestPath | ConvertFrom-Json
  $missing = New-Object System.Collections.Generic.List[string]
  foreach ($entry in $manifest.components) {
    if (-not $entry.required) { continue }
    $target = Join-Path $Root $entry.relative_path
    if ($entry.type -eq "directory") {
      if (-not (Test-Path -LiteralPath $target -PathType Container)) { $missing.Add($entry.component) }
    } else {
      if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
        $missing.Add($entry.component)
      } elseif ($entry.sha256) {
        $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne ([string]$entry.sha256).ToLowerInvariant()) { $missing.Add($entry.component + "_hash") }
      }
    }
  }
  $summary = Get-TreeSummary $Root
  if ($summary.file_count -lt [int]$manifest.minimum_file_count) { $missing.Add("installed_file_count_too_low") }
  if ($summary.total_size_bytes -lt [int64]$manifest.minimum_total_size_bytes) { $missing.Add("installed_size_too_low") }
  $rootItems = @(Get-ChildItem -LiteralPath $Root -Force -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
  if ($rootItems.Count -eq 1 -and $rootItems[0] -eq "addons") { $missing.Add("installed_root_addons_only") }
  return @{ passed = ($missing.Count -eq 0); missing = @($missing); summary = $summary }
}

try {
  Write-Bootstrap "install_attempt_id=$installAttemptId phase=BEGIN quiet=$silent"
  Write-Bootstrap "install_attempt_id=$installAttemptId installer_release_id=CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX"
  Write-Bootstrap "install_attempt_id=$installAttemptId install_root=$installDir"
  $payloadZip = Join-Path $payloadRoot "cp13a1_complete_payload.zip"
  $expectedManifest = Join-Path $payloadRoot "EXPECTED_INSTALL_MANIFEST.json"
  foreach ($required in @($payloadZip,$expectedManifest)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Installer payload is incomplete: $required" }
  }

  if (-not $silent) {
    $message = "Tool Auto Sub Beta hotfix will be installed for this Windows user.`n`nInstall:`n$installDir`n`nProjects and logs:`n$userRoot`n`nGemini, ElevenLabs and upload/publish are disabled."
    $choice = [System.Windows.Forms.MessageBox]::Show($message, "Tool Auto Sub Beta installer", "OKCancel", "Information")
    if ($choice -ne "OK") { exit 1 }
    $createDesktop = [System.Windows.Forms.MessageBox]::Show("Create a Desktop shortcut?", "Tool Auto Sub Beta", "YesNo", "Question") -eq "Yes"
    $launchAfter = [System.Windows.Forms.MessageBox]::Show("Launch Tool Auto Sub after installation?", "Tool Auto Sub Beta", "YesNo", "Question") -eq "Yes"
  }

  $modelRoot = Join-Path $installDir "models"
  foreach ($legacyModel in @("faster-whisper-tiny","faster-whisper-base")) {
    $legacyPath = Join-Path $modelRoot $legacyModel
    if (Test-Path -LiteralPath $legacyPath -PathType Container) {
      Remove-Item -LiteralPath $legacyPath -Recurse -Force
      Write-Bootstrap "install_attempt_id=$installAttemptId removed_legacy_model=$legacyModel"
    }
  }
  Expand-Zip $payloadZip $installDir
  Copy-Item -LiteralPath $expectedManifest -Destination (Join-Path $installDir "EXPECTED_INSTALL_MANIFEST.json") -Force
  $validation = Validate-InstalledPayload $installDir $expectedManifest
  Write-Bootstrap ("installed_payload_validation=" + ($validation | ConvertTo-Json -Depth 6 -Compress))
  if (-not $validation.passed) {
    $missingText = ($validation.missing -join ", ")
    throw "Cai dat Tool Auto Sub chua day du. Missing: $missingText"
  }

  $betaDir = Join-Path $installDir "beta"
  $launcher = Join-Path $installDir "ToolAutoSubBeta.cmd"
  if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw "Installed launcher is missing." }

  $wsh = New-Object -ComObject WScript.Shell
  $startMenu = if ($env:CP13A_START_MENU_DIR) { $env:CP13A_START_MENU_DIR } else { Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Tool Auto Sub Beta" }
  $desktopDir = if ($env:CP13A_DESKTOP_DIR) { $env:CP13A_DESKTOP_DIR } else { [Environment]::GetFolderPath("Desktop") }
  New-Item -ItemType Directory -Force $startMenu,$desktopDir | Out-Null
  function Resolve-ShortcutTarget($Target) {
    if ([System.IO.Path]::IsPathRooted($Target)) {
      if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) { throw "Shortcut target is missing: $Target" }
      return (Get-Item -LiteralPath $Target).FullName
    }
    $command = Get-Command $Target -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command -or [string]::IsNullOrWhiteSpace($command.Source)) {
      throw "Shortcut target cannot be resolved: $Target"
    }
    return $command.Source
  }
  function New-Shortcut($Path, $Target, $Arguments = "", $WorkingDirectory = "") {
    $resolvedTarget = Resolve-ShortcutTarget $Target
    if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) { throw "Shortcut working directory is missing: $WorkingDirectory" }
    $shortcut = $wsh.CreateShortcut($Path)
    $shortcut.TargetPath = $resolvedTarget
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Save()
  }
  New-Shortcut (Join-Path $startMenu "Tool Auto Sub Beta.lnk") $launcher "" $installDir
  New-Shortcut (Join-Path $startMenu "Stop Tool Auto Sub Beta.lnk") (Join-Path $installDir "StopToolAutoSubBeta.cmd") "" $installDir
  New-Shortcut (Join-Path $startMenu "Restart Tool Auto Sub Beta.lnk") (Join-Path $installDir "RestartToolAutoSubBeta.cmd") "" $installDir
  New-Shortcut (Join-Path $startMenu "Collect Diagnostics.lnk") (Join-Path $installDir "CollectDiagnostics.cmd") "" $installDir
  New-Shortcut (Join-Path $startMenu "Uninstall Tool Auto Sub Beta.lnk") "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$betaDir\UninstallToolAutoSubBeta.ps1`"" $userRoot
  if ($createDesktop) { New-Shortcut (Join-Path $desktopDir "Tool Auto Sub Beta.lnk") $launcher "" $installDir }

  New-Item -Path $uninstallKey -Force | Out-Null
  Set-ItemProperty -Path $uninstallKey -Name DisplayName -Value "Tool Auto Sub Beta"
  Set-ItemProperty -Path $uninstallKey -Name DisplayVersion -Value "CP13A1"
  Set-ItemProperty -Path $uninstallKey -Name Publisher -Value "Tool Auto Sub"
  Set-ItemProperty -Path $uninstallKey -Name InstallLocation -Value $installDir
  Set-ItemProperty -Path $uninstallKey -Name UninstallString -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$betaDir\UninstallToolAutoSubBeta.ps1`""

  Set-Content -Path (Join-Path $userRoot "install_state.json") -Encoding UTF8 -Value (@{
    schema_version = 1
    release_id = "CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX"
    install_dir = $installDir
    user_data_root = $userRoot
    installed_at = (Get-Date).ToString("o")
    providers = @{ gemini = "disabled"; elevenlabs = "disabled"; youtube = "disabled" }
  } | ConvertTo-Json -Depth 5)

  Write-Bootstrap "install_attempt_id=$installAttemptId install_result=PASS"
  if ($launchAfter) { Start-Process -FilePath $launcher -WorkingDirectory $installDir }
  if (-not $silent) {
    [System.Windows.Forms.MessageBox]::Show("Installation complete. You can launch Tool Auto Sub Beta from the Start Menu.", "Tool Auto Sub Beta", "OK", "Information") | Out-Null
  }
} catch {
  Write-Bootstrap ("install_attempt_id=$installAttemptId install_result=FAIL " + $_.Exception.Message)
  if ($silent -or $env:CP13A_HEADLESS -eq "1") {
    Write-Error $_.Exception.Message
  } else {
    [System.Windows.Forms.MessageBox]::Show("Cai dat Tool Auto Sub chua day du.`n`n$($_.Exception.Message)`n`nDiagnostics:`n$diagDir", "Tool Auto Sub Beta installer", "OK", "Error") | Out-Null
  }
  exit 1
}
'''

LAUNCHER_PS1 = r'''
param(
  [ValidateSet("Open","Stop","Restart")]
  [string]$Action = "Open"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms

$installDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$userRoot = if ($env:CP13A_USERDATA_DIR) { $env:CP13A_USERDATA_DIR } else { Join-Path $env:LOCALAPPDATA "ToolAutoSubBeta" }
$dataDir = Join-Path $userRoot "data"
$logDir = Join-Path $userRoot "logs"
$diagDir = Join-Path $userRoot "diagnostics"
$statePath = Join-Path $userRoot "runtime_state.json"
$bootstrapLog = Join-Path $logDir "launcher_bootstrap.log"
New-Item -ItemType Directory -Force $dataDir,$logDir,$diagDir | Out-Null

function Write-Bootstrap($Message) {
  ("{0} {1}" -f (Get-Date).ToString("o"), $Message) | Add-Content -Path $bootstrapLog -Encoding UTF8
}

function Show-FriendlyError($Reason) {
  Write-Bootstrap ("startup_result=FAIL " + $Reason)
  $message = "$Reason`n`nLog folder:`n$logDir`n`nRun Collect Diagnostics from the Start Menu and send the ZIP to support."
  if ($env:CP13A_HEADLESS -eq "1") { Write-Error $message } else { [System.Windows.Forms.MessageBox]::Show($message, "Tool Auto Sub Beta startup", "OK", "Error") | Out-Null }
}

function Read-State {
  if (Test-Path $statePath) {
    try { return Get-Content -Raw $statePath | ConvertFrom-Json } catch { return $null }
  }
  return $null
}

function Write-State($Payload) {
  $Payload | ConvertTo-Json -Depth 5 | Set-Content -Path $statePath -Encoding UTF8
}

function Get-CommandLine($PidValue) {
  try { return (Get-CimInstance Win32_Process -Filter "ProcessId=$PidValue").CommandLine } catch { return $null }
}

function Get-ExecutablePath($PidValue) {
  try { return (Get-CimInstance Win32_Process -Filter "ProcessId=$PidValue").ExecutablePath } catch { return $null }
}

function Test-ContainsPath($Value, $ExpectedPath) {
  if (-not $Value -or -not $ExpectedPath) { return $false }
  return $Value.IndexOf($ExpectedPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-OwnedProcess($PidValue) {
  $cmd = Get-CommandLine $PidValue
  $exe = Get-ExecutablePath $PidValue
  $runtimeEntry = Join-Path $installDir "beta\ToolAutoSubBetaRuntime.py"
  $runtimeCommand = (Test-ContainsPath $cmd $runtimeEntry) -or ((Test-ContainsPath $cmd $installDir) -and $cmd -and $cmd.Contains("app.main:app"))
  $installedExecutable = Test-ContainsPath $exe $installDir
  return $runtimeCommand -or ($installedExecutable -and $runtimeCommand)
}

function Get-OwnedRuntimePids {
  $owned = @()
  $runtimeEntry = Join-Path $installDir "beta\ToolAutoSubBetaRuntime.py"
  try {
    foreach ($item in Get-CimInstance Win32_Process) {
      $runtimeCommand = (Test-ContainsPath $item.CommandLine $runtimeEntry) -or ((Test-ContainsPath $item.CommandLine $installDir) -and $item.CommandLine -and $item.CommandLine.Contains("app.main:app"))
      if ($item.ProcessId -and $runtimeCommand) {
        $owned += [int]$item.ProcessId
      }
    }
  } catch {}
  return @($owned | Select-Object -Unique)
}

function Test-ProcessExists($PidValue) {
  return [bool](Get-Process -Id $PidValue -ErrorAction SilentlyContinue)
}

function Wait-ProcessesExit($PidValues, $TimeoutSeconds) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    $remaining = @($PidValues | Where-Object { Test-ProcessExists ([int]$_) })
    if ($remaining.Count -eq 0) { return @() }
    Start-Sleep -Milliseconds 100
  } while ((Get-Date) -lt $deadline)
  return @($PidValues | Where-Object { Test-ProcessExists ([int]$_) })
}

function Test-PortOpen($HostName, $Port) {
  try {
    $client = New-Object Net.Sockets.TcpClient
    $async = $client.BeginConnect($HostName, $Port, $null, $null)
    if ($async.AsyncWaitHandle.WaitOne(250, $false)) { $client.EndConnect($async); $client.Close(); return $true }
    $client.Close()
  } catch {}
  return $false
}

function Get-ListeningPid($HostName, $Port) {
  try {
    $connection = Get-NetTCPConnection -LocalAddress $HostName -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($connection) { return [int]$connection.OwningProcess }
  } catch {}
  return $null
}

function Stop-App {
  $stopAttemptId = [guid]::NewGuid().ToString("N")
  $state = Read-State
  $statePid = if ($state -and $state.pid) { [int]$state.pid } else { 0 }
  $launcherPid = if ($state -and $state.launcher_pid) { [int]$state.launcher_pid } else { 0 }
  $targetPort = if ($state -and $state.port) { [int]$state.port } else { 8173 }
  $listenerPid = Get-ListeningPid "127.0.0.1" $targetPort
  Write-Bootstrap "stop_attempt_id=$stopAttemptId phase=BEGIN state_pid=$statePid launcher_pid=$launcherPid listener_pid=$listenerPid port=$targetPort"

  $candidateMap = @{}
  foreach ($candidate in @($listenerPid, $statePid, $launcherPid) + @(Get-OwnedRuntimePids)) {
    if ($candidate -and [int]$candidate -gt 0) { $candidateMap[[string][int]$candidate] = [int]$candidate }
  }

  $verified = @()
  foreach ($candidate in $candidateMap.Values) {
    $exists = Test-ProcessExists $candidate
    $owned = $exists -and (Test-OwnedProcess $candidate)
    Write-Bootstrap "stop_attempt_id=$stopAttemptId identity_pid=$candidate exists=$exists owned=$owned"
    if ($owned) { $verified += [int]$candidate }
  }
  $verified = @($verified | Select-Object -Unique)

  $initialOrder = @()
  if ($listenerPid -and $verified -contains [int]$listenerPid) { $initialOrder += [int]$listenerPid }
  $initialOrder += @($verified | Where-Object { -not $listenerPid -or $_ -ne [int]$listenerPid })
  $initialOrder = @($initialOrder | Select-Object -Unique)

  $simulateFailure = $env:CP13A_LIFECYCLE_TEST_MODE -eq "1" -and $env:CP13A_STOP_TEST_SIMULATE_FAILURE -eq "1"
  foreach ($pidValue in $initialOrder) {
    if ($simulateFailure) {
      Write-Bootstrap "stop_attempt_id=$stopAttemptId stop_method=TEST_SIMULATED_FAILURE pid=$pidValue"
      continue
    }
    try {
      Stop-Process -Id $pidValue -ErrorAction Stop
      Write-Bootstrap "stop_attempt_id=$stopAttemptId stop_method=TERMINATE pid=$pidValue request=PASS"
    } catch {
      Write-Bootstrap "stop_attempt_id=$stopAttemptId stop_method=TERMINATE pid=$pidValue request=FAIL"
    }
  }

  $remaining = @(Wait-ProcessesExit $initialOrder 5)
  Write-Bootstrap "stop_attempt_id=$stopAttemptId graceful_wait_remaining=$($remaining.Count)"
  if ($remaining.Count -gt 0 -and -not $simulateFailure) {
    foreach ($pidValue in $remaining) {
      if (Test-OwnedProcess $pidValue) {
        try {
          Stop-Process -Id $pidValue -Force -ErrorAction Stop
          Write-Bootstrap "stop_attempt_id=$stopAttemptId escalation=FORCE pid=$pidValue request=PASS"
        } catch {
          Write-Bootstrap "stop_attempt_id=$stopAttemptId escalation=FORCE pid=$pidValue request=FAIL"
        }
      }
    }
    $remaining = @(Wait-ProcessesExit $remaining 5)
  }

  $finalListenerPid = Get-ListeningPid "127.0.0.1" $targetPort
  if ($finalListenerPid -and (Test-OwnedProcess $finalListenerPid) -and -not $simulateFailure) {
    try {
      Stop-Process -Id $finalListenerPid -Force -ErrorAction Stop
      Write-Bootstrap "stop_attempt_id=$stopAttemptId escalation=FORCE_PORT_OWNER pid=$finalListenerPid request=PASS"
    } catch {
      Write-Bootstrap "stop_attempt_id=$stopAttemptId escalation=FORCE_PORT_OWNER pid=$finalListenerPid request=FAIL"
    }
    $null = Wait-ProcessesExit @([int]$finalListenerPid) 5
  }

  $finalOwned = @(Get-OwnedRuntimePids)
  $finalListenerPid = Get-ListeningPid "127.0.0.1" $targetPort
  $finalListenerOwned = $finalListenerPid -and (Test-OwnedProcess $finalListenerPid)
  $portOpen = [bool]$finalListenerPid
  $trackedAlive = @($candidateMap.Values | Where-Object { (Test-ProcessExists $_) -and (Test-OwnedProcess $_) })
  $hasState = $state -ne $null
  $closurePassed = $remaining.Count -eq 0 -and $trackedAlive.Count -eq 0 -and $finalOwned.Count -eq 0 -and (-not $hasState -or -not $finalListenerPid)
  Write-Bootstrap "stop_attempt_id=$stopAttemptId final_process_count=$($finalOwned.Count) tracked_alive_count=$($trackedAlive.Count)"
  Write-Bootstrap "stop_attempt_id=$stopAttemptId final_port_open=$portOpen final_listener_pid=$finalListenerPid"
  if (-not $closurePassed) {
    Write-Bootstrap "stop_attempt_id=$stopAttemptId stop_result=FAIL"
    throw "Tool Auto Sub did not stop completely. See lifecycle log for attempt $stopAttemptId."
  }

  Remove-Item $statePath -Force -ErrorAction SilentlyContinue
  Write-Bootstrap "stop_attempt_id=$stopAttemptId state_cleanup=PASS stop_result=PASS"
}

function Assert-ReadyFiles {
  Write-Bootstrap "installer_release_id=CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX"
  Write-Bootstrap "install_root=$installDir"
  foreach ($path in @(
    (Join-Path $installDir "ToolAutoSubBeta.cmd"),
    (Join-Path $installDir "runtime\venv\Scripts\python.exe"),
    (Join-Path $installDir "addons\ocr_runtime\addon_manifest.json"),
    (Join-Path $installDir "ffmpeg\bin\ffmpeg.exe"),
    (Join-Path $installDir "ffmpeg\bin\ffprobe.exe"),
    (Join-Path $installDir "app\main.py"),
    (Join-Path $installDir "app\static\simple\index.html"),
    (Join-Path $installDir "release\CP13A1\INSTALL_RELEASE_MANIFEST.json")
  )) {
    Write-Bootstrap "required_path=$path exists=$(Test-Path -LiteralPath $path)"
    if (-not (Test-Path -LiteralPath $path)) { throw "Required runtime file is missing: $path" }
  }
  if (-not [Environment]::Is64BitOperatingSystem) { throw "This beta requires 64-bit Windows." }
  $free = (Get-PSDrive -Name ([IO.Path]::GetPathRoot($dataDir).TrimEnd(":\\"))).Free
  if ($free -lt 1073741824) { throw "Not enough free disk space for startup. Required at least 1 GiB." }
}

function Start-App {
  Assert-ReadyFiles
  $state = Read-State
  if ($state -and $state.pid) {
    $proc = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
    if ($proc -and (Test-OwnedProcess ([int]$state.pid))) {
      try {
        $health = Invoke-RestMethod -Uri "$($state.url)api/health" -TimeoutSec 2
        if ($health.status -eq "ok") {
          Write-Bootstrap "startup_result=REUSE url=$($state.url)"
          if ($env:CP13A_NO_BROWSER -ne "1") { Start-Process $state.url }
          return
        }
      } catch {}
    }
  }

  $bind = "127.0.0.1"
  $port = 8173
  if (Test-PortOpen $bind $port) {
    $port = $null
    foreach ($candidate in 8174..8199) {
      if (-not (Test-PortOpen $bind $candidate)) { $port = $candidate; break }
    }
    if (-not $port) { throw "No safe localhost port is available from 8173 through 8199." }
  }

  $env:TOOL_AUTO_SUB_ROOT = $installDir
  $env:TOOL_AUTO_SUB_DATA_DIR = $dataDir
  $env:TOOL_AUTO_SUB_DB_PATH = Join-Path $dataDir "app.db"
  $env:TOOL_AUTO_SUB_BUILD_COMMIT = "cp13a1"
  $env:TOOL_AUTO_SUB_SIMPLE_UI_VERSION = "cp13a"
  $env:TOOL_AUTO_SUB_OPERATOR_UI_VERSION = "cp09c"
  $env:TOOL_AUTO_SUB_PROVIDER_CALLS_ENABLED = "0"
  $env:TOOL_AUTO_SUB_UPLOAD_PUBLISH_ENABLED = "0"
  $env:PATH = (Join-Path $installDir "ffmpeg\bin") + ";" + $env:PATH
  $env:TOOL_AUTO_SUB_OCR_RUNTIME_CONFIG = Join-Path $installDir "operator\ocr_runtime_config.local.json"
  $env:TOOL_AUTO_SUB_TRANSLATION_RUNTIME_CONFIG = Join-Path $installDir "operator\translation_runtime_config.local.json"

  $python = Join-Path $installDir "runtime\venv\Scripts\python.exe"
  $outLog = Join-Path $logDir "backend.out.log"
  $errLog = Join-Path $logDir "backend.err.log"
  Write-Bootstrap "python_path=$python"
  Write-Bootstrap ("ffmpeg_path=" + (Join-Path $installDir "ffmpeg\bin\ffmpeg.exe"))
  Write-Bootstrap ("ocr_config=" + $env:TOOL_AUTO_SUB_OCR_RUNTIME_CONFIG)
  Write-Bootstrap ("translation_config=" + $env:TOOL_AUTO_SUB_TRANSLATION_RUNTIME_CONFIG)
  $runtimeEntry = Join-Path $installDir "beta\ToolAutoSubBetaRuntime.py"
  $args = @("`"$runtimeEntry`"","--host",$bind,"--port",[string]$port)
  $proc = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $installDir -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WindowStyle Hidden -PassThru
  $url = "http://$bind`:$port/"
  $healthUrl = "$url" + "api/health"
  $ready = $false
  for ($i = 0; $i -lt 240; $i++) {
    Start-Sleep -Milliseconds 500
    try {
      $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
      if (([string]$health.status).Trim().ToLowerInvariant() -eq "ok") { $ready = $true; break }
    } catch {}
  }
  if (-not $ready) {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    throw "Tool Auto Sub did not become ready before the startup timeout."
  }
  $listenerPid = Get-ListeningPid $bind $port
  $ownedPid = if ($listenerPid -and (Test-OwnedProcess $listenerPid)) { $listenerPid } else { $proc.Id }
  Write-State @{ pid = $ownedPid; launcher_pid = $proc.Id; url = $url; install_dir = $installDir; user_data_root = $userRoot; port = $port; runtime_entry = $runtimeEntry; started_at = (Get-Date).ToString("o") }
  Write-Bootstrap "startup_result=PASS url=$url listener_pid=$ownedPid launcher_pid=$($proc.Id) runtime_entry=$runtimeEntry"
  if ($env:CP13A_NO_BROWSER -ne "1") { Start-Process $url }
}

try {
  if ($Action -eq "Stop") { Stop-App; exit 0 }
  if ($Action -eq "Restart") { Stop-App }
  Start-App
} catch {
  if ($Action -eq "Stop") {
    Write-Error $_.Exception.Message
  } else {
    Show-FriendlyError $_.Exception.Message
  }
  exit 1
}
'''

DIAGNOSTICS_PS1 = r'''
$ErrorActionPreference = "Stop"
$installDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$userRoot = if ($env:CP13A_USERDATA_DIR) { $env:CP13A_USERDATA_DIR } else { Join-Path $env:LOCALAPPDATA "ToolAutoSubBeta" }
$diagRoot = Join-Path $userRoot "diagnostics"
$logDir = Join-Path $userRoot "logs"
New-Item -ItemType Directory -Force $diagRoot | Out-Null
$work = Join-Path $diagRoot ("diagnostics_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
New-Item -ItemType Directory -Force $work | Out-Null

function Redact($Text) {
  $value = [string]$Text
  if ($env:USERNAME) { $value = $value -replace [regex]::Escape($env:USERNAME), "<USER>" }
  if ($env:USERPROFILE) { $value = $value -replace [regex]::Escape($env:USERPROFILE), "<USER_HOME>" }
  $value = $value -replace "AIza[0-9A-Za-z_\-]{20,}", "<REDACTED_GOOGLE_KEY>"
  $value = $value -replace "\bsk-[A-Za-z0-9_\-]{20,}\b", "<REDACTED_API_KEY>"
  $value = $value -replace '("text"\s*:\s*")[^"]{40,}(")', '$1<REDACTED_TEXT>$2'
  $value = $value -replace '("source_path"\s*:\s*")[^"]+(")', '$1<REDACTED_SOURCE_PATH>$2'
  return $value
}

function Write-Safe($Name, $Content) {
  Set-Content -Path (Join-Path $work $Name) -Value (Redact $Content) -Encoding UTF8
}

Write-Safe "system.txt" ((Get-ComputerInfo | Select-Object WindowsProductName,WindowsVersion,OsArchitecture | Format-List | Out-String) + "`n" + (Get-PSDrive -PSProvider FileSystem | Format-Table -AutoSize | Out-String))
Write-Safe "runtime.txt" (@{
  release_id = "CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX"
  install_path = $installDir
  user_data_path = $userRoot
  python_exists = Test-Path (Join-Path $installDir "runtime\venv\Scripts\python.exe")
  ffmpeg_exists = Test-Path (Join-Path $installDir "ffmpeg\bin\ffmpeg.exe")
  ffprobe_exists = Test-Path (Join-Path $installDir "ffmpeg\bin\ffprobe.exe")
  ocr_config_exists = Test-Path (Join-Path $installDir "operator\ocr_runtime_config.local.json")
  translation_config_exists = Test-Path (Join-Path $installDir "operator\translation_runtime_config.local.json")
  installed_manifest_exists = Test-Path (Join-Path $installDir "EXPECTED_INSTALL_MANIFEST.json")
  provider_calls = @{ gemini = 0; elevenlabs = 0; youtube = 0 }
} | ConvertTo-Json -Depth 5)
if (Test-Path $logDir) {
  foreach ($log in Get-ChildItem $logDir -File -ErrorAction SilentlyContinue | Select-Object -First 8) {
    Write-Safe ("log_" + $log.Name + ".txt") (Get-Content $log.FullName -Tail 200 | Out-String)
  }
}

$zip = "$work.zip"
Compress-Archive -Path (Join-Path $work "*") -DestinationPath $zip -Force
Remove-Item $work -Recurse -Force
if ($env:CP13A_NO_EXPLORER -ne "1") { Start-Process explorer.exe "/select,`"$zip`"" }
Write-Host "Diagnostics saved: $zip"
'''

UNINSTALL_PS1 = r'''
param([switch]$RemoveUserData)

$ErrorActionPreference = "Stop"
$installDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$userRoot = if ($env:CP13A_USERDATA_DIR) { $env:CP13A_USERDATA_DIR } else { Join-Path $env:LOCALAPPDATA "ToolAutoSubBeta" }
''' + UNINSTALL_REGISTRY_RESOLUTION_PS1 + r'''
& (Join-Path $installDir "StopToolAutoSubBeta.cmd") | Out-Null
Set-Location $env:TEMP
Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue
$startMenu = if ($env:CP13A_START_MENU_DIR) { $env:CP13A_START_MENU_DIR } else { Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Tool Auto Sub Beta" }
$desktopDir = if ($env:CP13A_DESKTOP_DIR) { $env:CP13A_DESKTOP_DIR } else { [Environment]::GetFolderPath("Desktop") }
Remove-Item -LiteralPath $startMenu -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $desktopDir "Tool Auto Sub Beta.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $uninstallKey -Recurse -Force -ErrorAction SilentlyContinue
if ($RemoveUserData) {
  Remove-Item -LiteralPath $userRoot -Recurse -Force -ErrorAction SilentlyContinue
} else {
  Write-Host "User projects and database were preserved at: $userRoot"
}
'''


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text(path: Path, text: str, *, ps1: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if ps1 else "utf-8", newline="\r\n")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def expand_zip(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(destination)


def copy_tree_contents(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


def copy_current_app_overlay(stage: Path) -> dict:
    copied: list[str] = []
    for folder_name in ["app", "alembic"]:
        source = ROOT / folder_name
        target = stage / folder_name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        copied.append(folder_name)
    for rel in [
        "tools/ocr_runtime_worker.py",
        "tools/offline_translation_worker.py",
        "tools/storage_preflight.py",
        "operator/translation_config.env",
    ]:
        source = ROOT / rel
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(rel)
    return {"copied": copied}


def copy_ffmpeg(stage: Path) -> dict:
    required = [
        FFMPEG_ROOT / "bin" / "ffmpeg.exe",
        FFMPEG_ROOT / "bin" / "ffprobe.exe",
        FFMPEG_ROOT / "LICENSE",
        FFMPEG_ROOT / "README.txt",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"CP13A1_BLOCKED_FFMPEG_RUNTIME_MISSING: {missing}")
    target = stage / "ffmpeg"
    (target / "bin").mkdir(parents=True, exist_ok=True)
    for source in required:
        if source.name in {"ffmpeg.exe", "ffprobe.exe"}:
            shutil.copy2(source, target / "bin" / source.name)
        else:
            shutil.copy2(source, target / source.name)
    return {
        "source": str(FFMPEG_ROOT),
        "ffmpeg_sha256": sha256_file(FFMPEG_ROOT / "bin" / "ffmpeg.exe"),
        "ffprobe_sha256": sha256_file(FFMPEG_ROOT / "bin" / "ffprobe.exe"),
        "version": subprocess.check_output([str(FFMPEG_ROOT / "bin" / "ffmpeg.exe"), "-version"], text=True, stderr=subprocess.DEVNULL).splitlines()[0],
    }


def bundle_offline_asr(stage: Path) -> dict:
    if not ASR_REQUIREMENTS.is_file() or not ASR_LICENSE_SOURCE.is_file():
        raise RuntimeError("CP13A1_BLOCKED_OFFLINE_ASR_SOURCE_METADATA_MISSING")
    runtime_python = stage / "runtime" / "venv" / "Scripts" / "python.exe"
    if not runtime_python.is_file():
        raise RuntimeError("CP13A1_BLOCKED_RELEASE_LOCAL_PYTHON_MISSING")
    env = os.environ.copy()
    env.update({"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONNOUSERSITE": "1"})
    subprocess.run(
        [
            str(runtime_python),
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--upgrade",
        "--requirement",
        str(ASR_REQUIREMENTS),
    ],
    check=True,
    env=env,
    )
    for legacy_model in ("faster-whisper-tiny", "faster-whisper-base"):
        legacy_path = stage / "models" / legacy_model
        if legacy_path.is_dir():
            shutil.rmtree(legacy_path)
    for legacy_license in ("faster-whisper-tiny-MIT.txt", "faster-whisper-base-MIT.txt"):
        (stage / "licenses" / legacy_license).unlink(missing_ok=True)

    model_source_path = resolve_asr_model_path(ASR_MODEL_NAME)
    model_target = stage / "models" / model_directory_name(ASR_MODEL_NAME)
    model_target.mkdir(parents=True, exist_ok=True)
    model_inventory = []
    for filename in ASR_MODEL_REQUIRED_FILES:
        source = model_source_path / filename
        target = model_target / filename
        if not source.is_file():
            raise RuntimeError(f"CP13A1_BLOCKED_OFFLINE_ASR_MODEL_CACHE_MISSING: {filename}")
        shutil.copy2(source, target)
        actual_hash = sha256_file(target)
        model_inventory.append({"filename": filename, "size": target.stat().st_size, "sha256": actual_hash})
    snapshot_name = model_source_path.name if "snapshots" in model_source_path.parts else None
    model_metadata = {
        "schema_version": 1,
        "model_id": ASR_MODEL_ID,
        "model_name": ASR_MODEL_NAME,
        "snapshot": snapshot_name,
        "source": ASR_MODEL_SOURCE,
        "license": "MIT",
        "device": "cpu",
        "compute_type": "int8",
        "local_files_only": True,
        "files": model_inventory,
    }
    write_json(model_target / "MODEL_METADATA.json", model_metadata)
    license_target = stage / "licenses" / ASR_LICENSE_TARGET_NAME
    license_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ASR_LICENSE_SOURCE, license_target)
    shutil.copy2(ASR_REQUIREMENTS, stage / ASR_REQUIREMENTS.name)
    import_probe = subprocess.check_output(
        [
            str(runtime_python),
            "-I",
            "-c",
            (
                "import json,faster_whisper,ctranslate2,av,onnxruntime,tokenizers,PIL;"
                "print(json.dumps({'faster_whisper':faster_whisper.__file__,"
                "'ctranslate2':ctranslate2.__file__,'av':av.__file__,"
                "'onnxruntime':onnxruntime.__file__,'tokenizers':tokenizers.__file__,'PIL':PIL.__file__}))"
            ),
        ],
        text=True,
        env=env,
    ).strip()
    import_paths = json.loads(import_probe)
    site_packages = (stage / "runtime" / "venv" / "Lib" / "site-packages").resolve()
    if any(site_packages not in Path(path).resolve().parents for path in import_paths.values()):
        raise RuntimeError("CP13A1_BLOCKED_OFFLINE_ASR_GLOBAL_IMPORT")
    return {
        "provider": "faster_whisper",
        "requirements": ASR_REQUIREMENTS.name,
        "model": model_metadata,
        "model_size_bytes": sum(item["size"] for item in model_inventory),
        "model_path": f"models/{model_directory_name(ASR_MODEL_NAME)}",
        "import_paths": import_paths,
        "network_during_processing": "disabled",
    }


def bundle_offline_translation(stage: Path) -> dict:
    if not TRANSLATION_REQUIREMENTS.is_file() or not TRANSLATION_LICENSE_SOURCE.is_file():
        raise RuntimeError("CP13A1_BLOCKED_OFFLINE_TRANSLATION_SOURCE_METADATA_MISSING")
    if not (TRANSLATION_PACKAGE_SOURCE / "model" / "model.bin").is_file():
        raise RuntimeError("CP13A1_BLOCKED_OFFLINE_TRANSLATION_MODEL_CACHE_MISSING")
    runtime_python = stage / "runtime" / "venv" / "Scripts" / "python.exe"
    if not runtime_python.is_file():
        raise RuntimeError("CP13A1_BLOCKED_RELEASE_LOCAL_PYTHON_MISSING")
    env = os.environ.copy()
    env.update({"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONNOUSERSITE": "1"})
    subprocess.run(
        [
            str(runtime_python),
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--upgrade",
            "--requirement",
            str(TRANSLATION_REQUIREMENTS),
        ],
        check=True,
        env=env,
    )
    package_target = stage / "addons" / "translation_runtime" / "packages" / TRANSLATION_MODEL_ID
    if package_target.exists():
        shutil.rmtree(package_target)
    shutil.copytree(TRANSLATION_PACKAGE_SOURCE, package_target)
    shutil.copy2(TRANSLATION_REQUIREMENTS, stage / TRANSLATION_REQUIREMENTS.name)
    license_target = stage / "licenses" / "ARGOS_ZH_EN_NOTICE.txt"
    license_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TRANSLATION_LICENSE_SOURCE, license_target)
    config = {
        "schema_version": 1,
        "python_path": r"runtime\venv\Scripts\python.exe",
        "packages_root": r"addons\translation_runtime\packages",
        "model_id": TRANSLATION_MODEL_ID,
        "timeout_seconds": 3600,
        "network_during_processing": "disabled",
    }
    write_json(stage / "operator" / "translation_runtime_config.local.json", config)
    probe_env = {
        **env,
        "ARGOS_PACKAGES_DIR": str(package_target.parent),
        "PYTHONIOENCODING": "utf-8",
    }
    probe = subprocess.check_output(
        [
            str(runtime_python),
            "-I",
            "-c",
            (
                "import json,argostranslate.translate;"
                "t=argostranslate.translate.get_translation_from_codes('zh','en');"
                "h=t.hypotheses('\\u6211\\u6765\\u4e86',num_hypotheses=4);"
                "print(json.dumps({'argostranslate':argostranslate.translate.__file__,"
                "'hypothesis_count':len(h),'nonempty':bool(h and h[0].value.strip())}))"
            ),
        ],
        text=True,
        env=probe_env,
    ).strip()
    probe_payload = json.loads(probe)
    if not probe_payload.get("nonempty") or int(probe_payload.get("hypothesis_count") or 0) < 1:
        raise RuntimeError("CP13A1_BLOCKED_OFFLINE_TRANSLATION_PROBE_FAILED")
    probe_payload["argostranslate"] = "runtime/venv/Lib/site-packages/argostranslate/translate.py"
    return {
        "provider": "argostranslate",
        "model_id": TRANSLATION_MODEL_ID,
        "model_path": f"addons/translation_runtime/packages/{TRANSLATION_MODEL_ID}",
        "model_size_bytes": tree_summary(package_target)["total_size_bytes"],
        "requirements": TRANSLATION_REQUIREMENTS.name,
        "license_notice": "licenses/ARGOS_ZH_EN_NOTICE.txt",
        "probe": probe_payload,
        "network_during_processing": "disabled",
    }


def configure_full_video_ocr_timeout(stage: Path) -> dict:
    config_path = stage / "operator" / "ocr_runtime_config.local.json"
    if not config_path.is_file():
        raise RuntimeError("CP13A1_BLOCKED_OCR_RUNTIME_CONFIG_MISSING")
    payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    payload["timeout_seconds"] = 3600
    payload["full_video_batch_mode"] = "two_pass_dense_caption_region"
    write_json(config_path, payload)
    return {
        "config_path": "operator/ocr_runtime_config.local.json",
        "timeout_seconds": payload["timeout_seconds"],
        "full_video_batch_mode": payload["full_video_batch_mode"],
    }


def write_launchers(stage: Path) -> None:
    write_text(stage / "ToolAutoSubBeta.cmd", ROOT_LAUNCHER_CMD)
    write_text(stage / "StopToolAutoSubBeta.cmd", ROOT_STOP_CMD)
    write_text(stage / "RestartToolAutoSubBeta.cmd", ROOT_RESTART_CMD)
    write_text(stage / "CollectDiagnostics.cmd", ROOT_DIAGNOSTICS_CMD)
    write_text(stage / "BETA_TEST_5_MINUTES.txt", beta_guide("PENDING_FINAL_INSTALLER", "PENDING_FINAL_INSTALLER_HASH"))
    write_text(stage / "beta" / "ToolAutoSubBeta.ps1", LAUNCHER_PS1, ps1=True)
    write_text(stage / "beta" / "ToolAutoSubBetaRuntime.py", RUNTIME_PY)
    write_text(stage / "beta" / "CollectDiagnostics.ps1", DIAGNOSTICS_PS1, ps1=True)
    write_text(stage / "beta" / "UninstallToolAutoSubBeta.ps1", UNINSTALL_PS1, ps1=True)


def tree_summary(root: Path) -> dict:
    file_count = 0
    total_size = 0
    for path in root.rglob("*"):
        if path.is_file():
            file_count += 1
            total_size += path.stat().st_size
    return {"file_count": file_count, "total_size_bytes": total_size}


def component_entry(stage: Path, item: dict) -> dict:
    target = stage / item["relative_path"]
    entry = {
        "component": item["component"],
        "relative_path": item["relative_path"],
        "type": item["type"],
        "required": bool(item["critical"]),
        "critical": bool(item["critical"]),
        "release_source_path": str(target),
    }
    if item["type"] == "file":
        entry["size"] = target.stat().st_size if target.exists() else 0
        entry["sha256"] = sha256_file(target) if target.exists() else None
    else:
        entry["size"] = tree_summary(target)["total_size_bytes"] if target.exists() else 0
        entry["sha256"] = None
    return entry


def build_expected_install_manifest(stage: Path) -> dict:
    summary = tree_summary(stage)
    manifest = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "minimum_file_count": MIN_INSTALLED_FILE_COUNT,
        "minimum_total_size_bytes": MIN_INSTALLED_SIZE_BYTES,
        "installed_tree_summary": summary,
        "components": [component_entry(stage, item) for item in REQUIRED_COMPONENTS],
    }
    validate_installed_tree(stage, manifest)
    return manifest


def validate_installed_tree(installed_root: Path, manifest: dict) -> dict:
    missing: list[str] = []
    for entry in manifest["components"]:
        if not entry.get("required"):
            continue
        target = installed_root / entry["relative_path"]
        if entry["type"] == "directory":
            if not target.is_dir():
                missing.append(entry["component"])
        else:
            if not target.is_file():
                missing.append(entry["component"])
            elif entry.get("sha256") and sha256_file(target) != entry["sha256"]:
                missing.append(f"{entry['component']}_hash")
    summary = tree_summary(installed_root)
    root_items = [path.name for path in installed_root.iterdir()] if installed_root.exists() else []
    if root_items == ["addons"]:
        missing.append("installed_root_addons_only")
    if summary["file_count"] < manifest["minimum_file_count"]:
        missing.append("installed_file_count_too_low")
    if summary["total_size_bytes"] < manifest["minimum_total_size_bytes"]:
        missing.append("installed_size_too_low")
    result = {"passed": not missing, "missing": missing, "summary": summary, "root_items": sorted(root_items)}
    if missing:
        raise RuntimeError(f"installed tree validation failed: {missing}")
    return result


def stage_complete_payload(stage: Path) -> dict:
    scratch = stage.parent / "cp12b_extract"
    expand_zip(CP12B_ZIP, scratch)
    extracted_root = scratch / "Tool Auto Sub"
    if not extracted_root.is_dir():
        raise RuntimeError("CP12B portable root is missing from source ZIP")
    copy_tree_contents(extracted_root, stage)
    overlay = copy_current_app_overlay(stage)
    ocr_batch_config = configure_full_video_ocr_timeout(stage)
    ffmpeg = copy_ffmpeg(stage)
    offline_asr = bundle_offline_asr(stage)
    offline_translation = bundle_offline_translation(stage)
    write_launchers(stage)
    install_manifest = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "previous_failed_release": "CP13A_EXTERNAL_MACHINE_BETA_FAIL",
        "failure_reason": "INCOMPLETE_INSTALL_PAYLOAD_MAIN_APPLICATION_MISSING",
        "portable_baseline": {"release": "CP12B", "sha256": CP12B_SHA},
        "provider_disabled_state": {"gemini": "disabled", "elevenlabs": "disabled", "youtube": "disabled", "upload": "disabled", "publish": "disabled"},
        "offline_asr": offline_asr,
        "offline_translation": offline_translation,
        "ocr_batch_config": ocr_batch_config,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(stage / "release" / "CP13A1" / "INSTALL_RELEASE_MANIFEST.json", install_manifest)
    manifest = build_expected_install_manifest(stage)
    write_json(stage / "EXPECTED_INSTALL_MANIFEST.json", manifest)
    return {
        "overlay": overlay,
        "ffmpeg": ffmpeg,
        "offline_asr": offline_asr,
        "offline_translation": offline_translation,
        "ocr_batch_config": ocr_batch_config,
        "expected_install_manifest": manifest,
    }


def enumerate_stage_files(stage: Path) -> list[str]:
    stage_str = str(stage)
    result = []
    
    def on_error(os_error):
        raise RuntimeError(f"Scan error in enumerate_stage_files: {os_error}")
    
    for root, dirs, files in os.walk(stage_str, onerror=on_error, followlinks=False):
        for file in files:
            full_path = Path(root) / file
            if full_path.is_file() and not full_path.is_symlink():
                try:
                    rel_path = full_path.relative_to(stage).as_posix()
                    result.append(rel_path)
                except ValueError as e:
                    raise RuntimeError(f"Path relative error: {e}")
                    
    result.sort()
    
    seen = set()
    for rel_path in result:
        if rel_path in seen:
            raise RuntimeError(f"Duplicate relative path detected: {rel_path}")
        seen.add(rel_path)
        
    return result


def write_complete_payload_zip(stage: Path, output_zip: Path, expected_manifest: dict) -> dict:
    file_list = enumerate_stage_files(stage)
    
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for rel_path in file_list:
            full_path = stage / rel_path
            archive.write(full_path, rel_path)
            
    with zipfile.ZipFile(output_zip, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP file failed CRC test")
            
        zip_names = archive.namelist()
        if len(zip_names) != len(set(zip_names)):
            raise RuntimeError("ZIP contains duplicate names")
            
        zip_set = set(zip_names)
        expected_set = set(file_list)
        
        missing = expected_set - zip_set
        extra = zip_set - expected_set
        
        if missing or extra:
            raise RuntimeError(f"ZIP entries mismatch. Missing: {missing}. Extra: {extra}")
            
        if len(zip_names) != len(file_list):
            raise RuntimeError(f"ZIP entry count ({len(zip_names)}) does not match expected file count ({len(file_list)})")
            
        required_manifest_files = {}
        for comp in expected_manifest.get("components", []):
            if comp.get("type") == "file":
                rel = comp.get("relative_path")
                if rel:
                    required_manifest_files[rel] = comp

        for rel, comp in required_manifest_files.items():
            if rel not in zip_set:
                raise RuntimeError(f"Manifest required file missing in ZIP: {rel}")
            info = archive.getinfo(rel)
            if comp.get("size") is not None and info.file_size != comp["size"]:
                raise RuntimeError(f"Manifest size mismatch for {rel}: expected {comp['size']}, got {info.file_size}")
            
            if comp.get("sha256") is not None:
                with archive.open(rel) as f:
                    file_data = f.read()
                    actual_sha = hashlib.sha256(file_data).hexdigest()
                    if actual_sha != comp["sha256"]:
                        raise RuntimeError(f"Manifest hash mismatch for {rel}: expected {comp['sha256']}, got {actual_sha}")
                        
    return {"zip_name": output_zip.name, "zip_size": output_zip.stat().st_size, "zip_sha256": sha256_file(output_zip)}


def write_sed(payload_dir: Path, sed_path: Path) -> None:
    files = sorted(path for path in payload_dir.iterdir() if path.is_file())
    file_lines = [f"FILE{idx}={path.name}" for idx, path in enumerate(files)]
    source_entries = [f"{path.name}=" for path in files]
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
            "InstallPrompt=Install Tool Auto Sub Beta CP13A1 hotfix?",
            "DisplayLicense=",
            "FinishMessage=Tool Auto Sub Beta CP13A1 installer finished.",
            f"TargetName={INSTALLER}",
            "FriendlyName=Tool Auto Sub Beta CP13A1",
            "AppLaunched=cmd.exe /c install.cmd",
            "PostInstallCmd=<None>",
            "AdminQuietInstCmd=cmd.exe /c install.cmd /Q",
            "UserQuietInstCmd=cmd.exe /c install.cmd /Q",
            "SourceFiles=SourceFiles",
            *file_lines,
            "",
            "[Strings]",
            "",
            "[SourceFiles]",
            f"SourceFiles0={payload_dir}",
            "",
            "[SourceFiles0]",
            *source_entries,
            "",
        ]
    )
    sed_path.write_text(sed, encoding="utf-8")


def run_iexpress(sed_path: Path) -> dict:
    iexpress = shutil.which("iexpress.exe")
    if not iexpress:
        raise RuntimeError("CP13A1_BLOCKED_INSTALLER_TOOLCHAIN: iexpress.exe not found")
    version = subprocess.check_output(["powershell", "-NoProfile", "-Command", "(Get-Command iexpress.exe).Version.ToString()"], text=True).strip()
    completed = subprocess.run([iexpress, "/N", "/Q", str(sed_path)], cwd=ROOT, text=True, capture_output=True, check=False)
    if completed.returncode != 0 or not INSTALLER.exists():
        raise RuntimeError(f"CP13A1_BLOCKED_INSTALLER_TOOLCHAIN: IExpress failed: {completed.stderr or completed.stdout}")
    return {"tool": "iexpress.exe", "path": iexpress, "version": version}


def find_csharp_compiler() -> Path:
    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    found = shutil.which("csc.exe")
    if found:
        return Path(found)
    raise RuntimeError("CP13A1_BLOCKED_INSTALLER_TOOLCHAIN: csc.exe not found")


def write_installer_payload_zip(payload_dir: Path, output_zip: Path) -> dict:
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(payload_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(payload_dir).as_posix())
    return {
        "zip_name": output_zip.name,
        "zip_size": output_zip.stat().st_size,
        "zip_sha256": sha256_file(output_zip),
    }


def build_overlay_installer(payload_zip: Path, source_path: Path) -> dict:
    csc = find_csharp_compiler()
    source_path.write_text(BOOTSTRAP_CS, encoding="utf-8")
    stub_path = source_path.with_suffix(".exe")
    command = [
        str(csc),
        "/nologo",
        "/optimize+",
        "/target:exe",
        f"/out:{stub_path}",
        "/reference:System.IO.Compression.dll",
        "/reference:System.IO.Compression.FileSystem.dll",
        str(source_path),
    ]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if completed.returncode != 0 or not stub_path.exists():
        raise RuntimeError(f"CP13A1_BLOCKED_INSTALLER_TOOLCHAIN: C# bootstrap compile failed: {completed.stderr or completed.stdout}")
    INSTALLER.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(stub_path, INSTALLER)
    payload_size = payload_zip.stat().st_size
    with INSTALLER.open("ab") as output, payload_zip.open("rb") as payload:
        shutil.copyfileobj(payload, output, length=1024 * 1024)
        output.write(SFX_PAYLOAD_MAGIC)
        output.write(payload_size.to_bytes(8, byteorder="little", signed=True))
    version = subprocess.check_output([str(csc), "/help"], text=True, stderr=subprocess.STDOUT).splitlines()[0].strip()
    return {
        "tool": "custom_dotnet_overlay_sfx",
        "compiler": str(csc),
        "compiler_version": version,
        "payload_strategy": "append_zip_overlay_to_bootstrap_exe",
        "payload_zip": payload_zip.name,
        "payload_zip_size": payload_size,
        "payload_zip_sha256": sha256_file(payload_zip),
        "replaces": "iexpress.exe",
        "reason": "IExpress produced multivolume cabinets for the larger offline ASR payload.",
    }


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


def wait_runtime_state(state_path: Path, seconds: int = 120) -> dict:
    deadline = time.time() + seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            if state_path.exists():
                return json.loads(state_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            last_error = exc
        time.sleep(0.5)
    if last_error:
        raise RuntimeError(f"runtime state was not readable before timeout: {last_error}") from last_error
    raise RuntimeError(f"runtime state was not created before timeout: {state_path}")


def read_lnk_info(shortcut: Path) -> dict:
    escaped_shortcut = str(shortcut).replace("'", "''")
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{escaped_shortcut}'); "
        "[pscustomobject]@{TargetPath=$s.TargetPath;Arguments=$s.Arguments;WorkingDirectory=$s.WorkingDirectory} | ConvertTo-Json -Compress"
    )
    return json.loads(subprocess.check_output(["powershell", "-NoProfile", "-Command", script], text=True))


def read_lnk_target(shortcut: Path) -> str:
    return str(read_lnk_info(shortcut)["TargetPath"])


def validate_installer(expected_manifest: dict) -> dict:
    validation_root = Path(tempfile.mkdtemp(prefix="cp13a1_validation_"))
    install_dir = validation_root / "Install With Spaces" / "ToolAutoSubBeta"
    user_dir = validation_root / "User Data With Spaces"
    start_menu = validation_root / "Start Menu With Spaces"
    desktop = validation_root / "Desktop"
    validation_key_leaf = create_validation_uninstall_key_leaf()
    production_registry_before = _registry_snapshot_for_leaf(PRODUCTION_UNINSTALL_KEY_LEAF)
    validation_registry_before = _registry_snapshot_for_leaf(validation_key_leaf)
    if validation_registry_before["exists"]:
        raise RuntimeError("Generated validation uninstall registry key already exists")
    steps: list[dict] = []
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("TOOL_AUTO_SUB_"):
            env.pop(key, None)
    env.update(
        {
            "CP13A_CREATE_DESKTOP_SHORTCUT": "1",
            "CP13A_INSTALL_DIR": str(install_dir),
            "CP13A_USERDATA_DIR": str(user_dir),
            "CP13A_START_MENU_DIR": str(start_menu),
            "CP13A_DESKTOP_DIR": str(desktop),
            "CP13A_NO_BROWSER": "1",
            "CP13A_NO_EXPLORER": "1",
            "CP13A_HEADLESS": "1",
            "CP13A_INSTALL_SILENT": "1",
            "CP13A_LAUNCH_AFTER_INSTALL": "0",
            **validation_registry_environment(validation_key_leaf),
        }
    )

    def run_step(name: str, command: list[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        print(f"CP13A1 validation step: {name}", flush=True)
        started = time.time()
        log_path = validation_root / f"{name}.log"
        try:
            with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
                completed = subprocess.run(command, cwd=cwd or ROOT, env=env, text=True, stdout=log_handle, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:] if log_path.exists() else ""
            steps.append({"name": name, "status": "TIMEOUT", "timeout_seconds": timeout, "duration_seconds": round(time.time() - started, 3), "log_path": str(log_path), "log_tail": tail})
            raise RuntimeError(f"{name} timed out after {timeout} seconds") from exc
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:] if log_path.exists() else ""
        steps.append({"name": name, "returncode": completed.returncode, "duration_seconds": round(time.time() - started, 3), "log_path": str(log_path), "log_tail": tail})
        if completed.returncode != 0:
            raise RuntimeError(f"{name} returned {completed.returncode}: {tail}")
        return completed

    def stop_validation_processes() -> None:
        escaped = str(validation_root).replace("'", "''")
        script = f"$root='{escaped}'; Get-CimInstance Win32_Process | Where-Object {{ ($_.CommandLine -and $_.CommandLine.Contains($root)) -or ($_.ExecutablePath -and $_.ExecutablePath.Contains($root)) }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
        subprocess.run(["powershell", "-NoProfile", "-Command", script], text=True, capture_output=True, timeout=30, check=False)

    def run_shortcut_step(name: str, shortcut: Path, timeout: int, *, wait: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        escaped = str(shortcut).replace("'", "''")
        wait_arg = " -Wait" if wait else ""
        script = f"Start-Process -FilePath '{escaped}'{wait_arg} -WindowStyle Hidden"
        return run_step(name, ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout, cwd=cwd)

    def shortcut_inventory() -> dict[str, dict]:
        return {path.name: read_lnk_info(path) for path in sorted(start_menu.glob("*.lnk"))}

    def assert_shortcuts(label: str) -> dict[str, dict]:
        expected = {
            "Tool Auto Sub Beta.lnk",
            "Stop Tool Auto Sub Beta.lnk",
            "Restart Tool Auto Sub Beta.lnk",
            "Collect Diagnostics.lnk",
            "Uninstall Tool Auto Sub Beta.lnk",
        }
        inventory = shortcut_inventory()
        names = set(inventory)
        missing = sorted(expected - names)
        unexpected_duplicates = len(list(start_menu.glob("*.lnk"))) != len(names)
        if missing:
            raise RuntimeError(f"{label} Start Menu shortcuts missing: {', '.join(missing)}")
        if unexpected_duplicates:
            raise RuntimeError(f"{label} Start Menu shortcut inventory has duplicate names")
        return inventory

    def assert_uninstall_shortcut(inventory: dict[str, dict]) -> dict:
        info = inventory["Uninstall Tool Auto Sub Beta.lnk"]
        target = Path(info["TargetPath"])
        if not target.is_absolute() or not target.is_file():
            raise RuntimeError(f"uninstall shortcut target is not an existing absolute path: {info['TargetPath']}")
        if "powershell.exe" not in target.name.lower():
            raise RuntimeError(f"uninstall shortcut target is not PowerShell: {target}")
        expected_script = install_dir / "beta" / "UninstallToolAutoSubBeta.ps1"
        if str(expected_script) not in str(info["Arguments"]):
            raise RuntimeError(f"uninstall shortcut arguments do not reference installed uninstall script: {info['Arguments']}")
        working_dir = Path(info["WorkingDirectory"])
        if not working_dir.is_dir():
            raise RuntimeError(f"uninstall shortcut working directory does not exist: {info['WorkingDirectory']}")
        try:
            working_dir.resolve().relative_to(install_dir.resolve())
            raise RuntimeError(f"uninstall shortcut working directory must be outside install root: {info['WorkingDirectory']}")
        except ValueError:
            pass
        return info

    def copy_evidence(status: str, extra: dict) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        destination = EVIDENCE_ROOT / f"{stamp}-shortcut-product-fix-rerun"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(validation_root, destination)
        metadata = {
            "status": status,
            "root_cause": "missing_uninstall_shortcut_due_to_filesystem_only_bare_executable_check",
            "old_installer_sha256": OLD_DEFECTIVE_INSTALLER_SHA,
            "new_installer_sha256": sha256_file(INSTALLER) if INSTALLER.exists() else None,
            "old_evidence_root": r"C:\Users\ADMIN\AppData\Local\Temp\cp13a1_validation_9v4ar8n6",
            "old_evidence_status": "superseded_due_to_product_defect",
            "validation_root": str(validation_root),
            "copied_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        write_json(destination / "evidence_metadata.json", metadata)
        return destination

    def assert_validation_registry_created() -> None:
        if not _registry_snapshot_for_leaf(validation_key_leaf)["exists"]:
            raise RuntimeError("Validation installer did not create its isolated uninstall registry key")
        _assert_production_registry_snapshot(
            production_registry_before,
            _registry_snapshot_for_leaf(PRODUCTION_UNINSTALL_KEY_LEAF),
        )

    def assert_validation_registry_cleaned() -> None:
        cleanup_validation_uninstall_registry_key(validation_key_leaf)
        _assert_production_registry_snapshot(
            production_registry_before,
            _registry_snapshot_for_leaf(PRODUCTION_UNINSTALL_KEY_LEAF),
        )

    try:
        if install_dir.exists():
            raise RuntimeError("validation install dir unexpectedly exists before install")
        run_step("install", [str(INSTALLER), "/Q"], INSTALL_VALIDATION_TIMEOUT_SECONDS)
        assert_validation_registry_created()
        installed_manifest = json.loads((install_dir / "EXPECTED_INSTALL_MANIFEST.json").read_text(encoding="utf-8-sig"))
        validation = validate_installed_tree(install_dir, installed_manifest)
        if sorted(item["component"] for item in installed_manifest["components"]) != sorted(item["component"] for item in expected_manifest["components"]):
            raise RuntimeError("installed expected manifest components differ from release manifest")
        root_items = {path.name for path in install_dir.iterdir()}
        if root_items == {"addons"}:
            raise RuntimeError("installed root is addons-only")
        bootstrap_log = user_dir / "logs" / "launcher_bootstrap.log"
        bootstrap_text = bootstrap_log.read_text(encoding="utf-8-sig", errors="replace") if bootstrap_log.exists() else ""
        if "Shortcut target is missing: powershell.exe" in bootstrap_text or "install_result=FAIL" in bootstrap_text:
            raise RuntimeError("install bootstrap log contains shortcut failure")
        initial_inventory = assert_shortcuts("initial install")
        uninstall_shortcut_info = assert_uninstall_shortcut(initial_inventory)
        shortcut = start_menu / "Tool Auto Sub Beta.lnk"
        target = read_lnk_target(shortcut)
        if Path(target).resolve() != (install_dir / "ToolAutoSubBeta.cmd").resolve():
            raise RuntimeError(f"Start Menu shortcut target mismatch: {target}")
        if not Path(target).is_file():
            raise RuntimeError("Start Menu shortcut target does not exist")
        run_shortcut_step("launch_from_start_menu", shortcut, 60, wait=False, cwd=install_dir)
        state = wait_runtime_state(user_dir / "runtime_state.json")
        if not wait_health(state["url"]):
            raise RuntimeError("health check did not become ready")
        run_shortcut_step("start_menu_launch_reuse", shortcut, 60, wait=False, cwd=install_dir)
        run_shortcut_step("diagnostics", start_menu / "Collect Diagnostics.lnk", 120, cwd=install_dir)
        run_shortcut_step("stop", start_menu / "Stop Tool Auto Sub Beta.lnk", 90, cwd=install_dir)
        run_shortcut_step("uninstall_preserve_data", start_menu / "Uninstall Tool Auto Sub Beta.lnk", 120, cwd=validation_root)
        if install_dir.exists():
            raise RuntimeError("uninstall did not remove application binaries")
        if not user_dir.exists():
            raise RuntimeError("uninstall did not preserve user data")
        if start_menu.exists() and any(start_menu.glob("*.lnk")):
            raise RuntimeError("uninstall did not remove Start Menu shortcuts")
        assert_validation_registry_cleaned()
        run_step("reinstall_preserved_data", [str(INSTALLER), "/Q"], INSTALL_VALIDATION_TIMEOUT_SECONDS)
        assert_validation_registry_created()
        reinstall_validation = validate_installed_tree(install_dir, json.loads((install_dir / "EXPECTED_INSTALL_MANIFEST.json").read_text(encoding="utf-8-sig")))
        reinstall_inventory = assert_shortcuts("reinstall")
        reinstall_uninstall_shortcut_info = assert_uninstall_shortcut(reinstall_inventory)
        run_shortcut_step("launch_after_reinstall", start_menu / "Tool Auto Sub Beta.lnk", 60, wait=False, cwd=install_dir)
        state = wait_runtime_state(user_dir / "runtime_state.json")
        if not wait_health(state["url"]):
            raise RuntimeError("health check after reinstall did not become ready")
        run_shortcut_step("stop_after_reinstall", start_menu / "Stop Tool Auto Sub Beta.lnk", 90, cwd=install_dir)
        run_shortcut_step("final_uninstall_preserve_data", start_menu / "Uninstall Tool Auto Sub Beta.lnk", 120, cwd=validation_root)
        if install_dir.exists():
            raise RuntimeError("final uninstall did not remove application binaries")
        if start_menu.exists() and any(start_menu.glob("*.lnk")):
            raise RuntimeError("final uninstall did not remove Start Menu shortcuts")
        assert_validation_registry_cleaned()
        evidence_path = copy_evidence(
            "PASS",
            {
                "initial_shortcut_inventory": initial_inventory,
                "reinstall_shortcut_inventory": reinstall_inventory,
                "uninstall_shortcut": uninstall_shortcut_info,
                "reinstall_uninstall_shortcut": reinstall_uninstall_shortcut_info,
            },
        )
        return {
            "status": "PASS",
            "validation_root": str(validation_root),
            "evidence_root": str(evidence_path),
            "steps": steps,
            "installed_tree_validation": validation,
            "reinstall_validation": reinstall_validation,
            "start_menu_shortcut_target": target,
            "shortcut_inventory": initial_inventory,
            "reinstall_shortcut_inventory": reinstall_inventory,
            "uninstall_shortcut": uninstall_shortcut_info,
            "reinstall_uninstall_shortcut": reinstall_uninstall_shortcut_info,
            "bootstrap_log_exists": (user_dir / "logs" / "launcher_bootstrap.log").exists(),
            "validation_uninstall_registry_leaf": validation_key_leaf,
            "production_uninstall_registry_snapshot_preserved": True,
            "provider_calls": {"gemini": 0, "elevenlabs": 0, "youtube": 0},
        }
    except Exception as exc:
        cleanup_error = ""
        try:
            stop_validation_processes()
        except Exception as stop_exc:
            cleanup_error = f"; validation process cleanup failed: {stop_exc}"
        try:
            assert_validation_registry_cleaned()
        except Exception as cleanup_exc:
            cleanup_error += f"; validation registry cleanup failed: {cleanup_exc}"
        error_message = f"{exc}{cleanup_error}"
        evidence_path = copy_evidence("FAIL", {"error": error_message, "steps": steps})
        return {
            "status": "FAIL",
            "validation_root": str(validation_root),
            "evidence_root": str(evidence_path),
            "error": error_message,
            "steps": steps,
            "validation_uninstall_registry_leaf": validation_key_leaf,
            "production_uninstall_registry_snapshot_preserved": not cleanup_error,
        }


def write_payload_scripts(payload_dir: Path, complete_payload: Path, manifest: dict) -> None:
    write_text(payload_dir / "install.cmd", INSTALL_CMD)
    write_text(payload_dir / "install.ps1", INSTALL_PS1, ps1=True)
    shutil.copy2(complete_payload, payload_dir / "cp13a1_complete_payload.zip")
    write_json(payload_dir / "EXPECTED_INSTALL_MANIFEST.json", manifest)


def verify_protected_hashes() -> None:
    expected = [
        (CP12B_ZIP, CP12B_SHA),
        (ACCEPTED_MP4, ACCEPTED_SHA),
        (CP13A_INSTALLER, CP13A_SHA),
    ]
    for path, digest in expected:
        if sha256_file(path) != digest:
            raise SystemExit(f"protected hash mismatch: {path}")


def write_release_files(installer_hash: str, manifest: dict, payload_inventory: dict, toolchain: dict, gate: dict, validation: dict) -> None:
    release_manifest = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "application_version": "CP13A1",
        "build_commit": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip(),
        "installer_filename": INSTALLER.name,
        "installer_size": INSTALLER.stat().st_size,
        "installer_sha256": installer_hash,
        "expected_install_manifest": EXPECTED_MANIFEST.name,
        "expected_installed_file_count": manifest["installed_tree_summary"]["file_count"],
        "expected_installed_size_bytes": manifest["installed_tree_summary"]["total_size_bytes"],
        "bundled_backend_version": "0.2.0",
        "simple_ui_asset_version": "cp13a",
        "operator_ui_asset_version": "cp09c",
        "database_schema_version": "0009_subtitle_tracks",
        "target_windows_architecture": "x64",
        "installation_mode": "per-user-no-admin",
        "default_install_path": "%LOCALAPPDATA%\\Programs\\ToolAutoSubBeta",
        "default_user_data_path": "%LOCALAPPDATA%\\ToolAutoSubBeta\\data",
        "payload_packaging_strategy": "single_complete_payload_zip_with_precompile_expected_install_manifest",
        "canonical_release": "CP12B Full Portable",
        "source_baseline": {
            "name": "CP12B Full Portable",
            "release_id": "CP12B_WINDOWS_FULL_PORTABLE_CREATIVE_IMPORT_BETA",
            "package_path": "release\\CP12B\\tool_auto_sub_windows_full_portable_cp12b.zip",
            "package_sha256": CP12B_SHA,
        },
        "root_cause": {
            "failed_release": "CP13A",
            "external_verdict": "CP13A_EXTERNAL_MACHINE_BETA_FAIL",
            "reason": "INCOMPLETE_INSTALL_PAYLOAD_MAIN_APPLICATION_MISSING",
            "repair": "CP13A1 builds and validates a complete installed-root payload; addons cannot be the only installed payload.",
            "superseded_local_installer_sha256": OLD_DEFECTIVE_INSTALLER_SHA,
            "local_product_defect": "CP13A1 installer previously rejected bare executable shortcut targets and omitted the uninstall Start Menu shortcut.",
            "product_fix": "Resolve shortcut targets, propagate public /Q mode through install.cmd to install.ps1 -Quiet, and replace IExpress with a custom ZIP-overlay bootstrap EXE for larger offline ASR payloads.",
        },
        "complete_payload": payload_inventory,
        "offline_asr": payload_inventory.get("offline_asr"),
        "offline_translation": payload_inventory.get("offline_translation"),
        "installer_toolchain": toolchain,
        "provider_disabled_state": {"gemini": "disabled", "elevenlabs": "disabled", "youtube": "disabled", "upload": "disabled", "publish": "disabled"},
        "build_storage_preflight_result": gate,
        "validation_summary": validation,
        "protected_hashes": {"cp12b_zip": CP12B_SHA, "accepted_mp4": ACCEPTED_SHA, "cp13a_installer": CP13A_SHA},
        "machine_validation": {
            "claim": "CP13A1_COMPLETE_INSTALL_PAYLOAD_AND_UNINSTALL_SHORTCUT_MACHINE_PASS",
            "status": validation["status"],
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "old_evidence_root": r"C:\Users\ADMIN\AppData\Local\Temp\cp13a1_validation_9v4ar8n6",
            "old_evidence_status": "superseded_due_to_product_defect",
            "new_evidence_root": validation.get("evidence_root"),
            "uninstall_shortcut_validated": True,
        },
        "external_machine_beta": "pending",
        "publication": "manual_handoff_only",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(RELEASE_DIR / "RELEASE_MANIFEST.json", release_manifest)
    (RELEASE_DIR / "RELEASE_NOTES.md").write_text(
        "\n".join(
            [
                "# CP13A1 Complete Payload Hotfix",
                "",
                "Release ID: `CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX`",
                "",
                "CP13A failed external beta because the installed root contained only the OCR addon payload.",
                "CP13A1 replaces the installer payload strategy with one complete installed-root ZIP and an expected install manifest.",
                "Installation self-checks the launcher, backend, runtime, Simple UI, FFmpeg, OCR addon, migrations and critical hashes before creating shortcuts.",
                "The offline Chinese-to-English Argos model is bundled with its local runtime configuration and license notice; user workflows do not download it.",
                "A prior local CP13A1 installer artifact omitted the Uninstall Start Menu shortcut because bare `powershell.exe` was checked with a filesystem-only `Test-Path` call.",
                "This rebuild resolves executable shortcut targets with Windows command resolution before writing shortcuts.",
                "The uninstall shortcut is now inspected and invoked during machine validation.",
                "The installer now passes `/Q` to the embedded wrapper, which invokes `install.ps1 -Quiet` without requiring an operator environment variable.",
                "IExpress was replaced by a custom ZIP-overlay bootstrap EXE after the larger offline ASR payload produced a multivolume cabinet that failed before `install.cmd` could run.",
                "Interactive installation remains prompt-driven, while quiet failures are logged with a unique install-attempt ID and return a non-zero child exit code.",
                f"Superseded installer SHA-256: `{OLD_DEFECTIVE_INSTALLER_SHA}`.",
                f"Current installer SHA-256: `{installer_hash}`.",
                "CP12B remains the canonical release until external-machine beta acceptance promotes a newer release.",
                "Gemini, ElevenLabs, upload and publish remain disabled.",
                "External-machine PASS remains pending until this hotfix is installed on the external beta machine.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (RELEASE_DIR / "BETA_TEST_5_MINUTES.txt").write_text(beta_guide(INSTALLER.stat().st_size, installer_hash), encoding="utf-8")
    sums = {
        INSTALLER.name: installer_hash,
        EXPECTED_MANIFEST.name: sha256_file(EXPECTED_MANIFEST),
        "RELEASE_MANIFEST.json": sha256_file(RELEASE_DIR / "RELEASE_MANIFEST.json"),
        "RELEASE_NOTES.md": sha256_file(RELEASE_DIR / "RELEASE_NOTES.md"),
        "BETA_TEST_5_MINUTES.txt": sha256_file(RELEASE_DIR / "BETA_TEST_5_MINUTES.txt"),
    }
    (RELEASE_DIR / "SHA256SUMS.txt").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())), encoding="utf-8")


def main() -> None:
    verify_protected_hashes()
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    projected = CP12B_ZIP.stat().st_size + (FFMPEG_ROOT / "bin" / "ffmpeg.exe").stat().st_size + (FFMPEG_ROOT / "bin" / "ffprobe.exe").stat().st_size + 500 * 1024 * 1024
    gate = storage_preflight("package", ROOT, projected_workspace_bytes=projected, safety_reserve_bytes=PACKAGE_SAFETY_RESERVE_BYTES)
    if not gate["passed"]:
        raise SystemExit(json.dumps(gate, indent=2))

    with tempfile.TemporaryDirectory(prefix="cp13a1_build_") as temp_name:
        temp_root = Path(temp_name)
        stage = temp_root / "installed_root_stage"
        stage.mkdir()
        stage_inventory = stage_complete_payload(stage)
        expected_manifest = stage_inventory["expected_install_manifest"]
        write_json(EXPECTED_MANIFEST, expected_manifest)
        complete_payload = temp_root / "cp13a1_complete_payload.zip"
        payload_inventory = write_complete_payload_zip(stage, complete_payload, expected_manifest)
        payload_inventory["staged_file_count"] = expected_manifest["installed_tree_summary"]["file_count"]
        payload_inventory["staged_total_size_bytes"] = expected_manifest["installed_tree_summary"]["total_size_bytes"]
        payload_inventory["ffmpeg"] = stage_inventory["ffmpeg"]
        payload_inventory["offline_asr"] = stage_inventory["offline_asr"]
        payload_inventory["offline_translation"] = stage_inventory["offline_translation"]
        payload_inventory["ocr_batch_config"] = stage_inventory["ocr_batch_config"]
        payload_dir = temp_root / "installer_payload"
        payload_dir.mkdir()
        write_payload_scripts(payload_dir, complete_payload, expected_manifest)
        installer_payload_zip = temp_root / "cp13a1_installer_payload.zip"
        installer_payload = write_installer_payload_zip(payload_dir, installer_payload_zip)
        toolchain = build_overlay_installer(installer_payload_zip, temp_root / "ToolAutoSubBetaSetupBootstrap.cs")
        toolchain["installer_payload"] = installer_payload

    installer_hash = sha256_file(INSTALLER)
    validation = validate_installer(expected_manifest)
    if validation["status"] != "PASS":
        raise SystemExit(json.dumps(validation, indent=2))
    write_release_files(installer_hash, expected_manifest, payload_inventory, toolchain, gate, validation)
    print(json.dumps({"verdict": "CP13A1_COMPLETE_INSTALL_PAYLOAD_AND_UNINSTALL_SHORTCUT_MACHINE_PASS", "installer": str(INSTALLER), "sha256": installer_hash}, indent=2))


if __name__ == "__main__":
    main()
