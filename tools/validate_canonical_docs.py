from __future__ import annotations

import json
import re
import sys
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


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact_numbers(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d)", "", text)


def main() -> None:
    state_path = ROOT / "project_state.json"
    state = json.loads(read(state_path))
    missing = sorted(REQUIRED_STATE_FIELDS - set(state))
    if missing:
        fail(f"project_state.json missing required fields: {missing}")

    release = state["canonical_release"]
    current = read(ROOT / "docs" / "CURRENT_STATE.md")
    architecture = read(ROOT / "docs" / "ARCHITECTURE.md")
    operations = read(ROOT / "docs" / "OPERATIONS.md")
    storage_doc = read(ROOT / "docs" / "STORAGE_FOOTPRINT_AND_RETENTION.md")
    readme = read(ROOT / "README.md")

    expected = {
        "package_path": release["package_path"],
        "package_sha256": release["package_sha256"],
        "release_id": release["release_id"],
        "database_schema": state["database"]["schema"],
        "external_beta": state["beta"]["external_machine"],
        "storage_gate": state["storage"]["gate"],
    }
    for label, value in expected.items():
        if value not in current:
            fail(f"CURRENT_STATE.md does not contain {label}: {value}")
    if release["name"] != "CP12B Full Portable":
        fail("project_state.json canonical release is not CP12B")
    if state["beta"]["external_machine"] != "pending":
        fail("external-machine beta state must remain pending")
    cp13a = state["distribution"].get("one_click_beta_candidate")
    if cp13a:
        required_cp13a = {
            "release_id": cp13a["release_id"],
            "installer_path": cp13a["installer_path"],
            "installer_sha256": cp13a["installer_sha256"],
            "machine_validation": cp13a["machine_validation"],
        }
        for label, value in required_cp13a.items():
            if value not in current and value not in operations and value not in architecture:
                fail(f"active docs do not contain CP13A {label}: {value}")
        if cp13a.get("external_machine_beta") != "pending":
            fail("CP13A external-machine beta must remain pending")
        if state["runtime"].get("simple_ui_asset_version") != "cp13a":
            fail("runtime simple UI asset version does not identify CP13A")
        if state["beta"].get("cp13a_one_click_external_beta") != "machine_pass_external_machine_pending":
            fail("CP13A beta state is not machine_pass_external_machine_pending")

    storage = state["storage"]
    thresholds = storage.get("tiered_thresholds", {})
    expected_thresholds = {
        "run": 1073741824,
        "media": 2147483648,
        "package": 4294967296,
    }
    if thresholds != expected_thresholds:
        fail(f"tiered storage thresholds do not match expected values: {thresholds}")
    if storage.get("fixed_15_gib_gate") != "retired":
        fail("fixed 15 GiB storage gate is not marked retired")
    if set(storage.get("operation_status", {})) != set(expected_thresholds):
        fail("storage operation status does not include run/media/package")
    free = storage.get("current_free_bytes")
    if not isinstance(free, int) or free < 0:
        fail("storage current_free_bytes must be a non-negative integer")
    for operation, threshold in expected_thresholds.items():
        status = storage["operation_status"][operation]
        expected_status = "allowed" if free >= threshold else "blocked"
        expected_margin = free - threshold
        if status.get("status") != expected_status or status.get("margin_bytes") != expected_margin:
            fail(f"storage status for {operation} is inconsistent with current_free_bytes")
        for doc in [current, operations, storage_doc]:
            if f"`{operation}`" not in doc and operation not in doc:
                fail(f"active docs do not document storage operation: {operation}")
            if str(threshold) not in compact_numbers(doc):
                fail(f"active docs do not document {operation} storage threshold: {threshold}")
    forbidden_fixed_gate_claims = [
        r"Storage gate minimum:\s*`?15`?\s*GiB",
        r"disk gate remains blocked",
        r"storage gate remains blocked",
        r"blocked_below_safe_threshold",
        r"fixed 15 GiB threshold",
    ]
    active_storage_docs = "\n".join([current, operations, storage_doc])
    for pattern in forbidden_fixed_gate_claims:
        if re.search(pattern, active_storage_docs, re.IGNORECASE):
            fail(f"active docs still claim obsolete fixed 15 GiB storage gate: {pattern}")

    if "CP12B Full Portable" not in readme:
        fail("README.md does not identify CP12B as the current release")
    if "0009_subtitle_tracks" not in architecture or "0009_subtitle_tracks" not in operations:
        fail("living docs do not agree on database schema")
    if "CP10B is now reserved for the implemented Simple end-to-end workflow UI" not in read(ROOT / "docs" / "DECISIONS" / "REF-001_MODULE_BOUNDARIES_PROPOSAL.md"):
        fail("REF-001 does not resolve CP10B naming")

    active_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "CURRENT_STATE.md",
        ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "OPERATIONS.md",
        ROOT / "CHANGELOG.md",
        ROOT / "docs" / "MAINTENANCE_RESET_2026.md",
    ]
    combined = "\n".join(read(path) for path in active_docs)
    if re.search(r"CP10B package/module boundary|Proposed CP10B Plan|CP10B boundary plan", combined, re.IGNORECASE):
        fail("active docs still assign CP10B to module-boundary work")
    if re.search(r"CP10B .*incomplete|CP10B .*not implemented", combined, re.IGNORECASE):
        fail("active docs still claim CP10B is incomplete")

    ids = re.findall(r"\bCP\d{2}[A-Z]?\b", combined)
    active_duplicates = {item for item in ids if ids.count(item) > 20}
    if active_duplicates:
        fail(f"active docs repeat checkpoint IDs excessively: {sorted(active_duplicates)}")

    obsolete = [
        ROOT / "docs" / "CURRENT_BASELINE.md",
        ROOT / "docs" / "DOCUMENT_AUTHORITY_MATRIX.md",
        ROOT / "docs" / "TARGET_ARCHITECTURE_PROPOSAL.md",
        ROOT / "00_READ_ME_FIRST.md",
        ROOT / "01_IMPLEMENTATION_SPEC_V0.2.md",
        ROOT / "02_DECISION_LOG.md",
        ROOT / "03_ACCEPTANCE_AND_TEST_PLAN.md",
        ROOT / "04_RISK_REGISTER.md",
        ROOT / "05_CHECKPOINT_PROTOCOL.md",
        ROOT / "06_PROJECT_STATUS.md",
        ROOT / "07_WORKER_MASTER_PROMPT.md",
        ROOT / "08_WORKER_TASK_01_BOOTSTRAP_PREFLIGHT.md",
        ROOT / "09_HANDOFF_TEMPLATE.md",
        ROOT / "10_CHANGELOG_V0.1_TO_V0.2.md",
        ROOT / "CURRENT_EXECUTION_BRIEF.md",
        ROOT / "CONTRIBUTING.md",
    ]
    existing_obsolete = [str(path.relative_to(ROOT)) for path in obsolete if path.exists()]
    if existing_obsolete:
        fail(f"obsolete authority documents still active: {existing_obsolete}")

    print("PASS: canonical documentation is consistent")


if __name__ == "__main__":
    main()
