from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADDON_ZIP = ROOT / "release" / "CP11C" / "tool_auto_sub_ocr_runtime_addon_cp11c.zip"
ADDON_MANIFEST = ROOT / "release" / "CP11C" / "cp11c_ocr_runtime_addon_manifest.json"
APP_ZIP = ROOT / "release" / "CP11A" / "tool_auto_sub_windows_portable_cp11a.zip"
APP_ZIP_SHA256 = "116cc2295f6bd53a3fe81d2f86ef463adcaf4ed3ed68bb4450c97ef02b92f315"

pytestmark = pytest.mark.skipif(
    not ADDON_ZIP.exists() or not APP_ZIP.exists(),
    reason="CP11A/CP11C binary artifacts were pruned by maintenance; CP12B/CP13A are the retained release artifacts.",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_cp11c_addon_manifest_and_zip_exist():
    assert ADDON_ZIP.exists()
    assert ADDON_MANIFEST.exists()
    manifest = json.loads(ADDON_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["addon_id"] == "cp11c-ocr-runtime-addon"
    assert manifest["compatible_application_zip_sha256"] == APP_ZIP_SHA256
    assert manifest["redistribution_decision"] == "verified_and_allowed"
    assert manifest["offline_behavior"] == "offline_after_installation"


def test_cp11c_addon_zip_contains_required_scripts_and_checksums():
    with zipfile.ZipFile(ADDON_ZIP) as zf:
        names = set(zf.namelist())
    assert "ocr_addon/install_ocr_addon.cmd" in names
    assert "ocr_addon/verify_ocr_addon.cmd" in names
    assert "ocr_addon/remove_ocr_addon.cmd" in names
    assert "ocr_addon/SHA256SUMS.txt" in names
    assert any("[Content_Types].xml" in name for name in names)


def test_cp11c_addon_zip_excludes_project_media_secrets_and_databases():
    blocked_suffixes = (".mp4", ".mov", ".mkv", ".db", ".db-wal", ".db-shm", ".env", ".key", ".pem")
    with zipfile.ZipFile(ADDON_ZIP) as zf:
        names = zf.namelist()
    allowed_ca_bundles = {
        "ocr_addon/runtime/.venv/lib/site-packages/certifi/cacert.pem",
        "ocr_addon/runtime/.venv/lib/site-packages/pip/_vendor/certifi/cacert.pem",
    }
    offenders = [
        name
        for name in names
        if name.lower().endswith(blocked_suffixes) and name.lower() not in allowed_ca_bundles
    ]
    assert offenders == []


def test_cp11c_checksum_script_uses_literal_paths_for_bracketed_files():
    with zipfile.ZipFile(ADDON_ZIP) as zf:
        verify_script = zf.read("ocr_addon/verify_ocr_addon.ps1").decode("utf-8")
    assert "Test-Path -LiteralPath $path" in verify_script
    assert "Get-FileHash -LiteralPath $path" in verify_script


def test_cp11a_application_zip_hash_is_unchanged():
    assert _sha256(APP_ZIP) == APP_ZIP_SHA256
