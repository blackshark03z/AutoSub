#!/usr/bin/env python3
"""Lightweight state/temporal hazard classification, proof reuse and fast-debug helpers.

Design goals:
- S0/stateless work pays effectively zero ceremony.
- S1 only records a state signal; no extra acceptance command is required.
- S2+ requires a tiny authority/transition/invariant contract before implementation.
- S2 transition and S3+ temporal proofs are reusable until the declared contract/dependency fingerprint changes.
- Debug records are bounded failure signatures, not general application tracing.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_support import application_file_fingerprint, atomic_write_json, sha256_file, sha256_text

LEVELS = {"S0": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4}
CACHE_DIR = Path('.ai/evidence/_state_cache')
FAILURE_DIR = Path('.ai/runtime/state_failures')

# High-signal categories only. Generic words such as "state" or "save" alone do not
# escalate above S1; this keeps ordinary CRUD/UI work light.
CATEGORY_PATTERNS: dict[str, tuple[str, ...]] = {
    'persistence': (r'\bpersist(?:ed|ence|ing)?\b', r'\bdatabase\b', r'\bstorage\b', r'\bsave\b', r'\bcommit\b'),
    'draft': (r'\bdraft\b', r'\bdirty\b', r'\bedit(?:ing|able)?\b', r'\bunsaved\b'),
    'background': (r'\bpoll(?:ing)?\b', r'\bbackground\s+refresh\b', r'\brefresh\b', r'\bsync\b', r'\bwebsocket\b'),
    'async': (r'\basync\b', r'\bawait\b', r'\brace\b', r'\bconcurren', r'\bdebounc', r'\bthrottl'),
    'hydration': (r'\bhydrat', r'\breconcile', r'\breload\b', r'\brestore\b'),
    'cache': (r'\bcache\b', r'\bquery\s+cache\b', r'\bmemoiz'),
    'identity': (r'\bproject\s+(?:id|identity|switch)', r'\bsession\s+(?:id|identity|switch)', r'\btenant\b', r'\bworkspace\s+switch'),
    'optimistic': (r'\boptimistic\b', r'\brollback\s+ui\b'),
    'multi_writer': (r'\bmultiple\s+writers?\b', r'\bcompeting\s+writers?\b', r'\boverwrite\b', r'\blast[- ]write'),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _categories(text: str, explicit_signals: list[str] | None = None) -> set[str]:
    source = (text or '').casefold()
    cats: set[str] = set()
    for category, patterns in CATEGORY_PATTERNS.items():
        if any(re.search(pattern, source, re.IGNORECASE) for pattern in patterns):
            cats.add(category)
    for signal in explicit_signals or []:
        normalized = signal.strip().casefold().replace('-', '_').replace(' ', '_')
        if normalized in CATEGORY_PATTERNS:
            cats.add(normalized)
        elif normalized in {'multiple_writers', 'competing_writer', 'competing_writers'}:
            cats.add('multi_writer')
        elif normalized in {'polling', 'background_refresh'}:
            cats.add('background')
        elif normalized in {'persisted', 'persistence'}:
            cats.add('persistence')
        elif normalized in {'draft', 'dirty'}:
            cats.add('draft')
    return cats


def detect_level(*, requested: str = 'auto', outcome: str = '', modify: str = '', create: str = '', signals: list[str] | None = None) -> tuple[str, list[str]]:
    requested = (requested or 'auto').upper()
    if requested != 'AUTO':
        if requested not in LEVELS:
            raise SystemExit(f'Invalid state hazard level: {requested}')
        return requested, [f'explicit:{requested}'] if requested != 'S0' else []
    cats = _categories(' '.join([outcome, modify, create, ' '.join(signals or [])]), signals)
    if not cats:
        return 'S0', []
    # Strong competing-writer patterns: foreground draft plus a background/hydration writer,
    # or an explicit multi-writer/race signal. These are the bugs that benefit most from temporal proof.
    if 'multi_writer' in cats or ('draft' in cats and bool(cats & {'background', 'hydration', 'cache', 'optimistic'})) or ('async' in cats and 'persistence' in cats and 'draft' in cats):
        return 'S3', sorted(cats)
    # Two independent state mechanisms merit transition proof but not soak/temporal proof.
    meaningful = cats & {'persistence', 'draft', 'background', 'async', 'hydration', 'cache', 'identity', 'optimistic'}
    if len(meaningful) >= 2 or bool(cats & {'background', 'hydration', 'optimistic'}):
        return 'S2', sorted(cats)
    return 'S1', sorted(cats)


def build_contract(*, level: str, authority: str = '', transitions: list[str] | None = None, invariants: list[str] | None = None,
                   dependencies: list[str] | None = None, signals: list[str] | None = None) -> dict[str, Any]:
    level = level.upper()
    contract = {
        'schema_version': 1,
        'level': level,
        'authority': authority.strip(),
        'transitions': [x.strip() for x in (transitions or []) if x.strip()],
        'invariants': [x.strip() for x in (invariants or []) if x.strip()],
        'dependencies': [x.strip().replace('\\', '/') for x in (dependencies or []) if x.strip()],
        'signals': [x.strip() for x in (signals or []) if x.strip()],
    }
    if LEVELS[level] >= 2:
        missing = []
        if len(contract['authority']) < 3: missing.append('--state-authority')
        if not contract['transitions']: missing.append('--state-transition')
        if not contract['invariants']: missing.append('--state-invariant')
        if missing:
            raise SystemExit(
                f'{level} state hazard requires a minimal pre-code state contract: missing {", ".join(missing)}. '
                'Keep it short: authority, one representative transition, one overwrite/reconciliation invariant.'
            )
    canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    contract['contract_sha256'] = sha256_text(canonical)
    return contract


def parse_contract(raw: str) -> dict[str, Any]:
    if not raw or raw.upper() in {'NONE', 'UNSET'}:
        return {'schema_version': 1, 'level': 'S0', 'authority': '', 'transitions': [], 'invariants': [], 'dependencies': [], 'signals': [], 'contract_sha256': 'NONE'}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f'Invalid State Contract JSON: {exc}') from exc
    if not isinstance(value, dict):
        raise SystemExit('State Contract JSON must be an object')
    claimed = str(value.get('contract_sha256') or '')
    if claimed and claimed not in {'NONE', 'UNSET'}:
        payload = dict(value); payload.pop('contract_sha256', None)
        calculated = sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        if claimed != calculated:
            raise SystemExit('State contract hash mismatch; the pre-code contract was modified')
    level = str(value.get('level') or 'S0').upper()
    if level not in LEVELS:
        raise SystemExit(f'Invalid state contract level: {level}')
    if LEVELS[level] >= 2 and (not str(value.get('authority') or '').strip() or not (value.get('transitions') or []) or not (value.get('invariants') or [])):
        raise SystemExit(f'{level} state contract is incomplete')
    return value


def _candidate_files(root: Path, patterns: list[str]) -> list[str]:
    files: set[str] = set()
    if not patterns:
        return []
    # Walk once; ignore OS bookkeeping and VCS metadata.
    for path in root.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith('.ai/') or rel.startswith('.git/'):
            continue
        if any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.rstrip('/**')) for pattern in patterns):
            files.add(rel)
    return sorted(files)


def dependency_fingerprint(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    patterns = [str(x) for x in contract.get('dependencies', []) if str(x).strip()]
    files = _candidate_files(root, patterns)
    entries = {rel: application_file_fingerprint(root, rel) for rel in files}
    missing_patterns = [pattern for pattern in patterns if not any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.rstrip('/**')) for rel in files)]
    payload = {'patterns': patterns, 'files': entries, 'unmatched_patterns': missing_patterns}
    payload['sha256'] = sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    return payload


def proof_key(root: Path, contract: dict[str, Any], kind: str, command: str) -> tuple[str, dict[str, Any]]:
    dep = dependency_fingerprint(root, contract)
    payload = {
        'schema_version': 1,
        'contract_sha256': contract.get('contract_sha256'),
        'kind': kind,
        'command': command.strip(),
        'dependency_sha256': dep['sha256'],
    }
    return sha256_text(json.dumps(payload, sort_keys=True, separators=(',', ':'))), dep


def reusable_proof(root: Path, contract: dict[str, Any], kind: str, command: str) -> dict[str, Any] | None:
    key, dep = proof_key(root, contract, kind, command)
    path = root / CACHE_DIR / f'{key}.json'
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if value.get('proof_key') != key or value.get('dependency_sha256') != dep['sha256']:
        return None
    source = root / str(value.get('source_manifest') or '')
    if not source.is_file():
        return None
    try:
        manifest = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if manifest.get('final_verdict') != 'PASS':
        return None
    if sha256_file(source) != value.get('source_manifest_sha256'):
        return None
    return value


def cache_proof(root: Path, *, contract: dict[str, Any], kind: str, command: str, source_manifest: Path, source_check_id: str) -> dict[str, Any]:
    key, dep = proof_key(root, contract, kind, command)
    relative_manifest = source_manifest.relative_to(root).as_posix()
    value = {
        'schema_version': 1,
        'proof_key': key,
        'kind': kind,
        'command': command,
        'contract_sha256': contract.get('contract_sha256'),
        'dependency_sha256': dep['sha256'],
        'dependency_fingerprint': dep,
        'source_manifest': relative_manifest,
        'source_manifest_sha256': sha256_file(source_manifest),
        'source_check_id': source_check_id,
        'verified_at': now(),
    }
    atomic_write_json(root / CACHE_DIR / f'{key}.json', value)
    return value


def record_failure_signature(root: Path, *, task_id: str, revision: int, state: str, event: str, expected: str, observed: str,
                             hazard_class: str = 'UNKNOWN', suspects: list[str] | None = None) -> Path:
    # Bounded, structured debug memory. No raw application state/log capture.
    safe_id = re.sub(r'[^A-Za-z0-9._-]+', '_', task_id)[:96] or 'TASK'
    payload = {
        'schema_version': 1,
        'task_id': task_id,
        'task_revision': int(revision),
        'recorded_at': now(),
        'state': state[:240],
        'event': event[:240],
        'expected': expected[:500],
        'observed': observed[:500],
        'hazard_class': hazard_class[:80],
        'suspects': [x[:240] for x in (suspects or [])[:8]],
    }
    path = root / FAILURE_DIR / f'{safe_id}-r{revision:03d}.json'
    atomic_write_json(path, payload)
    return path
