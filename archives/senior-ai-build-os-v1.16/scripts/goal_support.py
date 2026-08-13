#!/usr/bin/env python3
"""Goal orchestration support for Senior AI Build OS v1.16.

The goal layer is deliberately thin: task execution remains owned by ai_os.py's
risk/scope/evidence kernel. Goal state only plans, dispatches and aggregates.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_support import atomic_write_json, atomic_write_text, sha256_file, sha256_text, validate_identifier, confined_child
from delegation_support import build_wave_delegation, recommend_node, select_parallel_writers
from health_support import snapshot as health_snapshot, compare as health_compare, save_snapshot as health_save_snapshot

GOAL_STATE = Path('.ai/GOAL_STATE.json')
GOAL_MD = Path('.ai/GOAL.md')
GOALS_DIR = Path('.ai/goals')
GOAL_STATUSES = {'ACTIVE', 'BLOCKED', 'COMPLETED', 'ABORTED'}
NODE_STATUSES = {'PLANNED', 'ACTIVE', 'DONE', 'BLOCKED', 'DEFERRED'}
SHIPPING_DELTAS = {'USER_VISIBLE_BEHAVIOR', 'EXECUTABLE_CAPABILITY'}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def empty_goal() -> dict[str, Any]:
    return {
        'schema_version': 1,
        'goal_id': 'NONE',
        'status': 'NONE',
        'goal_type': 'product',
        'outcome': '',
        'acceptance': [],
        'acceptance_quality_warnings': [],
        'non_goals': [],
        'risk_ceiling': 'R2',
        'budget': {
            'max_tasks': 8,
            'max_parallel': 1,
            'max_non_shipping_tasks': 2,
            'max_revisions_per_task': 2,
            'scope_growth_limit_percent': 30,
            'max_auto_scouts': 2,
            'scout_summary_token_budget': 350,
            'scout_input_token_budget': 24000,
            'scout_wall_minutes_budget': 5.0,
            'scout_provider_cost_budget': 0.0,
        },
        'created_at': None,
        'updated_at': None,
        'tasks': {},
        'discoveries': [],
        'decisions_required': [],
        'completed_non_shipping_streak': 0,
        'last_task_id': None,
        'acceptance_attempts': 0,
        'goal_acceptance_contract': {
            'schema_version': 1, 'mappings': [], 'probe_hashes': {},
            'contract_sha256': '', 'frozen_at': None, 'revision': 1,
        },
    }


def ensure_goal_files(root: Path) -> None:
    state_path = root / GOAL_STATE
    md_path = root / GOAL_MD
    if not state_path.exists():
        atomic_write_json(state_path, empty_goal())
    if not md_path.exists():
        atomic_write_text(md_path, render_goal(load_goal(root)))
    (root / GOALS_DIR).mkdir(parents=True, exist_ok=True)


def load_goal(root: Path) -> dict[str, Any]:
    path = root / GOAL_STATE
    if not path.exists():
        return empty_goal()
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise SystemExit(f'Invalid .ai/GOAL_STATE.json: {exc}')
    if not isinstance(value, dict):
        raise SystemExit('.ai/GOAL_STATE.json must be a JSON object')
    return value


def save_goal(root: Path, goal: dict[str, Any]) -> None:
    goal['updated_at'] = now()
    atomic_write_json(root / GOAL_STATE, goal)
    atomic_write_text(root / GOAL_MD, render_goal(goal))
    goal_id = str(goal.get('goal_id') or 'NONE')
    if goal_id != 'NONE':
        goal_dir = confined_child(root, GOALS_DIR, goal_id, 'goal ID')
        goal_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(goal_dir / 'plan.json', goal)


def render_goal(goal: dict[str, Any]) -> str:
    tasks = goal.get('tasks', {}) or {}
    acceptance = '\n'.join(f'- [ ] {item}' for item in goal.get('acceptance', [])) or '- [ ] UNSET'
    non_goals = '\n'.join(f'- {item}' for item in goal.get('non_goals', [])) or '- NONE'
    acceptance_quality = '\n'.join(f'- WARNING: {item}' for item in goal.get('acceptance_quality_warnings', [])) or '- Falsifiability heuristic: no high-confidence warnings'
    task_lines: list[str] = []
    for node_id, node in tasks.items():
        deps = ','.join(node.get('depends_on', [])) or '-'
        task_lines.append(
            f"| {node_id} | {node.get('status','PLANNED')} | {node.get('agent_role','WORKER')} | "
            f"{node.get('risk','auto')} | {node.get('delivery_delta','NO_DELTA')} | {deps} | {node.get('outcome','')} |"
        )
    if not task_lines:
        task_lines = ['| - | - | - | - | - | - | No planned tasks yet |']
    budget = goal.get('budget', {}) or {}
    goal_contract = goal.get('goal_acceptance_contract', {}) or {}
    contract_status = 'FROZEN ' + str(goal_contract.get('contract_sha256',''))[:12] if goal_contract.get('frozen_at') else 'UNFROZEN'
    return f"""# Active Goal\n\nGoal ID: {goal.get('goal_id','NONE')}\nGoal Status: {goal.get('status','NONE')}\nGoal Type: {goal.get('goal_type','product')}\nRisk Ceiling: {goal.get('risk_ceiling','R2')}\nUpdated: {goal.get('updated_at') or goal.get('created_at') or 'NONE'}\n\n## Outcome\n\n{goal.get('outcome') or 'NONE'}\n\n## Acceptance\n\n{acceptance}\n\n## Acceptance Quality\n\n{acceptance_quality}\n\n## Goal Acceptance Contract\n\n- Status: {contract_status}\n- Criterion mappings: {len(goal_contract.get('mappings', []) or [])}/{len(goal.get('acceptance', []) or [])}\n\n## Non-Goals\n\n{non_goals}\n\n## Budget\n\n- Maximum tasks: {budget.get('max_tasks',8)}\n- Maximum parallel writers: {budget.get('max_parallel',1)}\n- Maximum consecutive non-shipping tasks: {budget.get('max_non_shipping_tasks',2)}\n- Maximum revisions per task before stop-loss: {budget.get('max_revisions_per_task',2)}\n- Scope growth limit: {budget.get('scope_growth_limit_percent',30)}%\n- Scout input budget: {budget.get('scout_input_token_budget',24000)} tokens\n- Scout wall budget: {budget.get('scout_wall_minutes_budget',5.0)} minutes\n- Scout provider-cost budget: {budget.get('scout_provider_cost_budget',0.0)} (0 = unbounded/unavailable)\n\n## Task Graph\n\n| Node | Status | Agent | Risk | Delivery Delta | Depends On | Outcome |\n|---|---|---|---|---|---|---|\n{chr(10).join(task_lines)}\n\n## Human Interrupt Policy\n\nOnly interrupt the owner for a genuine product decision, risk above ceiling/authorization, destructive/production authority, unresolved blocker, or final Goal acceptance. Worker reports are machine-to-machine state, not owner handoffs.\n"""


def next_goal_id(root: Path) -> str:
    highest = 0
    for path in (root / GOALS_DIR).glob('G-*'):
        match = re.fullmatch(r'G-(\d+)', path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    current = load_goal(root).get('goal_id', 'NONE') if (root / GOAL_STATE).exists() else 'NONE'
    match = re.fullmatch(r'G-(\d+)', str(current))
    if match:
        highest = max(highest, int(match.group(1)))
    return f'G-{highest + 1:03d}'



def acceptance_quality_warnings(criteria: list[str]) -> list[str]:
    """Emit only high-confidence spec warnings; never add fixed ceremony for clear small work."""
    warnings: list[str] = []
    vague = re.compile(r'^(?:it\s+)?(?:works?(?:\s+correctly)?|done|successful|success|correct|looks?\s+good|no\s+bugs?)\.?$', re.IGNORECASE)
    for item in criteria:
        text = item.strip()
        if len(text.split()) < 3 or vague.fullmatch(text):
            warnings.append(f'Acceptance may not be falsifiable: {text!r}')
        elif re.search(r'\bworks?\s+correctly\b', text, re.IGNORECASE) and not re.search(r'\b(?:given|when|then|returns?|creates?|shows?|rejects?|persists?|emits?|equals?|status|count|without|does not)\b', text, re.IGNORECASE):
            warnings.append(f'Acceptance uses vague correctness language: {text!r}')
    return warnings

def begin_goal(root: Path, *, goal_id: str | None, outcome: str, acceptance: list[str], non_goals: list[str],
               goal_type: str, risk_ceiling: str, max_tasks: int, max_parallel: int,
               max_non_shipping: int, max_revisions: int, scope_growth_limit: int,
               max_auto_scouts: int = 2, scout_summary_token_budget: int = 350,
               scout_input_token_budget: int = 24000, scout_wall_minutes_budget: float = 5.0,
               scout_provider_cost_budget: float = 0.0) -> dict[str, Any]:
    ensure_goal_files(root)
    current = load_goal(root)
    if current.get('status') in {'ACTIVE', 'BLOCKED'}:
        raise SystemExit(f"Refusing to replace live goal {current.get('goal_id')}; complete/abort it first")
    if not acceptance:
        raise SystemExit('goal begin requires at least one --accept criterion')
    gid = validate_identifier(goal_id or next_goal_id(root), 'goal ID')
    existing_goal_dir = confined_child(root, GOALS_DIR, gid, 'goal ID')
    if existing_goal_dir.exists() and any(existing_goal_dir.iterdir()):
        raise SystemExit(f'Goal ID already has history and cannot be reused: {gid}')
    created = now()
    goal = empty_goal()
    goal.update({
        'goal_id': gid,
        'status': 'ACTIVE',
        'goal_type': goal_type,
        'outcome': outcome.strip(),
        'acceptance': [x.strip() for x in acceptance if x.strip()],
        'acceptance_quality_warnings': acceptance_quality_warnings([x.strip() for x in acceptance if x.strip()]),
        'non_goals': [x.strip() for x in non_goals if x.strip()],
        'risk_ceiling': risk_ceiling,
        'created_at': created,
        'updated_at': created,
        'codebase_health_baseline': health_snapshot(root),
        'budget': {
            'max_tasks': max(1, max_tasks),
            'max_parallel': max(1, max_parallel),
            'max_non_shipping_tasks': max(1, max_non_shipping),
            'max_revisions_per_task': max(1, max_revisions),
            'scope_growth_limit_percent': max(1, scope_growth_limit),
            'max_auto_scouts': max(0, max_auto_scouts),
            'scout_summary_token_budget': max(100, scout_summary_token_budget),
            'scout_input_token_budget': max(1000, scout_input_token_budget),
            'scout_wall_minutes_budget': max(0.5, scout_wall_minutes_budget),
            'scout_provider_cost_budget': max(0.0, scout_provider_cost_budget),
        },
    })
    save_goal(root, goal)
    (confined_child(root, GOALS_DIR, gid, 'goal ID') / 'decisions.jsonl').touch(exist_ok=True)
    return goal



def bind_goal_acceptance(root: Path, *, criterion_index: int, command: str = '', expected_output: str = '',
                         probe_file: str = '', inspection_requirement: str = '') -> dict[str, Any]:
    """Bind one declared Goal criterion to a judge before implementation is observed."""
    goal = load_goal(root)
    if goal.get('status') != 'ACTIVE':
        raise SystemExit('goal acceptance binding requires an ACTIVE goal')
    contract = goal.setdefault('goal_acceptance_contract', empty_goal()['goal_acceptance_contract'])
    if contract.get('frozen_at'):
        raise SystemExit('Goal acceptance contract is frozen; create a Goal revision/replan instead of changing the judge after Worker start')
    criteria = goal.get('acceptance', []) or []
    if criterion_index < 1 or criterion_index > len(criteria):
        raise SystemExit(f'criterion index must be 1..{len(criteria)}')
    command = command.strip(); expected_output = expected_output.strip(); inspection_requirement = inspection_requirement.strip()
    probe_file = probe_file.strip().replace('\\', '/')
    if bool(command) == bool(inspection_requirement):
        raise SystemExit('Bind exactly one evaluator: --command or --inspection-requirement')
    if command:
        normalized = re.sub(r'\s+', ' ', command.strip().casefold())
        vacuous = {'true', ':', 'exit 0', 'echo ok', 'echo pass', 'printf ok', 'printf pass'}
        if normalized in vacuous or re.fullmatch(r'(?:python|python3)\s+-c\s+[\"\']?(?:pass|exit\(0\)|sys\.exit\(0\))[\"\']?', normalized):
            raise SystemExit('Refusing an obviously vacuous Goal acceptance command; bind the criterion to behavior/probe evidence before implementation')
    mapping = {
        'criterion_index': criterion_index,
        'criterion': criteria[criterion_index - 1],
        'evaluator_type': 'command' if command else 'inspection',
        'command': command,
        'expected_output': expected_output,
        'probe_file': probe_file,
        'inspection_requirement': inspection_requirement,
    }
    mappings = [m for m in (contract.get('mappings') or []) if int(m.get('criterion_index', 0)) != criterion_index]
    mappings.append(mapping); mappings.sort(key=lambda m: int(m.get('criterion_index', 0)))
    contract['mappings'] = mappings
    goal['goal_acceptance_contract'] = contract
    save_goal(root, goal)
    return mapping


def freeze_goal_acceptance_contract(root: Path) -> dict[str, Any]:
    """Freeze Goal-level judge before the first write-capable Worker starts."""
    goal = load_goal(root)
    contract = goal.setdefault('goal_acceptance_contract', empty_goal()['goal_acceptance_contract'])
    if contract.get('frozen_at'):
        return contract
    criteria = goal.get('acceptance', []) or []
    mappings = contract.get('mappings', []) or []
    mapped = {int(m.get('criterion_index', 0)) for m in mappings}
    missing = [str(i) for i in range(1, len(criteria) + 1) if i not in mapped]
    if missing:
        raise SystemExit('Goal acceptance contract must map every declared criterion before the first Worker starts; missing criterion(s): ' + ', '.join(missing))
    probe_hashes: dict[str, str] = {}
    for mapping in mappings:
        relative = str(mapping.get('probe_file') or '').strip().replace('\\', '/')
        if not relative:
            continue
        path = (root / relative).resolve()
        try: path.relative_to(root.resolve())
        except ValueError as exc: raise SystemExit(f'Goal acceptance probe escapes repository: {relative}') from exc
        if not path.is_file(): raise SystemExit(f'Goal acceptance probe must exist before Worker start: {relative}')
        probe_hashes[relative] = sha256_file(path)
    frozen = {
        'schema_version': 1,
        'revision': int(contract.get('revision', 1) or 1),
        'mappings': mappings,
        'probe_hashes': probe_hashes,
        'frozen_at': now(),
    }
    canonical = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    frozen['contract_sha256'] = sha256_text(canonical)
    goal['goal_acceptance_contract'] = frozen
    save_goal(root, goal)
    return frozen


def verify_goal_acceptance_contract(root: Path) -> dict[str, Any]:
    goal = load_goal(root)
    contract = goal.get('goal_acceptance_contract', {}) or {}
    if not contract.get('frozen_at') or not contract.get('contract_sha256'):
        raise SystemExit('Goal acceptance contract is not frozen; bind criteria and freeze before implementation')
    for relative, expected in (contract.get('probe_hashes') or {}).items():
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise SystemExit(f'Goal acceptance probe changed after freeze: {relative}; replan/revalidate instead of changing the judge')
    frozen_copy = {k: v for k, v in contract.items() if k != 'contract_sha256'}
    canonical = json.dumps(frozen_copy, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if sha256_text(canonical) != contract.get('contract_sha256'):
        raise SystemExit('Goal acceptance contract integrity mismatch')
    return contract


def _cycle_exists(tasks: dict[str, Any]) -> bool:
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for dep in tasks.get(node_id, {}).get('depends_on', []):
            if dep in tasks and visit(dep):
                return True
        visiting.remove(node_id); visited.add(node_id)
        return False
    return any(visit(node_id) for node_id in tasks)


def add_task(root: Path, *, node_id: str, outcome: str, depends_on: list[str], agent_role: str,
             risk: str, delivery_delta: str, modify: str, create: str, success_criterion: str,
             acceptance: list[str], replaces: list[str] | None = None, negative_required: bool = False,
             data_operation: str = 'READ_ONLY', artifact_operation: str = 'CREATE_NEW_VERSION',
             acceptance_commands: list[str] | None = None, expected_outputs: list[str] | None = None,
             probe_files: list[str] | None = None, review_policy: str = 'auto',
             delegation_policy: str = 'auto', state_hazard: str = 'auto', state_signals: list[str] | None = None,
             state_authority: str = '', state_transitions: list[str] | None = None, state_invariants: list[str] | None = None,
             state_dependencies: list[str] | None = None) -> dict[str, Any]:
    goal = load_goal(root)
    if goal.get('status') != 'ACTIVE':
        raise SystemExit('goal add-task requires an ACTIVE goal')
    node_id = validate_identifier(node_id, 'goal node ID')
    for dep in depends_on:
        validate_identifier(dep, 'goal dependency node ID')
    tasks = goal.setdefault('tasks', {})
    if node_id in tasks:
        raise SystemExit(f'Goal node already exists: {node_id}')
    if len(tasks) >= int(goal.get('budget', {}).get('max_tasks', 8)):
        raise SystemExit('Goal task budget exhausted; split the goal or change the budget explicitly')
    missing = [dep for dep in depends_on if dep not in tasks]
    if missing:
        raise SystemExit('Unknown dependency nodes: ' + ', '.join(missing))
    if agent_role in {'SCOUT', 'REVIEWER'} and (modify.upper() != 'NONE' or create.upper() != 'NONE'):
        raise SystemExit(f'{agent_role} nodes are read-only; use WORKER for application writes')
    tasks[node_id] = {
        'node_id': node_id,
        'task_id': f"{goal['goal_id']}-{node_id}",
        'status': 'PLANNED',
        'outcome': outcome,
        'depends_on': depends_on,
        'agent_role': agent_role,
        'risk': risk,
        'delivery_delta': delivery_delta,
        'modify': modify,
        'create': create,
        'replaces': [x.strip().replace('\\','/') for x in (replaces or []) if x.strip()],
        'success_criterion': success_criterion,
        'acceptance': acceptance,
        'negative_required': bool(negative_required),
        'data_operation': data_operation,
        'artifact_operation': artifact_operation,
        'acceptance_contract': {
            'commands': [x.strip() for x in (acceptance_commands or []) if x.strip()],
            'expected_outputs': [x.strip() for x in (expected_outputs or []) if x.strip()],
            'probe_files': [x.strip().replace('\\', '/') for x in (probe_files or []) if x.strip()],
            'probe_hashes': {},
            'contract_sha256': '',
            'frozen_at': None,
        },
        'review_policy': review_policy,
        'delegation_policy': delegation_policy,
        'state_hazard': state_hazard,
        'state_signals': [x.strip() for x in (state_signals or []) if x.strip()],
        'state_authority': state_authority.strip(),
        'state_transitions': [x.strip() for x in (state_transitions or []) if x.strip()],
        'state_invariants': [x.strip() for x in (state_invariants or []) if x.strip()],
        'state_dependencies': [x.strip().replace('\\','/') for x in (state_dependencies or []) if x.strip()],
        'delegation': None,
        'result': None,
    }
    if _cycle_exists(tasks):
        del tasks[node_id]
        raise SystemExit('Goal task graph would contain a dependency cycle')

    # v1.13: automatically insert a cheap read-only Scout only when the
    # delegation planner has high confidence that discovery isolation will save
    # expensive Worker context. Small/clear work remains a single Worker.
    node = tasks[node_id]
    recommendation = recommend_node(goal, node, root=root)
    node['delegation'] = recommendation
    if agent_role == 'WORKER' and recommendation.get('action') == 'SCOUT_FIRST':
        auto_scouts = sum(
            1 for item in tasks.values()
            if item.get('agent_role') == 'SCOUT' and item.get('auto_generated') is True
        )
        scout_limit = int((goal.get('budget') or {}).get('max_auto_scouts', 2) or 0)
        scout_id = f'{node_id}__SCOUT'
        if auto_scouts < scout_limit and scout_id not in tasks and len(tasks) < int((goal.get('budget') or {}).get('max_tasks', 8)):
            original_deps = list(node.get('depends_on') or [])
            tasks[scout_id] = {
                'node_id': scout_id,
                'task_id': f"{goal['goal_id']}-{scout_id}",
                'status': 'PLANNED',
                'outcome': f"Map root cause/implementation surface for {outcome}",
                'depends_on': original_deps,
                'agent_role': 'SCOUT',
                'risk': 'R0',
                'delivery_delta': 'NO_DELTA',
                'modify': 'NONE',
                'create': 'NONE',
                'success_criterion': success_criterion,
                'acceptance': [f"Return root cause/affected files/risk signals in <= {(goal.get('budget') or {}).get('scout_summary_token_budget',350)} tokens"],
                'negative_required': False,
                'data_operation': 'READ_ONLY',
                'artifact_operation': 'READ_ONLY',
                'acceptance_contract': {'commands': [], 'expected_outputs': [], 'probe_files': [], 'probe_hashes': {}, 'contract_sha256': '', 'frozen_at': None},
                'review_policy': 'none',
                'delegation_policy': 'main',
                'delegation': {
                    'action': 'SPAWN_SCOUT', 'hard': True, 'model_class': 'CHEAP_FAST_READ_ONLY',
                    'summary_token_budget': int((goal.get('budget') or {}).get('scout_summary_token_budget',350) or 350),
                    'reasons': recommendation.get('reasons', []),
                },
                'auto_generated': True,
                'delegated_for': node_id,
                'result': None,
            }
            node['depends_on'] = [scout_id]
            node['delegation'] = {**recommendation, 'auto_scout_node': scout_id}
        else:
            if delegation_policy == 'scout':
                tasks.pop(node_id, None)
                raise SystemExit('Forced Scout delegation cannot be satisfied within max_tasks/max_auto_scouts budget')
            node['delegation'] = {
                'action': 'MAIN_WORKER_BUDGET_FALLBACK', 'hard': True, 'model_class': 'STRONG_WORKER',
                'summary_token_budget': None,
                'reasons': list(recommendation.get('reasons', [])) + ['Scout benefit detected but Goal delegation budget is exhausted'],
                'auto_inserted': False, 'budget_blocked': True,
            }
    if _cycle_exists(tasks):
        tasks.pop(node_id, None)
        tasks.pop(f'{node_id}__SCOUT', None)
        raise SystemExit('Goal task graph would contain a dependency cycle')
    save_goal(root, goal)
    return tasks[node_id]



def freeze_acceptance_contract(root: Path, node_id: str, effective_risk: str) -> dict[str, Any]:
    """Freeze Goal-owned acceptance before a higher-risk Worker starts.

    R2/R3 Goal workers must have at least one predeclared acceptance command. Probe
    files are optional, but when declared their hashes are bound into the contract so
    the Worker cannot silently rewrite the test/probe used as the judge.
    """
    goal = load_goal(root)
    if goal.get('status') != 'ACTIVE':
        raise SystemExit('Acceptance contract can only be frozen for an ACTIVE goal')
    node = (goal.get('tasks') or {}).get(node_id)
    if not node:
        raise SystemExit(f'Unknown goal node: {node_id}')
    contract = node.setdefault('acceptance_contract', {})
    commands = [str(x).strip() for x in contract.get('commands', []) if str(x).strip()]
    expected = [str(x).strip() for x in contract.get('expected_outputs', []) if str(x).strip()]
    probe_files = [str(x).strip().replace('\\', '/') for x in contract.get('probe_files', []) if str(x).strip()]
    if effective_risk in {'R2', 'R3'} and not commands:
        raise SystemExit(
            f'{effective_risk} Goal worker requires a predeclared acceptance contract. '
            'Add at least one `goal add-task --acceptance-command ...` before `goal start`.'
        )
    probe_hashes: dict[str, str] = {}
    for relative in probe_files:
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise SystemExit(f'Acceptance probe escapes repository: {relative}') from exc
        if not path.is_file():
            raise SystemExit(f'Acceptance probe file must exist before Worker start: {relative}')
        probe_hashes[relative] = sha256_file(path)
    frozen = {
        'commands': commands,
        'expected_outputs': expected,
        'probe_files': probe_files,
        'probe_hashes': probe_hashes,
        'effective_risk_at_freeze': effective_risk,
        'frozen_at': now(),
    }
    canonical = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    frozen['contract_sha256'] = sha256_text(canonical)
    node['acceptance_contract'] = frozen
    save_goal(root, goal)
    return frozen


def record_acceptance_attempt(root: Path, passed: bool) -> int:
    goal = load_goal(root)
    if goal.get('status') != 'ACTIVE':
        raise SystemExit('Goal acceptance attempt requires an ACTIVE goal')
    attempts = int(goal.get('acceptance_attempts', 0) or 0) + 1
    goal['acceptance_attempts'] = attempts
    goal.setdefault('acceptance_attempt_log', []).append({'at': now(), 'attempt': attempts, 'passed': bool(passed)})
    goal['acceptance_attempt_log'] = goal['acceptance_attempt_log'][-20:]
    save_goal(root, goal)
    return attempts

def _is_ancestor_of_shipping(tasks: dict[str, Any], node_id: str) -> bool:
    shipping = [nid for nid, node in tasks.items() if node.get('delivery_delta') in SHIPPING_DELTAS and node.get('status') not in {'DONE','DEFERRED'}]
    memo: dict[str, bool] = {}
    def depends_transitively(target: str, needle: str) -> bool:
        key = target + '\0' + needle
        if key in memo: return memo[key]
        deps = tasks.get(target, {}).get('depends_on', [])
        value = needle in deps or any(depends_transitively(dep, needle) for dep in deps if dep in tasks)
        memo[key] = value
        return value
    return any(depends_transitively(target, node_id) for target in shipping)


def ready_nodes(goal: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = goal.get('tasks', {}) or {}
    ready: list[dict[str, Any]] = []
    for node_id, node in tasks.items():
        if node.get('status') != 'PLANNED':
            continue
        if all(tasks.get(dep, {}).get('status') == 'DONE' for dep in node.get('depends_on', [])):
            copy = dict(node)
            copy['on_path_to_shipping'] = _is_ancestor_of_shipping(tasks, node_id)
            ready.append(copy)
    streak = int(goal.get('completed_non_shipping_streak', 0) or 0)
    limit = int(goal.get('budget', {}).get('max_non_shipping_tasks', 2) or 2)
    if streak >= limit:
        protected = [n for n in ready if n.get('delivery_delta') in SHIPPING_DELTAS or n.get('on_path_to_shipping')]
        if protected:
            ready = protected
    priority = {'USER_VISIBLE_BEHAVIOR': 0, 'EXECUTABLE_CAPABILITY': 1, 'RISK_RETIREMENT': 2, 'NO_DELTA': 3, 'DOCUMENTATION_ONLY': 4}
    ready.sort(key=lambda n: (priority.get(n.get('delivery_delta'), 5), n.get('node_id', '')))
    return ready


def next_wave(root: Path) -> dict[str, Any]:
    goal = load_goal(root)
    if goal.get('status') != 'ACTIVE':
        return {'goal_id': goal.get('goal_id'), 'status': goal.get('status'), 'ready': [], 'message': 'No ACTIVE goal'}
    ready = ready_nodes(goal)
    max_parallel = int(goal.get('budget', {}).get('max_parallel', 1) or 1)
    read_only = [n for n in ready if n.get('agent_role') in {'SCOUT', 'REVIEWER'}]
    all_writers = [n for n in ready if n.get('agent_role') == 'WORKER']
    writers, held = select_parallel_writers(all_writers, max_parallel)
    delegation = build_wave_delegation(goal, ready, writers, held, root=root)
    return {
        'goal_id': goal.get('goal_id'),
        'status': goal.get('status'),
        'ready': read_only + writers,
        'max_parallel_writers': max_parallel,
        'parallel_writers_require_isolated_worktrees': len(writers) > 1,
        'delegation': delegation,
        'owner_interrupt_required': bool(goal.get('decisions_required')),
        'decisions_required': goal.get('decisions_required', []),
    }


def record_discovery(root: Path, summary: str, *, affected_files: list[str] | None = None,
                     risk_signals: list[str] | None = None) -> None:
    goal = load_goal(root)
    if goal.get('status') not in {'ACTIVE', 'BLOCKED'}:
        raise SystemExit('No live goal')
    goal.setdefault('discoveries', []).append({
        'at': now(), 'summary': summary,
        'affected_files': affected_files or [], 'risk_signals': risk_signals or [],
    })
    goal['discoveries'] = goal['discoveries'][-20:]
    save_goal(root, goal)


def mark_scout_done(root: Path, node_id: str, summary: str, affected_files: list[str], risk_signals: list[str],
                    input_tokens: int | None = None, output_tokens: int | None = None,
                    provider_cost: float | None = None, wall_minutes: float | None = None,
                    invariants: list[str] | None = None, entry_point: str = '', confidence: str = 'MEDIUM',
                    recommended_scope: list[str] | None = None) -> None:
    goal = load_goal(root); node = (goal.get('tasks') or {}).get(node_id)
    if not node: raise SystemExit(f'Unknown goal node: {node_id}')
    if node.get('agent_role') not in {'SCOUT','REVIEWER'}: raise SystemExit('goal node-done only accepts read-only SCOUT/REVIEWER nodes')
    if node.get('status') != 'PLANNED': raise SystemExit(f'Read-only node is not PLANNED: {node.get("status")}')
    token_budget = int((goal.get('budget') or {}).get('scout_summary_token_budget', 350) or 350)
    approx_tokens = max(1, len(summary) // 4)
    if node.get('agent_role') == 'SCOUT' and approx_tokens > token_budget:
        raise SystemExit(f'Scout summary exceeds cost-control budget (~{approx_tokens} tokens > {token_budget}); return only root cause, affected files, invariants and risk signals')
    if node.get('agent_role') == 'SCOUT':
        budget = goal.get('budget') or {}
        input_limit = int(budget.get('scout_input_token_budget', 24000) or 24000)
        wall_limit = float(budget.get('scout_wall_minutes_budget', 5.0) or 5.0)
        cost_limit = float(budget.get('scout_provider_cost_budget', 0.0) or 0.0)
        if input_tokens is not None and input_tokens > input_limit:
            raise SystemExit(f'Scout input usage exceeds Goal delegation budget ({input_tokens} > {input_limit} tokens); use main Worker/replan or raise budget explicitly')
        if wall_minutes is not None and wall_minutes > wall_limit:
            raise SystemExit(f'Scout wall time exceeds Goal delegation budget ({wall_minutes:.2f} > {wall_limit:.2f} minutes)')
        if cost_limit > 0 and provider_cost is not None and provider_cost > cost_limit:
            raise SystemExit(f'Scout provider cost exceeds Goal delegation budget ({provider_cost:.6f} > {cost_limit:.6f})')
    confidence = confidence.upper()
    if confidence not in {'LOW','MEDIUM','HIGH'}: raise SystemExit('Scout confidence must be LOW, MEDIUM or HIGH')
    node['status'] = 'DONE'; node['result'] = {
        'summary': summary, 'affected_files': affected_files, 'risk_signals': risk_signals,
        'invariants': invariants or [], 'entry_point': entry_point.strip(), 'confidence': confidence,
        'recommended_scope': [x.strip().replace('\\','/') for x in (recommended_scope or []) if x.strip()],
        'completed_at': now(),
        'delegation_usage': {
            'input_tokens': input_tokens, 'output_tokens': output_tokens, 'provider_cost': provider_cost, 'wall_minutes': wall_minutes,
        },
    }
    goal.setdefault('discoveries', []).append({'at': now(), 'summary': summary, 'affected_files': affected_files, 'risk_signals': risk_signals})
    goal['last_task_id'] = node.get('task_id')
    save_goal(root, goal)



def scout_handoffs_for_node(goal: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    tasks = goal.get('tasks', {}) or {}; node = tasks.get(node_id) or {}
    values: list[dict[str, Any]] = []
    for dep in node.get('depends_on', []) or []:
        scout = tasks.get(dep) or {}
        if scout.get('agent_role') == 'SCOUT' and scout.get('status') == 'DONE' and scout.get('result'):
            values.append({'node_id': dep, **(scout.get('result') or {})})
    return values


def maybe_apply_scout_scope(root: Path, node_id: str) -> dict[str, Any]:
    """Narrow only on explicit HIGH-confidence Scout scope; otherwise preserve authorized scope."""
    goal = load_goal(root); node = (goal.get('tasks') or {}).get(node_id)
    if not node: raise SystemExit(f'Unknown goal node: {node_id}')
    handoffs = scout_handoffs_for_node(goal, node_id)
    if not handoffs: return node
    recommended: list[str] = []
    high = True
    for item in handoffs:
        if str(item.get('confidence','MEDIUM')).upper() != 'HIGH': high = False
        for path in item.get('recommended_scope', []) or []:
            if path not in recommended: recommended.append(path)
    current = str(node.get('modify') or 'NONE')
    broad = any(token in current for token in ('*','?','[')) or current.strip().rstrip('/') in {'src','app','server','backend','frontend','packages'}
    if high and recommended and broad:
        node['scope_narrowed_from'] = current
        node['modify'] = ','.join(recommended)
        node['scope_narrowed_by_scout'] = True
        save_goal(root, goal)
    return node



def _scope_patterns(value: str) -> list[str]:
    return [x.strip().replace('\\','/') for x in re.split(r'[,;\n]+', value or '') if x.strip() and x.strip().upper() not in {'NONE','UNSET'}]


def scope_footprint(root: Path, modify: str, create: str) -> set[str]:
    """Approximate authorized footprint with concrete matches plus pattern placeholders."""
    values: set[str] = set()
    for pattern in _scope_patterns(modify) + _scope_patterns(create):
        normalized = pattern.lstrip('./')
        matched = []
        try:
            matched = [p for p in root.glob(normalized) if p.is_file()]
        except (ValueError, OSError):
            matched = []
        for path in matched:
            try: values.add(path.relative_to(root).as_posix())
            except ValueError: pass
        # Keep an authorization placeholder so empty/new-file scopes still have a measurable baseline.
        values.add('PATTERN:' + normalized)
    return values


def link_task_active(root: Path, goal_id: str, node_id: str, task_id: str, revision: int) -> None:
    goal = load_goal(root)
    if goal.get('goal_id') != goal_id or goal.get('status') != 'ACTIVE':
        raise SystemExit(f'Goal link invalid: {goal_id} is not the active goal')
    node = (goal.get('tasks') or {}).get(node_id)
    if not node:
        raise SystemExit(f'Goal node not found: {node_id}')
    if node.get('status') != 'PLANNED':
        raise SystemExit(f'Goal node {node_id} cannot start from status {node.get("status")}')
    node['status'] = 'ACTIVE'; node['task_id'] = task_id; node['task_revision'] = revision; node['started_at'] = now()
    footprint = scope_footprint(root, str(node.get('modify') or 'NONE'), str(node.get('create') or 'NONE'))
    node['initial_scope_footprint'] = sorted(footprint)
    node['initial_scope_count'] = len(footprint)
    # Preserve the authorization grammar as well as the concrete footprint.
    # Later scope-growth checks must not count files that were already covered by
    # an original broad pattern (for example src/**) merely because those files
    # were created after the task started.
    node['initial_modify_scope'] = str(node.get('modify') or 'NONE')
    node['initial_create_scope'] = str(node.get('create') or 'NONE')
    save_goal(root, goal)


def sync_task_completion(root: Path, task_id: str, revision: int, outcome: str, delivery_delta: str,
                         evidence_bundle: str, risk_tier: str, first_pass_accepted: str,
                         goal_id: str = '', goal_node: str = '') -> None:
    if not goal_id or not goal_node or goal_id in {'NONE','UNSET'} or goal_node in {'NONE','UNSET'}:
        return
    goal = load_goal(root)
    if goal.get('goal_id') != goal_id:
        raise SystemExit(f'Task references non-active/mismatched goal {goal_id}')
    node = (goal.get('tasks') or {}).get(goal_node)
    if not node:
        raise SystemExit(f'Task references missing goal node {goal_node}')
    node['status'] = 'DONE'
    node['result'] = {
        'task_id': task_id, 'task_revision': revision, 'outcome': outcome,
        'delivery_delta': delivery_delta, 'risk_tier': risk_tier,
        'evidence_bundle': evidence_bundle, 'first_pass_accepted': first_pass_accepted,
        'completed_at': now(),
    }
    streak = int(goal.get('completed_non_shipping_streak', 0) or 0)
    goal['completed_non_shipping_streak'] = 0 if delivery_delta in SHIPPING_DELTAS else streak + 1
    goal['last_task_id'] = task_id
    save_goal(root, goal)



def sync_task_abort(root: Path, goal_id: str = '', goal_node: str = '', reason: str = 'task aborted') -> None:
    if not goal_id or not goal_node or goal_id in {'NONE','UNSET'} or goal_node in {'NONE','UNSET'}:
        return
    goal = load_goal(root)
    if goal.get('goal_id') != goal_id:
        return
    node = (goal.get('tasks') or {}).get(goal_node)
    if not node:
        return
    if node.get('status') == 'ACTIVE':
        node['status'] = 'PLANNED'
        node.pop('task_revision', None)
        node.pop('started_at', None)
        node['last_abort_reason'] = reason
        save_goal(root, goal)

def block_goal(root: Path, reason: str, owner_decision: bool = False) -> None:
    goal = load_goal(root)
    if goal.get('status') != 'ACTIVE': raise SystemExit('Only ACTIVE goals can be blocked')
    goal['status'] = 'BLOCKED'
    if owner_decision:
        goal.setdefault('decisions_required', []).append({'at': now(), 'reason': reason})
    record = {'at': now(), 'type': 'BLOCKED', 'reason': reason, 'owner_decision': owner_decision}
    path = confined_child(root, GOALS_DIR, str(goal['goal_id']), 'goal ID') / 'decisions.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    save_goal(root, goal)


def resume_goal(root: Path, decision: str = '') -> None:
    goal = load_goal(root)
    if goal.get('status') != 'BLOCKED': raise SystemExit('Only BLOCKED goals can be resumed')
    if decision:
        path = confined_child(root, GOALS_DIR, str(goal['goal_id']), 'goal ID') / 'decisions.jsonl'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'at': now(), 'type': 'DECISION', 'decision': decision}, ensure_ascii=False) + '\n')
    goal['decisions_required'] = []
    goal['status'] = 'ACTIVE'
    save_goal(root, goal)


def abort_goal(root: Path, reason: str) -> None:
    goal = load_goal(root)
    if goal.get('status') not in {'ACTIVE','BLOCKED'}: raise SystemExit('No live goal to abort')
    active = [n for n in (goal.get('tasks') or {}).values() if n.get('status') == 'ACTIVE']
    if active: raise SystemExit('Abort/finish the active task before aborting its goal')
    goal['status'] = 'ABORTED'; goal['abort_reason'] = reason
    save_goal(root, goal)


def defer_node(root: Path, node_id: str, reason: str) -> None:
    goal = load_goal(root); tasks = goal.get('tasks') or {}; node = tasks.get(node_id)
    if not node: raise SystemExit(f'Unknown goal node: {node_id}')
    if node.get('status') != 'PLANNED': raise SystemExit('Only PLANNED goal nodes can be deferred')
    dependents = [nid for nid, item in tasks.items() if node_id in item.get('depends_on', []) and item.get('status') not in {'DONE','DEFERRED'}]
    if dependents: raise SystemExit('Cannot defer a node that still has live dependents: ' + ', '.join(dependents))
    node['status'] = 'DEFERRED'; node['defer_reason'] = reason
    save_goal(root, goal)


def can_complete(goal: dict[str, Any]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    tasks = goal.get('tasks', {}) or {}
    unfinished = [nid for nid, node in tasks.items() if node.get('status') in {'PLANNED','ACTIVE','BLOCKED'}]
    if unfinished: issues.append('unfinished goal nodes: ' + ', '.join(unfinished))
    if goal.get('decisions_required'): issues.append('owner decisions remain unresolved')
    if goal.get('goal_type') == 'product':
        shipped = any((node.get('result') or {}).get('delivery_delta') in SHIPPING_DELTAS for node in tasks.values())
        if not shipped: issues.append('product goal has no accepted shipping task')
    return not issues, issues


def complete_goal(root: Path, *, acceptance_records: list[dict[str, Any]], inspected_by: str, known_limits: str) -> dict[str, Any]:
    goal = load_goal(root)
    if goal.get('status') != 'ACTIVE': raise SystemExit('goal done requires an ACTIVE goal')
    ok, issues = can_complete(goal)
    if not ok: raise SystemExit('; '.join(issues))
    if not acceptance_records: raise SystemExit('goal done requires at least one goal-level acceptance command')
    if any(item.get('result') != 'PASS' for item in acceptance_records): raise SystemExit('Goal acceptance contains a failed check')
    accepted_at = now()
    task_ids = {str(node.get('task_id')) for node in (goal.get('tasks') or {}).values() if node.get('task_id')}
    ledger_rows: list[dict[str, str]] = []
    ledger = root / '.ai' / 'COST_LEDGER.csv'
    if ledger.is_file():
        with ledger.open(encoding='utf-8', newline='') as handle:
            ledger_rows = [row for row in csv.DictReader(handle) if row.get('task_id') in task_ids and row.get('accepted') == 'yes']
    def total(field: str) -> float:
        value = 0.0
        for row in ledger_rows:
            try:
                value += float(row.get(field) or 0)
            except ValueError:
                pass
        return value
    try:
        created_dt = datetime.fromisoformat(str(goal.get('created_at')).replace('Z', '+00:00'))
        accepted_dt = datetime.fromisoformat(accepted_at.replace('Z', '+00:00'))
        goal_cycle_minutes = max(0.0, (accepted_dt - created_dt).total_seconds() / 60.0)
    except (TypeError, ValueError):
        goal_cycle_minutes = None
    readonly_nodes = [node for node in (goal.get('tasks') or {}).values() if node.get('agent_role') in {'SCOUT','REVIEWER'} and node.get('result')]
    scout_nodes = [node for node in readonly_nodes if node.get('agent_role') == 'SCOUT']
    auto_scout_nodes = [node for node in scout_nodes if node.get('auto_generated') is True]
    delegation_input_tokens = sum(int(((node.get('result') or {}).get('delegation_usage') or {}).get('input_tokens') or 0) for node in readonly_nodes)
    delegation_output_tokens = sum(int(((node.get('result') or {}).get('delegation_usage') or {}).get('output_tokens') or 0) for node in readonly_nodes)
    delegation_provider_cost_values = [((node.get('result') or {}).get('delegation_usage') or {}).get('provider_cost') for node in readonly_nodes]
    delegation_provider_cost = sum(float(value or 0) for value in delegation_provider_cost_values) if any(value is not None for value in delegation_provider_cost_values) else None
    delegation_wall_values = [((node.get('result') or {}).get('delegation_usage') or {}).get('wall_minutes') for node in readonly_nodes]
    delegation_wall_minutes = sum(float(value or 0) for value in delegation_wall_values) if any(value is not None for value in delegation_wall_values) else None
    health_delta = health_compare(root, baseline=goal.get('codebase_health_baseline') or {}, current=health_snapshot(root))
    result = {
        'schema_version': 4, 'goal_id': goal['goal_id'], 'outcome': goal['outcome'],
        'accepted_at': accepted_at, 'acceptance': goal.get('acceptance', []),
        'goal_acceptance_contract_sha256': (goal.get('goal_acceptance_contract') or {}).get('contract_sha256','NONE'),
        'checks': acceptance_records, 'inspected_by': inspected_by,
        'known_limits': known_limits,
        'codebase_health': health_delta,
        'acceptance_attempts': int(goal.get('acceptance_attempts', 0) or 0),
        'goal_first_pass_accepted': int(goal.get('acceptance_attempts', 0) or 0) <= 1,
        'metrics': {
            'goal_cycle_minutes': round(goal_cycle_minutes, 2) if goal_cycle_minutes is not None else None,
            'accepted_task_revisions': len(ledger_rows),
            'cost_telemetry_rows': sum(bool((row.get('estimated_ai_cost') or '').strip() or (row.get('provider_cost') or '').strip()) for row in ledger_rows),
            'token_telemetry_rows': sum(bool((row.get('coordination_input_tokens') or '').strip() or (row.get('implementation_input_tokens') or '').strip() or (row.get('output_tokens') or '').strip()) for row in ledger_rows),
            'human_time_telemetry_rows': sum(bool((row.get('human_review_minutes') or '').strip() or (row.get('human_wait_minutes') or '').strip()) for row in ledger_rows),
            'estimated_ai_cost': round(total('estimated_ai_cost'), 6) if any((row.get('estimated_ai_cost') or '').strip() for row in ledger_rows) else None,
            'provider_cost': round(total('provider_cost'), 6) if any((row.get('provider_cost') or '').strip() for row in ledger_rows) else None,
            'human_review_minutes': round(total('human_review_minutes'), 2) if any((row.get('human_review_minutes') or '').strip() for row in ledger_rows) else None,
            'human_wait_minutes': round(total('human_wait_minutes'), 2) if any((row.get('human_wait_minutes') or '').strip() for row in ledger_rows) else None,
            'coordination_input_tokens': int(total('coordination_input_tokens')) if any((row.get('coordination_input_tokens') or '').strip() for row in ledger_rows) else None,
            'implementation_input_tokens': int(total('implementation_input_tokens')) if any((row.get('implementation_input_tokens') or '').strip() for row in ledger_rows) else None,
            'output_tokens': int(total('output_tokens')) if any((row.get('output_tokens') or '').strip() for row in ledger_rows) else None,
            'delegation_read_only_nodes': len(readonly_nodes),
            'delegation_scout_nodes': len(scout_nodes),
            'delegation_auto_scout_nodes': len(auto_scout_nodes),
            'delegation_input_tokens': delegation_input_tokens if delegation_input_tokens else None,
            'delegation_output_tokens': delegation_output_tokens if delegation_output_tokens else None,
            'delegation_provider_cost': round(delegation_provider_cost, 6) if delegation_provider_cost is not None else None,
            'delegation_wall_minutes': round(delegation_wall_minutes, 2) if delegation_wall_minutes is not None else None,
        },
        'task_results': {
            nid: node.get('result') for nid, node in (goal.get('tasks') or {}).items() if node.get('result')
        },
        'final_verdict': 'PASS',
    }
    goal['status'] = 'COMPLETED'; goal['completed_at'] = result['accepted_at']; goal['result'] = result
    goal_dir = confined_child(root, GOALS_DIR, str(goal['goal_id']), 'goal ID'); goal_dir.mkdir(parents=True, exist_ok=True)
    result_path = goal_dir / 'result.json'
    if result_path.exists(): raise SystemExit(f'Immutable Goal result already exists: {result_path.relative_to(root)}')
    atomic_write_json(result_path, result)
    save_goal(root, goal)
    health_save_snapshot(root, health_delta.get('current') or health_snapshot(root))
    return result
