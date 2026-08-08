#!/usr/bin/env python3
"""Machine-readable v1.8 runtime snapshots and immutable history."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from runtime_support import application_snapshot, atomic_write_json, load_task_baseline, sha256_file, task_delta_files


def field(body: str, name: str, default: str = "") -> str:
    match = re.search(rf"^-?\s*{re.escape(name)}:\s*(.*?)\s*$", body, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else default


def sync_runtime(root: Path) -> None:
    ai = root / ".ai"
    runtime = ai / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    project_body = (ai / "PROJECT.md").read_text(encoding="utf-8")
    state_body = (ai / "STATE.md").read_text(encoding="utf-8")
    task_body = (ai / "ACTIVE_TASK.md").read_text(encoding="utf-8")
    snapshot = application_snapshot(root)
    try:
        non_shipping_count = int(field(state_body, "Consecutive Non-Shipping Tasks", "0"))
    except ValueError:
        non_shipping_count = 0
    try:
        non_shipping_threshold = max(1, int(field(project_body, "Maximum consecutive non-shipping tasks", "3")))
    except ValueError:
        non_shipping_threshold = 3
    derived_breaker = "ACTIVE" if non_shipping_count >= non_shipping_threshold else "INACTIVE"
    atomic_write_json(runtime / "project.json", {
        "schema_version": 1,
        "project_id": field(project_body, "Project ID"),
        "project_status": field(project_body, "Project Status"),
        "source_sha256": sha256_file(ai / "PROJECT.md"),
    })
    atomic_write_json(runtime / "state.json", {
        "schema_version": 1,
        "project_id": field(state_body, "Project ID"),
        "state_revision": field(state_body, "State Revision"),
        "active_task_id": field(state_body, "Active Task ID"),
        "branch": field(state_body, "Branch"),
        "head": field(state_body, "HEAD"),
        "worktree": field(state_body, "Worktree"),
        "application_snapshot": snapshot,
        "shipping_circuit_breaker": derived_breaker,
        "consecutive_non_shipping_tasks": non_shipping_count,
        "max_consecutive_non_shipping_tasks": non_shipping_threshold,
        "source_sha256": sha256_file(ai / "STATE.md"),
    })
    task_status = field(task_body, "Task Status")
    delta = task_delta_files(root, task_id=field(task_body, "Task ID"), task_revision=int(field(task_body, "Task Revision", "1") or 1)) if task_status in {"READY", "ACTIVE", "BLOCKED", "PAUSED"} else []
    baseline = load_task_baseline(root) or {}
    atomic_write_json(runtime / "task.json", {
        "schema_version": 2,
        "task_id": field(task_body, "Task ID"),
        "task_status": field(task_body, "Task Status"),
        "risk_tier": field(task_body, "Risk Tier"),
        "risk_floor": field(task_body, "Risk Floor"),
        "execution_profile": field(task_body, "Execution Profile"),
        "lease_status": field(task_body, "Lease Status"),
        "session_label": field(task_body, "Session Label"),
        "evidence_index": field(task_body, "Evidence index"),
        "task_delta_files": delta,
        "baseline_task_id": baseline.get("task_id", "NONE"),
        "source_sha256": sha256_file(ai / "ACTIVE_TASK.md"),
    })


def archive_history(root: Path, record: dict[str, Any]) -> Path:
    task_id = str(record["task_id"])
    revision = int(record["task_revision"])
    path = root / ".ai" / "history" / task_id / f"r{revision:03d}.json"
    if path.exists():
        raise SystemExit(f"Immutable task history already exists: {path.relative_to(root)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, record)
    return path


def load_history(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((root / ".ai" / "history").glob("*/r*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return records
