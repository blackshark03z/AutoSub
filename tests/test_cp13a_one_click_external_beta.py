from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.release

from app.core.config import get_settings


ROOT = Path(__file__).resolve().parents[1]
CP12B_ZIP = ROOT / "release" / "CP12B" / "tool_auto_sub_windows_full_portable_cp12b.zip"
ACCEPTED = ROOT / "data" / "projects" / "vertical_slice_cp07" / "renders" / "cp08e2_decoupled_suppression_english_plate_720p.mp4"
CP12B_SHA = "9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0"
ACCEPTED_SHA = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_cp13a_settings_separate_user_data_dir(monkeypatch, tmp_path):
    root = tmp_path / "install"
    data = tmp_path / "user-data"
    operator = root / "operator"
    operator.mkdir(parents=True)
    (operator / "run_config.json").write_text(json.dumps({"source": {"path": "input/source.mp4"}}), encoding="utf-8")

    monkeypatch.setenv("TOOL_AUTO_SUB_ROOT", str(root))
    monkeypatch.setenv("TOOL_AUTO_SUB_DATA_DIR", str(data))
    monkeypatch.delenv("TOOL_AUTO_SUB_DB_PATH", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.root == root.resolve()
        assert settings.data_dir == data.resolve()
        assert settings.db_path == (data / "app.db").resolve()
    finally:
        get_settings.cache_clear()


def test_cp13a_installer_scripts_have_required_beta_contracts():
    """The superseding CP13A1 builder is the current installer contract."""
    builder = (ROOT / "tools" / "build_cp13a1_complete_payload_hotfix.py").read_text(encoding="utf-8")
    assert "CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX" in builder
    assert "CP13A1_BLOCKED_INSTALLER_TOOLCHAIN" in builder
    assert "CP13A_INSTALL_DIR" in builder
    assert "CP13A_USERDATA_DIR" in builder
    assert "CP13A_CREATE_DESKTOP_SHORTCUT" in builder
    assert "CP13A_VALIDATION_UNINSTALL_KEY_LEAF" in builder
    assert "Collect Diagnostics" in builder
    assert "Uninstall" in builder
    assert "TOOL_AUTO_SUB_CP13A1_PAYLOAD_V1" in builder


def test_cp13a_simple_ui_is_beta_first_and_provider_safe():
    index = (ROOT / "app" / "static" / "simple" / "index.html").read_text(encoding="utf-8")
    assert "Tool Auto Sub Beta" in index
    assert "5-minute beta guide" in index
    assert "Gemini, ElevenLabs" in index
    assert "Open Advanced Operator UI" in index
    assert "không cần" in index.lower()
    assert "api key" in index.lower()


def test_cp13a_builder_uses_project_local_policy_and_not_external_downloads():
    builder = (ROOT / "tools" / "build_cp13a_one_click_beta.py").read_text(encoding="utf-8")
    assert "storage_preflight(\"package\"" in builder
    assert "iexpress.exe" in builder
    assert "ToolAutoSubBetaSetup_CP13A.exe" in builder
    assert "urllib.request.urlretrieve" not in builder
    assert "pip install" not in builder
    assert "CP13A_BLOCKED_INSTALLER_TOOLCHAIN" in builder
    assert "CP13A_FFMPEG_ROOT" in builder


def test_cp13a_protected_hashes_unchanged():
    assert _sha256(CP12B_ZIP) == CP12B_SHA
    assert _sha256(ACCEPTED) == ACCEPTED_SHA


@pytest.mark.skipif(not (ROOT / "release" / "CP13A" / "RELEASE_MANIFEST.json").exists(), reason="CP13A package not built yet")
def test_cp13a_release_manifest_after_build():
    manifest = json.loads((ROOT / "release" / "CP13A" / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    installer = ROOT / "release" / "CP13A" / manifest["installer_filename"]
    sums = (ROOT / "release" / "CP13A" / "SHA256SUMS.txt").read_text(encoding="utf-8")

    assert manifest["release_id"] == "CP13A_WINDOWS_ONE_CLICK_EXTERNAL_BETA"
    assert manifest["installation_mode"] == "per-user-no-admin"
    assert manifest["simple_ui_asset_version"] == "cp13a"
    assert manifest["provider_disabled_state"]["gemini"] == "disabled"
    assert manifest["validation_summary"]["status"] == "PASS"
    assert installer.exists()
    assert _sha256(installer) == manifest["installer_sha256"]
    assert re.search(r"^[0-9a-f]{64}  ToolAutoSubBetaSetup_CP13A\.exe$", sums, re.MULTILINE)
