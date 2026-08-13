#!/usr/bin/env python3
"""Risk-floor inference shared by lifecycle, reconciliation and validation."""
from __future__ import annotations

import re
from typing import Iterable, Mapping

RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}


def _meaningful(value: str | None) -> bool:
    return (value or "").strip().upper() not in {"", "NONE", "UNSET", "UNKNOWN", "NOT_APPLICABLE"}


def _contains_any(value: str, needles: Iterable[str]) -> bool:
    lower = value.casefold().replace("\\", "/")
    return any(needle in lower for needle in needles)


def _raise_floor(floor: str, reasons: list[str], risk: str, reason: str) -> str:
    if RISK_ORDER[risk] > RISK_ORDER[floor]:
        floor = risk
    reasons.append(f"{risk}:{reason}")
    return floor


def minimum_risk(
    *,
    data_operation: str = "READ_ONLY",
    artifact_operation: str = "CREATE_NEW_VERSION",
    files_overwritten: str = "NONE",
    data_mutated: str = "NONE",
    external_calls: str = "NONE",
    provider_calls: str = "NONE",
    modify: str = "",
    create: str = "",
) -> tuple[str, list[str]]:
    """Infer a non-downgradable risk floor from declared side effects and surfaces.

    This is the authorization-time floor. Acceptance-time reconciliation must also
    inspect the actual task delta because declarations can be incomplete or wrong.
    """
    floor = "R0"
    reasons: list[str] = []

    data_op = (data_operation or "READ_ONLY").upper()
    artifact_op = (artifact_operation or "CREATE_NEW_VERSION").upper()
    surfaces = f"{modify}\n{create}"

    if data_op in {"MUTATE_IN_PLACE", "DELETE"}:
        floor = _raise_floor(floor, reasons, "R3", f"data operation {data_op}")
    elif data_op == "CREATE_NEW_VERSION":
        floor = _raise_floor(floor, reasons, "R2", "persistent data version creation")

    if artifact_op in {"MUTATE_IN_PLACE", "OVERWRITE", "DELETE"}:
        floor = _raise_floor(floor, reasons, "R3", f"artifact operation {artifact_op}")
    if _meaningful(files_overwritten):
        floor = _raise_floor(floor, reasons, "R3", "files overwritten")
    if _meaningful(data_mutated):
        floor = _raise_floor(floor, reasons, "R3", "data mutated")

    if _meaningful(external_calls):
        if _contains_any(external_calls, ["production", "prod:", "prod ", "payment", "billing", "live system", "customer data"]):
            floor = _raise_floor(floor, reasons, "R3", "production/sensitive external call")
        else:
            floor = _raise_floor(floor, reasons, "R2", "external side effect")

    if _meaningful(provider_calls):
        floor = _raise_floor(floor, reasons, "R1", "provider call")

    normalized = surfaces.casefold().replace("\\", "/")
    if re.search(r"(^|/)(migrations?|terraform|k8s|kubernetes|deploy|deployment|production|prod|billing|payments?)(/|\b)", normalized):
        floor = _raise_floor(floor, reasons, "R3", "migration/deployment/production/payment surface")
    elif re.search(r"(^|/)(api|db|database|schema|auth|security|shared|workers?|queues?|jobs?|persistence)(/|\b)", normalized):
        floor = _raise_floor(floor, reasons, "R2", "shared/API/data/security/background surface")

    return floor, list(dict.fromkeys(reasons))


def _changed_lines(diff_text: str) -> str:
    """Return only added/removed content lines, excluding diff metadata."""
    lines: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---", "@@", "diff --git", "index ")):
            continue
        if line.startswith(("+", "-")):
            lines.append(line[1:])
    return "\n".join(lines)


def minimum_actual_risk(
    *,
    paths: Iterable[str],
    diffs: Mapping[str, str] | None = None,
    project_r2_paths: Iterable[str] = (),
    project_r3_paths: Iterable[str] = (),
    project_sensitive_terms: Iterable[str] = (),
) -> tuple[str, list[str]]:
    """Infer a conservative acceptance-time floor from the actual task delta.

    The classifier intentionally uses only high-signal path/content patterns. It is
    not a full static analyzer; uncertain cases remain warnings/owner policy rather
    than turning every code change into a high-risk task.
    """
    floor = "R0"
    reasons: list[str] = []
    normalized_paths = [p.replace("\\", "/") for p in paths]

    def path_matches(patterns: Iterable[str], path: str) -> bool:
        import fnmatch
        for raw in patterns:
            pattern = raw.strip().replace("\\", "/")
            if not pattern or pattern.upper() in {"NONE", "UNSET"}:
                continue
            if pattern.endswith("/**") and path.startswith(pattern[:-3].rstrip("/") + "/"):
                return True
            if pattern.endswith("/") and path.startswith(pattern):
                return True
            if fnmatch.fnmatch(path, pattern) or path == pattern:
                return True
        return False

    for path in normalized_paths:
        lower = path.casefold()
        if path_matches(project_r3_paths, path):
            floor = _raise_floor(floor, reasons, "R3", f"project R3 surface {path}")
        elif path_matches(project_r2_paths, path):
            floor = _raise_floor(floor, reasons, "R2", f"project R2 surface {path}")

        if re.search(r"(^|/)(migrations?|terraform|k8s|kubernetes|deploy|deployment|production|prod|billing|payments?)(/|\b)", lower):
            floor = _raise_floor(floor, reasons, "R3", f"actual sensitive surface {path}")
        elif re.search(r"(^|/)(api|db|database|schema|auth|security|shared|workers?|queues?|jobs?|persistence)(/|\b)", lower):
            floor = _raise_floor(floor, reasons, "R2", f"actual shared/data/security surface {path}")

    for path, diff_text in (diffs or {}).items():
        changed = _changed_lines(diff_text)
        if not changed:
            continue
        lower = changed.casefold()

        # Persistent/destructive mutations are R3 under the OS data/artifact policy.
        r3_patterns = [
            r"\binsert\s+into\b",
            r"\bupdate\s+[a-z_][\w.]*\s+set\b",
            r"\bdelete\s+from\b",
            r"\b(?:alter|drop|truncate)\s+(?:table|database|schema)\b",
            r"\b(?:session|conn|connection|db)\.commit\s*\(",
            r"\b(?:os\.)?(?:remove|unlink)\s*\(",
            r"\bshutil\.rmtree\s*\(",
            r"\.unlink\s*\(",
        ]
        if any(re.search(pattern, lower, re.IGNORECASE) for pattern in r3_patterns):
            floor = _raise_floor(floor, reasons, "R3", f"actual mutation/destructive code in {path}")
            continue

        # Shared boundaries, persistence access and side-effecting network calls are R2.
        r2_patterns = [
            r"\bsqlite3\.connect\s*\(",
            r"\b(?:psycopg2?|pymysql|mysql\.connector)\.connect\s*\(",
            r"\bcreate_engine\s*\(",
            r"\b(?:requests|httpx|axios)\.(?:post|put|patch|delete)\s*\(",
            r"\bfetch\s*\([^\n]{0,240}\bmethod\s*:\s*['\"](?:post|put|patch|delete)['\"]",
            r"@(?:app|router)\.(?:route|get|post|put|patch|delete)\s*\(",
            r"\b(?:enqueue|apply_async|delay)\s*\(",
        ]
        if any(re.search(pattern, lower, re.IGNORECASE) for pattern in r2_patterns):
            floor = _raise_floor(floor, reasons, "R2", f"actual shared/persistence/external code in {path}")

        # Project-specific business terms are opt-in because generic words such as
        # price/amount/quantity are too noisy across domains. Exact configured terms
        # raise the acceptance-time floor to R2 when they appear in changed lines.
        for raw_term in project_sensitive_terms:
            term = raw_term.strip().casefold()
            if not term or term.upper() in {"NONE", "UNSET"}:
                continue
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lower):
                floor = _raise_floor(floor, reasons, "R2", f"project sensitive business term {raw_term.strip()} in {path}")

    return floor, list(dict.fromkeys(reasons))



def semantic_uncertainty(root, *, paths: Iterable[str], diffs: Mapping[str, str] | None = None) -> tuple[bool, list[str]]:
    """Detect high-signal side-effect calls not covered by the known classifier.

    This deliberately models UNKNOWN as a first-class state. The project config may
    choose warn-only or fail-closed R2 escalation; defaults to fail-closed.
    """
    import fnmatch, json
    from pathlib import Path
    cfg_path=Path(root)/"config/risk_semantics.json"
    try: cfg=json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    except json.JSONDecodeError: cfg={}
    patterns=cfg.get("uncertain_side_effect_patterns") or []
    ignores=cfg.get("ignore_path_patterns") or []
    reasons=[]
    for path,diff_text in (diffs or {}).items():
        norm=path.replace("\\","/")
        if any(fnmatch.fnmatch(norm,pat) for pat in ignores): continue
        changed=_changed_lines(diff_text)
        for raw in patterns:
            try: matched=re.search(raw,changed,re.IGNORECASE)
            except re.error: matched=None
            if matched:
                reasons.append(f"semantic side effect uncertain in {path}: {matched.group(0)[:80]}")
                break
    return bool(reasons), reasons

def effective_risk(declared: str, **kwargs: str) -> tuple[str, str, list[str]]:
    """Return (effective, floor, reasons). `auto` behaves like an R0 declaration."""
    declared_norm = (declared or "auto").upper()
    base = "R0" if declared_norm == "AUTO" else declared_norm
    if base not in RISK_ORDER:
        raise ValueError(f"Unknown risk: {declared}")
    floor, reasons = minimum_risk(**kwargs)
    effective = base if RISK_ORDER[base] >= RISK_ORDER[floor] else floor
    return effective, floor, reasons
