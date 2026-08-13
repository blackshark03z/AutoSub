#!/usr/bin/env python3
"""Single source of truth for risk-tier quality gates."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = Path("config/gates.json")


def load_policy(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    path = root / POLICY_REL
    data = json.loads(path.read_text(encoding="utf-8"))
    for risk in ("R0", "R1", "R2", "R3"):
        if risk not in data:
            raise ValueError(f"Missing gate policy for {risk}")
    return data


def gate(root: Path, risk: str, name: str, default: str = "none") -> str:
    return str(load_policy(root).get(risk, {}).get(name, default))


def render_quality_gates(root: Path = DEFAULT_ROOT) -> str:
    p = load_policy(root)
    lines = ["# Quality Gates", "", "> Generated from `config/gates.json`. Edit the machine-readable policy, not this file.", ""]
    descriptions = {
        "R0": "Docs, read-only analysis, rename, format, metadata; cheapest relevant verification.",
        "R1": "Pure/local behavior and isolated bug fixes; negative verification only when a meaningful failure path exists.",
        "R2": "Shared/API/persistence/integration work; frozen Goal acceptance when Goal-linked; review only on elevated triggers.",
        "R3": "Production/destructive/payment/migration/deploy/critical paths; externally attested review required.",
    }
    labels = {
        "focused":"Focused check", "negative":"Negative/failure path", "integration":"Affected integration",
        "acceptance_contract":"Frozen acceptance", "rollback":"Rollback/recovery", "full_suite":"Broader/full suite", "review":"Review",
    }
    for risk in ("R0", "R1", "R2", "R3"):
        cfg = p[risk]
        lines += [f"## {risk} — {cfg['profile']}", "", descriptions[risk], "", "| Gate | Policy |", "|---|---|"]
        for key in ("focused","negative","integration","acceptance_contract","rollback","full_suite","review"):
            lines.append(f"| {labels[key]} | `{cfg.get(key,'none')}` |")
        lines += [""]
    lines += [
        "## Deterministic close gate", "",
        "`COMPLETED` requires a released lease, valid task-scoped evidence bound to the verified snapshot, accepted outcome/history, and synchronized state.", "",
        "## Visual/media/artifact gate", "",
        "Representative output must be opened/inspected when the artifact can be meaningfully inspected; risk-proportional evidence still applies.", "",
        "## Milestone gate", "",
        "A milestone closes only when its observable success criterion has demo/acceptance evidence; test count or document count alone is insufficient.", "",
    ]
    return "\n".join(lines)


def sync_quality_gates(root: Path = DEFAULT_ROOT) -> None:
    (root / ".ai" / "QUALITY_GATES.md").write_text(render_quality_gates(root), encoding="utf-8")
