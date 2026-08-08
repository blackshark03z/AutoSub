#!/usr/bin/env python3
"""Senior AI Build OS v1.8 lifecycle, task-delta and learning-loop CLI."""
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
    verify_bundle,
)
from refresh_context_capsule import refresh
from risk_support import RISK_ORDER, effective_risk, minimum_actual_risk, minimum_risk
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
)
from state_runtime import archive_history, load_history, sync_runtime
from validate_ai_os import validate

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
    }


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
    """Best-effort technical baseline without forcing the owner to restate the repo."""
    languages: list[str] = []
    framework = "NONE_DETECTED"
    database = "NONE_DETECTED"
    package_manager = "NONE_DETECTED"
    entry_point = "AUTO_DETECT_ON_FIRST_TASK"
    run_command = "PROJECT_SPECIFIC"
    test_command = "PROJECT_SPECIFIC"
    build_command = "NONE_REQUIRED_OR_PROJECT_SPECIFIC"

    package = root / "package.json"
    if package.is_file():
        languages.append("Node/TypeScript/JavaScript")
        try:
            data = json.loads(package.read_text(encoding="utf-8")); deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}; scripts = data.get("scripts") or {}
        except json.JSONDecodeError:
            deps = {}; scripts = {}
        for dep, name in [("next", "Next.js"), ("react", "React"), ("vue", "Vue"), ("svelte", "Svelte"), ("express", "Express"), ("@nestjs/core", "NestJS")]:
            if dep in deps: framework = name; break
        for dep, name in [("prisma", "Prisma/SQL"), ("@prisma/client", "Prisma/SQL"), ("mongoose", "MongoDB"), ("pg", "PostgreSQL"), ("better-sqlite3", "SQLite")]:
            if dep in deps: database = name; break
        if (root / "pnpm-lock.yaml").is_file(): package_manager = "pnpm"
        elif (root / "yarn.lock").is_file(): package_manager = "yarn"
        else: package_manager = "npm"
        if "dev" in scripts: run_command = f"{package_manager} run dev"
        elif "start" in scripts: run_command = f"{package_manager} run start"
        if "test" in scripts: test_command = f"{package_manager} run test"
        if "build" in scripts: build_command = f"{package_manager} run build"

    pyproject = root / "pyproject.toml"
    if pyproject.is_file() or (root / "requirements.txt").is_file() or (root / "setup.py").is_file():
        languages.append("Python")
        text = pyproject.read_text(encoding="utf-8", errors="ignore").casefold() if pyproject.is_file() else ""
        for needle, name in [("fastapi", "FastAPI"), ("django", "Django"), ("flask", "Flask")]:
            if needle in text: framework = name; break
        for needle, name in [("sqlalchemy", "SQLAlchemy/SQL"), ("psycopg", "PostgreSQL"), ("sqlite", "SQLite")]:
            if needle in text: database = name; break
        if (root / "uv.lock").is_file(): package_manager = "uv"
        elif "poetry" in text: package_manager = "poetry"
        elif package_manager == "NONE_DETECTED": package_manager = "pip"
        if (root / "tests").exists(): test_command = "python -m pytest -q"
        for candidate in ["main.py", "app.py", "src/main.py"]:
            if (root / candidate).is_file(): entry_point = candidate; break

    if (root / "go.mod").is_file():
        languages.append("Go"); package_manager = "go modules"; test_command = "go test ./..."; build_command = "go build ./..."
    if (root / "Cargo.toml").is_file():
        languages.append("Rust"); package_manager = "cargo"; test_command = "cargo test --all-targets"; build_command = "cargo build"
    important = [name for name in ["src", "app", "tests", "test", "packages", "services"] if (root / name).exists()]
    return {
        "Language/runtime": ", ".join(languages) if languages else "AUTO_DETECT_ON_FIRST_TASK",
        "Framework": framework, "Database": database, "Package manager": package_manager,
        "Entry point": entry_point, "Run command": run_command, "Test command": test_command,
        "Build command": build_command, "Important directories": ", ".join(important) if important else "AUTO_DETECT_ON_FIRST_TASK",
    }


def install_ci_workflow(root: Path) -> bool:
    """Install the managed GitHub Actions gate without overwriting an existing file."""
    source = root / "templates" / "CI_GITHUB_ACTIONS.yml"
    target = root / ".github" / "workflows" / "ai-build-os.yml"
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


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
    profile = PROFILE_BY_RISK[risk]
    revision = next_revision(root, args.task_id)
    failed_revisions = consecutive_failed_first_pass_revisions(root, args.task_id, revision)
    if failed_revisions >= 2 and not args.stop_loss_ack.strip():
        raise SystemExit(
            f"Stop-loss active for {args.task_id}: {failed_revisions} consecutive prior revisions had first_pass_accepted=no. "
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

    replacements = {
        "Task Status": "ACTIVE" if args.claim else "READY", "Task Mode": profile,
        "Task ID": args.task_id, "Task Revision": revision, "Created": timestamp[:10],
        "Owner Authorization": args.owner_authorization, "Authorization Reference": args.authorization_reference or "NONE",
        "Milestone ID": args.milestone_id, "Success Criterion": args.success_criterion,
        "Delivery Delta": args.delivery_delta, "Demonstrable Result": args.demonstrable_result,
        "Unlocks": args.unlocks, "Declared Risk Tier": args.risk.upper(), "Risk Tier": risk,
        "Risk Floor": risk_floor, "Risk Floor Reason": "; ".join(risk_reasons) or "none",
        "Execution Profile": profile, "Negative path required": "yes" if args.negative_required else "no",
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
        capture_task_baseline(root, args.task_id, revision)
        write(task_path, task)
        write(state_path, state)
        sync_and_validate(root)
    except BaseException:
        restore(snapshot)
        if old_baseline is None:
            baseline_path.unlink(missing_ok=True)
        else:
            write(baseline_path, old_baseline)
        raise
    if args.risk.upper() != risk:
        print(f"Risk auto-floor: declared={args.risk.upper()} effective={risk} floor={risk_floor} reasons={'; '.join(risk_reasons) or 'none'}")
    print(f"Started {args.task_id}/r{revision:03d} risk={risk} profile={profile} status={'ACTIVE' if args.claim else 'READY'} preexisting_dirty={len(app['changed_files'])}")

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
    final_bundle = ai / "evidence" / task_id / revision_name(revision)
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
            "risk_tier": risk, "delivery_delta": delta, "outcome": args.outcome,
            "completed_at": timestamp, "evidence_bundle": evidence_relative,
            "evidence_manifest_sha256": sha256_file(final_bundle / "manifest.json"),
            "verified_snapshot_sha256": manifest["verified_snapshot"]["snapshot_sha256"],
            "actual_risk_floor": actual_floor, "actual_risk_reasons": actual_risk_reasons,
            "first_pass_accepted": args.first_pass_accepted,
            "breaker_override": breaker_override_info(task)[0],
            "breaker_override_reason": breaker_override_info(task)[1],
            "quality_reconciliation": {"later_rework": "unknown", "escaped_defect": "unknown", "rollback_required": args.rollback_required},
        }
        history_path = archive_history(root, history_record)
        sync_runtime(root)
        errors, warnings = validate(root, strict=False)
        for warning in warnings: print("WARNING", warning)
        if errors: raise SystemExit("; ".join(errors))
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
    reconcile_actual_risk(root, task_body)
    risk = field(task_body, "Risk Tier")
    if not args.focused_command: raise SystemExit("done requires at least one --focused-command")
    negative_required = field(task_body, "Negative path required", "no").casefold() in {"yes", "true", "required"}
    if (risk in {"R2", "R3"} or negative_required) and not args.negative_command:
        raise SystemExit(f"{risk} done requires --negative-command for this task")
    if risk in {"R2", "R3"} and not args.integration_command: raise SystemExit(f"{risk} done requires --integration-command")
    if risk == "R3" and not args.rollback_command: raise SystemExit("R3 done requires --rollback-command rehearsal/proof")
    if risk == "R3" and not args.full_suite_command: raise SystemExit("R3 done requires --full-suite-command")
    if risk == "R3" and not args.review_report: raise SystemExit("R3 done requires --review-report")
    if risk in {"R1", "R2", "R3"} and not args.output_inspected_by:
        raise SystemExit(f"{risk} done requires --output-inspected-by agent:<id> or human:<id>")
    commands: list[tuple[str, str]] = []
    for kind, values in [("focused", args.focused_command), ("negative", args.negative_command), ("integration", args.integration_command), ("rollback", args.rollback_command), ("full_suite", args.full_suite_command)]:
        commands.extend((kind, value) for value in values or [])
    revision = int(field(task_body, "Task Revision", "1")); task_id = field(task_body, "Task ID")
    with transaction_journal(root, "done", {"task_id": task_id, "task_revision": revision}) as tx_dir:
        stage, manifest = create_staged_bundle(
            root, tx_dir, task=task_map(task_body), revision=revision, outcome=args.outcome,
            commands=commands, artifacts=args.artifact or [], timeout=args.command_timeout,
            allow_shell=args.allow_shell_command, inspected_by=args.output_inspected_by,
            cleanup_note=args.cleanup_note, known_limits=args.known_limits, review_report=args.review_report,
            compact=risk in {"R0", "R1"}, expected_outputs=args.expected_output,
        )
        enforce_scope(root, task_body)
        if risk == "R3":
            expected = {"task_id": task_id, "task_revision": revision, "snapshot_sha256": manifest["verified_snapshot"]["snapshot_sha256"], "writer_identity": field(task_body, "Session Label")}
            errors = validate_review_report(root, args.review_report, {k: str(v) for k, v in expected.items()})
            if errors: raise SystemExit("; ".join(errors))
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
            rollback_required=args.rollback_required, notes=args.notes,
        )
        close_with_bundle(root, close_args, stage=stage, staged_manifest=manifest)


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
        "profile": field(task, "Execution Profile"), "lease": field(task, "Lease Status"), "writer": field(task, "Session Label"),
        "branch": snap["branch"], "head": snap["head"], "worktree": snap["worktree"],
        "snapshot_sha256": snap["snapshot_sha256"], "changed_files": snap["changed_files"],
        "task_delta_files": delta, "preexisting_dirty_files": sorted((baseline.get("preexisting_changed_files") or {}).keys()),
        "first_runnable_at": field(task, "First Runnable At"),
        "shipping_circuit_breaker": derived_breaker,
        "consecutive_non_shipping_tasks": non_shipping_count,
        "max_consecutive_non_shipping_tasks": non_shipping_threshold,
        "next_action": next_action(root, emit=False),
    }
    if as_json: print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(f"Project: {value['project_id']}")
        print(f"Task: {value['task_id']}/r{str(value['task_revision']).zfill(3)} {value['task_status']} | {value['risk']}/{value['profile']}")
        print(f"Lease: {value['lease']} by {value['writer']}")
        print(f"Git: {value['branch']} {str(value['head'])[:12]} {value['worktree']}")
        print(f"Task delta: {', '.join(value['task_delta_files']) if value['task_delta_files'] else 'NONE'}")
        print(f"Pre-existing dirty: {len(value['preexisting_dirty_files'])}")
        print(f"Shipping breaker: {value['shipping_circuit_breaker']} ({value['consecutive_non_shipping_tasks']}/{value['max_consecutive_non_shipping_tasks']} non-shipping)")
        print(f"Next: {value['next_action']}")


def next_action(root: Path, emit: bool = True) -> str:
    task = read(root / ".ai" / "ACTIVE_TASK.md"); state = read(root / ".ai" / "STATE.md")
    status = field(task, "Task Status"); lease = field(task, "Lease Status"); risk = field(task, "Risk Tier")
    project = read(root / ".ai" / "PROJECT.md")
    breaker, _, _ = shipping_breaker_state(project, state)
    if status in {"NOT_CREATED", "COMPLETED", "ABANDONED", "ABORTED"}:
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
    with (root / ".ai" / "COST_LEDGER.csv").open(encoding="utf-8", newline="") as handle: rows = list(csv.DictReader(handle))[-last:]
    if not rows: print("No ledger data yet."); return
    accepted = [row for row in rows if row["accepted"] == "yes"]
    cycles = [float(row["cycle_minutes"]) for row in accepted if row["cycle_minutes"]]
    first_pass = [row for row in accepted if row["first_pass_accepted"] in {"yes", "no"}]
    rework = [row for row in accepted if row["later_rework"] in {"yes", "no"}]
    defects = [row for row in accepted if row["escaped_defect"] in {"yes", "no"}]
    ai_cost = sum(float(row["estimated_ai_cost"] or 0) for row in accepted)
    provider_cost = sum(float(row["provider_cost"] or 0) for row in accepted)
    print(f"Accepted outcomes: {len(accepted)}")
    print(f"Median cycle minutes: {statistics.median(cycles):.2f}" if cycles else "Median cycle minutes: unknown")
    print(f"First-pass acceptance: {sum(r['first_pass_accepted']=='yes' for r in first_pass)}/{len(first_pass)}" if first_pass else "First-pass acceptance: unknown")
    print(f"Later rework: {sum(r['later_rework']=='yes' for r in rework)}/{len(rework)}" if rework else "Later rework: unreconciled")
    print(f"Escaped defects: {sum(r['escaped_defect']=='yes' for r in defects)}/{len(defects)}" if defects else "Escaped defects: unreconciled")
    print(f"Recorded AI/provider cost: {ai_cost:.6f} + {provider_cost:.6f}")
    accepted_keys = {(row.get("task_id"), int(row.get("task_revision") or 0)) for row in accepted}
    recent_history = [
        item for item in load_history(root)
        if (item.get("task_id"), int(item.get("task_revision") or 0)) in accepted_keys
    ]
    overrides = [item for item in recent_history if item.get("breaker_override") is True]
    print(f"Shipping breaker overrides: {len(overrides)}/{len(accepted)}")
    if overrides:
        reasons: dict[str, int] = {}
        for item in overrides:
            reason = str(item.get("breaker_override_reason") or "UNSPECIFIED")
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))[:5]:
            print(f"  - {count}x {reason}")
    # Evidence-based recommendations, intentionally conservative.
    if len(first_pass) >= 3 and sum(r["first_pass_accepted"] == "yes" for r in first_pass) / len(first_pass) < 0.6:
        print("Recommendation: acceptance criteria or focused checks are too weak; strengthen preflight before adding broader gates.")
    r1 = [r for r in accepted if r["risk_tier"] in {"R0", "R1"}]
    if r1 and sum(int(r["integration_test_runs"] or 0) for r in r1) > len(r1):
        print("Recommendation: Fast Lane is over-testing; make integration trigger-based for R0/R1.")
    if accepted and len(overrides) / len(accepted) > 0.20:
        print("Recommendation: shipping-breaker overrides exceed 20% of recent accepted work; inspect threshold/policy abuse.")
    unreconciled = sum(r["later_rework"] == "unknown" or r["escaped_defect"] == "unknown" for r in accepted)
    if unreconciled: print(f"Recommendation: reconcile {unreconciled} accepted outcome(s) after operational use.")


def reconcile(root: Path, args: argparse.Namespace) -> None:
    updates = {k: v for k, v in {
        "later_rework": args.later_rework, "escaped_defect": args.escaped_defect,
        "rollback_required": args.rollback_required, "notes": args.notes,
        "human_review_minutes": str(args.human_review_minutes) if args.human_review_minutes is not None else None,
        "human_wait_minutes": str(args.human_wait_minutes) if args.human_wait_minutes is not None else None,
    }.items() if v is not None}
    reconcile_row(root, args.task_id, args.task_revision, updates)
    history = root / ".ai" / "history" / args.task_id / f"r{args.task_revision:03d}.json"
    if history.is_file():
        record = json.loads(history.read_text(encoding="utf-8")); record.setdefault("quality_reconciliation", {}).update(updates); atomic_write_json(history, record)


def check(root: Path, strict: bool) -> None:
    sync_runtime(root)
    errors, warnings = validate(root, strict=strict)
    for warning in warnings: print("WARNING", warning)
    for error in errors: print("ERROR", error)
    print(f"RESULT: {'FAIL' if errors else 'PASS'} errors={len(errors)} warnings={len(warnings)}")
    if errors: raise SystemExit(1)


def add_metrics(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--skip-ledger", action="store_true"); parser.add_argument("--cycle-minutes", type=float)
    parser.add_argument("--first-pass-accepted", choices=["yes", "no", "unknown"], default="unknown")
    for name in ["coordination-input-tokens", "implementation-input-tokens", "cached-input-tokens", "output-tokens", "worker-turns", "retries", "provider-runs", "human-review-minutes", "human-wait-minutes"]:
        parser.add_argument(f"--{name}", type=int)
    parser.add_argument("--estimated-ai-cost", type=float); parser.add_argument("--provider-cost", type=float)
    parser.add_argument("--currency", default="USD"); parser.add_argument("--rollback-required", choices=["yes", "no"], default="no"); parser.add_argument("--notes", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Senior AI Build OS v1.8")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--project-id", required=True)
    init.add_argument("--owner", required=True)
    init.add_argument("--problem", required=True)
    init.add_argument("--target-user", required=True)
    init.add_argument("--primary-action", required=True)
    init.add_argument("--observable-result", required=True)
    init.add_argument("--mvp-goal", required=True)
    init.add_argument("--acceptance-threshold", default="observable result passes representative fixtures")
    init.add_argument("--demo-method", default="local runtime demo")
    init.add_argument("--milestone-id", default="M-001")
    init.add_argument("--no-ci", action="store_true", help="Do not install the managed GitHub Actions workflow")

    start_p = sub.add_parser("start", aliases=["begin"])
    start_p.add_argument("--task-id", required=True)
    start_p.add_argument("--outcome", required=True)
    start_p.add_argument("--risk", default="auto", choices=["auto", "R0", "R1", "R2", "R3"])
    start_p.add_argument("--success-criterion", required=True)
    start_p.add_argument("--accept", action="append", default=[], help="Concrete acceptance criterion; repeat for multiple checks")
    start_p.add_argument("--delivery-delta", required=True, choices=["USER_VISIBLE_BEHAVIOR", "EXECUTABLE_CAPABILITY", "RISK_RETIREMENT", "DOCUMENTATION_ONLY", "NO_DELTA"])
    start_p.add_argument("--milestone-id", default="M-001")
    start_p.add_argument("--project-id")
    start_p.add_argument("--demonstrable-result", default="runtime/output evidence listed in task evidence index")
    start_p.add_argument("--unlocks", default="next milestone capability")
    start_p.add_argument("--read-scope", default="task-relevant repository files")
    start_p.add_argument("--modify", required=True)
    start_p.add_argument("--create", default="NONE")
    start_p.add_argument("--commands", default="focused checks and task-authorized commands")
    start_p.add_argument("--local-services", default="NONE")
    start_p.add_argument("--external-calls", default="NONE")
    start_p.add_argument("--data-operation", choices=["READ_ONLY", "CREATE_NEW_VERSION", "MUTATE_IN_PLACE", "DELETE"], default="READ_ONLY")
    start_p.add_argument("--artifact-operation", choices=["READ_ONLY", "CREATE_NEW_VERSION", "MUTATE_IN_PLACE", "OVERWRITE", "DELETE"], default="CREATE_NEW_VERSION")
    start_p.add_argument("--inputs", default="task-relevant source and fixtures")
    start_p.add_argument("--outputs", default="accepted outcome and evidence")
    start_p.add_argument("--files-created", default="NONE")
    start_p.add_argument("--files-overwritten", default="NONE")
    start_p.add_argument("--data-mutated", default="NONE")
    start_p.add_argument("--provider-calls", default="NONE")
    start_p.add_argument("--expected-provider-cost", type=float, default=0.0)
    start_p.add_argument("--disk-requirement", default="MINIMAL")
    start_p.add_argument("--ram-requirement", default="MINIMAL")
    start_p.add_argument("--process-port", default="NONE")
    start_p.add_argument("--artifact-lineage", default="source inputs and evidence manifest")
    start_p.add_argument("--rollback", default="revert task-scoped diff and remove new artifacts")
    start_p.add_argument("--expected-cost-range", default="small; investigate repeated attempts without evidence")
    start_p.add_argument("--primary-cost-drivers", default="implementation, verification and output inspection")
    start_p.add_argument("--evidence-sequence", default="focused → affected regression/runtime → diff review")
    start_p.add_argument("--escalation-conditions", default="risk exceeds profile or same approach fails twice")
    start_p.add_argument("--negative-required", action="store_true", help="Require an explicit negative/failure-path check even on Fast Lane")
    start_p.add_argument("--owner-authorization", choices=["NOT_REQUIRED", "APPROVED"], default="NOT_REQUIRED")
    start_p.add_argument("--authorization-reference", default="")
    start_p.add_argument("--breaker-override", action="store_true", help="Explicitly allow a non-shipping task while the shipping breaker is active")
    start_p.add_argument("--breaker-override-reason", default="", help="Audit reason required with --breaker-override")
    start_p.add_argument("--stop-loss-ack", default="", help="Required only after two consecutive prior revisions with first_pass_accepted=no")
    claim_mode = start_p.add_mutually_exclusive_group()
    claim_mode.add_argument("--claim", dest="claim", action="store_true", help="Auto-claim writer lease (default)")
    claim_mode.add_argument("--ready", "--no-claim", dest="claim", action="store_false", help="Create READY task without claiming it")
    start_p.set_defaults(claim=True)
    start_p.add_argument("--writer-role", default="WORKER")
    start_p.add_argument("--platform", default="ChatGPT")
    start_p.add_argument("--session-label", default="AI-WORKER")
    start_p.add_argument("--allow-no-git", action="store_true")

    claim = sub.add_parser("claim")
    claim.add_argument("--writer-role", default="WORKER")
    claim.add_argument("--platform", default="ChatGPT")
    claim.add_argument("--session-label", default="AI-WORKER")
    sub.add_parser("pause")
    resume = sub.add_parser("resume")
    resume.add_argument("--writer-role", default="WORKER")
    resume.add_argument("--platform", default="ChatGPT")
    resume.add_argument("--session-label", default="AI-WORKER")
    sub.add_parser("abort")

    amend = sub.add_parser("amend")
    amend.add_argument("--add-modify")
    amend.add_argument("--add-create")
    amend.add_argument("--risk", choices=["R0", "R1", "R2", "R3"])
    amend.add_argument("--data-operation", choices=["READ_ONLY", "CREATE_NEW_VERSION", "MUTATE_IN_PLACE", "DELETE"])
    amend.add_argument("--artifact-operation", choices=["READ_ONLY", "CREATE_NEW_VERSION", "MUTATE_IN_PLACE", "OVERWRITE", "DELETE"])
    amend.add_argument("--files-overwritten")
    amend.add_argument("--data-mutated")
    amend.add_argument("--external-calls")
    amend.add_argument("--provider-calls")
    amend.add_argument("--owner-authorization", choices=["APPROVED"])
    amend.add_argument("--authorization-reference", default="")
    amend.add_argument("--reason", required=True)

    runnable = sub.add_parser("runnable")
    runnable.add_argument("--evidence", required=True)
    runnable.add_argument("--at")

    done = sub.add_parser("done")
    done.add_argument("--outcome", required=True)
    done.add_argument("--focused-command", action="append", default=[])
    done.add_argument("--negative-command", action="append", default=[])
    done.add_argument("--integration-command", action="append", default=[])
    done.add_argument("--rollback-command", action="append", default=[], help="R3 rollback rehearsal/proof; must leave final application state intact")
    done.add_argument("--full-suite-command", action="append", default=[])
    done.add_argument("--artifact", action="append", default=[])
    done.add_argument("--review-report")
    done.add_argument("--command-timeout", type=int, default=300)
    done.add_argument("--allow-shell-command", action="store_true")
    done.add_argument("--output-inspected-by")
    done.add_argument("--expected-output", action="append", default=[], help="Optional exact marker that must appear in stored verification stdout/stderr")
    done.add_argument("--cleanup-note", default="no residual process; rollback remains available")
    done.add_argument("--known-limits", default="NONE")
    add_metrics(done)

    close = sub.add_parser("close")
    close.add_argument("--outcome", required=True)
    close.add_argument("--evidence-bundle", required=True)
    close.add_argument("--delivery-delta", choices=["USER_VISIBLE_BEHAVIOR", "EXECUTABLE_CAPABILITY", "RISK_RETIREMENT", "DOCUMENTATION_ONLY", "NO_DELTA"])
    close.add_argument("--completed-at")
    add_metrics(close)
    close.set_defaults(focused_test_runs=None, integration_test_runs=None, full_suite_runs=None)

    check_p = sub.add_parser("check")
    check_p.add_argument("--strict", action="store_true")
    sub.add_parser("doctor")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    sub.add_parser("next")
    history = sub.add_parser("history")
    history.add_argument("--limit", type=int, default=20)
    report_p = sub.add_parser("report")
    report_p.add_argument("--last", type=int, default=30)
    rec = sub.add_parser("reconcile")
    rec.add_argument("--task-id", required=True)
    rec.add_argument("--task-revision", type=int, required=True)
    rec.add_argument("--later-rework", choices=["yes", "no", "unknown"])
    rec.add_argument("--escaped-defect", choices=["yes", "no", "unknown"])
    rec.add_argument("--rollback-required", choices=["yes", "no"])
    rec.add_argument("--notes")
    rec.add_argument("--human-review-minutes", type=int)
    rec.add_argument("--human-wait-minutes", type=int)
    return parser

def main() -> None:
    args = build_parser().parse_args(); root = args.root.resolve()
    if args.command == "check": check(root, args.strict); return
    if args.command == "doctor": doctor(root); return
    if args.command == "status": show_status(root, args.json); return
    if args.command == "next": next_action(root); return
    if args.command == "history": show_history(root, args.limit); return
    if args.command == "report": report(root, args.last); return
    with lifecycle_lock(root):
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


if __name__ == "__main__": main()
