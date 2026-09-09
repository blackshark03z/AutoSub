from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_STATE_FIELDS = {
    "schema_version",
    "repository",
    "canonical_release",
    "runtime",
    "database",
    "distribution",
    "beta",
    "storage",
    "process",
}
EXPECTED_RELEASE = {
    "name": "AutoSub 1.9.0 Stable",
    "release_id": "AUTOSUB_1_9_0_STABLE",
    "version": "1.9.0",
    "git_tag": "v1.9.0",
}
EXPECTED_STORAGE_THRESHOLDS = {
    "run": 1073741824,
    "media": 2147483648,
    "package": 4294967296,
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact_numbers(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d)", "", text)


def main() -> None:
    state = json.loads(read(ROOT / "project_state.json"))
    missing = sorted(REQUIRED_STATE_FIELDS - set(state))
    if missing:
        fail(f"project_state.json missing required fields: {missing}")

    release = state["canonical_release"]
    for key, expected in EXPECTED_RELEASE.items():
        if release.get(key) != expected:
            fail(f"canonical release {key} mismatch: {release.get(key)!r}")

    version = read(ROOT / "VERSION").strip()
    if version != EXPECTED_RELEASE["version"]:
        fail(f"VERSION does not match canonical release: {version}")

    readme = read(ROOT / "README.md")
    current = read(ROOT / "docs" / "CURRENT_STATE.md")
    architecture = read(ROOT / "ARCHITECTURE.md")
    operations = read(ROOT / "docs" / "OPERATIONS.md")
    storage_doc = read(ROOT / "docs" / "STORAGE_FOOTPRINT_AND_RETENTION.md")

    for value in (
        release["name"],
        release["release_id"],
        release["version"],
        release["git_tag"],
        release["source_bundle_path"],
    ):
        if value not in current:
            fail(f"CURRENT_STATE.md does not contain canonical release value: {value}")
    if release["name"] not in readme or release["version"] not in readme:
        fail("README.md does not identify AutoSub 1.9.0 Stable")

    runtime = state["runtime"]
    if runtime.get("backend_version") != "1.9.0":
        fail("runtime backend version is not 1.9.0")
    if runtime.get("simple_ui_asset_version") != "fluent-settings-v1":
        fail("runtime Simple UI asset version is not fluent-settings-v1")

    schema = state["database"].get("schema")
    if schema != "0009_subtitle_tracks":
        fail(f"unexpected database schema: {schema}")
    if schema not in current or schema not in operations:
        fail("living docs do not agree on database schema")

    if "PaddleOCR" not in architecture or "Gemini" not in architecture:
        fail("ARCHITECTURE.md does not describe the OCR+Gemini boundary")
    if "AutoSubs" not in architecture or "Argos" not in architecture:
        fail("ARCHITECTURE.md does not describe the local speech boundary")

    storage = state["storage"]
    if storage.get("tiered_thresholds") != EXPECTED_STORAGE_THRESHOLDS:
        fail("tiered storage thresholds do not match the accepted policy")
    if storage.get("fixed_15_gib_gate") != "retired":
        fail("fixed 15 GiB storage gate is not retired")
    free = storage.get("current_free_bytes")
    if not isinstance(free, int) or free < 0:
        fail("storage current_free_bytes must be a non-negative integer")
    statuses = storage.get("operation_status") or {}
    if set(statuses) != set(EXPECTED_STORAGE_THRESHOLDS):
        fail("storage operation status does not cover run/media/package")
    for operation, threshold in EXPECTED_STORAGE_THRESHOLDS.items():
        expected_status = "allowed" if free >= threshold else "blocked"
        expected_margin = free - threshold
        status = statuses[operation]
        if status.get("status") != expected_status or status.get("margin_bytes") != expected_margin:
            fail(f"storage status is inconsistent for {operation}")
        for doc in (current, operations, storage_doc):
            if operation not in doc:
                fail(f"active docs do not document storage operation: {operation}")
            if str(threshold) not in compact_numbers(doc):
                fail(f"active docs do not document {operation} threshold: {threshold}")

    if state["repository"].get("legacy_build_os") != "retired":
        fail("legacy Build OS is not explicitly retired")
    obsolete_authority = [
        ROOT / "00_READ_ME_FIRST.md",
        ROOT / "01_IMPLEMENTATION_SPEC_V0.2.md",
        ROOT / "03_ACCEPTANCE_AND_TEST_PLAN.md",
        ROOT / "CURRENT_EXECUTION_BRIEF.md",
    ]
    present = [str(path.relative_to(ROOT)) for path in obsolete_authority if path.exists()]
    if present:
        fail(f"obsolete authority documents remain active: {present}")

    print("PASS: canonical documentation is consistent with AutoSub 1.9.0 Stable")


if __name__ == "__main__":
    main()
