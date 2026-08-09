#!/usr/bin/env python3
"""Generate a compact worker packet from canonical repository state."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from runtime_support import atomic_write_text, load_task_baseline, task_delta_files

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def field(body: str, name: str, default: str = "UNSET") -> str:
    match = re.search(rf"^-?\s*{re.escape(name)}:\s*(.*?)\s*$", body, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else default


def section(body: str, heading: str, default: str = "UNSET", limit: int = 900) -> str:
    lines = body.splitlines()
    start = None; level = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match and match.group(2).strip().casefold() == heading.casefold():
            start = index + 1; level = len(match.group(1)); break
    if start is None or level is None:
        return default
    end = len(lines)
    for index in range(start, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index])
        if match and len(match.group(1)) <= level:
            end = index; break
    value = "\n".join(lines[start:end]).strip()
    return (value or default)[:limit]


def compact_section(value: str, max_lines: int = 5) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return "\n".join(lines[:max_lines]) if lines else "- NONE"


def decision_excerpt(decisions: str, decision_id: str) -> str | None:
    match = re.search(rf"^##\s+({re.escape(decision_id)}[^\n]*)\n(.*?)(?=^##\s+DEC-|\Z)", decisions, re.MULTILINE | re.DOTALL)
    if not match:
        return None
    compact = re.sub(r"\s+", " ", match.group(2)).strip()[:180]
    return f"- {match.group(1).strip()}: {compact}" if compact else f"- {match.group(1).strip()}"


def refresh(root: Path) -> int:
    ai = root / ".ai"
    project = read(ai / "PROJECT.md")
    state = read(ai / "STATE.md")
    task = read(ai / "ACTIVE_TASK.md")
    decisions = read(ai / "DECISIONS.md")
    goal = read(ai / "GOAL.md") if (ai / "GOAL.md").exists() else ""
    old = read(ai / "CONTEXT_CAPSULE.md")
    try:
        revision = int(field(old, "Capsule Revision", "0")) + 1
    except ValueError:
        revision = 1

    profile = field(task, "Execution Profile", "LEAN")
    task_id = field(task, "Task ID")
    try:
        task_revision = int(field(task, "Task Revision", "1"))
    except ValueError:
        task_revision = 1
    baseline = load_task_baseline(root) or {}
    delta = task_delta_files(root, task_id=task_id, task_revision=task_revision) if field(task, "Task Status") in {"READY", "ACTIVE", "PAUSED", "BLOCKED"} else []
    preexisting_count = len(baseline.get("preexisting_changed_files", {}) or {}) if baseline.get("task_id") == task_id else 0

    outcome = compact_section(section(task, "Single Outcome", limit=420), 3)
    acceptance = compact_section(section(task, "Acceptance Criteria", limit=520), 5)
    verification = compact_section(section(task, "Verification Plan", limit=420), 4)
    modify = field(task, "Modify")
    create = field(task, "Create")
    external = field(task, "External calls", "NONE")
    data_op = field(task, "Data operation", "READ_ONLY")
    artifact_op = field(task, "Artifact operation", "CREATE_NEW_VERSION")
    negative = field(task, "Negative path required", "no")
    non_shipping = field(state, "Consecutive Non-Shipping Tasks", "0")
    max_non_shipping = field(project, "Maximum consecutive non-shipping tasks", "3")
    try:
        breaker = "ACTIVE" if int(non_shipping) >= max(1, int(max_non_shipping)) else "INACTIVE"
    except ValueError:
        breaker = field(state, "Shipping Circuit Breaker", "INACTIVE")
    goal_outcome = compact_section(section(goal, "Outcome", default="NONE", limit=240), 2) if goal else "NONE"
    contract_hash = field(task, "Acceptance Contract SHA256", "NONE")
    try:
        contract = json.loads(field(task, "Acceptance Contract JSON", "{}"))
        contract_commands = len(contract.get("commands", []) or []) if isinstance(contract, dict) else 0
        contract_probes = len(contract.get("probe_hashes", {}) or {}) if isinstance(contract, dict) else 0
    except json.JSONDecodeError:
        contract_commands = 0
        contract_probes = 0

    scout_handoff = "## Scout Handoff\n\nNONE"
    goal_id = field(task, 'Goal ID', 'NONE'); goal_node = field(task, 'Goal Node', 'NONE')
    if goal_id not in {'NONE','UNSET',''} and goal:
        try:
            goal_state = json.loads((root / '.ai' / 'GOAL_STATE.json').read_text(encoding='utf-8'))
            node = (goal_state.get('tasks') or {}).get(goal_node) or {}
            chunks = []
            for dep in node.get('depends_on', []) or []:
                scout = (goal_state.get('tasks') or {}).get(dep) or {}
                result = scout.get('result') or {}
                if scout.get('agent_role') != 'SCOUT' or scout.get('status') != 'DONE' or not result:
                    continue
                summary = compact_section(str(result.get('summary') or ''), 3)
                affected = ', '.join((result.get('affected_files') or [])[:8]) or 'NONE'
                invariants = '; '.join((result.get('invariants') or [])[:4]) or 'NONE'
                risks = '; '.join((result.get('risk_signals') or [])[:4]) or 'NONE'
                entry = str(result.get('entry_point') or 'NONE')
                confidence = str(result.get('confidence') or 'MEDIUM')
                chunks.append(f"Scout {dep} ({confidence}): {summary}\n- Affected: {affected}\n- Invariants: {invariants}\n- Risk: {risks}\n- Entry: {entry}")
            if chunks:
                scout_handoff = "## Scout Handoff\n\n" + "\n\n".join(chunks)
        except (OSError, json.JSONDecodeError):
            pass

    base = f"""# Worker Packet

Generated: {datetime.now(timezone.utc).isoformat(timespec="seconds")}
Capsule Revision: {revision}

## Task

- ID: {task_id}/r{task_revision:03d}
- Status: {field(task, 'Task Status')}
- Goal: {field(task, 'Goal ID', 'NONE')} / node {field(task, 'Goal Node', 'NONE')}
- Milestone / criterion: {field(task, 'Milestone ID')} / {field(task, 'Success Criterion')}
- Risk / profile: {field(task, 'Risk Tier')} / {profile}
- Negative path required: {negative}
- Shipping breaker: {breaker} ({non_shipping}/{max_non_shipping} non-shipping)

## Outcome

{outcome}

## Goal Context

{goal_outcome if field(task, 'Goal ID', 'NONE') not in {'NONE','UNSET'} else 'NONE'}

{scout_handoff}

## Scope

- Modify: {modify}
- Create: {create}
- External calls: {external}
- Pre-existing dirty files: {preexisting_count} (not part of task unless changed again)
- Current task delta: {', '.join(delta) if delta else 'NONE'}

## Acceptance

{acceptance}

## Verify

{verification}
- Acceptance contract: {contract_hash} (predeclared commands={contract_commands}, locked probes={contract_probes})
- Review policy: {field(task, 'Review policy', 'auto')}

## Stop

- Stop/change strategy after two failed attempts without new evidence.
- Amend scope instead of widening it silently.
- Final output and task delta must be inspected before acceptance.
- If shipping breaker is ACTIVE, do not start/continue non-shipping work without an explicit override.
"""

    if profile != "LEAN":
        relevant = section(task, "Relevant Decisions", default="", limit=600)
        ids = list(dict.fromkeys(re.findall(r"DEC-\d{3}", relevant)))[:5]
        decision_lines = [x for i in ids if (x := decision_excerpt(decisions, i))] or ["- NONE_LISTED"]
        product_goal = re.sub(r"\s+", " ", section(project, "MVP Goal", limit=260)).strip()
        base += f"""
## Shared/Operational Context

- Product goal: {product_goal}
- Data operation: {data_op}
- Artifact operation: {artifact_op}
- Rollback: {field(task, 'Rollback')}

## Relevant Decisions

{chr(10).join(decision_lines)}
"""
    if profile == "DEEP":
        base += f"""
## Critical Gates

- Owner authorization: {field(task, 'Owner Authorization')} / {field(task, 'Authorization Reference')}
- Human review required: {field(task, 'Human review required')}
- Full suite required: {field(task, 'Full suite required')}
- Specialist trigger: {field(task, 'Specialist reviewer trigger')}
"""

    max_lines = 70 if profile == "LEAN" else (100 if profile == "STANDARD" else 120)
    lines = base.splitlines()
    if len(lines) > max_lines:
        raise SystemExit(f"Refusing to write {profile} worker packet over {max_lines} lines: {len(lines)}")
    atomic_write_text(ai / "CONTEXT_CAPSULE.md", base)
    approx_tokens = max(1, len(base) // 4)
    print(f"Updated CONTEXT_CAPSULE.md revision={revision} profile={profile} lines={len(lines)} approx_tokens={approx_tokens}")
    return revision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    refresh(args.root.resolve())


if __name__ == "__main__":
    main()
