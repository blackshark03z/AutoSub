from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FULL_ZIP = ROOT / "release" / "CP11D" / "tool_auto_sub_windows_full_portable_cp11d.zip"
FULL_MANIFEST = ROOT / "release" / "CP11D" / "cp11d_windows_full_portable_manifest.json"
APP_ZIP = ROOT / "release" / "CP11A" / "tool_auto_sub_windows_portable_cp11a.zip"
ADDON_ZIP = ROOT / "release" / "CP11C" / "tool_auto_sub_ocr_runtime_addon_cp11c.zip"
ACCEPTED = ROOT / "data" / "projects" / "production_golden_path_cp09" / "exports" / "release_20260718_050055_88c16e_37394ab6_dir" / "final_video.mp4"

APP_SHA = "116cc2295f6bd53a3fe81d2f86ef463adcaf4ed3ed68bb4450c97ef02b92f315"
ADDON_SHA = "23e130a9ccbdb1bc13eb309a6ab5edf96e779252068ae686ccbed3ec1953e5a2"
ACCEPTED_SHA = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"

pytestmark = pytest.mark.skipif(
    not FULL_ZIP.exists() or not APP_ZIP.exists() or not ADDON_ZIP.exists(),
    reason="CP11A/CP11C/CP11D binary artifacts were pruned by maintenance; CP12B/CP13A are the retained release artifacts.",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_cp11d_full_package_manifest_and_zip_exist():
    assert FULL_ZIP.exists()
    assert FULL_MANIFEST.exists()
    manifest = json.loads(FULL_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["package_id"] == "CP11D_WINDOWS_FULL_PORTABLE_BETA"
    assert manifest["cp11a_package_sha256"] == APP_SHA
    assert manifest["cp11c_addon_sha256"] == ADDON_SHA
    assert manifest["accepted_release_sha256"] == ACCEPTED_SHA
    assert manifest["validation_result"] == "PASS"


def test_cp11d_zip_contains_one_click_launchers_and_local_ocr():
    with zipfile.ZipFile(FULL_ZIP) as archive:
        names = set(archive.namelist())
        config = json.loads(archive.read("Tool Auto Sub/operator/ocr_runtime_config.local.json").decode("utf-8"))
        portable = json.loads(archive.read("Tool Auto Sub/config/portable_config.json").decode("utf-8"))
    lower_names = {name.lower() for name in names}
    required = {
        "tool auto sub/start_tool.cmd",
        "tool auto sub/stop_tool.cmd",
        "tool auto sub/tool_status.cmd",
        "tool auto sub/open_operator_ui.cmd",
        "tool auto sub/collect_diagnostics.cmd",
        "tool auto sub/readme_first.txt",
        "tool auto sub/licenses_and_notices.txt",
        "tool auto sub/addons/ocr_runtime/addon_manifest.json",
        "tool auto sub/addons/ocr_runtime/runtime/.venv/scripts/python.exe",
        "tool auto sub/addons/ocr_runtime/runtime/models/ch_det/inference.pdmodel",
        "tool auto sub/addons/ocr_runtime/runtime/models/ch_rec/inference.pdmodel",
        "tool auto sub/addons/ocr_runtime/runtime/models/ch_cls/inference.pdmodel",
    }
    assert required.issubset(lower_names)
    assert config["discovery_source"] == "cp11d-full-portable-release-local"
    assert config["runtime_root"] == "addons\\ocr_runtime\\runtime"
    assert portable["ocr_runtime_path"] == "addons\\ocr_runtime"
    assert portable["provider_calls_enabled"] is False
    assert portable["upload_publish_enabled"] is False


def test_cp11d_package_excludes_private_media_secrets_and_dev_paths():
    blocked_suffixes = (".mp4", ".mov", ".mkv", ".db", ".db-wal", ".db-shm", ".env", ".key", ".sqlite")
    allowed = {
        "tool auto sub/runtime/venv/lib/site-packages/certifi/cacert.pem",
        "tool auto sub/runtime/venv/lib/site-packages/pip/_vendor/certifi/cacert.pem",
        "tool auto sub/addons/ocr_runtime/runtime/.venv/lib/site-packages/certifi/cacert.pem",
        "tool auto sub/addons/ocr_runtime/runtime/.venv/lib/site-packages/pip/_vendor/certifi/cacert.pem",
    }
    with zipfile.ZipFile(FULL_ZIP) as archive:
        names = archive.namelist()
        text_payloads = [
            archive.read(name).decode("utf-8", errors="ignore")
            for name in names
            if name.lower().endswith((".py", ".ps1", ".cmd", ".txt", ".json", ".md", ".ini", ".css", ".js", ".html"))
        ]
    offenders = [name for name in names if name.lower().endswith(blocked_suffixes) and name.lower() not in allowed]
    assert offenders == []
    combined_text = "\n".join(text_payloads)
    assert "D:\\tool_auto_sub_ocr_runtime" not in combined_text
    assert "D:\\tool_auto_sub_worker_handoff_v0.2" not in combined_text


def test_cp11d_preserves_existing_authoritative_hashes():
    assert _sha256(APP_ZIP) == APP_SHA
    assert _sha256(ADDON_ZIP) == ADDON_SHA
    assert _sha256(ACCEPTED) == ACCEPTED_SHA
