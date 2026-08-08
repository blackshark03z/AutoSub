#!/usr/bin/env python3
"""Append and reconcile accepted-outcome economics in COST_LEDGER.csv."""
from __future__ import annotations

import argparse
import csv
import io
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from runtime_support import atomic_write_text, lifecycle_lock

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "date", "task_id", "task_revision", "success_criterion", "delivery_delta",
    "risk_tier", "model", "profile", "accepted", "started_at",
    "first_runnable_at", "completed_at", "cycle_minutes", "first_pass_accepted",
    "coordination_input_tokens", "implementation_input_tokens", "cached_input_tokens",
    "output_tokens", "estimated_ai_cost", "currency", "provider_cost", "worker_turns",
    "retries", "focused_test_runs", "integration_test_runs", "full_suite_runs",
    "provider_runs", "human_review_minutes", "human_wait_minutes", "outcome",
    "later_rework", "escaped_defect", "rollback_required", "notes",
]


def _read(ledger: Path) -> list[dict[str, str]]:
    with ledger.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise SystemExit("Ledger header mismatch; refusing mutation")
        return list(reader)


def _write(ledger: Path, rows: list[dict[str, Any]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(ledger, buffer.getvalue())


def cycle_minutes(started_at: str, completed_at: str, explicit: float | None) -> float | None:
    if explicit is not None:
        return explicit
    if not started_at or not completed_at:
        return None
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    return max(0.0, (end - start).total_seconds() / 60.0)


def append_row(root: Path, values: dict[str, Any]) -> None:
    ledger = root / ".ai" / "COST_LEDGER.csv"
    rows = _read(ledger)
    revision = str(values["task_revision"])
    key = (str(values["task_id"]), revision)
    if key in {(row["task_id"], row["task_revision"]) for row in rows}:
        raise SystemExit(f"Duplicate ledger key task_id/task_revision={key}; refusing append")

    def optional(value: Any, digits: int | None = None) -> str:
        if value is None or value == "":
            return ""
        return f"{float(value):.{digits}f}" if digits is not None else str(value)

    started = str(values.get("started_at") or "")
    completed = str(values.get("completed_at") or "")
    calculated = cycle_minutes(started, completed, values.get("cycle_minutes"))
    row: dict[str, Any] = {
        "date": values.get("date", date.today().isoformat()),
        "task_id": values["task_id"],
        "task_revision": revision,
        "success_criterion": values["success_criterion"],
        "delivery_delta": values["delivery_delta"],
        "risk_tier": values["risk_tier"],
        "model": values.get("model") or "UNSPECIFIED",
        "profile": values["profile"],
        "accepted": values.get("accepted", "yes"),
        "started_at": started,
        "first_runnable_at": values.get("first_runnable_at") or "",
        "completed_at": completed,
        "cycle_minutes": optional(calculated, 2),
        "first_pass_accepted": values.get("first_pass_accepted", "unknown"),
        "coordination_input_tokens": optional(values.get("coordination_input_tokens")),
        "implementation_input_tokens": optional(values.get("implementation_input_tokens")),
        "cached_input_tokens": optional(values.get("cached_input_tokens")),
        "output_tokens": optional(values.get("output_tokens")),
        "estimated_ai_cost": optional(values.get("estimated_ai_cost"), 6),
        "currency": str(values.get("currency") or "USD").upper(),
        "provider_cost": optional(values.get("provider_cost"), 6),
        "worker_turns": optional(values.get("worker_turns")),
        "retries": optional(values.get("retries")),
        "focused_test_runs": optional(values.get("focused_test_runs")),
        "integration_test_runs": optional(values.get("integration_test_runs")),
        "full_suite_runs": optional(values.get("full_suite_runs")),
        "provider_runs": optional(values.get("provider_runs")),
        "human_review_minutes": optional(values.get("human_review_minutes")),
        "human_wait_minutes": optional(values.get("human_wait_minutes")),
        "outcome": values["outcome"],
        "later_rework": values.get("later_rework", "unknown"),
        "escaped_defect": values.get("escaped_defect", "unknown"),
        "rollback_required": values.get("rollback_required", "no"),
        "notes": values.get("notes", ""),
    }
    if len(row["currency"]) != 3 or not row["currency"].isalpha():
        raise SystemExit("Currency must be a three-letter code such as USD or VND")
    rows.append({name: str(row.get(name, "")) for name in FIELDS})
    _write(ledger, rows)
    print(f"Appended task_id={row['task_id']} revision={revision} cycle_minutes={row['cycle_minutes']}")


def reconcile_row(root: Path, task_id: str, revision: int, updates: dict[str, str]) -> None:
    ledger = root / ".ai" / "COST_LEDGER.csv"
    rows = _read(ledger)
    matches = [row for row in rows if row["task_id"] == task_id and row["task_revision"] == str(revision)]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one ledger row for {task_id}/r{revision:03d}, found {len(matches)}")
    allowed = {"later_rework", "escaped_defect", "rollback_required", "notes", "human_review_minutes", "human_wait_minutes"}
    unknown = set(updates) - allowed
    if unknown:
        raise SystemExit(f"Unsupported reconcile fields: {sorted(unknown)}")
    for row in rows:
        if row["task_id"] == task_id and row["task_revision"] == str(revision):
            for key, value in updates.items():
                if key == "notes" and row[key] and value:
                    row[key] = f"{row[key]} | reconcile {datetime.now(timezone.utc).isoformat(timespec='seconds')}: {value}"
                else:
                    row[key] = value
    _write(ledger, rows)
    print(f"Reconciled {task_id}/r{revision:03d}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-revision", type=int, required=True)
    parser.add_argument("--success-criterion", required=True)
    parser.add_argument("--delivery-delta", required=True)
    parser.add_argument("--risk-tier", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--completed-at", default=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    args = parser.parse_args()
    values = vars(args).copy()
    root = values.pop("root").resolve()
    with lifecycle_lock(root):
        append_row(root, values)


if __name__ == "__main__":
    main()
