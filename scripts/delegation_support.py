#!/usr/bin/env python3
"""Cost-aware delegation heuristics for Senior AI Build OS v1.13.

The planner is intentionally conservative. It may recommend or auto-insert a
read-only Scout only when discovery uncertainty is high enough to justify the
bootstrap cost. It never auto-adds generic specialist personas and never treats
parallelism as a token-saving mechanism.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from telemetry_support import delegation_feedback

RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
READ_ONLY_ROLES = {"SCOUT", "REVIEWER"}
UNCERTAINTY_TERMS = (
    "investigate", "diagnose", "debug", "root cause", "unknown", "flaky",
    "intermittent", "trace", "failing", "failure", "regression", "crash",
    "bug", "issue", "why ", "find where", "locate", "reproduce",
    "điều tra", "chẩn đoán", "gỡ lỗi", "nguyên nhân", "không rõ", "lỗi",
)
BROAD_ROOTS = {
    "src", "app", "server", "services", "packages", "backend", "frontend",
    "lib", "core", "modules", "components", "api", "web", "internal",
}


def _scope_parts(value: str) -> list[str]:
    value = (value or "").strip()
    if not value or value.upper() == "NONE":
        return []
    parts = [part.strip().replace("\\", "/") for part in re.split(r"[,;\n]+", value) if part.strip()]
    if len(parts) == 1 and " " in parts[0] and not any(ch in parts[0] for ch in "'\""):
        maybe = [p.strip() for p in parts[0].split() if p.strip()]
        if len(maybe) > 1 and all("/" in p or p.startswith(".") for p in maybe):
            parts = maybe
    return parts


def _static_prefix(pattern: str) -> str:
    pattern = pattern.strip().replace("\\", "/").lstrip("./")
    if not pattern:
        return ""
    special = len(pattern)
    for token in ("*", "?", "["):
        pos = pattern.find(token)
        if pos >= 0:
            special = min(special, pos)
    prefix = pattern[:special].rstrip("/")
    return prefix


def scope_features(value: str) -> dict[str, Any]:
    parts = _scope_parts(value)
    prefixes = [_static_prefix(part) for part in parts]
    top_roots = {prefix.split("/", 1)[0] for prefix in prefixes if prefix}
    explicit_files = [
        part for part in parts
        if not any(token in part for token in ("*", "?", "["))
        and "/" in part and "." in part.rsplit("/", 1)[-1]
    ]
    broad_patterns = []
    for part, prefix in zip(parts, prefixes):
        root = prefix.split("/", 1)[0] if prefix else ""
        if "**" in part or part in {"*", "**", ".", "./"} or (
            root in BROAD_ROOTS and (prefix == root or part.rstrip("/").endswith("/**"))
        ):
            broad_patterns.append(part)
    return {
        "parts": parts,
        "prefixes": prefixes,
        "top_roots": sorted(top_roots),
        "explicit_file_count": len(explicit_files),
        "has_wildcard": any(any(token in part for token in ("*", "?", "[")) for part in parts),
        "broad": bool(broad_patterns or len(top_roots) >= 3),
        "broad_patterns": broad_patterns,
    }


def scopes_overlap(left: str, right: str) -> bool:
    """Conservative overlap check for declared write scopes.

    False means the declared prefixes are clearly disjoint. True includes
    uncertainty, which deliberately prevents unsafe parallel writer advice.
    """
    a = scope_features(left)["prefixes"]
    b = scope_features(right)["prefixes"]
    if not a or not b:
        return True
    for x in a:
        for y in b:
            if not x or not y:
                return True
            if x == y or x.startswith(y.rstrip("/") + "/") or y.startswith(x.rstrip("/") + "/"):
                return True
    return False


def _has_scout_dependency(goal: dict[str, Any], node: dict[str, Any]) -> bool:
    tasks = goal.get("tasks", {}) or {}
    for dep in node.get("depends_on", []) or []:
        if (tasks.get(dep) or {}).get("agent_role") == "SCOUT":
            return True
    return False


def _uncertainty_score(node: dict[str, Any]) -> tuple[int, list[str]]:
    text = " ".join([
        str(node.get("outcome") or ""),
        " ".join(str(x) for x in (node.get("acceptance") or [])),
    ]).casefold()
    reasons: list[str] = []
    score = 0
    matches = [term for term in UNCERTAINTY_TERMS if term in text]
    if matches:
        score += 2
        reasons.append("outcome indicates diagnosis/root-cause uncertainty")
    features = scope_features(str(node.get("modify") or ""))
    if features["broad"]:
        score += 2
        reasons.append("declared write scope is broad")
    elif features["has_wildcard"]:
        score += 1
        reasons.append("declared write scope uses wildcard discovery")
    if len(features["top_roots"]) >= 3:
        score += 1
        reasons.append("scope spans multiple top-level surfaces")
    risk = str(node.get("risk") or "auto").upper()
    if risk in {"R2", "R3"} and features["broad"]:
        score += 1
        reasons.append(f"{risk} work benefits from reducing expensive Worker discovery")
    return score, reasons


def work_class(node: dict[str, Any]) -> str:
    text=(str(node.get("outcome") or "")+" "+str(node.get("modify") or "")).casefold()
    if any(x in text for x in ("bug","debug","diagnose","root cause","lỗi","regression")): kind="BUG_UNKNOWN"
    elif any(x in text for x in ("auth","security","payment","billing","migration")): kind="SENSITIVE"
    else: kind="BOUNDED_CHANGE"
    feat=scope_features(str(node.get("modify") or ""))
    return f"{kind}:{'BROAD' if feat['broad'] else 'NARROW'}"

def recommend_node(goal: dict[str, Any], node: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    role = str(node.get("agent_role") or "WORKER").upper()
    if role in READ_ONLY_ROLES:
        return {
            "action": f"SPAWN_{role}",
            "hard": True,
            "model_class": "CHEAP_FAST_READ_ONLY" if role == "SCOUT" else "FRESH_REVIEW_CONTEXT",
            "summary_token_budget": 350 if role == "SCOUT" else 500,
            "reasons": ["node is explicitly read-only"],
        }

    policy = str(node.get("delegation_policy") or "auto").casefold()
    features = scope_features(str(node.get("modify") or ""))
    risk = str(node.get("risk") or "auto").upper()
    has_scout = _has_scout_dependency(goal, node)

    if policy == "main":
        return {
            "action": "MAIN_WORKER",
            "hard": True,
            "model_class": "STRONG_WORKER",
            "summary_token_budget": None,
            "reasons": ["delegation policy explicitly disables pre-scouting"],
        }
    if has_scout:
        return {
            "action": "WORKER_AFTER_SCOUT",
            "hard": True,
            "model_class": "STRONG_WORKER",
            "summary_token_budget": None,
            "reasons": ["read-only Scout dependency already isolates discovery context"],
        }

    uncertainty, reasons = _uncertainty_score(node)
    feedback = delegation_feedback(root, work_class(node)) if root is not None else {'status':'UNAVAILABLE'}
    if feedback.get('status') == 'OBSERVED' and feedback.get('verdict') == 'NEGATIVE' and policy == 'auto':
        uncertainty = max(0, uncertainty - 2)
        reasons.append('historical telemetry shows Scout did not reduce strong-Worker input for this work class')
    elif feedback.get('status') == 'OBSERVED' and feedback.get('verdict') == 'POSITIVE':
        uncertainty += 1
        reasons.append('historical telemetry shows >=30% strong-Worker input reduction with Scout for this work class')
    small_explicit = (
        features["explicit_file_count"] in {1, 2}
        and not features["has_wildcard"]
        and len(features["parts"]) <= 2
        and risk in {"AUTO", "R0", "R1"}
    )
    if small_explicit and policy != "scout":
        return {
            "action": "MAIN_WORKER",
            "hard": True,
            "model_class": "STRONG_WORKER",
            "summary_token_budget": None,
            "reasons": ["small explicit R0/R1 scope; Scout bootstrap likely costs more than discovery"],
        }

    if policy == "scout" or uncertainty >= 4:
        if policy == "scout":
            reasons = ["delegation policy explicitly requests pre-scouting"] + reasons
        return {
            "action": "SCOUT_FIRST",
            "hard": True,
            "model_class": "CHEAP_FAST_READ_ONLY",
            "summary_token_budget": 350,
            "reasons": reasons or ["discovery isolation explicitly requested"],
            "historical_telemetry": feedback,
            "work_class": work_class(node),
        }
    if uncertainty >= 2:
        return {
            "action": "SCOUT_OPTIONAL",
            "hard": False,
            "model_class": "CHEAP_FAST_READ_ONLY",
            "summary_token_budget": 350,
            "reasons": reasons,
            "historical_telemetry": feedback,
            "work_class": work_class(node),
        }
    return {
        "action": "MAIN_WORKER",
        "hard": True,
        "model_class": "STRONG_WORKER",
        "summary_token_budget": None,
        "reasons": ["no high-confidence delegation benefit detected"],
        "historical_telemetry": feedback,
        "work_class": work_class(node),
    }



SENSITIVE_INTERFACE_TERMS = ('schema', 'contract', 'protocol', 'interface', 'migration', 'auth', 'payment', 'billing', 'api/')

def _semantic_parallel_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    material = ' '.join([
        str(left.get('modify') or ''), str(left.get('create') or ''), str(left.get('outcome') or ''),
        str(right.get('modify') or ''), str(right.get('create') or ''), str(right.get('outcome') or ''),
    ]).casefold()
    # Conservative: if both writers touch/describe the same sensitive contract family, keep sequential.
    for term in SENSITIVE_INTERFACE_TERMS:
        l = ' '.join([str(left.get('modify') or ''), str(left.get('create') or ''), str(left.get('outcome') or '')]).casefold()
        r = ' '.join([str(right.get('modify') or ''), str(right.get('create') or ''), str(right.get('outcome') or '')]).casefold()
        if term in l and term in r:
            return True
    return False

def select_parallel_writers(writers: list[dict[str, Any]], max_parallel: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Greedily select clearly disjoint writers; keep uncertain/overlapping work sequential."""
    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for node in writers:
        if len(selected) >= max_parallel:
            held.append(node)
            continue
        if all(
            not _semantic_parallel_conflict(node, other)
            and not scopes_overlap(str(node.get("modify") or "NONE"), str(other.get("modify") or "NONE"))
            and not scopes_overlap(str(node.get("create") or "NONE"), str(other.get("create") or "NONE"))
            and not scopes_overlap(str(node.get("modify") or "NONE"), str(other.get("create") or "NONE"))
            and not scopes_overlap(str(node.get("create") or "NONE"), str(other.get("modify") or "NONE"))
            for other in selected
        ):
            selected.append(node)
        else:
            held.append(node)
    return selected, held


def build_wave_delegation(goal: dict[str, Any], ready: list[dict[str, Any]], selected_writers: list[dict[str, Any]], held_writers: list[dict[str, Any]], root: Path | None = None) -> dict[str, Any]:
    recommendations = []
    for node in ready:
        rec = recommend_node(goal, node, root=root)
        recommendations.append({
            "node_id": node.get("node_id"),
            "agent_role": node.get("agent_role"),
            **rec,
        })
    parallel = []
    if len(selected_writers) > 1:
        parallel.append({
            "action": "PARALLEL_WORKERS",
            "nodes": [node.get("node_id") for node in selected_writers],
            "hard": False,
            "requires_isolated_worktrees": True,
            "reason": "ready writers have clearly disjoint declared write scopes; parallelism targets wall-clock, not token reduction",
        })
    # Even with the safe default max_parallel=1, surface a speed opportunity so
    # an outer environment with isolated worktrees can opt in without the owner
    # manually discovering independent branches. It remains advisory because
    # parallel writer bootstrap often increases total token usage.
    all_writers = [node for node in ready if node.get('agent_role') == 'WORKER']
    opportunity, _ = select_parallel_writers(all_writers, 2)
    parallel_opportunity = None
    if len(opportunity) > 1 and int((goal.get('budget') or {}).get('max_parallel', 1) or 1) < 2:
        parallel_opportunity = {
            'action': 'ENABLE_PARALLEL_IF_WORKTREES_AVAILABLE',
            'nodes': [node.get('node_id') for node in opportunity],
            'hard': False,
            'requires_isolated_worktrees': True,
            'cost_note': 'wall-clock may improve; total token usage may increase from duplicated bootstrap context',
        }
    return {
        "policy": "cost_aware_conservative",
        "recommendations": recommendations,
        "parallel_groups": parallel,
        "parallel_opportunity": parallel_opportunity,
        "held_sequential": [node.get("node_id") for node in held_writers],
        "guardrails": {
            "auto_scout_only_high_confidence": True,
            "small_r0_r1_stays_single_worker": True,
            "read_only_summary_token_budget": int((goal.get("budget") or {}).get("scout_summary_token_budget", 350) or 350),
            "parallelism_is_not_assumed_to_reduce_tokens": True,
        },
    }
