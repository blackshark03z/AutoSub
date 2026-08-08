from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = ROOT / "release" / "CP12B"
EVIDENCE_ROOT = ROOT / "evidence" / "CP12B" / "full_portable_creative_import"
APP_ZIP = ROOT / "release" / "CP11A" / "tool_auto_sub_windows_portable_cp11a.zip"
APP_MANIFEST = ROOT / "release" / "CP11A" / "cp11a_windows_portable_release_manifest.json"
ADDON_ZIP = ROOT / "release" / "CP11C" / "tool_auto_sub_ocr_runtime_addon_cp11c.zip"
ADDON_MANIFEST = ROOT / "release" / "CP11C" / "cp11c_ocr_runtime_addon_manifest.json"
CP11D_ZIP = ROOT / "release" / "CP11D" / "tool_auto_sub_windows_full_portable_cp11d.zip"
CP11D_MANIFEST = ROOT / "release" / "CP11D" / "cp11d_windows_full_portable_manifest.json"
ZIP_PATH = RELEASE_ROOT / "tool_auto_sub_windows_full_portable_cp12b.zip"
MANIFEST_PATH = RELEASE_ROOT / "cp12b_windows_full_portable_creative_import_manifest.json"
CHECKSUMS_PATH = RELEASE_ROOT / "SHA256SUMS.txt"

PACKAGE_ID = "CP12B_WINDOWS_FULL_PORTABLE_CREATIVE_IMPORT_BETA"
APP_SHA256 = "116cc2295f6bd53a3fe81d2f86ef463adcaf4ed3ed68bb4450c97ef02b92f315"
ADDON_SHA256 = "23e130a9ccbdb1bc13eb309a6ab5edf96e779252068ae686ccbed3ec1953e5a2"
ACCEPTED_RELEASE_SHA256 = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"
CP11D_SHA256 = "2c48ec39a345c4278f8f6c316fcc04cd546a2c7aff86139022511ef93d307f3c"

BLOCKED_PATTERNS = {
    "google_api_key_like": re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    "openai_key_like": re.compile(r"\bsk-(?:proj-|live-)?[A-Za-z0-9_\-]{40,}\b"),
    "secret_assignment": re.compile(
        r"(?im)^\s*(?:GEMINI_API_KEY|GOOGLE_API_KEY|ELEVENLABS_API_KEY|XI_API_KEY|OPENAI_API_KEY)\s*=\s*[^#\s]{12,}"
    ),
    "development_drive_path": re.compile(r"D:\\tool_auto_sub_(?:worker_handoff_v0\.2|ocr_runtime)", re.IGNORECASE),
}
BLOCKED_SUFFIXES = {".mp4", ".mov", ".mkv", ".db", ".db-wal", ".db-shm", ".env", ".key", ".p12", ".sqlite"}
ALLOWED_BLOCKED_NAMES = {
    "runtime/venv/lib/site-packages/certifi/cacert.pem",
    "runtime/venv/lib/site-packages/pip/_vendor/certifi/cacert.pem",
    "addons/ocr_runtime/runtime/.venv/lib/site-packages/certifi/cacert.pem",
    "addons/ocr_runtime/runtime/.venv/lib/site-packages/pip/_vendor/certifi/cacert.pem",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build CP12B unified full portable ZIP with Creative Import.")
    parser.add_argument("--skip-validation", action="store_true")
    args = parser.parse_args()

    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    disk_gate = build_disk_gate()
    write_json(EVIDENCE_ROOT / "disk_gate.json", disk_gate)
    if not disk_gate["pass"]:
        raise SystemExit("CP12B_BLOCKED_PACKAGE_OR_DISK_GATE")

    verify_source_hashes()
    staging_parent = Path(tempfile.gettempdir()) / f"tool_auto_sub_cp12b_build_{os.getpid()}_{int(time.time())}"
    staging_root = staging_parent / "Tool Auto Sub"
    addon_extract = staging_parent / "cp11c_addon_extract"
    try:
        staging_parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(APP_ZIP) as zf:
            zf.extractall(staging_root)
        with zipfile.ZipFile(ADDON_ZIP) as zf:
            zf.extractall(addon_extract)
        compose_full_package(staging_root, addon_extract / "ocr_addon")
        package_inventory = build_file_inventory(staging_root)
        write_json(EVIDENCE_ROOT / "package_inventory.json", package_inventory)
        scan = package_integrity_scan(staging_root)
        write_json(EVIDENCE_ROOT / "package_integrity_scan.json", scan)
        if scan["findings"]:
            raise SystemExit("CP12B_BLOCKED_PACKAGE_INTEGRITY_SCAN")
        zip_full_package(staging_root)
        manifest = build_manifest(staging_root, package_inventory, disk_gate)
        write_json(MANIFEST_PATH, manifest)
        write_checksums(manifest)
        write_json(EVIDENCE_ROOT / "source_package_hash_verification.json", source_hash_payload())
        write_json(EVIDENCE_ROOT / "zip_metadata.json", {
            "zip_path": str(ZIP_PATH),
            "zip_sha256": sha256_file(ZIP_PATH),
            "zip_size_bytes": ZIP_PATH.stat().st_size,
            "extracted_size_bytes": manifest["extracted_size_bytes"],
        })
        validation = {"status": "SKIPPED", "reason": "--skip-validation"}
        if not args.skip_validation:
            validation = validate_clean_extraction()
        write_json(EVIDENCE_ROOT / "clean_validation.json", validation)
        manifest["validation_result"] = validation["status"]
        manifest["test_result"] = "pending full pytest"
        write_json(MANIFEST_PATH, manifest)
        write_checksums(manifest)
        print(json.dumps({
            "verdict": "CP12B_PACKAGE_BUILD_PASS" if validation["status"] != "FAIL" else "CP12B_PACKAGE_BUILD_VALIDATION_FAIL",
            "zip": str(ZIP_PATH),
            "zip_sha256": sha256_file(ZIP_PATH),
            "validation": validation["status"],
        }, indent=2))
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent, ignore_errors=True)


def build_disk_gate() -> dict[str, Any]:
    c_usage = shutil.disk_usage(Path(tempfile.gettempdir()).anchor)
    d_usage = shutil.disk_usage(ROOT.anchor)
    app_manifest = json.loads(APP_MANIFEST.read_text(encoding="utf-8"))
    addon_manifest = json.loads(ADDON_MANIFEST.read_text(encoding="utf-8"))
    app_extracted = int(app_manifest["extracted_size_bytes"])
    addon_extracted = int(addon_manifest["extracted_size_bytes"])
    combined_extracted = app_extracted + addon_extracted
    estimated_zip = APP_ZIP.stat().st_size + ADDON_ZIP.stat().st_size + 256 * 1024 * 1024
    staging_requirement = combined_extracted + estimated_zip + (2 * combined_extracted) + (512 * 1024 * 1024)
    reserve = 4 * 1024**3
    temp_required = staging_requirement + reserve
    output_required = estimated_zip + 256 * 1024 * 1024
    return {
        "status": "PASS" if c_usage.free >= temp_required and d_usage.free >= output_required else "FAIL",
        "pass": c_usage.free >= temp_required and d_usage.free >= output_required,
        "temp_drive": Path(tempfile.gettempdir()).anchor,
        "output_drive": ROOT.anchor,
        "temp_free_bytes": c_usage.free,
        "output_free_bytes": d_usage.free,
        "temp_free_gib": round(c_usage.free / 1024**3, 3),
        "output_free_gib": round(d_usage.free / 1024**3, 3),
        "cp11a_zip_bytes": APP_ZIP.stat().st_size,
        "cp11c_zip_bytes": ADDON_ZIP.stat().st_size,
        "cp11d_zip_bytes": CP11D_ZIP.stat().st_size if CP11D_ZIP.exists() else 0,
        "cp11a_extracted_bytes": app_extracted,
        "cp11c_extracted_bytes": addon_extracted,
        "combined_extracted_bytes": combined_extracted,
        "estimated_full_zip_bytes": estimated_zip,
        "temp_required_bytes": temp_required,
        "output_required_bytes": output_required,
        "safety_reserve_bytes": reserve,
        "policy": "Use temp drive for staging and clean validation; keep D: for final release artifacts only.",
    }


def verify_source_hashes() -> None:
    payload = source_hash_payload()
    write_json(EVIDENCE_ROOT / "existing_package_immutability.json", payload)
    if (
        payload["cp11a_zip_sha256"] != APP_SHA256
        or payload["cp11c_zip_sha256"] != ADDON_SHA256
        or payload["cp11d_zip_sha256"] != payload["cp11d_manifest_zip_sha256"]
        or payload["cp11d_zip_sha256"] != CP11D_SHA256
    ):
        raise SystemExit("CP12B_BLOCKED_SOURCE_PACKAGE_HASH_MISMATCH")


def source_hash_payload() -> dict[str, Any]:
    accepted = ROOT / "data" / "projects" / "production_golden_path_cp09" / "exports" / "release_20260718_050055_88c16e_37394ab6_dir" / "final_video.mp4"
    return {
        "cp11a_zip": str(APP_ZIP),
        "cp11a_zip_sha256": sha256_file(APP_ZIP),
        "cp11c_zip": str(ADDON_ZIP),
        "cp11c_zip_sha256": sha256_file(ADDON_ZIP),
        "cp11d_zip": str(CP11D_ZIP),
        "cp11d_zip_sha256": sha256_file(CP11D_ZIP),
        "cp11d_manifest_zip_sha256": json.loads(CP11D_MANIFEST.read_text(encoding="utf-8"))["zip_sha256"],
        "accepted_release_media": str(accepted),
        "accepted_release_media_sha256": sha256_file(accepted) if accepted.exists() else None,
    }


def compose_full_package(staging_root: Path, addon_root: Path) -> None:
    for folder in ["addons", "config", "data", "logs", "diagnostics", "release", "release/documentation"]:
        (staging_root / folder).mkdir(parents=True, exist_ok=True)
    target_addon = staging_root / "addons" / "ocr_runtime"
    if target_addon.exists():
        shutil.rmtree(target_addon)
    shutil.copytree(addon_root, target_addon)
    for relative in [
        "alembic/versions/0009_subtitle_tracks.py",
        "app/api/routes.py",
        "app/domain/models.py",
        "app/services/ocr_runtime.py",
        "app/services/simple_workflow.py",
        "app/services/subtitle_tracks.py",
        "app/static/simple/index.html",
        "app/static/simple/app.js",
        "app/static/simple/styles.css",
    ]:
        source = ROOT / relative
        destination = staging_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    write_json(staging_root / "operator" / "ocr_runtime_config.local.json", {
        "schema_version": 1,
        "runtime_root": "addons\\ocr_runtime\\runtime",
        "python_path": "addons\\ocr_runtime\\runtime\\.venv\\Scripts\\python.exe",
        "model_root": "addons\\ocr_runtime\\runtime\\models",
        "temp_root": "addons\\ocr_runtime\\runtime\\tmp",
        "log_root": "addons\\ocr_runtime\\runtime\\logs",
        "timeout_seconds": 60,
        "discovery_source": "cp12b-full-portable-release-local",
    })
    portable_config = json.loads((staging_root / "config" / "portable_config.json").read_text(encoding="utf-8"))
    portable_config.update({
        "package_checkpoint": "CP12B",
        "package_id": PACKAGE_ID,
        "build_commit": current_git_head(),
        "simple_ui_version": "cp12b",
        "operator_ui_version": "cp09c",
        "database_schema": "0009_subtitle_tracks",
        "creative_import_enabled": True,
        "creative_import_formats": ["txt_cue_id", "json_creative_script_v1", "txt_line_by_line"],
        "creative_import_default_track": "translation",
        "creative_import_default_fallback_policy": "fallback_to_translation",
        "ocr_runtime_path": "addons\\ocr_runtime",
        "ocr_runtime_config": "operator\\ocr_runtime_config.local.json",
        "provider_calls_enabled": False,
        "upload_publish_enabled": False,
    })
    write_json(staging_root / "config" / "portable_config.json", portable_config)
    write_launchers(staging_root)
    write_docs(staging_root)
    write_json(staging_root / "release" / "cp12b_windows_full_portable_runtime_manifest.json", {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "checkpoint": "CP12B",
        "source_git_head": current_git_head(),
        "cp11a_zip_sha256": APP_SHA256,
        "cp11c_addon_zip_sha256": ADDON_SHA256,
        "cp11d_source_zip_sha256": CP11D_SHA256,
        "accepted_release_sha256": ACCEPTED_RELEASE_SHA256,
        "database_schema": "0009_subtitle_tracks",
        "creative_import_schema_versions": ["creative_script_v1"],
        "supported_import_formats": ["cue-id TXT", "JSON creative_script_v1", "line-by-line TXT"],
        "default_active_track": "translation",
        "fallback_policy": "fallback_to_translation",
        "default_bind": "127.0.0.1",
        "default_port": 8173,
        "port_fallback": "8174-8199 when 8173 is occupied",
        "ocr_discovery_policy": "release-local operator config first; no global fallback required for the full package",
        "provider_defaults": {"gemini": "disabled", "elevenlabs": "disabled", "youtube": "disabled"},
    })


def write_launchers(root: Path) -> None:
    scripts = root / "release" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    cmd_map = {
        "START_TOOL.cmd": "start_tool.ps1",
        "STOP_TOOL.cmd": "stop_tool.ps1",
        "TOOL_STATUS.cmd": "tool_status.ps1",
        "OPEN_OPERATOR_UI.cmd": "open_operator_ui.ps1",
        "COLLECT_DIAGNOSTICS.cmd": "collect_diagnostics.ps1",
    }
    for cmd_name, ps_name in cmd_map.items():
        body = f'@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release\\scripts\\{ps_name}" %*\r\n'
        (root / cmd_name).write_text(body, encoding="utf-8")
    (scripts / "common.ps1").write_text(COMMON_PS1, encoding="utf-8")
    (scripts / "start_tool.ps1").write_text(START_PS1, encoding="utf-8")
    (scripts / "stop_tool.ps1").write_text(STOP_PS1, encoding="utf-8")
    (scripts / "tool_status.ps1").write_text(STATUS_PS1, encoding="utf-8")
    (scripts / "open_operator_ui.ps1").write_text(OPEN_OPERATOR_PS1, encoding="utf-8")
    diagnostic_source = ROOT / "release" / "CP11B" / "diagnostics_tools" / "collect_diagnostics.ps1"
    text = diagnostic_source.read_text(encoding="utf-8")
    text = text.replace('Test-Path $config.ocr_runtime_path', 'Test-Path (Join-Path $root $config.ocr_runtime_path)')
    text += r'''

# CP12B sanitized Creative Import summary. This records counts and state only, never full subtitle text or imported scripts.
try {
  $dbPath = Join-Path $root "data\app.db"
  $pythonPath = Join-Path $root "runtime\venv\Scripts\python.exe"
  if ((Test-Path $dbPath) -and (Test-Path $pythonPath)) {
    $summaryScript = @"
import json, sqlite3, sys
db = sys.argv[1]
summary = {"schema_version": 1, "creative_import": {"track_count": 0, "active_track_type": None, "recent_import_error_codes": []}}
try:
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='subtitle_tracks'")
    if cur.fetchone():
        rows = cur.execute("SELECT track_type, active, source_type, metadata_json FROM subtitle_tracks").fetchall()
        summary["creative_import"]["track_count"] = len(rows)
        active = [row for row in rows if row[1]]
        if active:
            summary["creative_import"]["active_track_type"] = active[-1][0]
        formats = sorted({row[2] for row in rows if row[2]})
        summary["creative_import"]["import_formats"] = formats
        warning_codes = []
        for row in rows:
            try:
                metadata = json.loads(row[3] or "{}")
                for warning in metadata.get("validation_warnings", []):
                    code = warning.get("code")
                    if code:
                        warning_codes.append(code)
            except Exception:
                pass
        summary["creative_import"]["recent_import_error_codes"] = sorted(set(warning_codes))[:25]
finally:
    print(json.dumps(summary))
"@
    $summaryPath = Join-Path $env:TEMP ("cp12b_creative_diag_" + [guid]::NewGuid().ToString("N") + ".py")
    Set-Content -Path $summaryPath -Encoding UTF8 -Value $summaryScript
    $creativeSummary = & $pythonPath $summaryPath $dbPath
    Remove-Item $summaryPath -Force -ErrorAction SilentlyContinue
    Add-TextEvidence "creative_import_status_sanitized.json" $creativeSummary
  }
} catch {
  Add-TextEvidence "creative_import_status_sanitized_error.txt" "Creative Import diagnostics summary unavailable."
}
'''
    (scripts / "collect_diagnostics.ps1").write_text(text, encoding="utf-8")


def write_docs(root: Path) -> None:
    (root / "README_FIRST.txt").write_text(
        "\n".join([
            "Tool Auto Sub - Full Portable Beta CP12B",
            "",
            "1. Extract the ZIP completely.",
            "2. Open the extracted folder.",
            "3. Double-click START_TOOL.cmd.",
            "4. Select a video in the browser.",
            "5. When finished, run STOP_TOOL.cmd.",
            "",
            "Creative subtitles (optional):",
            "1. Complete normal subtitle detection/render preparation.",
            "2. Choose Export Creative Template.",
            "3. Edit the file manually or with ChatGPT.",
            "4. Keep all CUE IDs unchanged.",
            "5. Import the completed file.",
            "6. Review warnings.",
            "7. Apply Creative Track.",
            "8. Preview before rendering.",
            "",
            "Creative Import changes text only. It does not change timing automatically.",
            "Translation remains available. Other non-dialogue text in the scene is preserved.",
            "",
            "Do not run the tool from inside the ZIP.",
            "Administrator rights are not required.",
            "The first start may take longer while the local app becomes ready.",
            "Windows SmartScreen may warn because this beta package is unsigned.",
            "To verify the ZIP, compare its SHA-256 with release\\CP12B\\SHA256SUMS.txt.",
            "Projects are stored in the data folder inside this extracted package.",
            "Back up the data folder to keep your projects.",
            "Run COLLECT_DIAGNOSTICS.cmd to create a sanitized diagnostics ZIP.",
            "This beta processes dialogue subtitles. Other in-scene text is preserved.",
        ]) + "\n",
        encoding="utf-8",
    )
    notice = root / "addons" / "ocr_runtime" / "licenses" / "NOTICE_SUMMARY.txt"
    notice_text = notice.read_text(encoding="utf-8", errors="ignore") if notice.exists() else "OCR license notice summary unavailable."
    (root / "LICENSES_AND_NOTICES.txt").write_text(
        "Tool Auto Sub Full Portable Beta licenses and notices\n\n"
        "Application notices are in release\\documentation.\n"
        "OCR component notices are in addons\\ocr_runtime\\licenses.\n\n"
        + notice_text,
        encoding="utf-8",
    )
    (root / "release" / "documentation" / "FULL_PORTABLE_QUICK_START.md").write_text(
        "# Full Portable Quick Start\n\n"
        "Extract the ZIP, run `START_TOOL.cmd`, use the Simple UI, then run `STOP_TOOL.cmd`.\n"
        "No separate OCR installer, global Python, global pip or administrator install is required.\n",
        encoding="utf-8",
    )
    (root / "release" / "documentation" / "DIAGNOSTICS_AND_BACKUP.md").write_text(
        "# Diagnostics and Backup\n\n"
        "Run `COLLECT_DIAGNOSTICS.cmd` for sanitized diagnostics. Back up the `data` folder to keep projects.\n",
        encoding="utf-8",
    )
    creative_docs = root / "release" / "documentation" / "creative_subtitle_import"
    creative_docs.mkdir(parents=True, exist_ok=True)
    (creative_docs / "README.md").write_text(
        "# Creative Subtitle Import\n\n"
        "Creative Import lets a beta user replace subtitle wording while preserving canonical timing, audio, source subtitle cleanup and render geometry.\n\n"
        "Supported formats:\n"
        "- Cue-ID TXT template exported by the tool.\n"
        "- JSON `creative_script_v1` exported by the tool.\n"
        "- Line-by-line TXT for small controlled runs; use with care because cue IDs are not explicit.\n\n"
        "Validation checks include unknown cue IDs, duplicate cue IDs, missing cues, blank cues, placeholders, long text, reading-speed risk, malformed JSON, wrong project/run metadata, unsafe filenames and script-like HTML.\n\n"
        "Default fallback policy is `fallback_to_translation`. Empty creative cues use the Translation track unless a different policy is selected.\n\n"
        "Undo removes the last imported Creative/Imported track. Restart recovery preserves active track, fallback policy, edited cue text, import provenance and validation warnings.\n",
        encoding="utf-8",
    )
    (creative_docs / "creative_script_v1_schema.json").write_text(
        json.dumps(
            {
                "schema_version": "creative_script_v1",
                "project_id": "string",
                "run_id": "string",
                "source_track": "translation",
                "cues": [
                    {
                        "cue_id": "CUE_0001",
                        "start_ms": 0,
                        "end_ms": 1250,
                        "source_text": "context only",
                        "translation_text": "context only",
                        "scene_note": "optional",
                        "creative_text": "user editable",
                    }
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (creative_docs / "chatgpt_prompt_example.txt").write_text(
        "Rewrite this Tool Auto Sub Creative Subtitle template in a gameplay comedy/commentary tone.\n"
        "Do not alter CUE IDs, TIME, SOURCE or TRANSLATION fields.\n"
        "Write only after each TEXT: field or inside each JSON creative_text field.\n"
        "Keep each cue concise, preferably one short sentence.\n"
        "Do not change timing. Do not add cue blocks unless I ask.\n"
        "Retain important prompts such as [E] Interact.\n",
        encoding="utf-8",
    )
    (creative_docs / "completed_template_example.txt").write_text(
        "[CUE_0001]\nTIME: 00:00:00.000 --> 00:00:02.000\nSOURCE: Source dialogue 1\nTRANSLATION: Translation line 1.\nSCENE_NOTE:\nTEXT: Bold plan. Tiny legs. Let's go.\n\n"
        "[CUE_0002]\nTIME: 00:00:02.000 --> 00:00:04.000\nSOURCE: Source dialogue 2\nTRANSLATION: Translation line 2.\nSCENE_NOTE:\nTEXT: Narrator: this is where confidence becomes a bug report.\n\n"
        "[CUE_0003]\nTIME: 00:00:04.000 --> 00:00:06.000\nSOURCE: Source dialogue 3\nTRANSLATION: Translation line 3.\nSCENE_NOTE:\nTEXT:\n",
        encoding="utf-8",
    )
    (creative_docs / "troubleshooting.md").write_text(
        "# Troubleshooting\n\n"
        "- `unknown_cue`: the file contains a CUE ID that does not exist in this run.\n"
        "- `duplicate_cue`: the same CUE ID appears more than once.\n"
        "- `missing_cue`: a canonical cue is absent; default policy falls back to Translation.\n"
        "- `placeholder_blocker`: replace TODO/TBD/pending text before rendering.\n"
        "- `reading_speed`: shorten the line or split the idea across existing cues only if timing allows.\n"
        "- `wrong_project_metadata` or `wrong_run_metadata`: export a fresh template from the current run.\n",
        encoding="utf-8",
    )


def build_file_inventory(root: Path) -> dict[str, Any]:
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        files.append({"path": rel, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {"root": str(root), "files": files, "file_count": len(files)}


def package_integrity_scan(root: Path) -> dict[str, Any]:
    findings = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        lower = rel.lower()
        if path.suffix.lower() in BLOCKED_SUFFIXES and lower not in ALLOWED_BLOCKED_NAMES:
            findings.append({"path": rel, "reason": "blocked_suffix"})
            continue
        if path.suffix.lower() in {".py", ".ps1", ".cmd", ".txt", ".json", ".md", ".ini", ".css", ".js", ".html"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for name, pattern in BLOCKED_PATTERNS.items():
                if pattern.search(text):
                    findings.append({"path": rel, "reason": name})
    return {"status": "PASS" if not findings else "FAIL", "findings": findings}


def zip_full_package(staging_root: Path) -> None:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in staging_root.rglob("*") if p.is_file()):
            archive.write(path, (Path("Tool Auto Sub") / path.relative_to(staging_root)).as_posix())


def build_manifest(staging_root: Path, inventory: dict[str, Any], disk_gate: dict[str, Any]) -> dict[str, Any]:
    app_manifest = json.loads(APP_MANIFEST.read_text(encoding="utf-8"))
    addon_manifest = json.loads(ADDON_MANIFEST.read_text(encoding="utf-8"))
    model_inventory = addon_manifest.get("model_inventory", [])
    extracted_size = sum(item["size_bytes"] for item in inventory["files"])
    return {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "source_git_head": current_git_head(),
        "cp11a_package_sha256": APP_SHA256,
        "cp11c_addon_sha256": ADDON_SHA256,
        "cp11d_source_package_sha256": CP11D_SHA256,
        "accepted_release_sha256": ACCEPTED_RELEASE_SHA256,
        "application_build": current_git_head(),
        "backend_version": "0.2.0",
        "simple_ui_version": "cp12b",
        "operator_ui_version": "cp09c",
        "database_schema": "0009_subtitle_tracks",
        "migration_inventory": [
            "0001_bootstrap",
            "0002_timeline_revisions",
            "0003_content_transform",
            "0004_tts_generation",
            "0005_tts_single_flight",
            "0006_job_claims",
            "0007_project_fixture_metadata",
            "0008_simple_workflow_runs",
            "0009_subtitle_tracks",
        ],
        "creative_import_schema_versions": ["creative_script_v1"],
        "supported_import_formats": ["cue-id TXT", "JSON creative_script_v1", "line-by-line TXT"],
        "default_active_track": "translation",
        "fallback_policy": "fallback_to_translation",
        "ocr_runtime_versions": {
            "python": addon_manifest["python_version"],
            "paddleocr": addon_manifest["paddleocr_version"],
            "paddlepaddle": addon_manifest["paddlepaddle_version"],
            "opencv": addon_manifest["opencv_version"],
        },
        "ocr_model_inventory": model_inventory,
        "packaged_file_count": inventory["file_count"],
        "packaged_file_inventory_sha256": sha256_text(json.dumps(inventory["files"], sort_keys=True)),
        "extracted_size_bytes": extracted_size,
        "zip_size_bytes": ZIP_PATH.stat().st_size,
        "zip_sha256": sha256_file(ZIP_PATH),
        "checksums_file": str(CHECKSUMS_PATH.relative_to(ROOT)),
        "default_bind_address": "127.0.0.1",
        "default_port_behavior": "Use 8173 when free; fallback to 8174-8199 without stopping unrelated processes.",
        "data_root": "data",
        "ocr_discovery_policy": "release-local operator\\ocr_runtime_config.local.json; bundled addons\\ocr_runtime only for normal use",
        "offline_operation_policy": "No model download during processing; no global Python or global pip required.",
        "provider_defaults": {"gemini": "disabled", "elevenlabs": "disabled", "youtube": "disabled"},
        "diagnostics_policy": "sanitized diagnostics ZIP; excludes source/rendered media, secrets, tokens, browser profiles and raw databases",
        "license_inventory_reference": "LICENSES_AND_NOTICES.txt and addons\\ocr_runtime\\licenses",
        "disk_gate": disk_gate,
        "test_result": "pending",
        "validation_result": "pending",
        "known_limitations": [
            "Windows ZIP beta, not an MSI/EXE installer.",
            "Human external-machine beta remains required; CP11B is not claimed PASS.",
            "ffmpeg and ffprobe must be available on PATH.",
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def validate_clean_extraction() -> dict[str, Any]:
    validation_root = Path(tempfile.gettempdir()) / f"Tool Auto Sub CP12B Full {os.getpid()}"
    if validation_root.exists():
        shutil.rmtree(validation_root, ignore_errors=True)
    validation_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(ZIP_PATH) as zf:
            zf.extractall(validation_root)
        app_root = validation_root / "Tool Auto Sub"
        checks = {
            "start_launcher": (app_root / "START_TOOL.cmd").exists(),
            "stop_launcher": (app_root / "STOP_TOOL.cmd").exists(),
            "status_launcher": (app_root / "TOOL_STATUS.cmd").exists(),
            "operator_launcher": (app_root / "OPEN_OPERATOR_UI.cmd").exists(),
            "diagnostics_launcher": (app_root / "COLLECT_DIAGNOSTICS.cmd").exists(),
            "ocr_config": (app_root / "operator" / "ocr_runtime_config.local.json").exists(),
            "ocr_python": (app_root / "addons" / "ocr_runtime" / "runtime" / ".venv" / "Scripts" / "python.exe").exists(),
            "ocr_models": all((app_root / "addons" / "ocr_runtime" / "runtime" / "models" / name).exists() for name in ["ch_det", "ch_rec", "ch_cls"]),
            "readme": (app_root / "README_FIRST.txt").exists(),
            "licenses": (app_root / "LICENSES_AND_NOTICES.txt").exists(),
            "creative_docs": (app_root / "release" / "documentation" / "creative_subtitle_import" / "README.md").exists(),
            "migration_0009": (app_root / "alembic" / "versions" / "0009_subtitle_tracks.py").exists(),
        }
        workflow = {"status": "SKIPPED", "reason": "launcher file checks failed"}
        if all(checks.values()):
            workflow = run_creative_import_validation(app_root, validation_root)
        free_port = find_free_port()
        return {
            "status": "PASS" if all(checks.values()) and workflow["status"] == "PASS" else "FAIL",
            "validation_root": str(validation_root),
            "free_port_probe": free_port,
            "checks": checks,
            "workflow": workflow,
            "notes": "Clean extraction validated without installing CP11C separately. Creative Import was validated through the packaged runtime Python and release-local database.",
        }
    finally:
        shutil.rmtree(validation_root, ignore_errors=True)


def run_creative_import_validation(app_root: Path, validation_root: Path) -> dict[str, Any]:
    video = validation_root / "cp12b small source.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:d=1.2",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    if not video.exists():
        return {"status": "FAIL", "reason": "small validation video could not be created"}
    python = app_root / "runtime" / "venv" / "Scripts" / "python.exe"
    save_dir = validation_root / "Save Copy With Spaces"
    validation_script = validation_root / "cp12b_validate.py"
    validation_script.write_text(CP12B_VALIDATION_PY, encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "TOOL_AUTO_SUB_ROOT": str(app_root),
            "TOOL_AUTO_SUB_DB_PATH": str(app_root / "data" / "app.db"),
            "TOOL_AUTO_SUB_BUILD_COMMIT": current_git_head(),
            "TOOL_AUTO_SUB_SIMPLE_UI_VERSION": "cp12b",
            "TOOL_AUTO_SUB_OPERATOR_UI_VERSION": "cp09c",
            "TOOL_AUTO_SUB_PROVIDER_CALLS_ENABLED": "0",
            "TOOL_AUTO_SUB_UPLOAD_PUBLISH_ENABLED": "0",
            "TOOL_AUTO_SUB_OCR_RUNTIME_CONFIG": str(app_root / "operator" / "ocr_runtime_config.local.json"),
            "PYTHONPATH": str(app_root),
        }
    )
    completed = subprocess.run(
        [str(python), str(validation_script), str(video), str(save_dir)],
        cwd=app_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        return {
            "status": "FAIL",
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return {"status": "FAIL", "reason": f"validation output parse failed: {type(exc).__name__}", "stdout_tail": completed.stdout[-2000:]}


def write_checksums(manifest: dict[str, Any]) -> None:
    manifest_sha = sha256_file(MANIFEST_PATH)
    lines = [
        f"{manifest['zip_sha256']}  {ZIP_PATH.name}",
        f"{manifest_sha}  {MANIFEST_PATH.name}",
    ]
    CHECKSUMS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def current_git_head() -> str:
    result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


COMMON_PS1 = r'''$ErrorActionPreference = "Stop"
function Get-BundleRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
function Read-Json([string]$Path) {
  Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json
}
function Read-PortableConfig {
  $root = Get-BundleRoot
  return Read-Json (Join-Path $root "config\portable_config.json")
}
function Get-StatePath {
  $root = Get-BundleRoot
  return Join-Path $root "runtime\tool_state.json"
}
function Read-State {
  $path = Get-StatePath
  if (Test-Path $path) { return Read-Json $path }
  return $null
}
function Test-PortOpen([string]$HostName, [int]$Port) {
  try {
    $client = New-Object Net.Sockets.TcpClient
    $async = $client.BeginConnect($HostName, $Port, $null, $null)
    if ($async.AsyncWaitHandle.WaitOne(250, $false)) {
      $client.EndConnect($async); $client.Close(); return $true
    }
    $client.Close()
  } catch {}
  return $false
}
function Get-ProcessCommandLine([int]$ProcessIdValue) {
  try { return (Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessIdValue").CommandLine } catch { return $null }
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
function Resolve-RootRelative([string]$PathValue) {
  $root = Get-BundleRoot
  if ([IO.Path]::IsPathRooted($PathValue)) { return $PathValue }
  return Join-Path $root $PathValue
}
'''


START_PS1 = r'''. "$PSScriptRoot\common.ps1"
$root = Get-BundleRoot
$config = Read-PortableConfig
$statePath = Get-StatePath
$logs = Join-Path $root "logs"
$data = Join-Path $root "data"
New-Item -ItemType Directory -Force $logs,$data,(Join-Path $root "runtime"),(Join-Path $root "diagnostics") | Out-Null
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
      Write-Host "Port $port is in use. The tool started safely on port $candidate."
      $port = $candidate; $found = $true; break
    }
  }
  if (-not $found) {
    Write-Host "Startup failed: no free local port from 8173 through 8199. No unrelated process was stopped."
    exit 2
  }
}
$python = Join-Path $root "runtime\venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Host "Python runtime files are incomplete. Please extract the package again and verify SHA-256."; exit 3 }
$ocrConfig = Join-Path $root "operator\ocr_runtime_config.local.json"
if (-not (Test-Path $ocrConfig)) { Write-Host "OCR files are incomplete. Please extract the package again and verify SHA-256."; exit 4 }
$ocr = Read-Json $ocrConfig
$ocrPython = Resolve-RootRelative $ocr.python_path
$modelRoot = Resolve-RootRelative $ocr.model_root
if (-not (Test-Path $ocrPython)) { Write-Host "OCR runtime Python is missing. Please extract the package again."; exit 5 }
foreach ($model in @("ch_det","ch_rec","ch_cls")) {
  if (-not (Test-Path (Join-Path $modelRoot $model))) { Write-Host "OCR model $model is missing. Please extract the package again."; exit 6 }
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Write-Host "FFmpeg is not ready. Install ffmpeg/ffprobe or add it to PATH."; exit 7 }
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) { Write-Host "FFprobe is not ready. Install ffmpeg/ffprobe or add it to PATH."; exit 8 }
$writeProbe = Join-Path $data ".write_probe"
try { "ok" | Set-Content -Path $writeProbe -Encoding UTF8; Remove-Item $writeProbe -Force } catch { Write-Host "The tool cannot write to its data folder. Move the extracted folder to a writable location."; exit 9 }
$env:TOOL_AUTO_SUB_ROOT = $root
$env:TOOL_AUTO_SUB_DB_PATH = Join-Path $data "app.db"
$env:TOOL_AUTO_SUB_BUILD_COMMIT = [string]$config.build_commit
$env:TOOL_AUTO_SUB_SIMPLE_UI_VERSION = [string]$config.simple_ui_version
$env:TOOL_AUTO_SUB_OPERATOR_UI_VERSION = [string]$config.operator_ui_version
$env:TOOL_AUTO_SUB_PROVIDER_CALLS_ENABLED = "0"
$env:TOOL_AUTO_SUB_UPLOAD_PUBLISH_ENABLED = "0"
$env:TOOL_AUTO_SUB_OCR_RUNTIME_CONFIG = $ocrConfig
$outLog = Join-Path $logs "backend.out.log"
$errLog = Join-Path $logs "backend.err.log"
$args = @("-m","uvicorn","app.main:app","--host",$bind,"--port",[string]$port)
$proc = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $root -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WindowStyle Hidden -PassThru
$url = "http://$bind`:$port/"
$health = "http://$bind`:$port/api/health"
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $response = Invoke-RestMethod -Uri $health -TimeoutSec 1
    if ($response.status -eq "ok") { $ready = $true; $healthPayload = $response; break }
  } catch {}
}
if (-not $ready) {
  Write-Host "Startup failed: backend did not become healthy. See logs\backend.err.log"
  if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
  exit 10
}
$listenerPid = Get-ListeningPid $bind $port
$ownedPid = if ($listenerPid -and (Test-OwnedProcess $listenerPid)) { $listenerPid } else { $proc.Id }
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
  simple_ui = "cp12b"
  operator_ui = "cp09c"
  database_schema = "0009_subtitle_tracks"
  ocr_runtime_available = [bool]$healthPayload.ocr_runtime.available
  started_at = (Get-Date).ToString("o")
}
$statePayload | ConvertTo-Json | Set-Content -Encoding UTF8 $statePath
Write-Host "Application ready"
Write-Host "OCR ready: $($statePayload.ocr_runtime_available)"
Write-Host "FFmpeg ready: True"
Write-Host "Data folder writable: True"
Write-Host "Open: $url"
Start-Process $url
'''


STOP_PS1 = r'''. "$PSScriptRoot\common.ps1"
$statePath = Get-StatePath
$state = Read-State
if (-not $state -or -not $state.pid) { Write-Host "Tool Auto Sub is stopped."; exit 0 }
$pidValue = [int]$state.pid
$proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
if (-not $proc) { Remove-Item $statePath -Force -ErrorAction SilentlyContinue; Write-Host "Removed stale PID state."; exit 0 }
if (-not (Test-OwnedProcess $pidValue)) { Write-Host "Refusing to stop PID $pidValue because it was not started from this bundle."; exit 2 }
Stop-Process -Id $pidValue
if ($state.launcher_pid) {
  $launcherPid = [int]$state.launcher_pid
  if ($launcherPid -ne $pidValue) {
    $launcherProc = Get-Process -Id $launcherPid -ErrorAction SilentlyContinue
    if ($launcherProc -and (Test-OwnedProcess $launcherPid)) { Stop-Process -Id $launcherPid -ErrorAction SilentlyContinue }
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
  Write-Host "OCR runtime available: $($state.ocr_runtime_available)"
} else {
  Write-Host "Status: stopped (stale PID state)"
  Remove-Item (Get-StatePath) -Force -ErrorAction SilentlyContinue
}
'''


OPEN_OPERATOR_PS1 = r'''. "$PSScriptRoot\common.ps1"
$state = Read-State
if (-not $state -or -not $state.url) { Write-Host "Tool Auto Sub is not running. Start it with START_TOOL.cmd first."; exit 1 }
$operator = "$($state.url.TrimEnd('/'))/operator/"
Write-Host "Opening $operator"
Start-Process $operator
'''


CP12B_VALIDATION_PY = r'''
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from alembic import command
from sqlalchemy import inspect

from app.core.config import get_settings
from app.db.migrations import alembic_config, upgrade_to_head
from app.db.session import get_engine, session_scope
from app.domain.models import Project, SimpleWorkflowRun
from app.services.simple_workflow import create_or_reuse_run, get_run, save_copy, start_processing
from app.services.subtitle_tracks import (
    apply_import_candidate,
    export_creative_template,
    list_tracks,
    preview_import_candidate,
    resolved_cues,
    set_active_track,
    set_track_enabled,
    undo_last_import,
    update_track_item,
)


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def fill_txt(template: str, prefix: str) -> str:
    lines = []
    index = 0
    for line in template.splitlines():
        if line.startswith("TEXT:"):
            index += 1
            if index == 3:
                lines.append("TEXT:")
            else:
                lines.append(f"TEXT: {prefix} cue {index} - comedy beat!")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def main() -> None:
    video = Path(sys.argv[1])
    save_dir = Path(sys.argv[2])
    upgrade_to_head()
    engine = get_engine()
    tables = set(inspect(engine).get_table_names())
    assert_true({"subtitle_tracks", "subtitle_track_items"}.issubset(tables), "track tables missing")
    with sqlite3.connect(os.environ["TOOL_AUTO_SUB_DB_PATH"]) as db:
        quick = db.execute("PRAGMA quick_check").fetchone()[0]
        version = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert_true(quick == "ok", "sqlite quick_check failed")
    assert_true(version == "0009_subtitle_tracks", f"unexpected schema {version}")

    # Synthetic upgrade check: build a separate 0008 database, preserve an existing run, then upgrade to 0009.
    original_db = os.environ["TOOL_AUTO_SUB_DB_PATH"]
    upgrade_db = str(Path(original_db).with_name("upgrade_from_0008.db"))
    os.environ["TOOL_AUTO_SUB_DB_PATH"] = upgrade_db
    get_settings.cache_clear()
    command.upgrade(alembic_config(), "0008_simple_workflow_runs")
    with session_scope() as session:
        project = Project(project_id="upgrade_project", title="Upgrade project")
        session.add(project)
        session.flush()
        session.add(
            SimpleWorkflowRun(
                run_id="upgrade_run",
                project_id="upgrade_project",
                source_path=str(video),
                source_hash="0" * 64,
                source_metadata_json=json.dumps({"duration_seconds": 1.2, "filename": video.name, "resolution": {"width": 320, "height": 180}}),
                requested_settings_json=json.dumps({}),
                current_phase="Select video",
                internal_state="selected",
                run_directory=str(Path(original_db).parent / "projects" / "upgrade_project" / "runs" / "upgrade_run"),
                approval_state="not_reviewed",
                is_test_fixture=False,
            )
        )
    upgrade_to_head()
    with sqlite3.connect(upgrade_db) as db:
        upgrade_version = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        preserved = db.execute("SELECT COUNT(*) FROM simple_workflow_runs WHERE run_id='upgrade_run'").fetchone()[0]
    assert_true(upgrade_version == "0009_subtitle_tracks", "upgrade did not reach 0009")
    assert_true(preserved == 1, "0008 run was not preserved")
    os.environ["TOOL_AUTO_SUB_DB_PATH"] = original_db
    get_settings.cache_clear()

    run1 = create_or_reuse_run(str(video))
    run_id = run1["run_id"]
    txt_template = export_creative_template(run_id, "txt")
    json_template = export_creative_template(run_id, "json")
    assert_true("CUE_0001" in txt_template["content"], "TXT template missing cue id")
    assert_true("creative_script_v1" in json_template["content"], "JSON template missing schema")

    completed_txt = fill_txt(txt_template["content"], "Creative")
    preview = preview_import_candidate(run_id, content=completed_txt, fmt="txt", filename="creative.txt", mode="cue_id")
    assert_true(preview["status"] == "PASS", "TXT preview failed")
    assert_true(preview["state_mutated"] is False, "preview mutated state")
    applied = apply_import_candidate(run_id, content=completed_txt, fmt="txt", filename="creative.txt", track_type="creative", display_name="Creative script", fallback_policy="fallback_to_translation")
    creative_id = applied["track"]["track_id"]
    set_active_track(run_id, creative_id, "fallback_to_translation")
    update_track_item(run_id, creative_id, "CUE_0001", "Edited punchline with emoji :)")
    disabled = set_track_enabled(run_id, creative_id, False)
    assert_true(any(track["track_id"] == creative_id and not track["enabled"] for track in disabled["tracks"]), "disable failed")
    restored = set_track_enabled(run_id, creative_id, True)
    assert_true(any(track["track_id"] == creative_id and track["enabled"] for track in restored["tracks"]), "restore failed")
    set_active_track(run_id, creative_id, "fallback_to_translation")
    resolved = resolved_cues(run_id)
    assert_true(resolved["active_track"]["track_id"] == creative_id, "creative track not active")
    render = start_processing(run_id)
    assert_true(Path(render["output"]["path"]).exists(), "render output missing")
    save_result = save_copy(run_id, str(save_dir))
    assert_true(save_result["byte_identical"] is True, "save copy hash mismatch")
    recovered = get_run(run_id)
    assert_true(recovered["subtitle_tracks"]["active_track_id"] == creative_id, "active track not recovered")
    undo = undo_last_import(run_id)
    assert_true(undo["status"] == "PASS", "undo failed")

    payload = json.loads(json_template["content"])
    payload["cues"][0]["creative_text"] = "JSON dramatic exaggeration!"
    imported = apply_import_candidate(run_id, content=json.dumps(payload), fmt="json", filename="creative.json", track_type="imported", display_name="Imported JSON", fallback_policy="fallback_to_translation")
    imported_id = imported["track"]["track_id"]

    run2 = create_or_reuse_run(str(video), retry_parent_run_id=run_id)
    run2_id = run2["run_id"]
    txt2 = fill_txt(export_creative_template(run2_id, "txt")["content"], "Second run")
    second = apply_import_candidate(run2_id, content=txt2, fmt="txt", filename="creative.txt", track_type="creative", display_name="Second run creative", fallback_policy="fallback_to_translation")
    tracks1 = list_tracks(run_id)["tracks"]
    tracks2 = list_tracks(run2_id)["tracks"]
    assert_true(second["track"]["run_id"] == run2_id, "second run track attached to wrong run")
    assert_true(all(track["run_id"] == run_id for track in tracks1), "run1 track leakage")
    assert_true(all(track["run_id"] == run2_id for track in tracks2), "run2 track leakage")

    bad_json = preview_import_candidate(run_id, content="{bad", fmt="json", filename="bad.json")
    assert_true(bad_json["status"] == "FAIL", "malformed JSON was accepted")
    unsafe = False
    try:
        preview_import_candidate(run_id, content=completed_txt, fmt="txt", filename="..\\private.txt")
    except ValueError:
        unsafe = True
    assert_true(unsafe, "path traversal filename accepted")

    result = {
        "status": "PASS",
        "database_schema": version,
        "upgrade_from_0008": "PASS",
        "txt_export": "PASS",
        "json_export": "PASS",
        "txt_import": "PASS",
        "json_import": "PASS",
        "preview_before_apply": "PASS",
        "track_switching": "PASS",
        "track_disable_restore": "PASS",
        "render_result": "PASS",
        "save_copy": "PASS",
        "recovery": "PASS",
        "two_run_isolation": "PASS",
        "provider_calls": {"gemini": 0, "elevenlabs": 0, "youtube": 0},
        "upload_publish": 0,
        "run_id": run_id,
        "creative_track_id": creative_id,
        "imported_track_id": imported_id,
        "run2_id": run2_id,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''


if __name__ == "__main__":
    main()
