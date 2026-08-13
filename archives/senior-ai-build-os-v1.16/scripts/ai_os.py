#!/usr/bin/env python3
"""Senior AI Build OS v1.16 cost-aware subagent delegation, goal orchestration, acceptance-contract, lifecycle and learning-loop CLI."""
from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import re
import shutil
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from append_cost_ledger import append_row, reconcile_row
from evidence_support import (
    create_staged_bundle,
    next_revision,
    publish_bundle,
    revision_name,
    validate_review_report,
    validate_review_attestation,
    verify_bundle,
    run_command,
)
from refresh_context_capsule import refresh
from goal_support import (
    GOALS_DIR,
    add_task as goal_add_task,
    abort_goal,
    begin_goal,
    block_goal,
    complete_goal,
    defer_node,
    ensure_goal_files,
    load_goal,
    mark_scout_done,
    next_wave as goal_next_wave,
    record_discovery,
    resume_goal,
    sync_task_completion,
    sync_task_abort,
    link_task_active,
    freeze_acceptance_contract,
    freeze_goal_acceptance_contract,
    verify_goal_acceptance_contract,
    bind_goal_acceptance,
    maybe_apply_scout_scope,
    scope_footprint,
    record_acceptance_attempt,
)
from risk_support import RISK_ORDER, effective_risk, minimum_actual_risk, minimum_risk, semantic_uncertainty
from runtime_support import (
    TASK_BASELINE_RELATIVE,
    application_snapshot,
    atomic_write_json,
    atomic_write_text,
    capture_task_baseline,
    in_git_repo,
    lifecycle_lock,
    load_task_baseline,
    sha256_file,
    task_delta_diffs,
    task_delta_files,
    transaction_journal,
    validate_identifier,
    confined_child,
)
from state_runtime import archive_history, load_history, sync_runtime
from state_hazard_support import detect_level as detect_state_hazard, build_contract as build_state_contract, parse_contract as parse_state_contract, LEVELS as STATE_LEVELS, cache_proof as cache_state_proof, record_failure_signature
from validate_ai_os import validate
from project_support import detect_technical_baseline as _project_detect_baseline, install_ci_workflow as _project_install_ci
from policy_support import gate as policy_gate, sync_quality_gates
from health_support import check_delta as health_check_delta, check_repository as health_check_repository, compare as health_compare, report as health_report, save_snapshot as health_save_snapshot, snapshot as health_snapshot, set_architecture_waiver
from lane_support import recommend_lane
from telemetry_support import ingest as telemetry_ingest, summarize as telemetry_summarize
from assurance_support import achieved_assurance, review_requirement
from field_support import record as field_record, report as field_report, load as field_load
from decision_support import record as decision_record, digest as decision_digest
from reporting_support import report as _report_impl
from cli_support import build_parser

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
SHIPPING_DELTAS = {"USER_VISIBLE_BEHAVIOR", "EXECUTABLE_CAPABILITY"}
PROFILE_BY_RISK = {"R0": "LEAN", "R1": "LEAN", "R2": "STANDARD", "R3": "DEEP"}
TEMPLATE_BY_RISK = {"R0": "TASK_LEAN.md", "R1": "TASK_LEAN.md", "R2": "TASK_STANDARD.md", "R3": "TASK_DEEP.md"}
LIVE_STATUSES = {"READY", "ACTIVE", "BLOCKED", "PAUSED"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, body: str) -> None:
    atomic_write_text(path, body)


def field(body: str, name: str, default: str = "") -> str:
    match = re.search(rf"^-?\s*{re.escape(name)}:\s*(.*?)\s*$", body, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else default


def set_field(body: str, name: str, value: str, *, required: bool = True) -> str:
    pattern = re.compile(rf"^(?P<prefix>-?\s*{re.escape(name)}:\s*).*?$", re.MULTILINE | re.IGNORECASE)
    updated, count = pattern.subn(lambda m: m.group("prefix") + str(value), body, count=1)
    if required and count == 0:
        raise ValueError(f"Field not found: {name}")
    return updated


def set_field_if_present(body: str, name: str, value: str) -> str:
    return set_field(body, name, value, required=False)


def find_section_span(body: str, heading: str) -> tuple[int, int] | None:
    lines = body.splitlines(keepends=True)
    offset = 0
    start = None
    level = None
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.rstrip("\r\n"))
        if match and match.group(2).strip().casefold() == heading.casefold():
            start = offset + len(line)
            level = len(match.group(1))
            break
        offset += len(line)
    if start is None or level is None:
        return None
    end = len(body)
    offset = 0
    for line in lines:
        next_offset = offset + len(line)
        if next_offset <= start:
            offset = next_offset
            continue
        match = re.match(r"^(#{1,6})\s+", line)
        if match and len(match.group(1)) <= level:
            end = offset
            break
        offset = next_offset
    return start, end


def set_section(body: str, heading: str, content: str) -> str:
    span = find_section_span(body, heading)
    if span is None:
        raise ValueError(f"Section not found: {heading}")
    start, end = span
    return body[:start] + "\n" + content.strip() + "\n\n" + body[end:].lstrip("\n")


def set_field_in_section(body: str, heading: str, name: str, value: str) -> str:
    span = find_section_span(body, heading)
    if span is None:
        raise ValueError(f"Section not found: {heading}")
    start, end = span
    chunk = set_field(body[start:end], name, value)
    return body[:start] + chunk + body[end:]


def integer_field(body: str, name: str, default: int = 0) -> int:
    try:
        return int(field(body, name, str(default)))
    except ValueError:
        return default


def task_map(body: str) -> dict[str, str]:
    return {
        "task_id": field(body, "Task ID"),
        "task_revision": field(body, "Task Revision", "1"),
        "risk_tier": field(body, "Risk Tier"),
        "execution_profile": field(body, "Execution Profile"),
        "success_criterion": field(body, "Success Criterion"),
        "writer_identity": field(body, "Session Label"),
        "goal_id": field(body, "Goal ID", "NONE"),
        "goal_node": field(body, "Goal Node", "NONE"),
        "acceptance_contract_sha256": field(body, "Acceptance Contract SHA256", "NONE"),
        "acceptance_contract_json": field(body, "Acceptance Contract JSON", "{}"),
        "review_policy": field(body, "Review policy", "auto"),
        "state_hazard_level": field(body, "State Hazard Level", "S0"),
        "state_contract_sha256": field(body, "State Contract SHA256", "NONE"),
        "state_contract_json": field(body, "State Contract JSON", "{}"),
    }



def json_field(body: str, name: str, default: Any) -> Any:
    raw = field(body, name, '')
    if not raw or raw.upper() in {'NONE', 'UNSET'}:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f'Invalid JSON in task field {name}: {exc}')


def acceptance_contract(task_body: str) -> dict[str, Any]:
    value = json_field(task_body, 'Acceptance Contract JSON', {})
    if not isinstance(value, dict):
        raise SystemExit('Acceptance Contract JSON must be an object')
    return value


def verify_acceptance_contract(root: Path, task_body: str, risk: str) -> tuple[list[str], list[str]]:
    """Return frozen acceptance commands/markers and verify probe immutability."""
    goal_id = field(task_body, 'Goal ID', 'NONE')
    if goal_id in {'', 'NONE', 'UNSET'}:
        return [], []
    contract = acceptance_contract(task_body)
    contract_hash = field(task_body, 'Acceptance Contract SHA256', 'NONE')
    if risk in {'R2', 'R3'} and (not contract or contract_hash in {'', 'NONE', 'UNSET'}):
        raise SystemExit(
            f'{risk} Goal-linked task requires an acceptance contract frozen before Worker start; '
            'revert/abort and restart the Goal node after adding --acceptance-command.'
        )
    if not contract:
        return [], []
    payload = dict(contract)
    embedded_hash = str(payload.pop('contract_sha256', '') or '')
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    from runtime_support import sha256_text
    calculated = sha256_text(canonical)
    if contract_hash != calculated or (embedded_hash and embedded_hash != calculated):
        raise SystemExit('Acceptance contract hash mismatch; refusing mutable builder-owned acceptance')
    commands = [str(x).strip() for x in contract.get('commands', []) if str(x).strip()]
    expected = [str(x).strip() for x in contract.get('expected_outputs', []) if str(x).strip()]
    if risk in {'R2', 'R3'} and not commands:
        raise SystemExit(f'{risk} Goal-linked task has no predeclared acceptance command')
    probe_hashes = contract.get('probe_hashes', {}) or {}
    if not isinstance(probe_hashes, dict):
        raise SystemExit('Acceptance probe hashes must be an object')
    for relative, expected_hash in probe_hashes.items():
        path = root / str(relative)
        if not path.is_file():
            raise SystemExit(f'Acceptance probe disappeared after Worker start: {relative}')
        if sha256_file(path) != str(expected_hash):
            raise SystemExit(f'Acceptance probe changed after Worker start: {relative}; builder may not rewrite the judge')
    return commands, expected


def set_delegation_request(root: Path, *, action: str, task_body: str, reasons: list[str], model_class: str, summary_token_budget: int = 500) -> None:
    """Publish a machine-readable subagent request for the outer coding environment."""
    runtime = root / '.ai' / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_write_json(runtime / 'delegation_request.json', {
        'schema_version': 1,
        'created_at': now(),
        'action': action,
        'hard': True,
        'model_class': model_class,
        'summary_token_budget': summary_token_budget,
        'task_id': field(task_body, 'Task ID', 'NONE'),
        'task_revision': integer_field(task_body, 'Task Revision', 1),
        'goal_id': field(task_body, 'Goal ID', 'NONE'),
        'goal_node': field(task_body, 'Goal Node', 'NONE'),
        'reasons': reasons,
        'instruction': 'Use a fresh read-only context. Return verdict, concrete findings and evidence only; do not duplicate Worker exploration.',
    })


def clear_delegation_request(root: Path) -> None:
    path = root / '.ai' / 'runtime' / 'delegation_request.json'
    if path.exists():
        path.unlink()


def r2_review_reasons(root: Path, task_body: str, first_pass_accepted: str, actual_reasons: list[str]) -> list[str]:
    if field(task_body, 'Risk Tier', 'R0') != 'R2':
        return []
    policy = field(task_body, 'Review policy', 'auto').casefold()
    if policy == 'required':
        return ['review policy=required']
    if policy == 'none':
        return []
    reasons: list[str] = []
    if first_pass_accepted == 'no':
        reasons.append('first pass was not accepted')
    task_id = field(task_body, 'Task ID')
    revision = integer_field(task_body, 'Task Revision', 1)
    delta = task_delta_files(root, task_id=task_id, task_revision=revision)
    if len(delta) > 8:
        reasons.append(f'large task delta ({len(delta)} files)')
    risk_at_start = field(task_body, 'Risk At Start', field(task_body, 'Risk Tier', 'R2'))
    if risk_at_start in RISK_ORDER and RISK_ORDER[risk_at_start] < RISK_ORDER['R2']:
        reasons.append(f'risk escalated after Worker start ({risk_at_start}->R2)')
    if any('sensitive business term' in reason.casefold() or 'project r2 surface' in reason.casefold() for reason in actual_reasons):
        reasons.append('project-declared sensitive boundary detected in actual delta')
    sensitive_path_terms = ('auth', 'security', 'payment', 'billing', 'refund', 'wallet')
    if any(any(term in path.casefold().replace('\\', '/') for term in sensitive_path_terms) for path in delta):
        reasons.append('auth/security/financial path detected in actual delta')
    return list(dict.fromkeys(reasons))

def backups(root: Path) -> dict[Path, str]:
    paths = [root / ".ai" / name for name in ["ACTIVE_TASK.md", "STATE.md", "CONTEXT_CAPSULE.md", "COST_LEDGER.csv"]]
    return {path: read(path) for path in paths}


def restore(snapshot: dict[Path, str]) -> None:
    for path, body in snapshot.items():
        write(path, body)


def sync_and_validate(root: Path, *, strict: bool = False) -> None:
    refresh(root)
    sync_runtime(root)
    errors, warnings = validate(root, strict=strict)
    for warning in warnings:
        print("WARNING", warning)
    if errors:
        raise SystemExit("; ".join(errors))


def parse_patterns(value: str) -> list[str]:
    return [item.strip().replace("\\", "/") for item in re.split(r"[,;\n]", value) if item.strip() and item.strip().upper() not in {"NONE", "UNSET"}]


def path_allowed(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    for pattern in patterns:
        p = pattern.strip().replace("\\", "/")
        if p.endswith("/") and normalized.startswith(p):
            return True
        if p.endswith("/**") and normalized.startswith(p[:-3].rstrip("/") + "/"):
            return True
        if fnmatch.fnmatch(normalized, p):
            return True
        if normalized == p:
            return True
    return False


def task_risk_floor(task: str) -> tuple[str, list[str]]:
    return minimum_risk(
        data_operation=field(task, "Data operation", "READ_ONLY"),
        artifact_operation=field(task, "Artifact operation", "CREATE_NEW_VERSION"),
        files_overwritten=field(task, "Files overwritten", "NONE"),
        data_mutated=field(task, "Data mutated", "NONE"),
        external_calls=field(task, "External calls", "NONE"),
        provider_calls=field(task, "External/provider calls", field(task, "Provider calls", "NONE")),
        modify=field(task, "Modify", ""),
        create=field(task, "Create", ""),
    )


def enforce_risk_floor(task: str) -> None:
    effective = field(task, "Risk Tier", "R0")
    declared = field(task, "Declared Risk Tier", "auto").upper()
    floor, reasons = task_risk_floor(task)
    required = floor
    if declared in RISK_ORDER and RISK_ORDER[declared] > RISK_ORDER[required]:
        required = declared
    if effective not in RISK_ORDER or RISK_ORDER[effective] < RISK_ORDER[required]:
        detail = "; ".join(reasons) or f"authorized declaration {declared}"
        raise SystemExit(f"Risk tier {effective} is below required floor {required}: {detail}")


def enforce_scope(root: Path, task: str) -> None:
    allowed = parse_patterns(field(task, "Modify")) + parse_patterns(field(task, "Create"))
    task_id = field(task, "Task ID")
    revision = integer_field(task, "Task Revision", 1)
    changed = task_delta_files(root, task_id=task_id, task_revision=revision)
    unauthorized = [path for path in changed if not path_allowed(path, allowed)]
    if unauthorized:
        raise SystemExit("Unauthorized task-delta paths modified: " + ", ".join(unauthorized))


def max_non_shipping_tasks(project: str) -> int:
    value = integer_field(project, "Maximum consecutive non-shipping tasks", 3)
    return max(1, value)


def shipping_breaker_state(project: str, state: str) -> tuple[str, int, int]:
    """Derive breaker state from canonical counter + project threshold, never trust the label alone."""
    counter = integer_field(state, "Consecutive Non-Shipping Tasks")
    threshold = max_non_shipping_tasks(project)
    return ("ACTIVE" if counter >= threshold else "INACTIVE"), counter, threshold


def project_risk_surfaces(project: str) -> tuple[list[str], list[str], list[str]]:
    """Optional project-specific path/term floors used by actual-delta reconciliation."""
    return (
        parse_patterns(field(project, "R2 paths", "NONE")),
        parse_patterns(field(project, "R3 paths", "NONE")),
        parse_patterns(field(project, "Sensitive business terms", "NONE")),
    )


def reconcile_actual_risk(root: Path, task: str) -> tuple[str, list[str]]:
    """Fail closed when actual changed paths/content imply a higher risk than authorized."""
    task_id = field(task, "Task ID")
    revision = integer_field(task, "Task Revision", 1)
    paths = task_delta_files(root, task_id=task_id, task_revision=revision)
    diffs = task_delta_diffs(root, task_id=task_id, task_revision=revision)
    project = read(root / ".ai" / "PROJECT.md")
    r2_paths, r3_paths, sensitive_terms = project_risk_surfaces(project)
    floor, reasons = minimum_actual_risk(
        paths=paths, diffs=diffs, project_r2_paths=r2_paths, project_r3_paths=r3_paths,
        project_sensitive_terms=sensitive_terms,
    )
    uncertain, uncertainty_reasons = semantic_uncertainty(root, paths=paths, diffs=diffs)
    if uncertain:
        reasons.extend("UNCERTAIN:" + x for x in uncertainty_reasons)
        try: field_record(root,'UNEXPECTED_RISK_ESCALATION',phase='ACCEPTANCE',severity='MEDIUM',trigger='; '.join(uncertainty_reasons),automatic=True,evidence_code='SEMANTIC_RISK_UNCERTAIN')
        except Exception: pass
        try:
            cfg=json.loads((root/'config/risk_semantics.json').read_text(encoding='utf-8'))
        except Exception: cfg={}
        if str(cfg.get('uncertainty_mode') or 'raise_to_R2').lower()=='raise_to_r2' and RISK_ORDER[floor] < RISK_ORDER['R2']:
            floor='R2'
    authorized = field(task, "Risk Tier", "R0")
    if authorized not in RISK_ORDER:
        raise SystemExit(f"Unknown authorized risk tier: {authorized}")
    if RISK_ORDER[floor] > RISK_ORDER[authorized]:
        detail = "; ".join(reasons) or "actual task delta"
        raise SystemExit(
            f"Actual task delta requires {floor}, but task is authorized as {authorized}: {detail}. "
            "Run `ai_os.py amend --risk <required> --reason ...` (and R3 owner authorization when required), "
            "then rerun verification with the gates for the escalated tier."
        )
    return floor, reasons


def _docs_or_test_only_path(path: str) -> bool:
    p = path.replace("\\", "/").casefold()
    name = p.rsplit("/", 1)[-1]
    if p.startswith(("docs/", "doc/", "tests/", "test/")) or "/__tests__/" in f"/{p}":
        return True
    if name.startswith(("readme", "license", "changelog", "contributing")):
        return True
    if name.endswith((".md", ".mdx", ".rst", ".txt")):
        return True
    if re.search(r"(^|/)(test_[^/]+\.py|[^/]+_(?:test|tests)\.py|[^/]+\.(?:test|spec)\.[^/]+)$", p):
        return True
    return False


def reconcile_delivery_delta(root: Path, task: str, delivery_delta: str) -> list[str]:
    """Reject only high-confidence fake-shipping declarations."""
    task_id = field(task, "Task ID")
    revision = integer_field(task, "Task Revision", 1)
    paths = task_delta_files(root, task_id=task_id, task_revision=revision)
    if delivery_delta in SHIPPING_DELTAS:
        if not paths:
            raise SystemExit(f"Delivery Delta {delivery_delta} is inconsistent with an empty application task delta")
        if all(_docs_or_test_only_path(path) for path in paths):
            raise SystemExit(
                f"Delivery Delta {delivery_delta} is inconsistent with docs/tests-only task delta: {', '.join(paths)}. "
                "Use DOCUMENTATION_ONLY/NO_DELTA/RISK_RETIREMENT as appropriate, or include the actual shipping code change."
            )
    return paths


def detect_technical_baseline(root: Path) -> dict[str, str]:
    """Compatibility wrapper; implementation lives in project_support.py."""
    return _project_detect_baseline(root)


def install_ci_workflow(root: Path) -> bool:
    """Compatibility wrapper; implementation lives in project_support.py."""
    return _project_install_ci(root)

def consecutive_failed_first_pass_revisions(root: Path, task_id: str, next_revision_number: int) -> int:
    """Count immediately preceding accepted revisions whose first pass was explicitly rejected."""
    ledger = root / ".ai" / "COST_LEDGER.csv"
    if not ledger.is_file():
        return 0
    values: dict[int, str] = {}
    for record in load_history(root):
        if record.get("task_id") != task_id:
            continue
        try:
            revision = int(record.get("task_revision") or 0)
        except (TypeError, ValueError):
            continue
        value = str(record.get("first_pass_accepted") or "").casefold()
        if value:
            values[revision] = value
    with ledger.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("task_id") != task_id or row.get("accepted") != "yes":
                continue
            try:
                revision = int(row.get("task_revision") or 0)
            except ValueError:
                continue
            values[revision] = (row.get("first_pass_accepted") or values.get(revision, "")).casefold()
    count = 0
    revision = next_revision_number - 1
    while revision > 0 and values.get(revision) == "no":
        count += 1
        revision -= 1
    return count


def breaker_override_info(task: str) -> tuple[bool, str]:
    span = find_section_span(task, "Shipping Breaker Override")
    if span is None:
        return False, ""
    chunk = task[span[0]:span[1]]
    return True, field(chunk, "Reason", "")


def initialize_project(root: Path, args: argparse.Namespace) -> None:
    ensure_goal_files(root)
    project_path = root / ".ai" / "PROJECT.md"
    state_path = root / ".ai" / "STATE.md"
    task_path = root / ".ai" / "ACTIVE_TASK.md"
    project = read(project_path)
    state = read(state_path)
    task = read(task_path)
    timestamp = now()[:10]
    for name, value in {
        "Updated": timestamp,
        "Project ID": args.project_id,
        "Owner": args.owner,
        "Project Status": "ACTIVE",
    }.items():
        project = set_field(project, name, value)
    project = set_section(project, "Product Problem", args.problem)
    project = set_section(project, "Target User", args.target_user)
    project = set_section(project, "Primary User Workflow", f"1. {args.primary_action}\n2. System performs the core behavior.\n3. {args.observable_result}")
    project = set_section(project, "MVP Goal", args.mvp_goal)
    project = set_section(project, "Success Criteria", f"### SC-001\n- User: {args.target_user}\n- Action: {args.primary_action}\n- Observable result: {args.observable_result}\n- Acceptance threshold: {args.acceptance_threshold}\n- Demo method: {args.demo_method}")
    project = set_section(project, "In Scope", "- Primary workflow required to satisfy SC-001.")
    project = set_section(project, "Out of Scope / Later", "- Anything not required for SC-001 unless explicitly authorized by a later task/milestone.")
    project = set_section(project, "Supported Cases", "- Representative inputs and the primary SC-001 workflow.")
    project = set_section(project, "Explicitly Unsupported Cases", "- Unspecified edge cases until they are accepted into product scope.")
    project = set_field_in_section(project, "Current Milestone", "Milestone ID", args.milestone_id)
    project = set_field_in_section(project, "Current Milestone", "User outcome", args.mvp_goal)
    project = set_field_in_section(project, "Current Milestone", "Success Criterion", "SC-001")
    project = set_field_in_section(project, "Current Milestone", "Demonstrable outcome", args.observable_result)
    project = set_field(project, "User", args.target_user)
    project = set_field(project, "Action", args.primary_action)
    project = set_field(project, "Observable result", args.observable_result)
    project = set_field(project, "Acceptance threshold", args.acceptance_threshold)
    project = set_field(project, "Demo method", args.demo_method)
    project = set_field_in_section(project, "Current Milestone", "Time-to-first-demo expectation", "smallest vertical slice first")
    for name, value in detect_technical_baseline(root).items():
        project = set_field_in_section(project, "Technical Baseline", name, value)
    for name, value in {
        "Canonical data": "NONE_IDENTIFIED_AT_INIT", "Test/clone data": "fixtures/local clones only",
        "Backup/rollback": "revert task delta; backup before approved destructive operations",
    }.items():
        project = set_field_in_section(project, "Data and Artifact Policy", name, value)
    for name, value in {
        "Time": "OWNER_DEFINED", "Financial": "OWNER_DEFINED", "Privacy": "OWNER_DEFINED",
        "Platform": "OWNER_DEFINED", "Licensing": "OWNER_DEFINED", "External services": "NONE_DECLARED_AT_INIT",
    }.items():
        project = set_field_in_section(project, "Constraints", name, value)
    state = set_field(state, "Updated", timestamp)
    state = set_field_in_section(state, "Continuity Fingerprint", "Project ID", args.project_id)
    ci_installed = False
    if in_git_repo(root) and not args.no_ci:
        ci_installed = install_ci_workflow(root)
    snap = application_snapshot(root)
    state = set_field_in_section(state, "Continuity Fingerprint", "Branch", snap["branch"])
    state = set_field_in_section(state, "Continuity Fingerprint", "HEAD", snap["head"])
    state = set_field_in_section(state, "Continuity Fingerprint", "Worktree", snap["worktree"])
    state = set_field_in_section(state, "Continuity Fingerprint", "Last Known Good Commit", snap["head"])
    state = set_field_in_section(state, "Continuity Fingerprint", "Runtime/Data Fingerprint", "NOT_CAPTURED")
    state = set_field_in_section(state, "Current Product Position", "Current milestone", args.milestone_id)
    state = set_field_in_section(state, "Current Product Position", "Success criterion", "SC-001")
    state = set_field_in_section(state, "Current Product Position", "Current user-visible limitation", "MVP outcome not yet demonstrated")
    state = set_field_in_section(state, "Delivery Pulse", "Next required demo", args.observable_result)
    state = set_field_in_section(state, "Cost Efficiency State", "Expected cost range", "small until evidence justifies escalation")
    state = set_field_in_section(state, "Cost Efficiency State", "Next spend expected to buy", "first runnable SC-001 evidence")
    state = set_section(state, "Later / Not MVP", "- Anything outside the initialized SC-001 scope.")
    task = set_field_if_present(task, "Project ID", args.project_id)
    task = set_field_if_present(task, "Branch", str(snap["branch"]))
    task = set_field_if_present(task, "HEAD", str(snap["head"]))
    task = set_field_if_present(task, "Worktree", str(snap["worktree"]))
    task = set_field_if_present(task, "Starting Snapshot SHA256", str(snap["snapshot_sha256"]))
    write(project_path, project)
    write(state_path, state)
    write(task_path, task)
    sync_quality_gates(root)
    health_save_snapshot(root)
    sync_and_validate(root)
    print(f"Initialized project {args.project_id}" + ("; installed .github/workflows/ai-build-os.yml" if ci_installed else ""))


def start_task(root: Path, args: argparse.Namespace) -> None:
    ai = root / ".ai"
    task_path, state_path = ai / "ACTIVE_TASK.md", ai / "STATE.md"
    snapshot = backups(root)
    old_task = snapshot[task_path]
    if field(old_task, "Task Status") in LIVE_STATUSES:
        raise SystemExit(f"Refusing to replace live task {field(old_task, 'Task ID')}")
    project = read(ai / "PROJECT.md")
    state = snapshot[state_path]
    project_id = args.project_id or field(project, "Project ID") or field(state, "Project ID")
    if project_id in {"", "UNSET", "UNKNOWN"}:
        raise SystemExit("Project ID is not initialized; run `ai_os.py init`")
    breaker, _, _ = shipping_breaker_state(project, state)
    if breaker == "ACTIVE" and args.delivery_delta not in SHIPPING_DELTAS:
        if not args.breaker_override:
            raise SystemExit(
                "Shipping Circuit Breaker is ACTIVE: the next task must declare USER_VISIBLE_BEHAVIOR or "
                "EXECUTABLE_CAPABILITY. Use --breaker-override --breaker-override-reason ... only for an explicit exception."
            )
        if not args.breaker_override_reason.strip():
            raise SystemExit("--breaker-override requires --breaker-override-reason")
    app = application_snapshot(root)
    if not app["git"] and not args.allow_no_git:
        raise SystemExit("Git repository required; pass --allow-no-git only for temporary evaluation")

    args.task_id = validate_identifier(args.task_id, "task ID")
    state_level, detected_state_signals = detect_state_hazard(
        requested=getattr(args, "state_hazard", "auto"), outcome=args.outcome, modify=args.modify, create=args.create,
        signals=list(getattr(args, "state_signal", []) or []),
    )
    state_dependencies = list(getattr(args, "state_dependency", []) or []) or (parse_patterns(args.modify) + parse_patterns(args.create))
    state_contract = build_state_contract(
        level=state_level, authority=getattr(args, "state_authority", ""),
        transitions=list(getattr(args, "state_transition", []) or []), invariants=list(getattr(args, "state_invariant", []) or []),
        dependencies=state_dependencies, signals=sorted(set(detected_state_signals) | set(getattr(args, "state_signal", []) or [])),
    )

    risk, risk_floor, risk_reasons = effective_risk(
        args.risk,
        data_operation=args.data_operation,
        artifact_operation=args.artifact_operation,
        files_overwritten=args.files_overwritten,
        data_mutated=args.data_mutated,
        external_calls=args.external_calls,
        provider_calls=args.provider_calls,
        modify=args.modify,
        create=args.create,
    )
    goal_id = getattr(args, "goal_id", None) or "NONE"
    goal_node = getattr(args, "goal_node", None) or "NONE"
    if goal_id not in {"", "NONE", "UNSET"}:
        goal_id = validate_identifier(goal_id, "goal ID")
        goal_node = validate_identifier(goal_node, "goal node ID")
        goal = load_goal(root)
        if goal.get("goal_id") != goal_id or goal.get("status") != "ACTIVE":
            raise SystemExit(f"Task goal link invalid: {goal_id} is not the ACTIVE goal")
        ceiling = str(goal.get("risk_ceiling", "R2"))
        if RISK_ORDER[risk] > RISK_ORDER[ceiling]:
            raise SystemExit(f"Effective task risk {risk} exceeds Goal {goal_id} risk ceiling {ceiling}; owner decision/escalation required")
        node = (goal.get("tasks") or {}).get(goal_node)
        if goal_node in {"", "NONE", "UNSET"} or not node:
            raise SystemExit("Goal-linked task requires a valid --goal-node")
        if node.get("status") != "PLANNED":
            raise SystemExit(f"Goal node {goal_node} is not PLANNED: {node.get('status')}")
    profile = PROFILE_BY_RISK[risk]
    revision = next_revision(root, args.task_id)
    if goal_id not in {"", "NONE", "UNSET"}:
        revision_limit = max(1, int((goal.get("budget") or {}).get("max_revisions_per_task", 2) or 2))
        if revision > revision_limit and not args.stop_loss_ack.strip():
            raise SystemExit(
                f"Goal revision budget exceeded for {args.task_id}: attempting r{revision:03d} with max_revisions_per_task={revision_limit}. "
                "Use --stop-loss-ack with the changed root-cause hypothesis, or split/replan the node."
            )
    failed_revisions = consecutive_failed_first_pass_revisions(root, args.task_id, revision)
    stop_loss_threshold = 2
    if goal_id not in {"", "NONE", "UNSET"}:
        stop_loss_threshold = max(1, int((goal.get("budget") or {}).get("max_revisions_per_task", 2) or 2))
    if failed_revisions >= stop_loss_threshold and not args.stop_loss_ack.strip():
        raise SystemExit(
            f"Stop-loss active for {args.task_id}: {failed_revisions} consecutive prior revisions had first_pass_accepted=no; "
            f"Goal budget allows {stop_loss_threshold} before acknowledgement. "
            "Start the next revision only with --stop-loss-ack 'what root-cause hypothesis changed'."
        )
    task = read(root / "templates" / TEMPLATE_BY_RISK[risk])
    state_revision = integer_field(state, "State Revision") + 1
    capsule_revision = integer_field(snapshot[ai / "CONTEXT_CAPSULE.md"], "Capsule Revision")
    timestamp = now()

    if risk == "R3" and args.owner_authorization != "APPROVED":
        detail = "; ".join(risk_reasons) or "declared R3"
        raise SystemExit(f"Effective R3 requires --owner-authorization APPROVED ({detail})")
    if args.owner_authorization == "APPROVED" and not args.authorization_reference.strip():
        raise SystemExit("Approved work requires --authorization-reference")

    clear_delegation_request(root)
    replacements = {
        "Task Status": "ACTIVE" if args.claim else "READY", "Task Mode": profile,
        "Task ID": args.task_id, "Task Revision": revision, "Created": timestamp[:10],
        "Owner Authorization": args.owner_authorization, "Authorization Reference": args.authorization_reference or "NONE",
        "Milestone ID": args.milestone_id, "Success Criterion": args.success_criterion,
        "Goal ID": getattr(args, "goal_id", None) or "NONE", "Goal Node": getattr(args, "goal_node", None) or "NONE",
        "Delivery Delta": args.delivery_delta, "Demonstrable Result": args.demonstrable_result,
        "Unlocks": args.unlocks, "Declared Risk Tier": args.risk.upper(), "Risk At Start": risk, "Risk Tier": risk,
        "Risk Floor": risk_floor, "Risk Floor Reason": "; ".join(risk_reasons) or "none",
        "Execution Profile": profile, "Negative path required": "yes" if args.negative_required else "no",
        "Review policy": getattr(args, "review_policy", "auto"),
        "Acceptance Contract SHA256": getattr(args, "acceptance_contract_sha256", "NONE"),
        "Acceptance Contract JSON": getattr(args, "acceptance_contract_json", "{}"),
        "State Hazard Level": state_level,
        "State Hazard Signals": ", ".join(state_contract.get("signals") or []) or "NONE",
        "State Contract SHA256": state_contract.get("contract_sha256", "NONE"),
        "State Contract JSON": json.dumps(state_contract, ensure_ascii=False, separators=(",", ":")),
        "Project ID": project_id, "Branch": app["branch"], "HEAD": app["head"], "Worktree": app["worktree"],
        "Starting Snapshot SHA256": app["snapshot_sha256"], "Verified Snapshot SHA256": "NONE",
        "State Revision": state_revision, "Context Capsule Revision": capsule_revision,
        "Read": args.read_scope, "Modify": args.modify, "Create": args.create, "Commands": args.commands,
        "Local services": args.local_services, "External calls": args.external_calls,
        "Data operation": args.data_operation, "Artifact operation": args.artifact_operation,
        "Inputs": args.inputs, "Outputs": args.outputs, "Files created": args.files_created,
        "Files overwritten": args.files_overwritten, "Data mutated": args.data_mutated,
        "External/provider calls": args.provider_calls, "Expected provider cost": args.expected_provider_cost,
        "Disk requirement": args.disk_requirement, "RAM/GPU requirement": args.ram_requirement,
        "Process/port": args.process_port, "Cache/artifact lineage": args.artifact_lineage,
        "Rollback": args.rollback, "Expected cost range": args.expected_cost_range,
        "Primary cost drivers": args.primary_cost_drivers, "Cheapest evidence-first sequence": args.evidence_sequence,
        "Initial execution profile": profile, "Escalation conditions": args.escalation_conditions,
        "Lease Status": "CLAIMED" if args.claim else "UNCLAIMED", "Writer Role": args.writer_role,
        "Platform": args.platform, "Identity Verification": "VERIFIED" if args.claim else "PENDING",
        "Session Label": args.session_label, "Claimed At": timestamp if args.claim else "NONE",
        "Last Heartbeat": timestamp if args.claim else "NONE", "Started At": timestamp,
        "First Runnable At": "NONE", "First Runnable Evidence": "NONE", "Completed At": "NONE",
        "Evidence Bundle": "NONE",
    }
    for name, value in replacements.items():
        task = set_field_if_present(task, name, str(value))
    task = set_section(task, "Single Outcome", args.outcome)
    if args.accept:
        task = set_section(task, "Acceptance Criteria", "\n".join(f"- [ ] {item}" for item in args.accept))
    if getattr(args, "replaces", None):
        task = task.rstrip() + "\n\n## Replacement Contract\n\n" + "\n".join(f"- {item}" for item in args.replaces if item.strip()) + "\n"
    if args.breaker_override:
        task = task.rstrip() + (
            "\n\n## Shipping Breaker Override\n\n"
            f"- Breaker at start: {breaker}\n"
            f"- Reason: {args.breaker_override_reason.strip()}\n"
        )
    if args.stop_loss_ack.strip():
        task = task.rstrip() + (
            "\n\n## Revision Stop-Loss Acknowledgement\n\n"
            f"- Prior failed-first-pass revisions: {failed_revisions}\n"
            f"- Changed root-cause hypothesis: {args.stop_loss_ack.strip()}\n"
        )
    if risk == "R3":
        task = set_field_if_present(task, "Human review required", "yes")
        task = set_field_if_present(task, "Full suite required", "yes")
        task = set_field_if_present(task, "Specialist reviewer trigger", "security/data/operations")

    state = set_field(state, "Updated", timestamp[:10])
    state = set_field(state, "State Revision", str(state_revision))
    for name, value in {"Project ID": project_id, "Branch": app["branch"], "HEAD": app["head"], "Worktree": app["worktree"], "Active Task ID": args.task_id}.items():
        state = set_field_in_section(state, "Continuity Fingerprint", name, str(value))
    state = set_field_in_section(state, "Current Product Position", "Current milestone", args.milestone_id)
    state = set_field_in_section(state, "Current Product Position", "Success criterion", args.success_criterion)
    state = set_field_in_section(state, "Active Work", "Status", "ACTIVE" if args.claim else "READY")
    state = set_field_in_section(state, "Active Work", "Task ID", args.task_id)
    state = set_field_in_section(state, "Active Work", "Writer session", args.session_label if args.claim else "NONE")
    state = set_field_in_section(state, "Active Work", "What is changing", args.outcome)
    state = set_field_in_section(state, "Active Work", "Current checkpoint", "STARTED" if args.claim else "AUTHORIZED")
    state = set_section(state, "Next Exact Action", "1. Inspect only the smallest relevant source/test surface.\n2. Make the smallest patch, run the cheapest relevant verification, then inspect output and task delta.")

    baseline_path = root / TASK_BASELINE_RELATIVE
    old_baseline = baseline_path.read_text(encoding="utf-8") if baseline_path.is_file() else None
    try:
        capture_task_baseline(root, args.task_id, revision, parse_patterns(args.modify) + parse_patterns(args.create), state_contract_sha256=str(state_contract.get("contract_sha256") or "NONE"))
        write(task_path, task)
        write(state_path, state)
        sync_and_validate(root)
        if goal_id not in {"", "NONE", "UNSET"}:
            link_task_active(root, goal_id, goal_node, args.task_id, revision)
    except BaseException:
        restore(snapshot)
        if old_baseline is None:
            baseline_path.unlink(missing_ok=True)
        else:
            write(baseline_path, old_baseline)
        raise
    if args.breaker_override:
        try: field_record(root,'POLICY_OVERRIDE',phase='TASK_START',severity='MEDIUM',trigger=args.breaker_override_reason.strip(),automatic=True,evidence_code='SHIPPING_BREAKER_OVERRIDE',metadata={'risk':risk,'lane':profile})
        except Exception: pass
    if args.risk.upper() != risk:
        print(f"Risk auto-floor: declared={args.risk.upper()} effective={risk} floor={risk_floor} reasons={'; '.join(risk_reasons) or 'none'}")
    print(f"Started {args.task_id}/r{revision:03d} risk={risk} profile={profile} status={'ACTIVE' if args.claim else 'READY'} preexisting_dirty={len(app['changed_files'])}")
    if state_level != 'S0':
        print(f"State hazard: {state_level} signals={','.join(state_contract.get('signals') or []) or 'declared'}" + ("; transition proof required" if STATE_LEVELS[state_level] >= 2 else ""))

def _save_task_state(root: Path, task: str, state: str, snapshot: dict[Path, str]) -> None:
    try:
        write(root / ".ai" / "ACTIVE_TASK.md", task)
        write(root / ".ai" / "STATE.md", state)
        sync_and_validate(root)
    except BaseException:
        restore(snapshot)
        raise


def claim_task(root: Path, args: argparse.Namespace) -> None:
    ai = root / ".ai"; snapshot = backups(root); task = snapshot[ai / "ACTIVE_TASK.md"]; state = snapshot[ai / "STATE.md"]
    if field(task, "Task Status") != "READY" or field(task, "Lease Status") != "UNCLAIMED":
        raise SystemExit("claim requires READY task with UNCLAIMED lease")
    timestamp = now()
    for name, value in {
        "Task Status": "ACTIVE", "Lease Status": "CLAIMED", "Identity Verification": "VERIFIED",
        "Session Label": args.session_label, "Writer Role": args.writer_role, "Platform": args.platform,
        "Claimed At": timestamp, "Last Heartbeat": timestamp,
    }.items():
        task = set_field_if_present(task, name, value)
    state = set_field_in_section(state, "Active Work", "Status", "ACTIVE")
    state = set_field_in_section(state, "Active Work", "Writer session", args.session_label)
    state = set_field_in_section(state, "Active Work", "Current checkpoint", "STARTED")
    state = set_section(state, "Next Exact Action", "1. Inspect the smallest relevant source/test surface.\n2. Patch, run focused verification, inspect output and task delta.")
    _save_task_state(root, task, state, snapshot)
    print(f"Claimed {field(task, 'Task ID')} as {args.session_label}")


def pause_task(root: Path, args: argparse.Namespace) -> None:
    ai = root / ".ai"; snapshot = backups(root); task = snapshot[ai / "ACTIVE_TASK.md"]; state = snapshot[ai / "STATE.md"]
    if field(task, "Task Status") != "ACTIVE":
        raise SystemExit("pause requires ACTIVE task")
    timestamp = now()
    task = set_field(task, "Task Status", "PAUSED")
    task = set_field_if_present(task, "Lease Status", "RELEASED")
    task = set_field_if_present(task, "Identity Verification", "PENDING")
    task = set_field_if_present(task, "Released At", timestamp)
    task = set_field_if_present(task, "Last Heartbeat", timestamp)
    state = set_field_in_section(state, "Active Work", "Status", "PAUSED")
    state = set_field_in_section(state, "Active Work", "Writer session", "NONE")
    state = set_field_in_section(state, "Active Work", "Current checkpoint", "PAUSED")
    state = set_section(state, "Next Exact Action", f"1. Resume `{field(task, 'Task ID')}` with `ai_os.py resume` when ready.\n2. Do not absorb its task delta into another task.")
    _save_task_state(root, task, state, snapshot)
    print(f"Paused {field(task, 'Task ID')}")


def resume_task(root: Path, args: argparse.Namespace) -> None:
    ai = root / ".ai"; snapshot = backups(root); task = snapshot[ai / "ACTIVE_TASK.md"]; state = snapshot[ai / "STATE.md"]
    if field(task, "Task Status") != "PAUSED":
        raise SystemExit("resume requires PAUSED task")
    timestamp = now()
    for name, value in {
        "Task Status": "ACTIVE", "Lease Status": "CLAIMED", "Identity Verification": "VERIFIED",
        "Session Label": args.session_label, "Writer Role": args.writer_role, "Platform": args.platform,
        "Claimed At": timestamp, "Last Heartbeat": timestamp,
    }.items():
        task = set_field_if_present(task, name, value)
    state = set_field_in_section(state, "Active Work", "Status", "ACTIVE")
    state = set_field_in_section(state, "Active Work", "Writer session", args.session_label)
    state = set_field_in_section(state, "Active Work", "Current checkpoint", "RESUMED")
    state = set_section(state, "Next Exact Action", "1. Read the compact context capsule and current task delta.\n2. Continue from the last evidence-producing checkpoint.")
    _save_task_state(root, task, state, snapshot)
    print(f"Resumed {field(task, 'Task ID')} as {args.session_label}")


def abort_task(root: Path, args: argparse.Namespace) -> None:
    ai = root / ".ai"; snapshot = backups(root); task = snapshot[ai / "ACTIVE_TASK.md"]; state = snapshot[ai / "STATE.md"]
    if field(task, "Task Status") not in LIVE_STATUSES:
        raise SystemExit("abort requires a live READY/ACTIVE/PAUSED/BLOCKED task")
    delta = task_delta_files(root, task_id=field(task, "Task ID"), task_revision=integer_field(task, "Task Revision", 1))
    if delta:
        raise SystemExit("Refusing to abort with task delta present; revert/stash it or amend/finish the task first: " + ", ".join(delta))
    timestamp = now(); snap = application_snapshot(root)
    task = set_field(task, "Task Status", "ABORTED")
    task = set_field_if_present(task, "Lease Status", "RELEASED")
    task = set_field_if_present(task, "Identity Verification", "PENDING")
    task = set_field_if_present(task, "Released At", timestamp)
    task = set_field_if_present(task, "Completed At", timestamp)
    for name, value in {"Branch": snap["branch"], "HEAD": snap["head"], "Worktree": snap["worktree"], "Active Task ID": "NONE"}.items():
        state = set_field_in_section(state, "Continuity Fingerprint", name, str(value))
    for name, value in {"Status": "IDLE", "Task ID": "NONE", "Writer session": "NONE", "What is changing": "NOTHING", "Current checkpoint": "ABORTED"}.items():
        state = set_field_in_section(state, "Active Work", name, value)
    state = set_section(state, "Next Exact Action", "1. Create the next smallest milestone-linked task.\n2. Keep aborted work out of the application worktree.")
    _save_task_state(root, task, state, snapshot)
    sync_task_abort(root, field(task, "Goal ID", "NONE"), field(task, "Goal Node", "NONE"), reason="task aborted with zero delta")
    sync_runtime(root)
    print(f"Aborted {field(task, 'Task ID')} with zero task delta")


def _merge_scope(existing: str, addition: str | None) -> str:
    values = parse_patterns(existing)
    for item in parse_patterns(addition or ""):
        if item not in values:
            values.append(item)
    return ",".join(values) if values else "NONE"


def amend_task(root: Path, args: argparse.Namespace) -> None:
    ai = root / ".ai"; snapshot = backups(root); task = snapshot[ai / "ACTIVE_TASK.md"]; state = snapshot[ai / "STATE.md"]
    if field(task, "Task Status") not in LIVE_STATUSES:
        raise SystemExit("amend requires a live task")
    if not any([args.add_modify, args.add_create, args.risk, args.data_operation, args.artifact_operation, args.files_overwritten, args.data_mutated, args.external_calls, args.provider_calls]):
        raise SystemExit("amend requires a scope, risk or side-effect change")

    old_risk = field(task, "Risk Tier", "R0")
    modify = _merge_scope(field(task, "Modify"), args.add_modify)
    create = _merge_scope(field(task, "Create"), args.add_create)
    data_operation = args.data_operation or field(task, "Data operation", "READ_ONLY")
    artifact_operation = args.artifact_operation or field(task, "Artifact operation", "CREATE_NEW_VERSION")
    files_overwritten = args.files_overwritten if args.files_overwritten is not None else field(task, "Files overwritten", "NONE")
    data_mutated = args.data_mutated if args.data_mutated is not None else field(task, "Data mutated", "NONE")
    external_calls = args.external_calls if args.external_calls is not None else field(task, "External calls", "NONE")
    provider_calls = args.provider_calls if args.provider_calls is not None else field(task, "External/provider calls", "NONE")
    requested = old_risk
    if args.risk and RISK_ORDER[args.risk] > RISK_ORDER[requested]:
        requested = args.risk
    risk, floor, reasons = effective_risk(
        requested, data_operation=data_operation, artifact_operation=artifact_operation,
        files_overwritten=files_overwritten, data_mutated=data_mutated,
        external_calls=external_calls, provider_calls=provider_calls, modify=modify, create=create,
    )
    if RISK_ORDER[risk] < RISK_ORDER[old_risk]:
        risk = old_risk
    linked_goal_id = field(task, "Goal ID", "NONE")
    if linked_goal_id not in {"", "NONE", "UNSET"}:
        goal = load_goal(root)
        ceiling = str(goal.get("risk_ceiling", "R2"))
        if RISK_ORDER[risk] > RISK_ORDER[ceiling]:
            raise SystemExit(f"Amendment risk {risk} exceeds Goal {linked_goal_id} risk ceiling {ceiling}; owner decision/escalation required")
        goal_node_id = field(task, "Goal Node", "NONE")
        goal_node = (goal.get("tasks") or {}).get(goal_node_id) or {}
        initial = set(goal_node.get("initial_scope_footprint") or [])
        if initial:
            proposed = scope_footprint(root, modify, create)
            original_patterns = (
                parse_patterns(str(goal_node.get("initial_modify_scope") or "NONE"))
                + parse_patterns(str(goal_node.get("initial_create_scope") or "NONE"))
            )
            # Count only authorization that is genuinely outside the scope grammar
            # frozen at task start. A new file created under an already-authorized
            # wildcard such as src/** is not scope growth.
            newly_authorized = set()
            for item in proposed:
                candidate = item[len("PATTERN:"):] if item.startswith("PATTERN:") else item
                if not path_allowed(candidate, original_patterns):
                    newly_authorized.add(item)
            growth = (len(newly_authorized) / max(1, len(initial))) * 100.0
            limit = float((goal.get("budget") or {}).get("scope_growth_limit_percent", 30) or 30)
            if growth > limit:
                raise SystemExit(
                    f"Scope growth {growth:.1f}% exceeds Goal limit {limit:.1f}% ({len(initial)} baseline footprint, "
                    f"{len(newly_authorized)} additional entries). SPLIT_OR_REPLAN instead of widening this task."
                )
    if risk == "R3" and field(task, "Owner Authorization") != "APPROVED":
        if args.owner_authorization != "APPROVED" or not args.authorization_reference:
            raise SystemExit("Amendment escalates to R3; provide --owner-authorization APPROVED --authorization-reference ...")
        task = set_field_if_present(task, "Owner Authorization", "APPROVED")
        task = set_field_if_present(task, "Authorization Reference", args.authorization_reference)

    updates = {
        "Modify": modify, "Create": create, "Data operation": data_operation,
        "Artifact operation": artifact_operation, "Files overwritten": files_overwritten,
        "Data mutated": data_mutated, "External calls": external_calls,
        "External/provider calls": provider_calls, "Declared Risk Tier": risk if RISK_ORDER[risk] > RISK_ORDER[old_risk] else field(task, "Declared Risk Tier", old_risk),
        "Risk Tier": risk, "Risk Floor": floor,
        "Risk Floor Reason": "; ".join(reasons) or "none", "Execution Profile": PROFILE_BY_RISK[risk],
    }
    for name, value in updates.items():
        task = set_field_if_present(task, name, value)
    if risk == "R3":
        task = set_field_if_present(task, "Human review required", "yes")
        task = set_field_if_present(task, "Full suite required", "yes")
    timestamp = now()
    entry = f"- {timestamp}: {args.reason} | modify+={args.add_modify or 'NONE'} | create+={args.add_create or 'NONE'} | risk {old_risk}->{risk}"
    span = find_section_span(task, "Scope Amendments")
    if span:
        existing = task[span[0]:span[1]].strip()
        task = set_section(task, "Scope Amendments", (existing + "\n" + entry).strip())
    else:
        task = task.rstrip() + "\n\n## Scope Amendments\n\n" + entry + "\n"
    state = set_field_in_section(state, "Active Work", "Current checkpoint", "SCOPE_AMENDED")
    state = set_section(state, "Next Exact Action", "1. Continue with the newly authorized smallest scope.\n2. Re-run the cheapest check affected by the amendment before broader verification.")
    _save_task_state(root, task, state, snapshot)
    print(f"Amended {field(task, 'Task ID')} risk={risk} modify={modify} create={create}")


def mark_runnable(root: Path, args: argparse.Namespace) -> None:
    ai = root / ".ai"; task_path = ai / "ACTIVE_TASK.md"; state_path = ai / "STATE.md"
    snapshot = backups(root); task = snapshot[task_path]; state = snapshot[state_path]
    if field(task, "Task Status") != "ACTIVE" or field(task, "Lease Status") != "CLAIMED":
        raise SystemExit("runnable requires ACTIVE task with CLAIMED lease")
    evidence_path = (root / args.evidence).resolve()
    try: evidence_path.relative_to(root.resolve())
    except ValueError as exc: raise SystemExit("Runnable evidence must be inside repository") from exc
    if not evidence_path.is_file(): raise SystemExit(f"Runnable evidence missing: {args.evidence}")
    timestamp = args.at or now()
    task = set_field(task, "First Runnable At", timestamp)
    task = set_field_if_present(task, "First Runnable Evidence", f"{args.evidence} SHA256 {sha256_file(evidence_path)}")
    state = set_field_in_section(state, "Current Product Position", "Demo evidence", args.evidence)
    state = set_field_in_section(state, "Delivery Pulse", "Time since last runnable demo", "0")
    state = set_field_in_section(state, "Active Work", "Current checkpoint", "FIRST_RUNNABLE")
    try:
        write(task_path, task); write(state_path, state); sync_and_validate(root)
    except BaseException:
        restore(snapshot); raise
    print(f"Recorded explicit first runnable evidence for {field(task, 'Task ID')}")


def close_with_bundle(root: Path, args: argparse.Namespace, stage: Path | None = None, staged_manifest: dict[str, Any] | None = None) -> None:
    ai = root / ".ai"; task_path = ai / "ACTIVE_TASK.md"; state_path = ai / "STATE.md"
    snapshot_files = backups(root); task = snapshot_files[task_path]; state = snapshot_files[state_path]
    if field(task, "Task Status") != "ACTIVE" or field(task, "Lease Status") != "CLAIMED" or field(task, "Identity Verification") != "VERIFIED":
        raise SystemExit("close requires ACTIVE task with CLAIMED lease and VERIFIED identity")
    task_id = field(task, "Task ID"); revision = int(field(task, "Task Revision", "1")); risk = field(task, "Risk Tier")
    enforce_risk_floor(task)
    actual_floor, actual_risk_reasons = reconcile_actual_risk(root, task)
    linked_goal_id = field(task, "Goal ID", "NONE")
    linked_goal_node = field(task, "Goal Node", "NONE")
    if linked_goal_id not in {"", "NONE", "UNSET"}:
        goal = load_goal(root)
        ceiling = str(goal.get("risk_ceiling", "R2"))
        if RISK_ORDER[actual_floor] > RISK_ORDER[ceiling]:
            raise SystemExit(f"Actual task delta requires {actual_floor}, above Goal {linked_goal_id} risk ceiling {ceiling}; owner decision/escalation required")
    final_bundle = confined_child(root, Path(".ai/evidence"), validate_identifier(task_id, "task ID"), "task ID") / revision_name(revision)
    published = False; history_path: Path | None = None
    if stage is not None:
        manifest = staged_manifest or json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
        bundle_for_validation = stage
    else:
        bundle_for_validation = (root / args.evidence_bundle).resolve()
        manifest = json.loads((bundle_for_validation / "manifest.json").read_text(encoding="utf-8"))
    bundle_errors = verify_bundle(root, bundle_for_validation)
    if bundle_errors: raise SystemExit("; ".join(bundle_errors))
    if manifest["task_id"] != task_id or int(manifest["task_revision"]) != revision:
        raise SystemExit("Evidence bundle task identity/revision mismatch")
    if manifest.get("risk_tier") != risk or manifest.get("execution_profile") != field(task, "Execution Profile"):
        raise SystemExit("Evidence bundle risk/profile is stale; rerun evidence after risk/profile amendment")
    current = application_snapshot(root)
    if current["snapshot_sha256"] != manifest["verified_snapshot"]["snapshot_sha256"]:
        raise SystemExit("Application snapshot changed after verification; rerun evidence")
    enforce_scope(root, task)
    if risk == "R3" and not manifest.get("review"):
        raise SystemExit("R3 requires bundled independent review")
    if risk == "R3" and (manifest.get("review") or {}).get("trust") != "SIGNED_GUARDIAN":
        raise SystemExit("R3 requires SIGNED_GUARDIAN review evidence")
    timestamp = args.completed_at or now(); delta = args.delivery_delta or field(task, "Delivery Delta")
    reconcile_delivery_delta(root, task, delta)
    evidence_relative = f".ai/evidence/{task_id}/{revision_name(revision)}"
    state_revision = integer_field(state, "State Revision") + 1
    try:
        if stage is not None:
            publish_bundle(root, stage, task_id, revision); published = True
        elif bundle_for_validation != final_bundle.resolve():
            raise SystemExit("Manual close requires the immutable canonical evidence bundle path")
        task_updates = {
            "Task Status": "COMPLETED", "Delivery Delta": delta, "Lease Status": "RELEASED",
            "Last Heartbeat": timestamp, "Released At": timestamp, "Outcome": args.outcome,
            "Evidence index": f"{evidence_relative}/EVIDENCE_INDEX.md",
            "Worker report": f"{evidence_relative}/WORKER_REPORT.md",
            "Review report": f"{evidence_relative}/{manifest['review']['bundle_path']}" if manifest.get("review") else "NONE",
            "Evidence Bundle": evidence_relative, "Ending HEAD": str(manifest["verified_snapshot"]["head"]),
            "Verified Snapshot SHA256": str(manifest["verified_snapshot"]["snapshot_sha256"]),
            "Lease release": "RELEASED", "Completed At": timestamp,
        }
        for name, value in task_updates.items(): task = set_field_if_present(task, name, str(value))
        previous_counter = integer_field(state, "Consecutive Non-Shipping Tasks")
        threshold = max_non_shipping_tasks(read(ai / "PROJECT.md"))
        counter = 0 if delta in SHIPPING_DELTAS else previous_counter + 1
        breaker = "ACTIVE" if counter >= threshold else "INACTIVE"
        state = set_field(state, "Updated", timestamp[:10]); state = set_field(state, "State Revision", str(state_revision))
        for name, value in {"Branch": current["branch"], "HEAD": current["head"], "Worktree": current["worktree"], "Active Task ID": "NONE", "Last Known Good Commit": current["head"]}.items():
            state = set_field_in_section(state, "Continuity Fingerprint", name, str(value))
        for name, value in {"Last completed Task ID": task_id, "Last Delivery Delta": delta, "Consecutive Non-Shipping Tasks": str(counter), "Shipping Circuit Breaker": breaker}.items():
            state = set_field_in_section(state, "Delivery Pulse", name, str(value))
        if delta in SHIPPING_DELTAS:
            state = set_field_in_section(state, "Current Product Position", "Last demonstrated behavior/capability", args.outcome)
            state = set_field_in_section(state, "Current Product Position", "Demo evidence", f"{evidence_relative}/EVIDENCE_INDEX.md")
            state = set_field_in_section(state, "Delivery Pulse", "Time since last runnable demo", "0")
        for name, value in {"Status": "IDLE", "Task ID": "NONE", "Writer session": "NONE", "What is changing": "NOTHING", "Current checkpoint": "COMPLETED"}.items():
            state = set_field_in_section(state, "Active Work", name, value)
        span = find_section_span(state, "Completed and Verified")
        existing = state[span[0]:span[1]].strip() if span else ""
        entries = [] if existing in {"", "- NONE"} else [line for line in existing.splitlines() if line.strip()]
        entries.append(f"- {task_id}/r{revision:03d}: {args.outcome} | evidence: `{evidence_relative}`")
        state = set_section(state, "Completed and Verified", "\n".join(entries[-5:]))
        state = set_section(state, "Verification State", f"- {task_id}/r{revision:03d} accepted.\n- Snapshot: `{manifest['verified_snapshot']['snapshot_sha256']}`.\n- Evidence: `{evidence_relative}`.")
        state = set_field_in_section(state, "Cost Efficiency State", "Actual cost signal", "SKIPPED_BY_OPERATOR" if args.skip_ledger else f"ledger:{task_id}:{revision}")
        state = set_field_in_section(state, "Cost Efficiency State", "Marginal value status", "ACCEPTED")
        state = set_section(state, "Next Exact Action", "1. Select the next smallest milestone-linked outcome.\n2. Run `python scripts/ai_os.py report` periodically to tune gates from actual data.")
        write(task_path, task); write(state_path, state); refresh(root)
        if not args.skip_ledger:
            append_row(root, {
                "date": timestamp[:10], "task_id": task_id, "task_revision": revision,
                "success_criterion": field(task, "Success Criterion"), "delivery_delta": delta,
                "risk_tier": risk, "model": field(task, "Model Claimed", "UNSPECIFIED"),
                "profile": field(task, "Execution Profile"), "accepted": "yes",
                "started_at": field(task, "Started At"),
                "first_runnable_at": "" if field(task, "First Runnable At") in {"", "NONE", "UNSET"} else field(task, "First Runnable At"),
                "completed_at": timestamp, "cycle_minutes": args.cycle_minutes,
                "first_pass_accepted": args.first_pass_accepted,
                "coordination_input_tokens": args.coordination_input_tokens,
                "implementation_input_tokens": args.implementation_input_tokens,
                "cached_input_tokens": args.cached_input_tokens, "output_tokens": args.output_tokens,
                "estimated_ai_cost": args.estimated_ai_cost, "currency": args.currency,
                "provider_cost": args.provider_cost, "worker_turns": args.worker_turns,
                "retries": args.retries, "focused_test_runs": args.focused_test_runs,
                "integration_test_runs": args.integration_test_runs, "full_suite_runs": args.full_suite_runs,
                "provider_runs": args.provider_runs, "human_review_minutes": args.human_review_minutes,
                "human_wait_minutes": args.human_wait_minutes, "outcome": args.outcome,
                "later_rework": "unknown", "escaped_defect": "unknown",
                "rollback_required": args.rollback_required, "notes": args.notes,
            })
        history_record = {
            "schema_version": 1, "task_id": task_id, "task_revision": revision,
            "goal_id": linked_goal_id, "goal_node": linked_goal_node,
            "risk_tier": risk, "delivery_delta": delta, "outcome": args.outcome,
            "completed_at": timestamp, "evidence_bundle": evidence_relative,
            "evidence_manifest_sha256": sha256_file(final_bundle / "manifest.json"),
            "verified_snapshot_sha256": manifest["verified_snapshot"]["snapshot_sha256"],
            "actual_risk_floor": actual_floor, "actual_risk_reasons": actual_risk_reasons,
            "first_pass_accepted": args.first_pass_accepted,
            "breaker_override": breaker_override_info(task)[0],
            "breaker_override_reason": breaker_override_info(task)[1],
            "review_required": bool(risk == "R3" or getattr(args, "r2_review_reasons", [])),
            "review_reasons": list(getattr(args, "r2_review_reasons", [])),
            "acceptance_contract_sha256": field(task, "Acceptance Contract SHA256", "NONE"),
            "state_hazard_level": field(task, "State Hazard Level", "S0"),
            "state_contract_sha256": field(task, "State Contract SHA256", "NONE"),
            "quality_reconciliation": {"later_rework": "unknown", "escaped_defect": "unknown", "rollback_required": args.rollback_required},
        }
        history_path = archive_history(root, history_record)
        sync_runtime(root)
        errors, warnings = validate(root, strict=False)
        for warning in warnings: print("WARNING", warning)
        if errors: raise SystemExit("; ".join(errors))
        sync_task_completion(
            root, task_id, revision, args.outcome, delta, evidence_relative, risk, args.first_pass_accepted,
            goal_id=linked_goal_id, goal_node=linked_goal_node,
        )
        sync_runtime(root)
        # Promote only successfully published state proofs. Reused proofs remain linked to their original immutable source.
        try:
            state_contract = parse_state_contract(field(task, "State Contract JSON", "{}"))
            source_manifest = final_bundle / "manifest.json"
            for check_item in manifest.get("checks", []) or []:
                if check_item.get("kind") in {"state_transition", "state_temporal"} and check_item.get("result") == "PASS" and not check_item.get("reused"):
                    cache_state_proof(root, contract=state_contract, kind=str(check_item.get("kind")), command=str(check_item.get("command") or ""), source_manifest=source_manifest, source_check_id=str(check_item.get("id") or ""))
        except Exception as exc:
            print(f"WARNING state proof cache not updated: {exc}")
    except BaseException:
        restore(snapshot_files)
        if published and final_bundle.exists(): shutil.rmtree(final_bundle)
        if history_path and history_path.exists(): history_path.unlink()
        sync_runtime(root)
        raise
    print(f"Closed {task_id}/r{revision:03d} delta={delta} circuit_breaker={breaker}")


def finish_task(root: Path, args: argparse.Namespace) -> None:
    task_body = read(root / ".ai" / "ACTIVE_TASK.md")
    if field(task_body, "Task Status") != "ACTIVE" or field(task_body, "Lease Status") != "CLAIMED" or field(task_body, "Identity Verification") != "VERIFIED":
        raise SystemExit("done requires ACTIVE task with CLAIMED lease and VERIFIED identity")
    enforce_risk_floor(task_body)
    actual_floor, actual_risk_reasons = reconcile_actual_risk(root, task_body)
    risk = field(task_body, "Risk Tier")
    task_id_for_health = field(task_body, "Task ID")
    revision_for_health = integer_field(task_body, "Task Revision", 1)
    health_delta = task_delta_files(root, task_id=task_id_for_health, task_revision=revision_for_health)
    task_health_baseline = (load_task_baseline(root) or {}).get('codebase_health') or health_snapshot(root, file_loc_patterns=health_delta)
    health_result = health_check_delta(root, health_delta, baseline=task_health_baseline, hard_fail=True)
    for warning in health_result.get("warnings", []): print("HEALTH WARNING", warning)
    replacement_span = find_section_span(task_body, "Replacement Contract")
    if replacement_span is not None:
        chunk = task_body[replacement_span[0]:replacement_span[1]]
        obsolete = [line[2:].strip() for line in chunk.splitlines() if line.strip().startswith("- ")]
        remaining = [rel for rel in obsolete if (root / rel).exists()]
        if remaining:
            raise SystemExit("Replacement contract not satisfied; obsolete path(s) still exist: " + ", ".join(remaining))
    health_ratchet = health_compare(root)
    task_baseline_health = task_health_baseline
    base_deps = int(task_baseline_health.get('runtime_dependencies') or 0)
    current_deps = int((health_ratchet.get('current') or {}).get('runtime_dependencies') or 0)
    if current_deps > base_deps:
        structured = {
            "--dependency-capability": args.dependency_capability.strip(),
            "--dependency-alternatives-considered": args.dependency_alternatives_considered.strip(),
            "--dependency-removal-cost": args.dependency_removal_cost.strip(),
        }
        missing=[name for name,value in structured.items() if len(value)<8]
        if missing:
            raise SystemExit(f"Runtime dependencies increased during this task {base_deps}→{current_deps}; structured dependency decision incomplete: missing/too-short {', '.join(missing)}. New production dependencies must buy concrete capability, consider existing alternatives, and state exit/removal cost.")
    for warning in health_ratchet.get('warnings', []): print('HEALTH RATCHET', warning)
    contract_commands, contract_expected = verify_acceptance_contract(root, task_body, risk)
    r2_review = r2_review_reasons(root, task_body, args.first_pass_accepted, actual_risk_reasons)
    state_level = field(task_body, "State Hazard Level", "S0").upper()
    state_contract = parse_state_contract(field(task_body, "State Contract JSON", "{}"))
    state_contract_hash = field(task_body, "State Contract SHA256", "NONE")
    baseline_state_hash = str((load_task_baseline(root) or {}).get("state_contract_sha256") or "NONE")
    if state_contract_hash != str(state_contract.get("contract_sha256") or "NONE") or state_contract_hash != baseline_state_hash:
        raise SystemExit("State contract changed after task start; abort/restart or amend implementation scope without rewriting the pre-code state authority/invariant")
    if state_level != str(state_contract.get("level") or "S0").upper():
        raise SystemExit("State Hazard Level does not match frozen State Contract")
    if state_level not in STATE_LEVELS:
        raise SystemExit(f"Invalid State Hazard Level in task: {state_level}")
    if STATE_LEVELS[state_level] >= 2 and not args.state_transition_command:
        raise SystemExit(f"{state_level} state hazard requires --state-transition-command; exact proof will be reused automatically while its contract/dependencies are unchanged")
    if STATE_LEVELS[state_level] >= 3 and not args.state_temporal_command:
        raise SystemExit(f"{state_level} state hazard requires --state-temporal-command for background/competing-writer behavior; reusable proof avoids repeated soak/browser cost")
    if policy_gate(root, risk, "focused") == "required" and not args.focused_command:
        raise SystemExit("done requires at least one --focused-command")
    negative_required = field(task_body, "Negative path required", "no").casefold() in {"yes", "true", "required"}
    negative_policy = policy_gate(root, risk, "negative")
    if (negative_policy == "required" or (negative_policy == "when_failure_behavior" and negative_required)) and not args.negative_command:
        raise SystemExit(f"{risk} done requires --negative-command for this task")
    if policy_gate(root, risk, "integration") == "required" and not args.integration_command:
        raise SystemExit(f"{risk} done requires --integration-command")
    recovery_relevant = field(task_body, "Data operation", "READ_ONLY") in {"MUTATE_IN_PLACE", "DELETE"} or field(task_body, "Artifact operation", "READ_ONLY") in {"MUTATE_IN_PLACE", "OVERWRITE", "DELETE"}
    rollback_policy = policy_gate(root, risk, "rollback")
    if (rollback_policy == "required" or (rollback_policy == "when_recovery_relevant" and recovery_relevant)) and not args.rollback_command:
        raise SystemExit(f"{risk} done requires --rollback-command for this recovery-relevant task")
    if policy_gate(root, risk, "full_suite") == "required" and not args.full_suite_command:
        raise SystemExit(f"{risk} done requires --full-suite-command")
    if risk == "R3" and not args.review_report:
        set_delegation_request(root, action='SPAWN_REVIEWER', task_body=task_body, reasons=['R3 independent review is mandatory'], model_class='FRESH_REVIEW_CONTEXT', summary_token_budget=500)
        raise SystemExit("R3 done requires --review-report; DELEGATION_REQUEST=SPAWN_REVIEWER written to .ai/runtime/delegation_request.json")
    review_required = risk == "R3" or (risk == "R2" and bool(r2_review))
    required_review_trust = review_requirement(root, risk, r2_review_triggered=bool(r2_review))
    if review_required and not args.review_attestation:
        set_delegation_request(root, action='SPAWN_REVIEWER', task_body=task_body, reasons=[f'{risk} review requires external Guardian-attested reviewer session'], model_class='FRESH_REVIEW_CONTEXT', summary_token_budget=500)
        raise SystemExit(f"{risk} review requires --review-attestation from a separate external reviewer session; configured trust={required_review_trust}")
    if risk == "R2" and r2_review and not args.review_report:
        set_delegation_request(root, action='SPAWN_REVIEWER', task_body=task_body, reasons=r2_review, model_class='FRESH_REVIEW_CONTEXT', summary_token_budget=500)
        raise SystemExit("R2 elevated review required: " + "; ".join(r2_review) + ". DELEGATION_REQUEST=SPAWN_REVIEWER written to .ai/runtime/delegation_request.json. Supply --review-report from a fresh reviewer or explicitly plan --review-policy none before Worker start for a justified low-coupling R2 task.")
    if args.review_report:
        clear_delegation_request(root)
    if risk in {"R1", "R2", "R3"} and not args.output_inspected_by:
        raise SystemExit(f"{risk} done requires --output-inspected-by agent:<id> or human:<id>")
    commands: list[tuple[str, str]] = []
    for kind, values in [("focused", args.focused_command), ("negative", args.negative_command), ("integration", args.integration_command), ("state_transition", args.state_transition_command), ("state_temporal", args.state_temporal_command), ("acceptance_contract", contract_commands), ("rollback", args.rollback_command), ("full_suite", args.full_suite_command)]:
        commands.extend((kind, value) for value in values or [])
    revision = int(field(task_body, "Task Revision", "1")); task_id = field(task_body, "Task ID")
    review_attestation_payload = None
    if args.review_attestation:
        current_snapshot = application_snapshot(root)["snapshot_sha256"]
        expected_attestation = {"task_id": task_id, "task_revision": revision, "snapshot_sha256": current_snapshot, "writer_identity": field(task_body, "Session Label")}
        att_errors, review_attestation_payload = validate_review_attestation(root, args.review_attestation, {k: str(v) for k, v in expected_attestation.items()}, require_signed=(required_review_trust=='SIGNED_GUARDIAN'))
        if att_errors: raise SystemExit("; ".join(att_errors))
    with transaction_journal(root, "done", {"task_id": task_id, "task_revision": revision}) as tx_dir:
        stage, manifest = create_staged_bundle(
            root, tx_dir, task=task_map(task_body), revision=revision, outcome=args.outcome,
            commands=commands, artifacts=args.artifact or [], timeout=args.command_timeout,
            allow_shell=args.allow_shell_command, inspected_by=args.output_inspected_by,
            cleanup_note=args.cleanup_note, known_limits=args.known_limits, review_report=args.review_report, review_attestation=review_attestation_payload,
            compact=risk in {"R0", "R1"}, expected_outputs=list(dict.fromkeys(contract_expected + (args.expected_output or []))),
        )
        enforce_scope(root, task_body)
        if risk == "R3" or (risk == "R2" and r2_review):
            expected = {"task_id": task_id, "task_revision": revision, "snapshot_sha256": manifest["verified_snapshot"]["snapshot_sha256"], "writer_identity": field(task_body, "Session Label")}
            report_path=(root/args.review_report).resolve()
            if report_path.is_file(): expected["review_report_sha256"] = sha256_file(report_path)
            errors = validate_review_report(root, args.review_report, {k: str(v) for k, v in expected.items()})
            if errors: raise SystemExit("; ".join(errors))
            if args.review_attestation:
                att_errors, _ = validate_review_attestation(root, args.review_attestation, {k: str(v) for k, v in expected.items()}, require_signed=(required_review_trust=='SIGNED_GUARDIAN'))
                if att_errors: raise SystemExit("; ".join(att_errors))
        close_args = argparse.Namespace(
            outcome=args.outcome, evidence_bundle="", delivery_delta=None, completed_at=None,
            skip_ledger=args.skip_ledger, cycle_minutes=None, first_pass_accepted=args.first_pass_accepted,
            coordination_input_tokens=args.coordination_input_tokens, implementation_input_tokens=args.implementation_input_tokens,
            cached_input_tokens=args.cached_input_tokens, output_tokens=args.output_tokens,
            estimated_ai_cost=args.estimated_ai_cost, currency=args.currency, provider_cost=args.provider_cost,
            worker_turns=args.worker_turns, retries=args.retries,
            focused_test_runs=len(args.focused_command or []), integration_test_runs=len(args.integration_command or []),
            full_suite_runs=len(args.full_suite_command or []), provider_runs=args.provider_runs,
            human_review_minutes=args.human_review_minutes, human_wait_minutes=args.human_wait_minutes,
            rollback_required=args.rollback_required, notes=(args.notes + ((" | dependency: capability=" + args.dependency_capability + "; alternatives=" + args.dependency_alternatives_considered + "; removal_cost=" + args.dependency_removal_cost + ("; note=" + args.dependency_justification if args.dependency_justification else "")) if current_deps > base_deps else "")), r2_review_reasons=r2_review,
        )
        close_with_bundle(root, close_args, stage=stage, staged_manifest=manifest)
        if args.first_pass_accepted == 'no':
            try: field_record(root,'FIRST_PASS_FAILURE',phase='TASK_ACCEPTANCE',severity='MEDIUM',trigger=f'{task_id}/r{revision}',automatic=True,evidence_code='TASK_FIRST_PASS_NO')
            except Exception: pass
        if field(task_body, "Goal ID", "NONE") in {"", "NONE", "UNSET"}:
            health_save_snapshot(root)


def doctor(root: Path) -> None:
    findings: list[tuple[str, str]] = []
    findings.append(("PASS" if sys.version_info >= (3, 10) else "FAIL", f"Python {sys.version.split()[0]}"))
    snap = application_snapshot(root)
    findings.append(("PASS" if snap["git"] else "WARN", f"Git repository: {snap['git']}"))
    core = [".ai/PROJECT.md", ".ai/STATE.md", ".ai/ACTIVE_TASK.md", ".ai/COST_LEDGER.csv"]
    for relative in core: findings.append(("PASS" if (root / relative).is_file() else "FAIL", relative))
    incomplete = []
    for tx in (root / ".ai" / "transactions").glob("TX-*"):
        if not (tx / "commit.json").exists() and not (tx / "abort.json").exists(): incomplete.append(tx.name)
    findings.append(("PASS" if not incomplete else "FAIL", f"Incomplete transactions: {', '.join(incomplete) if incomplete else 'none'}"))
    pycache = list(root.rglob("__pycache__"))
    findings.append(("WARN" if pycache else "PASS", f"Package __pycache__: {len(pycache)}"))
    errors, warnings = validate(root, strict=False)
    findings.append(("PASS" if not errors else "FAIL", f"Validator errors={len(errors)} warnings={len(warnings)}"))
    health = health_check_repository(root, hard_fail=False)
    findings.append(("PASS" if not health.get("errors") else "FAIL", f"Codebase health hard errors={len(health.get('errors', []))} warnings={len(health.get('warnings', []))}"))
    assurance=achieved_assurance(root)
    findings.append(("PASS" if assurance.get('level') in {'A3','A4'} else "WARN", f"Assurance {assurance.get('level')}: {'; '.join(assurance.get('reasons') or [])}"))
    for status, message in findings: print(f"{status:4} {message}")
    if any(status == "FAIL" for status, _ in findings): raise SystemExit(1)


def show_status(root: Path, as_json: bool) -> None:
    task = read(root / ".ai" / "ACTIVE_TASK.md"); state = read(root / ".ai" / "STATE.md"); snap = application_snapshot(root)
    delta = task_delta_files(root, task_id=field(task, "Task ID"), task_revision=integer_field(task, "Task Revision", 1)) if field(task, "Task Status") in LIVE_STATUSES else []
    baseline = load_task_baseline(root) or {}
    project = read(root / ".ai" / "PROJECT.md")
    derived_breaker, non_shipping_count, non_shipping_threshold = shipping_breaker_state(project, state)
    value = {
        "project_id": field(state, "Project ID"), "task_id": field(task, "Task ID"),
        "task_revision": field(task, "Task Revision"), "task_status": field(task, "Task Status"),
        "risk": field(task, "Risk Tier"), "risk_floor": field(task, "Risk Floor", field(task, "Risk Tier")),
        "profile": field(task, "Execution Profile"), "state_hazard": field(task, "State Hazard Level", "S0"), "lease": field(task, "Lease Status"), "writer": field(task, "Session Label"),
        "branch": snap["branch"], "head": snap["head"], "worktree": snap["worktree"],
        "snapshot_sha256": snap["snapshot_sha256"], "changed_files": snap["changed_files"],
        "task_delta_files": delta, "preexisting_dirty_files": sorted((baseline.get("preexisting_changed_files") or {}).keys()),
        "first_runnable_at": field(task, "First Runnable At"),
        "shipping_circuit_breaker": derived_breaker,
        "consecutive_non_shipping_tasks": non_shipping_count,
        "max_consecutive_non_shipping_tasks": non_shipping_threshold,
        "next_action": next_action(root, emit=False),
        "assurance": achieved_assurance(root),
    }
    if as_json: print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(f"Project: {value['project_id']}")
        print(f"Task: {value['task_id']}/r{str(value['task_revision']).zfill(3)} {value['task_status']} | {value['risk']}/{value['profile']} state={value['state_hazard']}")
        print(f"Lease: {value['lease']} by {value['writer']}")
        print(f"Git: {value['branch']} {str(value['head'])[:12]} {value['worktree']}")
        print(f"Task delta: {', '.join(value['task_delta_files']) if value['task_delta_files'] else 'NONE'}")
        print(f"Pre-existing dirty: {len(value['preexisting_dirty_files'])}")
        print(f"Shipping breaker: {value['shipping_circuit_breaker']} ({value['consecutive_non_shipping_tasks']}/{value['max_consecutive_non_shipping_tasks']} non-shipping)")
        print(f"Assurance: {value['assurance'].get('level')} — {'; '.join(value['assurance'].get('reasons') or [])}")
        print(f"Next: {value['next_action']}")


def evidence_infra_stop_loss(root: Path, task_id: str) -> tuple[bool, str, int]:
    rows = [r for r in field_load(root, 100) if r.get("event_type") == "EVIDENCE_INFRA_FAILURE" and str((r.get("metadata") or {}).get("task_id") or "") == task_id]
    if not rows:
        return False, "", 0
    method = str((rows[-1].get("metadata") or {}).get("method") or "UNKNOWN")
    count = 0
    for row in reversed(rows):
        if str((row.get("metadata") or {}).get("method") or "UNKNOWN") != method:
            break
        count += 1
    return count >= 2, method, count


def next_action(root: Path, emit: bool = True) -> str:
    task = read(root / ".ai" / "ACTIVE_TASK.md"); state = read(root / ".ai" / "STATE.md")
    status = field(task, "Task Status"); lease = field(task, "Lease Status"); risk = field(task, "Risk Tier")
    project = read(root / ".ai" / "PROJECT.md")
    breaker, _, _ = shipping_breaker_state(project, state)
    infra_stop, infra_method, infra_count = evidence_infra_stop_loss(root, field(task, "Task ID"))
    if infra_stop and status in {"READY", "ACTIVE", "BLOCKED", "PAUSED"}:
        action = f"Evidence stop-loss: {infra_method} infrastructure failed {infra_count} consecutive times. Change acceptance method; do not spend another Worker cycle repairing the same verifier unless product-failure evidence appears."
    elif status in {"NOT_CREATED", "COMPLETED", "ABANDONED", "ABORTED"}:
        action = (
            "Shipping Circuit Breaker is ACTIVE: create the next smallest USER_VISIBLE_BEHAVIOR or EXECUTABLE_CAPABILITY task."
            if breaker == "ACTIVE" else "Create the next smallest milestone-linked task with `ai_os.py begin`."
        )
    elif status == "READY":
        action = "Claim with `ai_os.py claim` before modifying application code."
    elif status == "PAUSED":
        action = "Resume with `ai_os.py resume`; the original task baseline remains authoritative."
    elif lease != "CLAIMED":
        action = "Restore a claimed writer lease before modifying application code."
    elif STATE_LEVELS.get(field(task, "State Hazard Level", "S0").upper(), 0) >= 3:
        action = "Implement against the frozen state authority/invariant; verify transition + competing-writer temporal behavior. Reuse proof automatically when dependencies are unchanged."
    elif STATE_LEVELS.get(field(task, "State Hazard Level", "S0").upper(), 0) >= 2:
        action = "Implement against the frozen state authority/invariant; run one representative transition proof, then finish. Exact proof is reusable until affected source changes."
    elif risk in {"R0", "R1"}:
        action = "Make the smallest patch, run focused verification, inspect output + task delta, then `ai_os.py done`."
    elif risk == "R2":
        action = "Run focused + negative + affected integration checks, inspect output + task delta, then `ai_os.py done`."
    else:
        action = "Run critical R3 gates (focused, negative, integration, full suite, rollback/review) then `ai_os.py done`."
    if emit: print(action)
    return action


def show_history(root: Path, limit: int) -> None:
    records = sorted(load_history(root), key=lambda r: r.get("completed_at", ""), reverse=True)[:limit]
    if not records: print("No accepted task history."); return
    for item in records:
        print(f"{item['completed_at']} {item['task_id']}/r{int(item['task_revision']):03d} {item['risk_tier']} {item['delivery_delta']} — {item['outcome']}")



def report(root: Path, last: int) -> None:
    return _report_impl(root, last)

def reconcile(root: Path, args: argparse.Namespace) -> None:
    updates = {k: v for k, v in {
        "later_rework": args.later_rework, "escaped_defect": args.escaped_defect,
        "rollback_required": args.rollback_required, "notes": args.notes,
        "human_review_minutes": str(args.human_review_minutes) if args.human_review_minutes is not None else None,
        "human_wait_minutes": str(args.human_wait_minutes) if args.human_wait_minutes is not None else None,
    }.items() if v is not None}
    safe_task_id = validate_identifier(args.task_id, "task ID")
    reconcile_row(root, safe_task_id, args.task_revision, updates)
    history = confined_child(root, Path(".ai/history"), safe_task_id, "task ID") / f"r{args.task_revision:03d}.json"
    if history.is_file():
        record = json.loads(history.read_text(encoding="utf-8")); record.setdefault("quality_reconciliation", {}).update(updates); atomic_write_json(history, record)


def check(root: Path, strict: bool) -> None:
    sync_runtime(root)
    errors, warnings = validate(root, strict=strict)
    for warning in warnings: print("WARNING", warning)
    for error in errors: print("ERROR", error)
    print(f"RESULT: {'FAIL' if errors else 'PASS'} errors={len(errors)} warnings={len(warnings)}")
    if errors: raise SystemExit(1)



def main() -> None:
    args = build_parser().parse_args(); root = args.root.resolve()
    if args.command == "check": check(root, args.strict); return
    if args.command == "doctor": doctor(root); return
    if args.command == "status": show_status(root, args.json); return
    if args.command == "next": next_action(root); return
    if args.command == "history": show_history(root, args.limit); return
    if args.command == "report": report(root, args.last); return
    if args.command == "route":
        value = recommend_lane(outcome=args.outcome, acceptance=args.accept, modify=args.modify, risk=args.risk, dependency_count=args.dependency_count, acceptance_surfaces=args.acceptance_surfaces, parallel_opportunity=args.parallel_opportunity)
        print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else f"ROUTE={value['lane']} orchestration={value['orchestration']} reason={'; '.join(value['reasons'])}")
        return
    if args.command == "assurance":
        print(json.dumps(achieved_assurance(root), ensure_ascii=False, indent=2))
        return
    if args.command == "field":
        if args.field_command == "report":
            print(json.dumps(field_report(root,args.last,global_scope=args.global_scope),ensure_ascii=False,indent=2))
        else:
            item=field_record(root,args.event_type,phase=args.phase,severity=args.severity,trigger=args.trigger,automatic=False,extra_wall_seconds=args.extra_wall_seconds,extra_input_tokens=args.extra_input_tokens,extra_output_tokens=args.extra_output_tokens,extra_provider_cost=args.extra_provider_cost,evidence_code=args.evidence_code)
            print(json.dumps(item,ensure_ascii=False,indent=2))
        return
    if args.command == "health":
        if args.health_command == "baseline":
            print(json.dumps(health_save_snapshot(root), ensure_ascii=False, indent=2))
        elif args.health_command == "check":
            result = health_check_repository(root, hard_fail=args.ci)
            for w in result.get('warnings', []): print('HEALTH WARNING', w)
            for e in result.get('errors', []): print('HEALTH ERROR', e)
            if not result.get('errors'): print('CODEBASE_HEALTH: PASS')
        elif args.health_command == "architecture-decision":
            set_architecture_waiver(root,args.no_boundaries_reason); print('ARCHITECTURE_DECISION: recorded explicit no-boundaries reason')
        else:
            print(json.dumps(health_report(root), ensure_ascii=False, indent=2))
        return
    if args.command == "telemetry":
        if args.telemetry_command == "ingest":
            path=Path(args.file); text=path.read_text(encoding='utf-8'); records=[]
            try:
                obj=json.loads(text); records=obj if isinstance(obj,list) else [obj]
            except json.JSONDecodeError:
                records=[json.loads(line) for line in text.splitlines() if line.strip()]
            print(f"TELEMETRY ingested={telemetry_ingest(root, records)}")
        else:
            print(json.dumps(telemetry_summarize(root), ensure_ascii=False, indent=2))
        return
    if args.command == "goal" and args.goal_command in {"status", "next"}:
        ensure_goal_files(root)
        if args.goal_command == "status":
            goal = load_goal(root)
            if args.json: print(json.dumps(goal, ensure_ascii=False, indent=2))
            else: print((root / ".ai" / "GOAL.md").read_text(encoding="utf-8"))
        else:
            wave = goal_next_wave(root)
            if args.json: print(json.dumps(wave, ensure_ascii=False, indent=2))
            else:
                print(f"Goal {wave.get('goal_id')} status={wave.get('status')}")
                for node in wave.get('ready', []):
                    print(f"READY {node['node_id']} agent={node['agent_role']} delta={node['delivery_delta']} depends={','.join(node.get('depends_on',[])) or '-'} outcome={node['outcome']}")
                delegation = wave.get('delegation') or {}
                for item in delegation.get('recommendations', []):
                    if item.get('action') in {'SPAWN_SCOUT','SPAWN_REVIEWER','SCOUT_FIRST','SCOUT_OPTIONAL'}:
                        print(f"DELEGATE {item.get('node_id')} action={item.get('action')} model={item.get('model_class')} hard={'yes' if item.get('hard') else 'no'} budget={item.get('summary_token_budget') or '-'}t reason={'; '.join(item.get('reasons') or [])}")
                for group in delegation.get('parallel_groups', []):
                    print(f"PARALLEL candidates={','.join(group.get('nodes') or [])} isolated_worktrees=required reason={group.get('reason')}")
                opportunity = delegation.get('parallel_opportunity') or {}
                if opportunity:
                    print(f"PARALLEL-OPPORTUNITY candidates={','.join(opportunity.get('nodes') or [])} action={opportunity.get('action')} note={opportunity.get('cost_note')}")
                held = delegation.get('held_sequential') or []
                if held:
                    print(f"SEQUENTIAL held={','.join(held)} reason=write-scope overlap/uncertainty or parallel budget")
                if wave.get('parallel_writers_require_isolated_worktrees'):
                    print("PARALLEL: use isolated Git worktrees; a single worktree remains single-writer")
        return
    with lifecycle_lock(root):
        if args.command == "goal":
            ensure_goal_files(root)
            if args.goal_command == "decision":
                item=decision_record(root,args.type,args.text,confidence=args.confidence,reversibility=args.reversibility,owner_impact=args.owner_impact,reason=args.reason)
                print(json.dumps(item,ensure_ascii=False,indent=2))
            elif args.goal_command == "digest":
                print(json.dumps(decision_digest(root,args.goal_id),ensure_ascii=False,indent=2))
            elif args.goal_command == "begin":
                value = begin_goal(root, goal_id=args.goal_id, outcome=args.goal_outcome, acceptance=args.accept, non_goals=args.non_goal, goal_type=args.goal_type, risk_ceiling=args.risk_ceiling, max_tasks=args.max_tasks, max_parallel=args.max_parallel, max_non_shipping=args.max_non_shipping, max_revisions=args.max_revisions, scope_growth_limit=args.scope_growth_limit, max_auto_scouts=args.max_auto_scouts, scout_summary_token_budget=args.scout_summary_token_budget, scout_input_token_budget=args.scout_input_token_budget, scout_wall_minutes_budget=args.scout_wall_minutes_budget, scout_provider_cost_budget=args.scout_provider_cost_budget)
                print(f"Goal {value['goal_id']} ACTIVE risk_ceiling={value['risk_ceiling']}")
                for warning in value.get('acceptance_quality_warnings', []):
                    print('WARNING', warning)
            elif args.goal_command == "add-task":
                node = goal_add_task(root, node_id=args.node, outcome=args.outcome, depends_on=args.depends_on, agent_role=args.agent_role, risk=args.risk, delivery_delta=args.delivery_delta, modify=args.modify, create=args.create, replaces=args.replaces, success_criterion=args.success_criterion, acceptance=args.accept, negative_required=args.negative_required, data_operation=args.data_operation, artifact_operation=args.artifact_operation, acceptance_commands=args.acceptance_command, expected_outputs=args.expected_output, probe_files=args.probe_file, review_policy=args.review_policy, delegation_policy=args.delegation_policy, state_hazard=args.state_hazard, state_signals=args.state_signal, state_authority=args.state_authority, state_transitions=args.state_transition, state_invariants=args.state_invariant, state_dependencies=args.state_dependency)
                print(f"Planned {node['node_id']} task={node['task_id']} agent={node['agent_role']}")
                delegation = node.get('delegation') or {}
                if delegation.get('auto_scout_node'):
                    print(f"DELEGATION auto-scout={delegation['auto_scout_node']} model=CHEAP_FAST_READ_ONLY summary_budget={delegation.get('summary_token_budget',350)}t")
                elif delegation.get('action') == 'SCOUT_OPTIONAL':
                    print('DELEGATION optional Scout: ' + '; '.join(delegation.get('reasons') or []))
            elif args.goal_command == "bind-acceptance":
                mapping = bind_goal_acceptance(root, criterion_index=args.criterion, command=args.judge_command, expected_output=args.expected_output, probe_file=args.probe_file, inspection_requirement=args.inspection_requirement)
                print(f"Goal acceptance criterion {mapping['criterion_index']} bound evaluator={mapping['evaluator_type']}")
            elif args.goal_command == "start":
                goal = load_goal(root); node = (goal.get("tasks") or {}).get(args.node)
                if not node: raise SystemExit(f"Unknown Goal node: {args.node}")
                ready_map = {item['node_id']: item for item in goal_next_wave(root).get('ready', [])}
                if args.node not in ready_map: raise SystemExit(f"Goal node {args.node} is not READY; satisfy dependencies or resolve blockers first")
                if node.get("agent_role") in {"SCOUT", "REVIEWER"}: raise SystemExit("Read-only SCOUT/REVIEWER nodes complete with `goal node-done`, not task start")
                selected = ready_map[args.node]
                node = maybe_apply_scout_scope(root, args.node)
                goal = load_goal(root)
                planned_risk, _, _ = effective_risk(
                    node.get('risk', 'auto'), data_operation=node.get('data_operation', 'READ_ONLY'),
                    artifact_operation=node.get('artifact_operation', 'CREATE_NEW_VERSION'), files_overwritten='NONE',
                    data_mutated='NONE', external_calls='NONE', provider_calls='NONE',
                    modify=node.get('modify', 'NONE'), create=node.get('create', 'NONE'),
                )
                ceiling = str(goal.get('risk_ceiling', 'R2'))
                if RISK_ORDER[planned_risk] > RISK_ORDER[ceiling]:
                    raise SystemExit(f"Effective task risk {planned_risk} exceeds Goal {goal['goal_id']} risk ceiling {ceiling}; owner decision/escalation required")
                # Freeze the Goal-level judge only after authority/risk checks pass, but before Worker materialization.
                freeze_goal_acceptance_contract(root)
                frozen_contract = freeze_acceptance_contract(root, args.node, planned_risk)
                auto_breaker_override = node.get('delivery_delta') not in SHIPPING_DELTAS and bool(selected.get('on_path_to_shipping'))
                ns = argparse.Namespace(
                    task_id=node['task_id'], outcome=node['outcome'], risk=node.get('risk','auto'), success_criterion=node.get('success_criterion','SC-001'),
                    accept=node.get('acceptance',[]), delivery_delta=node.get('delivery_delta','NO_DELTA'), milestone_id='M-001', project_id=None,
                    goal_id=goal['goal_id'], goal_node=args.node, demonstrable_result='runtime/output evidence listed in task evidence index', unlocks='next ready Goal node',
                    read_scope='task-relevant repository files', modify=node.get('modify','NONE'), create=node.get('create','NONE'), replaces=node.get('replaces',[]), commands='focused checks and task-authorized commands',
                    local_services='NONE', external_calls='NONE', data_operation=node.get('data_operation','READ_ONLY'), artifact_operation=node.get('artifact_operation','CREATE_NEW_VERSION'),
                    inputs='task-relevant source and fixtures', outputs='accepted outcome and evidence', files_created='NONE', files_overwritten='NONE', data_mutated='NONE', provider_calls='NONE',
                    expected_provider_cost=0.0, disk_requirement='MINIMAL', ram_requirement='MINIMAL', process_port='NONE', artifact_lineage='source inputs and evidence manifest',
                    rollback='revert task-scoped diff and remove new artifacts', expected_cost_range='small; investigate repeated attempts without evidence', primary_cost_drivers='implementation, verification and output inspection',
                    evidence_sequence='focused → affected regression/runtime → acceptance contract → diff review', escalation_conditions='risk exceeds profile or same approach fails twice', negative_required=bool(node.get('negative_required')),
                    acceptance_contract_sha256=frozen_contract.get('contract_sha256','NONE'), acceptance_contract_json=json.dumps(frozen_contract, ensure_ascii=False, separators=(',', ':')), review_policy=node.get('review_policy','auto'),
                    state_hazard=node.get('state_hazard','auto'), state_signal=node.get('state_signals',[]), state_authority=node.get('state_authority',''), state_transition=node.get('state_transitions',[]), state_invariant=node.get('state_invariants',[]), state_dependency=node.get('state_dependencies',[]),
                    owner_authorization=args.owner_authorization, authorization_reference=args.authorization_reference, breaker_override=auto_breaker_override, breaker_override_reason='Goal dependency path to accepted shipping node' if auto_breaker_override else '', stop_loss_ack=args.stop_loss_ack, claim=True, writer_role='WORKER', platform=args.platform, session_label=args.session_label, allow_no_git=args.allow_no_git,
                )
                start_task(root, ns)
            elif args.goal_command == "discover":
                record_discovery(root, args.summary, affected_files=args.affected_file, risk_signals=args.risk_signal); print("Goal discovery recorded")
            elif args.goal_command in {"scout-done", "node-done"}:
                mark_scout_done(root, args.node, args.summary, args.affected_file, args.risk_signal, input_tokens=args.input_tokens, output_tokens=args.output_tokens, provider_cost=args.provider_cost, wall_minutes=args.wall_minutes, invariants=args.invariant, entry_point=args.entry_point, confidence=args.confidence, recommended_scope=args.recommended_scope); print(f"Read-only node {args.node} DONE")
            elif args.goal_command == "defer": defer_node(root, args.node, args.reason); print(f"Goal node {args.node} DEFERRED")
            elif args.goal_command == "block":
                block_goal(root, args.reason, owner_decision=args.owner_decision)
                if args.owner_decision:
                    try: field_record(root,'OWNER_INTERRUPT',phase='GOAL',severity='MEDIUM',trigger=args.reason,automatic=True,evidence_code='GOAL_BLOCK_OWNER_DECISION')
                    except Exception: pass
                print("Goal BLOCKED")
            elif args.goal_command == "resume": resume_goal(root, args.decision); print("Goal ACTIVE")
            elif args.goal_command == "abort": abort_goal(root, args.reason); print("Goal ABORTED")
            elif args.goal_command == "done":
                goal = load_goal(root); contract = verify_goal_acceptance_contract(root)
                logs = confined_child(root, GOALS_DIR, str(goal.get('goal_id')), 'goal ID') / 'acceptance'; logs.mkdir(parents=True, exist_ok=True)
                records = []; passed = True; executed = {}; next_index = 1
                inspection_confirmed = set(args.inspection_confirm or [])
                for mapping in contract.get('mappings', []) or []:
                    idx = int(mapping.get('criterion_index', 0)); kind = mapping.get('evaluator_type')
                    if kind == 'inspection':
                        ok = idx in inspection_confirmed
                        records.append({'criterion_index': idx, 'criterion': mapping.get('criterion'), 'kind': 'declared_inspection', 'requirement': mapping.get('inspection_requirement'), 'result': 'PASS' if ok else 'FAIL', 'inspected_by': args.output_inspected_by})
                        print(f"goal_acceptance:A{idx} {'PASS' if ok else 'FAIL'} declared_inspection")
                        if not ok: passed = False; break
                        continue
                    command = str(mapping.get('command') or '').strip()
                    snapshot_key = application_snapshot(root)['snapshot_sha256']; key = (command, snapshot_key, bool(args.allow_shell_command))
                    if key in executed:
                        record = dict(executed[key]); record['reused_execution'] = True
                    else:
                        record = run_command(root, f'goal_A{idx}', command, args.command_timeout, logs, next_index, allow_shell=args.allow_shell_command, inspected_by=args.output_inspected_by)
                        next_index += 1; executed[key] = record
                    record = dict(record); record['criterion_index'] = idx; record['criterion'] = mapping.get('criterion')
                    marker = str(mapping.get('expected_output') or '')
                    if record.get('result') == 'PASS' and marker:
                        stdout = (logs / record['stdout']['path']).read_text(encoding='utf-8', errors='replace')
                        stderr = (logs / record['stderr']['path']).read_text(encoding='utf-8', errors='replace')
                        record['expected_output'] = marker; record['expected_output_matched'] = marker in (stdout + '\n' + stderr)
                        if not record['expected_output_matched']: record['result'] = 'FAIL'
                    records.append(record); print(f"goal_acceptance:A{idx} {record['result']} command={command}")
                    if record['result'] != 'PASS': passed = False; break
                attempt = record_acceptance_attempt(root, passed)
                if not passed: raise SystemExit(f'Goal acceptance attempt {attempt} failed; Goal remains ACTIVE')
                goal_health = health_check_repository(root, hard_fail=True)
                for warning in goal_health.get('warnings', []): print('HEALTH WARNING', warning)
                result = complete_goal(root, acceptance_records=records, inspected_by=args.output_inspected_by, known_limits=args.known_limits)
                digest = decision_digest(root, result['goal_id'])
                atomic_write_json(confined_child(root, GOALS_DIR, str(result['goal_id']), 'goal ID') / 'owner_digest.json', digest)
                if not result.get('goal_first_pass_accepted', True):
                    try: field_record(root,'FIRST_PASS_FAILURE',phase='GOAL_ACCEPTANCE',severity='MEDIUM',trigger=str(result['goal_id']),automatic=True,evidence_code='GOAL_ACCEPTANCE_RETRY')
                    except Exception: pass
                for warning in (result.get('codebase_health', {}) or {}).get('warnings', []): print('HEALTH RATCHET', warning)
                if digest.get('attention_required'): print(f"OWNER_DIGEST attention={len(digest['attention_required'])} path=.ai/goals/{result['goal_id']}/owner_digest.json")
                print(f"Goal {result['goal_id']} COMPLETED")
            sync_runtime(root)
            return
        if args.command == "debug":
            task_body = read(root / ".ai" / "ACTIVE_TASK.md")
            if args.debug_command == "state-failure":
                task_id = validate_identifier(field(task_body, "Task ID"), "task ID")
                revision = integer_field(task_body, "Task Revision", 1)
                path = record_failure_signature(root, task_id=task_id, revision=revision, state=args.state, event=args.event, expected=args.expected, observed=args.observed, hazard_class=args.hazard_class, suspects=args.suspect)
                print(f"State failure signature recorded: {path.relative_to(root)}")
                return
            if args.debug_command == "evidence-infra-failure":
                task_id = validate_identifier(field(task_body, "Task ID"), "task ID")
                revision = integer_field(task_body, "Task Revision", 1)
                field_record(root, 'EVIDENCE_INFRA_FAILURE', phase='TASK_ACCEPTANCE', severity='MEDIUM', trigger=f'{task_id}/r{revision}:{args.method}', automatic=False, evidence_code='EVIDENCE_METHOD_FAILURE', metadata={'task_id':task_id,'task_revision':revision,'method':args.method,'note':args.note[:240]})
                stop, method, count = evidence_infra_stop_loss(root, task_id)
                print(f"Evidence infrastructure failure recorded: method={method} consecutive={count}")
                if stop:
                    print("STOP_LOSS: change acceptance method before another expensive verification attempt")
                return
        if args.command == "init": initialize_project(root, args)
        elif args.command in {"start", "begin"}: start_task(root, args)
        elif args.command == "claim": claim_task(root, args)
        elif args.command == "pause": pause_task(root, args)
        elif args.command == "resume": resume_task(root, args)
        elif args.command == "abort": abort_task(root, args)
        elif args.command == "amend": amend_task(root, args)
        elif args.command == "runnable": mark_runnable(root, args)
        elif args.command == "done": finish_task(root, args)
        elif args.command == "close": close_with_bundle(root, args)
        elif args.command == "reconcile": reconcile(root, args)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        code=exc.code if isinstance(exc.code,int) else (0 if exc.code in {None,''} else 1)
        if code:
            try:
                root=Path(sys.argv[sys.argv.index('--root')+1]).resolve() if '--root' in sys.argv else DEFAULT_ROOT
                field_record(root,'WORKFLOW_RETRY',phase='CLI',severity='LOW',trigger=str(exc),automatic=True,evidence_code='NONZERO_SYSTEM_EXIT',metadata={'argv':sys.argv[1:4]})
            except Exception:
                pass
        raise
