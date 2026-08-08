from __future__ import annotations

import subprocess

import pytest

from tools import build_cp13a1_complete_payload_hotfix as hotfix


def test_production_default_registry_key_is_unchanged_and_ignores_validation_leaf() -> None:
    assert hotfix.uninstall_registry_key_path() == (
        r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ToolAutoSubBeta"
    )
    assert hotfix.uninstall_registry_key_path(
        validation_mode=False,
        validation_key_leaf="ToolAutoSubBetaValidation_ignored",
    ).endswith(r"\ToolAutoSubBeta")


def test_validation_key_is_unique_safe_and_used_by_both_generated_scripts() -> None:
    first = hotfix.create_validation_uninstall_key_leaf()
    second = hotfix.create_validation_uninstall_key_leaf()

    assert first != second
    assert first.startswith(hotfix.VALIDATION_UNINSTALL_KEY_PREFIX)
    assert hotfix.uninstall_registry_key_path(validation_mode=True, validation_key_leaf=first).endswith("\\" + first)
    for script in (hotfix.INSTALL_PS1, hotfix.UNINSTALL_PS1):
        assert "CP13A_VALIDATION_MODE" in script
        assert "CP13A_VALIDATION_UNINSTALL_KEY_LEAF" in script
        assert "$uninstallKey = Join-Path $uninstallRegistryBase $uninstallLeaf" in script


@pytest.mark.parametrize("invalid", [
    r"ToolAutoSubBetaValidation_bad\\leaf",
    "ToolAutoSubBetaValidation_bad/leaf",
    "ToolAutoSubBetaValidation_bad:leaf",
    "ToolAutoSubBetaValidation_bad leaf",
    "ToolAutoSubBetaValidation_bad;Remove-Item",
    "ToolAutoSubBeta",
    "WrongPrefix_token",
    "ToolAutoSubBetaValidation_" + "a" * 97,
])
def test_invalid_validation_registry_leaf_is_rejected(invalid: str) -> None:
    with pytest.raises(ValueError, match="Invalid validation uninstall registry key leaf"):
        hotfix.validate_validation_uninstall_key_leaf(invalid)


def test_validation_cleanup_rejects_production_key_before_any_registry_process(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(hotfix.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(ValueError):
        hotfix.cleanup_validation_uninstall_registry_key(hotfix.PRODUCTION_UNINSTALL_KEY_LEAF)

    assert calls == []


def test_failure_between_install_and_uninstall_cleans_only_validation_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    leaf = "ToolAutoSubBetaValidation_deadbeef"
    production = {"exists": True, "values": {"InstallLocation": r"C:\Prod\ToolAutoSubBeta"}}
    states = {
        hotfix.PRODUCTION_UNINSTALL_KEY_LEAF: production,
        leaf: {"exists": False, "values": {}},
    }
    removed: list[str] = []
    validation_root = tmp_path / "cp13a1_validation_fixture"
    validation_root.mkdir()

    monkeypatch.setattr(hotfix.tempfile, "mkdtemp", lambda prefix: str(validation_root))
    monkeypatch.setattr(hotfix, "create_validation_uninstall_key_leaf", lambda: leaf)
    monkeypatch.setattr(hotfix, "_registry_snapshot_for_leaf", lambda key: states[key])

    def remove(key: str) -> None:
        removed.append(key)
        states[key] = {"exists": False, "values": {}}

    monkeypatch.setattr(hotfix, "_remove_validation_registry_key", remove)
    monkeypatch.setattr(
        hotfix.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode=1, stdout="", stderr="install failure"),
    )
    monkeypatch.setattr(hotfix, "EVIDENCE_ROOT", tmp_path / "evidence")

    result = hotfix.validate_installer({"components": []})

    assert result["status"] == "FAIL"
    assert removed == [leaf]
    assert states[hotfix.PRODUCTION_UNINSTALL_KEY_LEAF] == production
    assert states[leaf]["exists"] is False


def test_production_snapshot_mismatch_fails_validation_and_still_cleans_temp_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    leaf = "ToolAutoSubBetaValidation_snapshot"
    before = {"exists": True, "values": {"InstallLocation": r"C:\Prod\ToolAutoSubBeta"}}
    after = {"exists": True, "values": {"InstallLocation": r"C:\Temp\validation"}}
    calls = {"production": 0, "validation": 0}
    removed: list[str] = []
    validation_root = tmp_path / "cp13a1_validation_fixture"
    validation_root.mkdir()

    monkeypatch.setattr(hotfix.tempfile, "mkdtemp", lambda prefix: str(validation_root))
    monkeypatch.setattr(hotfix, "create_validation_uninstall_key_leaf", lambda: leaf)

    def snapshot(key: str) -> dict:
        if key == hotfix.PRODUCTION_UNINSTALL_KEY_LEAF:
            calls["production"] += 1
            return before if calls["production"] == 1 else after
        calls["validation"] += 1
        return {"exists": calls["validation"] == 2, "values": {}}

    monkeypatch.setattr(hotfix, "_registry_snapshot_for_leaf", snapshot)
    monkeypatch.setattr(hotfix, "_remove_validation_registry_key", lambda key: removed.append(key))
    monkeypatch.setattr(
        hotfix.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(hotfix, "EVIDENCE_ROOT", tmp_path / "evidence")

    result = hotfix.validate_installer({"components": []})

    assert result["status"] == "FAIL"
    assert "Production uninstall registry snapshot changed during validation" in result["error"]
    assert removed == [leaf]
