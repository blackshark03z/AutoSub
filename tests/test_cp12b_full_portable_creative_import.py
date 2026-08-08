from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CP12B_ZIP = ROOT / "release" / "CP12B" / "tool_auto_sub_windows_full_portable_cp12b.zip"
CP12B_MANIFEST = ROOT / "release" / "CP12B" / "cp12b_windows_full_portable_creative_import_manifest.json"
CP11D_ZIP = ROOT / "release" / "CP11D" / "tool_auto_sub_windows_full_portable_cp11d.zip"
ACCEPTED = ROOT / "data" / "projects" / "production_golden_path_cp09" / "exports" / "release_20260718_050055_88c16e_37394ab6_dir" / "final_video.mp4"

CP11D_SHA = "2c48ec39a345c4278f8f6c316fcc04cd546a2c7aff86139022511ef93d307f3c"
ACCEPTED_SHA = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_cp12b_manifest_and_zip_exist_after_build():
    assert CP12B_ZIP.exists()
    assert CP12B_MANIFEST.exists()
    manifest = json.loads(CP12B_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["package_id"] == "CP12B_WINDOWS_FULL_PORTABLE_CREATIVE_IMPORT_BETA"
    assert manifest["simple_ui_version"] == "cp12b"
    assert manifest["database_schema"] == "0009_subtitle_tracks"
    assert manifest["fallback_policy"] == "fallback_to_translation"
    assert "JSON creative_script_v1" in manifest["supported_import_formats"]
    assert manifest["validation_result"] == "PASS"


def test_cp12b_zip_contains_creative_import_runtime_surface():
    with zipfile.ZipFile(CP12B_ZIP) as archive:
        names = {name.lower() for name in archive.namelist()}
        index = archive.read("Tool Auto Sub/app/static/simple/index.html").decode("utf-8")
        routes = archive.read("Tool Auto Sub/app/api/routes.py").decode("utf-8")
        diagnostics = archive.read("Tool Auto Sub/release/scripts/collect_diagnostics.ps1").decode("utf-8")
        config = json.loads(archive.read("Tool Auto Sub/config/portable_config.json").decode("utf-8"))
    required = {
        "tool auto sub/alembic/versions/0009_subtitle_tracks.py",
        "tool auto sub/app/services/subtitle_tracks.py",
        "tool auto sub/release/documentation/creative_subtitle_import/readme.md",
        "tool auto sub/release/documentation/creative_subtitle_import/creative_script_v1_schema.json",
        "tool auto sub/release/cp12b_windows_full_portable_runtime_manifest.json",
    }
    assert required.issubset(names)
    assert "Creative subtitle script" in index
    assert "Apply as Imported Track" in index
    assert "/creative/import/preview" in routes
    assert "/enabled" in routes
    assert "creative_import_status_sanitized" in diagnostics
    assert config["package_checkpoint"] == "CP12B"
    assert config["creative_import_enabled"] is True


def test_cp12b_package_excludes_private_media_secrets_dev_paths_and_databases():
    blocked_suffixes = (".mp4", ".mov", ".mkv", ".db", ".db-wal", ".db-shm", ".env", ".key", ".sqlite")
    allowed = {
        "tool auto sub/runtime/venv/lib/site-packages/certifi/cacert.pem",
        "tool auto sub/runtime/venv/lib/site-packages/pip/_vendor/certifi/cacert.pem",
        "tool auto sub/addons/ocr_runtime/runtime/.venv/lib/site-packages/certifi/cacert.pem",
        "tool auto sub/addons/ocr_runtime/runtime/.venv/lib/site-packages/pip/_vendor/certifi/cacert.pem",
    }
    with zipfile.ZipFile(CP12B_ZIP) as archive:
        names = archive.namelist()
        text_payload = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in names
            if name.lower().endswith((".py", ".ps1", ".cmd", ".txt", ".json", ".md", ".ini", ".css", ".js", ".html"))
        )
    offenders = [name for name in names if name.lower().endswith(blocked_suffixes) and name.lower() not in allowed]
    assert offenders == []
    assert "D:\\tool_auto_sub_worker_handoff_v0.2" not in text_payload
    assert "D:\\tool_auto_sub_ocr_runtime" not in text_payload
    assert re.search(r"AIza[0-9A-Za-z_\-]{20,}", text_payload) is None
    assert re.search(r"(?im)^\s*(?:XI_API_KEY|ELEVENLABS_API_KEY|GEMINI_API_KEY)\s*=\s*[^#\s]{12,}", text_payload) is None


def test_cp12b_preserves_authoritative_artifacts():
    if not CP11D_ZIP.exists():
        pytest.skip("CP11D binary artifact was pruned by maintenance; CP12B/CP13A are the retained release artifacts.")
    assert _sha256(CP11D_ZIP) == CP11D_SHA
    assert _sha256(ACCEPTED) == ACCEPTED_SHA
