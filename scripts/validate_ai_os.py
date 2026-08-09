#!/usr/bin/env python3
"""Invariant validator for Senior AI Build OS v1.16."""
from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from append_cost_ledger import FIELDS as LEDGER_FIELDS
from evidence_support import parse_iso, verify_bundle
from risk_support import RISK_ORDER, minimum_risk
from runtime_support import application_file_fingerprint, application_snapshot, load_task_baseline, sha256_file, task_delta_files, validate_identifier, confined_child
from assurance_support import verify_guardian_signature
from state_hazard_support import LEVELS as STATE_LEVELS, parse_contract as parse_state_contract
from policy_support import gate as policy_gate, render_quality_gates

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
LIVE = {"READY", "ACTIVE", "BLOCKED", "PAUSED"}
PLACEHOLDERS = {"", "UNSET", "UNKNOWN", "TASK-XXX", "SC-XXX", "M-XXX", "YYYY-MM-DD"}
PROFILE_BY_RISK = {"R0": "LEAN", "R1": "LEAN", "R2": "STANDARD", "R3": "DEEP"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def field(body: str, name: str, default: str = "") -> str:
    match = re.search(rf"^-?\s*{re.escape(name)}:\s*(.*?)\s*$", body, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else default


def meaningful(value: str) -> bool:
    return value.strip().upper() not in PLACEHOLDERS | {"NONE", "NOT_APPLICABLE", "PENDING"}


def section(body: str, heading: str) -> str | None:
    lines = body.splitlines()
    start = None; level = 0
    for i, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match and match.group(2).strip().casefold() == heading.casefold(): start = i + 1; level = len(match.group(1)); break
    if start is None: return None
    end = len(lines)
    for i in range(start, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[i])
        if match and len(match.group(1)) <= level: end = i; break
    return "\n".join(lines[start:end]).strip()


def parse_patterns(value: str) -> list[str]:
    return [x.strip().replace("\\", "/") for x in re.split(r"[,;\n]", value) if x.strip() and x.strip().upper() not in {"NONE", "UNSET"}]


def allowed(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/") and path.startswith(pattern): return True
        if pattern.endswith("/**") and path.startswith(pattern[:-3].rstrip("/") + "/"): return True
        if fnmatch.fnmatch(path, pattern) or path == pattern: return True
    return False


def review_fields(body: str) -> dict[str, str]:
    names = ["Task ID", "Task revision", "Reviewed snapshot SHA256", "Reviewer identity", "Reviewer role", "Independent from writer", "Verdict", "Reviewed at"]
    return {name: field(body, name) for name in names}


def validate_review(bundle: Path, manifest: dict[str, Any], writer: str, *, label: str = "High-risk") -> list[str]:
    errors: list[str] = []
    review = manifest.get("review")
    if not review: return [f"{label} evidence manifest missing review"]
    if review.get("trust") not in {"SIGNED_GUARDIAN"}:
        errors.append(f"{label} review trust must be SIGNED_GUARDIAN")
    path = bundle / review.get("bundle_path", "")
    if not path.is_file(): return [f"{label} bundled review file missing"]
    if sha256_file(path) != review.get("sha256"): errors.append(f"{label} bundled review hash mismatch")
    att_path = bundle / review.get("attestation_bundle_path", "")
    if not att_path.is_file():
        errors.append(f"{label} signed Guardian attestation bundle missing")
    else:
        if review.get("attestation_sha256") and sha256_file(att_path) != review.get("attestation_sha256"):
            errors.append(f"{label} Guardian attestation hash mismatch")
        try:
            att=json.loads(read(att_path))
            repo_root=bundle.parents[3]
            errors.extend(verify_guardian_signature(repo_root,att))
            if str(att.get('task_id')) != str(manifest.get('task_id')): errors.append('Guardian attestation Task ID mismatch')
            if str(att.get('task_revision')) != str(manifest.get('task_revision')): errors.append('Guardian attestation task revision mismatch')
            if str(att.get('reviewed_snapshot_sha256')) != str(manifest.get('verified_snapshot',{}).get('snapshot_sha256')): errors.append('Guardian attestation snapshot mismatch')
            if str(att.get('writer_session_id')) != str(writer): errors.append('Guardian attestation writer identity mismatch')
            signed_report_hash=str(att.get('review_report_sha256') or '').strip()
            if int(att.get('schema_version') or 0) >= 3 and not signed_report_hash:
                errors.append('Guardian attestation schema v3+ must bind review_report_sha256')
            if signed_report_hash and signed_report_hash != sha256_file(path):
                errors.append('Guardian attestation review report hash mismatch')
            if str(att.get('reviewer_session_id')) == str(att.get('writer_session_id')): errors.append('Guardian reviewer session must differ from writer session')
            if str(att.get('verdict')).upper() not in {'PASS','ACCEPTED'}: errors.append('Guardian attestation verdict must be PASS/ACCEPTED')
        except (OSError,json.JSONDecodeError,IndexError) as exc:
            errors.append(f'{label} Guardian attestation invalid: {exc}')
    values = review_fields(read(path))
    expected = {
        "Task ID": str(manifest.get("task_id")),
        "Task revision": str(manifest.get("task_revision")),
        "Reviewed snapshot SHA256": str(manifest.get("verified_snapshot", {}).get("snapshot_sha256")),
    }
    for key, value in expected.items():
        if values[key] != value: errors.append(f"Review report {key} mismatch")
    if not values["Reviewer identity"]: errors.append("Review report missing Reviewer identity")
    if values["Reviewer identity"] == writer: errors.append("Review reviewer must differ from writer")
    if values["Independent from writer"].casefold() not in {"yes", "true"}: errors.append("Review must confirm independence")
    if values["Verdict"].upper() not in {"PASS", "ACCEPTED"}: errors.append("Review verdict must be PASS/ACCEPTED")
    try: parse_iso(values["Reviewed at"])
    except ValueError: errors.append("Review Reviewed at invalid")
    return errors


def validate_runtime_mirrors(root: Path) -> list[str]:
    errors: list[str] = []
    mapping = {"project.json": ".ai/PROJECT.md", "state.json": ".ai/STATE.md", "task.json": ".ai/ACTIVE_TASK.md", "goal.json": ".ai/GOAL_STATE.json"}
    runtime = root / ".ai" / "runtime"
    for json_name, source_name in mapping.items():
        path = runtime / json_name
        if not path.exists(): continue
        try: value = json.loads(read(path))
        except json.JSONDecodeError as exc: errors.append(f"Runtime mirror invalid JSON {json_name}: {exc}"); continue
        if value.get("source_sha256") != sha256_file(root / source_name): errors.append(f"Runtime mirror stale: {json_name}")
    return errors


CI_EXCLUDED_PREFIXES = (".ai/", ".git/", ".github/workflows/ai-build-os.yml")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _resolve_ref(root: Path, ref: str) -> str | None:
    if not ref or set(ref) == {"0"}:
        return None
    result = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else None


def resolve_ci_base(root: Path, explicit: str | None = None) -> str | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).is_file():
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
            if isinstance(event.get("pull_request"), dict):
                candidates.append(str(event["pull_request"].get("base", {}).get("sha", "")))
            candidates.append(str(event.get("before", "")))
        except (json.JSONDecodeError, OSError):
            pass
    base_ref = os.environ.get("GITHUB_BASE_REF", "").strip()
    if base_ref:
        candidates.extend([f"origin/{base_ref}", base_ref])
    candidates.append("HEAD^")
    for candidate in candidates:
        resolved = _resolve_ref(root, candidate)
        if resolved:
            return resolved
    return None


def ci_commit_binding_errors(root: Path, base_ref: str | None = None) -> list[str]:
    """Bind committed application content to evidence added on the same CI change set."""
    errors: list[str] = []
    base = resolve_ci_base(root, base_ref)
    if base is None:
        return ["CI could not resolve a base commit; pass --ci-base-ref or ensure full Git history is available"]
    merge = _git(root, "merge-base", base, "HEAD")
    if merge.returncode != 0 or not merge.stdout.strip():
        return [f"CI could not compute merge-base for {base}"]
    merge_base = merge.stdout.strip()

    changed = _git(root, "diff", "--name-only", "--diff-filter=ACDMRTUXB", merge_base, "HEAD")
    if changed.returncode != 0:
        return ["CI failed to enumerate committed application delta: " + changed.stderr.strip()]
    app_paths = sorted({
        line.strip().replace("\\", "/")
        for line in changed.stdout.splitlines()
        if line.strip() and not line.strip().replace("\\", "/").startswith(CI_EXCLUDED_PREFIXES)
    })
    if not app_paths:
        return []

    evidence_delta = _git(root, "diff", "--name-only", merge_base, "HEAD", "--", ".ai/evidence")
    if evidence_delta.returncode != 0:
        return ["CI failed to enumerate evidence delta: " + evidence_delta.stderr.strip()]
    manifest_paths = sorted({
        line.strip().replace("\\", "/")
        for line in evidence_delta.stdout.splitlines()
        if line.strip().endswith("/manifest.json")
    })
    if not manifest_paths:
        return ["Committed application delta has no new/updated AI Build OS evidence manifest in this change set"]

    verified_hashes: dict[str, set[str]] = {}
    for relative in manifest_paths:
        path = root / relative
        if not path.is_file():
            errors.append(f"CI evidence manifest missing from HEAD: {relative}")
            continue
        bundle = path.parent
        bundle_errors = verify_bundle(root, bundle)
        if bundle_errors:
            errors.extend(f"CI {relative}: {item}" for item in bundle_errors)
            continue
        try:
            manifest = json.loads(read(path))
        except json.JSONDecodeError as exc:
            errors.append(f"CI evidence manifest invalid JSON {relative}: {exc}")
            continue
        hashes = manifest.get("task_delta_file_hashes")
        if not isinstance(hashes, dict) or not hashes:
            errors.append(f"CI evidence manifest lacks v1.10 task_delta_file_hashes: {relative}")
            continue
        task_id = str(manifest.get("task_id", ""))
        try:
            revision = int(manifest.get("task_revision"))
        except (TypeError, ValueError):
            errors.append(f"CI evidence manifest has invalid task revision: {relative}")
            continue
        history = confined_child(root, Path(".ai/history"), validate_identifier(task_id, "task ID"), "task ID") / f"r{revision:03d}.json"
        if not history.is_file():
            errors.append(f"CI evidence missing immutable history record for {task_id}/r{revision:03d}")
        else:
            try:
                record = json.loads(read(history))
                if record.get("evidence_manifest_sha256") != sha256_file(path):
                    errors.append(f"CI history evidence hash mismatch for {task_id}/r{revision:03d}")
            except json.JSONDecodeError:
                errors.append(f"CI history record invalid JSON for {task_id}/r{revision:03d}")
        for app_path, fingerprint in hashes.items():
            if isinstance(app_path, str) and isinstance(fingerprint, str):
                verified_hashes.setdefault(app_path.replace("\\", "/"), set()).add(fingerprint)

    for relative in app_paths:
        fingerprint = application_file_fingerprint(root, relative)
        if fingerprint not in verified_hashes.get(relative, set()):
            errors.append(
                f"CI unbound application change: {relative} current fingerprint {fingerprint} "
                "does not match any verified task-delta hash added in this change set"
            )
    return errors


def validate(root: Path, strict: bool = False, *, ci: bool = False, ci_base_ref: str | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []; warnings: list[str] = []
    required = [
        ".ai/PROJECT.md", ".ai/STATE.md", ".ai/ACTIVE_TASK.md", ".ai/CONTEXT_CAPSULE.md",
        ".ai/GOAL.md", ".ai/GOAL_STATE.json", ".ai/COST_LEDGER.csv", "scripts/ai_os.py", "scripts/goal_support.py", "scripts/delegation_support.py", "scripts/evidence_support.py", "scripts/runtime_support.py",
        "scripts/state_runtime.py", "scripts/state_hazard_support.py", "scripts/risk_support.py", "scripts/project_ci.py",
        "config/gates.json", "config/codebase_health.json", "config/quality_policy.json", "config/assurance.json", "config/risk_semantics.json", "config/kernel_contract.json",
        "templates/TASK_LEAN.md", "templates/TASK_STANDARD.md", "templates/TASK_DEEP.md",
    ]
    for relative in required:
        if not (root / relative).is_file(): errors.append(f"Required file missing: {relative}")
    if errors: return errors, warnings
    project = read(root / ".ai" / "PROJECT.md"); state = read(root / ".ai" / "STATE.md"); task = read(root / ".ai" / "ACTIVE_TASK.md"); capsule = read(root / ".ai" / "CONTEXT_CAPSULE.md")
    quality_path = root / ".ai" / "QUALITY_GATES.md"
    if quality_path.is_file() and read(quality_path).strip() != render_quality_gates(root).strip():
        errors.append("QUALITY_GATES.md drift: regenerate from config/gates.json")
    for cfg_rel, required_keys in (("config/codebase_health.json", {"ratchets","hard_ratchets","architecture_policy"}), ("config/quality_policy.json", {"required_for_executable","recommended_for_executable","capability_waivers"}), ("config/assurance.json", {"review_trust","guardian","field_learning"}), ("config/risk_semantics.json", {"uncertainty_mode","uncertain_side_effect_patterns"}), ("config/kernel_contract.json", {"stable_core","tunable_policy","promotion_rule","release_discipline"})):
        try:
            cfg_obj=json.loads(read(root/cfg_rel))
            missing=sorted(required_keys-set(cfg_obj))
            if missing: errors.append(f"{cfg_rel} missing keys: {', '.join(missing)}")
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid {cfg_rel}: {exc}")
    try:
        goal_state = json.loads(read(root / ".ai" / "GOAL_STATE.json"))
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid GOAL_STATE.json: {exc}"); goal_state = {}
    if goal_state:
        goal_status = str(goal_state.get("status", "NONE"))
        if goal_status not in {"NONE", "ACTIVE", "BLOCKED", "COMPLETED", "ABORTED"}: errors.append(f"Invalid Goal status: {goal_status}")
        tasks = goal_state.get("tasks", {}) or {}
        if not isinstance(tasks, dict): errors.append("Goal tasks must be an object")
        else:
            goal_id = str(goal_state.get("goal_id") or "")
            if goal_id and goal_id.upper() != "NONE":
                try: validate_identifier(goal_id, "goal ID")
                except SystemExit as exc: errors.append(str(exc))
            for node_id, node in tasks.items():
                try: validate_identifier(str(node_id), "goal node ID")
                except SystemExit as exc: errors.append(str(exc))
                if node.get("status") not in {"PLANNED", "ACTIVE", "DONE", "BLOCKED", "DEFERRED"}: errors.append(f"Invalid Goal node status: {node_id}")
                for dep in node.get("depends_on", []):
                    try: validate_identifier(str(dep), "goal dependency ID")
                    except SystemExit as exc: errors.append(str(exc))
                    if dep not in tasks: errors.append(f"Goal node {node_id} depends on missing node {dep}")
            active_nodes = [nid for nid, node in tasks.items() if node.get("status") == "ACTIVE"]
            if len(active_nodes) > 1: warnings.append("Multiple ACTIVE Goal nodes require isolated worktrees; single-root ACTIVE_TASK remains single-writer")
    template_state = field(project, "Project ID") in {"UNSET", ""} and field(task, "Task Status") == "NOT_CREATED"
    status = field(task, "Task Status"); lease = field(task, "Lease Status"); risk = field(task, "Risk Tier"); profile = field(task, "Execution Profile")
    task_id = field(task, "Task ID"); revision = field(task, "Task Revision", "1")
    task_id_valid = True
    if task_id and task_id.upper() not in {"NONE", "UNSET", "TASK-XXX"}:
        try: validate_identifier(task_id, "task ID")
        except SystemExit as exc:
            errors.append(str(exc)); task_id_valid = False
    state_level = field(task, "State Hazard Level", "S0").upper()
    state_contract: dict[str, Any] = {}
    if state_level not in STATE_LEVELS:
        errors.append(f"Invalid State Hazard Level: {state_level}")
    else:
        try:
            state_contract = parse_state_contract(field(task, "State Contract JSON", "{}"))
        except SystemExit as exc:
            errors.append(str(exc))
        if state_contract:
            contract_level = str(state_contract.get("level") or "S0").upper()
            if contract_level != state_level:
                errors.append(f"State hazard/contract level mismatch: {state_level} vs {contract_level}")
            task_contract_hash = field(task, "State Contract SHA256", "NONE")
            embedded_hash = str(state_contract.get("contract_sha256") or "NONE")
            # Template S0 intentionally carries `{}` + NONE. Live/complete tasks have a frozen hash.
            if status in LIVE | {"COMPLETED"} and task_contract_hash != embedded_hash:
                errors.append("State Contract SHA256 does not match embedded contract")

    if risk in PROFILE_BY_RISK and profile != PROFILE_BY_RISK[risk]: errors.append(f"Risk/profile mismatch: {risk} requires {PROFILE_BY_RISK[risk]}")
    if status == "ACTIVE" and (lease != "CLAIMED" or field(task, "Identity Verification") != "VERIFIED"): errors.append("ACTIVE task requires CLAIMED lease and VERIFIED identity")
    if status == "READY" and lease != "UNCLAIMED": errors.append("READY task requires UNCLAIMED lease")
    if status == "COMPLETED" and lease != "RELEASED": errors.append("COMPLETED task requires RELEASED lease")
    if status in LIVE:
        for heading in ["Single Outcome", "Permission Matrix", "Acceptance Criteria", "Verification Plan", "Execution Lease", "Completion"]:
            if section(task, heading) is None: errors.append(f"Live task missing section: {heading}")
        patterns = parse_patterns(field(task, "Modify")) + parse_patterns(field(task, "Create"))
        if task_id_valid:
            delta = task_delta_files(root, task_id=task_id, task_revision=int(revision))
            unauthorized = [path for path in delta if not allowed(path, patterns)]
            if unauthorized: errors.append("Unauthorized task-delta paths modified: " + ", ".join(unauthorized))
        baseline = load_task_baseline(root)
        if not baseline: errors.append("Live task missing task baseline")
        elif baseline.get("task_id") != task_id or int(baseline.get("task_revision", -1)) != int(revision): errors.append("Task baseline identity/revision mismatch")
        elif baseline.get("state_contract_sha256", "NONE") != field(task, "State Contract SHA256", "NONE"):
            errors.append("Frozen state contract hash differs from task-start baseline")
    floor, floor_reasons = minimum_risk(
        data_operation=field(task, "Data operation", "READ_ONLY"),
        artifact_operation=field(task, "Artifact operation", "CREATE_NEW_VERSION"),
        files_overwritten=field(task, "Files overwritten", "NONE"),
        data_mutated=field(task, "Data mutated", "NONE"),
        external_calls=field(task, "External calls", "NONE"),
        provider_calls=field(task, "External/provider calls", "NONE"),
        modify=field(task, "Modify", ""), create=field(task, "Create", ""),
    )
    declared_risk = field(task, "Declared Risk Tier", "auto").upper()
    required_floor = floor
    if declared_risk in RISK_ORDER and RISK_ORDER[declared_risk] > RISK_ORDER[required_floor]: required_floor = declared_risk
    if risk in RISK_ORDER and RISK_ORDER[risk] < RISK_ORDER[required_floor]:
        errors.append(f"Risk tier {risk} below required floor {required_floor}: {'; '.join(floor_reasons) or 'authorized declaration'}")
    if meaningful(field(task, "Risk Floor")) and field(task, "Risk Floor") != floor:
        warnings.append(f"Stored Risk Floor {field(task, 'Risk Floor')} differs from inferred {floor}; refresh via amend/start")
    if risk == "R3":
        if field(task, "Owner Authorization") != "APPROVED": errors.append("R3 requires Owner Authorization: APPROVED")
        if not meaningful(field(task, "Authorization Reference")): errors.append("R3 requires Authorization Reference")
        if not meaningful(field(task, "Rollback")): errors.append("R3 requires concrete rollback")

    mutation = field(task, "Data operation") in {"MUTATE_IN_PLACE", "DELETE"} or field(task, "Artifact operation") in {"MUTATE_IN_PLACE", "DELETE", "OVERWRITE"} or meaningful(field(task, "Files overwritten")) or meaningful(field(task, "Data mutated"))
    if mutation and field(task, "Owner Authorization") != "APPROVED": errors.append("Mutation/delete/overwrite requires approval")

    # Cross-file continuity.
    project_id = field(project, "Project ID"); state_project = field(state, "Project ID"); task_project = field(task, "Project ID")
    if not template_state:
        if len({project_id, state_project, task_project}) != 1: errors.append("Project ID mismatch across PROJECT/STATE/TASK")
        active_state = field(state, "Active Task ID")
        if status in LIVE and active_state != task_id: errors.append("STATE Active Task ID mismatch")
        if status == "COMPLETED" and active_state != "NONE": errors.append("Completed task requires STATE Active Task ID: NONE")

    # Completed evidence, history, ledger and source snapshot binding.
    if status == "COMPLETED":
        if not meaningful(field(task, "Evidence Bundle")): errors.append("Completed task missing Evidence Bundle")
        bundle_rel = field(task, "Evidence Bundle"); bundle = root / bundle_rel
        errors.extend(verify_bundle(root, bundle))
        manifest: dict[str, Any] = {}
        if (bundle / "manifest.json").is_file():
            try: manifest = json.loads(read(bundle / "manifest.json"))
            except json.JSONDecodeError as exc: errors.append(f"Evidence manifest invalid: {exc}")
        if manifest:
            if manifest.get("task_id") != task_id: errors.append("Evidence Task ID mismatch")
            if str(manifest.get("task_revision")) != revision: errors.append("Evidence task revision mismatch")
            if manifest.get("accepted_outcome") != field(task, "Outcome"): errors.append("Evidence accepted outcome mismatch")
            verified = manifest.get("verified_snapshot", {}).get("snapshot_sha256")
            if verified != field(task, "Verified Snapshot SHA256"): errors.append("Task verified snapshot mismatch")
            manifest_state_level = str(manifest.get("state_hazard_level") or "S0").upper()
            manifest_state_hash = str(manifest.get("state_contract_sha256") or "NONE")
            if manifest_state_level != state_level: errors.append("Evidence State Hazard Level mismatch")
            if manifest_state_hash != field(task, "State Contract SHA256", "NONE"): errors.append("Evidence state contract hash mismatch")
            kinds = []
            for item in manifest.get("checks", []):
                kinds.extend(item.get("satisfies") or [item.get("kind")])
            if state_level in STATE_LEVELS and STATE_LEVELS[state_level] >= 2 and "state_transition" not in kinds:
                errors.append(f"{state_level} missing required state-transition evidence")
            if state_level in STATE_LEVELS and STATE_LEVELS[state_level] >= 3 and "state_temporal" not in kinds:
                errors.append(f"{state_level} missing required temporal-stability evidence")
            if policy_gate(root, risk, "focused") == "required" and "focused" not in kinds: errors.append("Completed task missing focused evidence")
            negative_required = field(task, "Negative path required", "no").casefold() in {"yes", "true", "required"}
            np = policy_gate(root, risk, "negative")
            if (np == "required" or (np == "when_failure_behavior" and negative_required)) and "negative" not in kinds: errors.append(f"{risk} missing required negative evidence")
            if policy_gate(root, risk, "integration") == "required" and "integration" not in kinds: errors.append(f"{risk} missing integration evidence")
            recovery_relevant = field(task, "Data operation", "READ_ONLY") in {"MUTATE_IN_PLACE", "DELETE"} or field(task, "Artifact operation", "READ_ONLY") in {"MUTATE_IN_PLACE", "OVERWRITE", "DELETE"}
            rp = policy_gate(root, risk, "rollback")
            if (rp == "required" or (rp == "when_recovery_relevant" and recovery_relevant)) and "rollback" not in kinds: errors.append(f"{risk} missing rollback/recovery evidence")
            if policy_gate(root, risk, "full_suite") == "required" and "full_suite" not in kinds: errors.append(f"{risk} missing full-suite evidence")
            if risk in {"R1", "R2", "R3"} and any(item.get("inspection_status") == "AUTOMATED_EXIT_ONLY" for item in manifest.get("checks", [])):
                errors.append(f"{risk} requires explicit output inspection for verification checks")
            if risk == "R3": errors.extend(validate_review(bundle, manifest, field(task, "Session Label"), label="R3"))
            elif risk == "R2" and manifest.get("review"):
                errors.extend(validate_review(bundle, manifest, field(task, "Session Label"), label="R2 triggered"))
        history = confined_child(root, Path(".ai/history"), validate_identifier(task_id, "task ID"), "task ID") / f"r{int(revision):03d}.json" if task_id_valid else None
        if history is None or not history.is_file(): errors.append("Immutable history record missing")
        elif manifest:
            try:
                record = json.loads(read(history))
                if record.get("evidence_manifest_sha256") != sha256_file(bundle / "manifest.json"): errors.append("History evidence hash mismatch")
                if risk == "R2" and bool(record.get("review_required")):
                    if not manifest.get("review"):
                        errors.append("R2 triggered review required by immutable history but evidence review is missing")
                    elif manifest.get("review", {}).get("trust") != "SIGNED_GUARDIAN":
                        errors.append("R2 triggered review must use SIGNED_GUARDIAN trust")
            except json.JSONDecodeError: errors.append("History record invalid JSON")
        # When idle after close, untracked source edits are not allowed outside a new task.
        current = application_snapshot(root)
        if not ci and field(state, "Active Task ID") == "NONE" and manifest and current["snapshot_sha256"] != manifest.get("verified_snapshot", {}).get("snapshot_sha256"):
            errors.append("Application changed after accepted snapshot without a new active task")

    # Ledger structure and uniqueness.
    ledger = root / ".ai" / "COST_LEDGER.csv"
    with ledger.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != LEDGER_FIELDS: errors.append("COST_LEDGER header mismatch")
        rows = list(reader)
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(rows, start=2):
        key = (row.get("task_id", ""), row.get("task_revision", ""))
        if key in seen: errors.append(f"Duplicate ledger key at row {i}: {key}")
        seen.add(key)
        for name in ["started_at", "first_runnable_at", "completed_at"]:
            if row.get(name):
                try: parse_iso(row[name])
                except ValueError: errors.append(f"Invalid ledger timestamp row {i}: {name}")
    if status == "COMPLETED" and (task_id, revision) not in seen and field(state, "Actual cost signal") != "SKIPPED_BY_OPERATOR": errors.append("Completed task missing ledger row")

    # Git continuity.
    snap = application_snapshot(root)
    if snap["git"] and not template_state and not ci:
        if field(state, "Branch") != snap["branch"]: errors.append("Git branch mismatch with STATE")
        if field(state, "HEAD") != snap["head"]: errors.append("Git HEAD mismatch with STATE")
    if ci:
        if status in LIVE:
            errors.append(f"CI requires no live task; current task status is {status}")
        errors.extend(ci_commit_binding_errors(root, ci_base_ref))

    # Runtime mirrors and transaction integrity.
    errors.extend(validate_runtime_mirrors(root))
    lifecycle_in_progress = (root / ".ai" / ".lifecycle.lock").exists()
    for tx in (root / ".ai" / "transactions").glob("TX-*"):
        if not (tx / "commit.json").exists() and not (tx / "abort.json").exists() and not lifecycle_in_progress:
            errors.append(f"Incomplete lifecycle transaction: {tx.name}")
    pycache = list(root.rglob("__pycache__"))
    if pycache: warnings.append(f"Found {len(pycache)} __pycache__ directories; exclude from release package")
    if len(state.splitlines()) > 180: errors.append("STATE.md exceeds 180-line operating-state budget")
    if len(capsule.splitlines()) > 120: errors.append("CONTEXT_CAPSULE.md exceeds 120-line worker-packet budget")

    if strict:
        if template_state: warnings.append("Project remains idle/template state")
        else:
            strict_text = project + state + (task if status in LIVE | {"COMPLETED"} else "")
            unresolved = re.findall(r"\b(?:UNSET|TASK-XXX|SC-XXX|M-XXX|YYYY-MM-DD)\b", strict_text)
            if unresolved: errors.append(f"Strict mode found {len(unresolved)} unresolved placeholders in active contract/state")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--template", action="store_true")
    mode.add_argument("--strict", action="store_true")
    mode.add_argument("--ci", action="store_true", help="Validate commit-aware CI provenance instead of local HEAD/worktree continuity")
    parser.add_argument("--ci-base-ref", help="Explicit base commit/ref for --ci; otherwise GitHub event metadata or HEAD^ is used")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    errors, warnings = validate(args.root.resolve(), strict=args.strict, ci=args.ci, ci_base_ref=args.ci_base_ref)
    if args.json: print(json.dumps({"errors": errors, "warnings": warnings, "ok": not errors}, ensure_ascii=False, indent=2))
    else:
        for warning in warnings: print("WARNING", warning)
        for error in errors: print("ERROR", error)
        print(f"RESULT: {'FAIL' if errors else 'PASS'} errors={len(errors)} warnings={len(warnings)}")
    return 1 if errors else 0


if __name__ == "__main__": raise SystemExit(main())
