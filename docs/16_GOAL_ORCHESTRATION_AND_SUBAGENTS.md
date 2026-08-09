# 16 — Goal Orchestration and Subagents

v1.13 giữ Automatic Delegation của v1.11 nhưng khóa Goal Judge và nối Scout → Worker thành handoff thật sự.

```text
OWNER → GOAL → ORCHESTRATOR → DAG → SCOUT/WORKER/REVIEWER → GOAL ACCEPTANCE → OWNER
```

Owner không còn là message bus. Task/report là machine-to-machine state.

## Canonical files

- `.ai/GOAL.md`: human-readable active Goal.
- `.ai/GOAL_STATE.json`: canonical machine state/DAG.
- `.ai/goals/<goal-id>/plan.json`: goal snapshot/history-friendly plan.
- `.ai/goals/<goal-id>/result.json`: final Goal acceptance record.
- `.ai/goals/<goal-id>/decisions.jsonl`: explicit blockers/owner decisions.

## Minimal flow

```bash
python scripts/ai_os.py goal begin \
  --goal "User can import a CSV and see validated rows" \
  --accept "valid fixture imports successfully" \
  --accept "invalid rows are surfaced" \
  --risk-ceiling R2

python scripts/ai_os.py goal add-task ...
# Orchestrator maps every Goal criterion to a judge before first Worker:
python scripts/ai_os.py goal bind-acceptance --criterion 1 --command "<behavior check>" [--probe-file path]
python scripts/ai_os.py goal next --json
python scripts/ai_os.py goal start --node W1
# Worker executes normal task kernel and `done`.
python scripts/ai_os.py goal next --json
python scripts/ai_os.py goal done --output-inspected-by agent:orchestrator
```

## Subagent economics

Subagents are an optimization primitive, not an organization chart.

Use Scout when expensive Worker context can be avoided. Use parallel Workers only for truly independent slices and isolated worktrees. Parallelism usually lowers wall-clock but can increase total tokens due to repeated bootstrap context. Reviewer is fresh-context assurance for higher-risk/large changes, not mandatory ceremony for every R0/R1 task.

## Goal budgets

Goal state records max tasks, max parallel writers, non-shipping streak, revision stop-loss target and risk ceiling. Risk ceiling is enforced before task start and again against acceptance-time actual risk. Project/task CI, scope, evidence and R3 authority remain unchanged underneath.

## Single-root limitation

`.ai/ACTIVE_TASK.md` remains single-writer. `goal next` can identify a parallel wave, but multiple writer nodes require isolated Git worktrees/branches. Read-only Scout work may run concurrently.

Parallel writers are **opt-in** (`--max-parallel >1`); default is 1 because Goal state is repository-local. Use >1 only when the external coding environment manages isolated worktrees and state/merge reconciliation.


## v1.10 acceptance contract

Goal-linked R2/R3 Worker nodes phải predeclare ít nhất một acceptance command trước `goal start`. `goal start` freezes command/expected-output/probe hashes into the task. `done` verifies probe immutability and automatically runs the frozen command.

```bash
python scripts/ai_os.py goal add-task ... \
  --risk R2 \
  --acceptance-command "python -m pytest -q tests/acceptance/test_flow.py" \
  --probe-file tests/acceptance/test_flow.py
```

R2 reviewer policy defaults `auto`; normal R2 is not reviewed. Elevated signals trigger fresh review. R3 remains mandatory review.


## v1.11 automatic delegation planner

The planner is deliberately conservative:

- **MAIN_WORKER:** explicit 1–2 file R0/R1 work, clear scope/root cause.
- **SCOUT_FIRST:** high-confidence combination of diagnostic uncertainty + broad/multi-surface scope. The Goal kernel auto-inserts `<node>__SCOUT` up to the Goal cap.
- **SCOUT_OPTIONAL:** some context benefit exists but bootstrap ROI is uncertain; advisory only.
- **PARALLEL_WORKERS:** only clearly disjoint declared write scopes and only up to Goal parallel budget.
- **PARALLEL_OPPORTUNITY:** independent nodes are visible even when safe default `max_parallel=1`; an external environment may opt in if it can create isolated worktrees.
- **SPAWN_REVIEWER:** hard runtime request for elevated R2 or R3. It is written to `.ai/runtime/delegation_request.json`.

Auto Scouts are capped (default 2/Goal) and use a compact return budget (default ~350 tokens). This prevents a common multi-agent failure mode where repeated repo bootstrap/search costs more than the Worker context it was meant to save.

`goal node-done` accepts optional `--input-tokens`, `--output-tokens`, `--provider-cost`, and `--wall-minutes`; Goal result/report keeps read-only delegation cost separate so future policy can be tuned from observed ROI instead of intuition.
## v1.13 Goal Judge and Scout handoff

Goal acceptance and task acceptance are separate contracts. Task R2/R3 contracts protect a Worker node; the **Goal Acceptance Contract** protects the final user outcome. The orchestrator binds each Goal criterion before the first write-capable Worker. `goal start` freezes the complete mapping and probe hashes; `goal done` can only execute/confirm that frozen contract.

Scout result is no longer merely stored in `GOAL_STATE.json`. Worker `CONTEXT_CAPSULE.md` receives a bounded `Scout Handoff` section containing root cause/map, affected files, invariants, risk signals and entry point. A Scout may additionally return `--confidence HIGH --recommended-scope ...`; only that explicit high-confidence case may narrow a broad Writer scope automatically.

Scout budget layers:
- summary output budget (default ~350 tokens);
- observed input-token ceiling (default 24k when telemetry is available);
- observed wall-time ceiling (default 5 minutes);
- optional provider-cost ceiling (`0` means disabled/unavailable).

Goal scope/revision budgets are enforced, not decorative. Scope growth beyond the Goal limit produces `SPLIT_OR_REPLAN`; revisions beyond budget require a stop-loss acknowledgement describing what changed in the root-cause hypothesis.

Parallel safety is fail-closed: unknown scope means overlap. Exact file disjointness is not enough when both nodes touch a sensitive shared contract surface (schema/interface/auth/payment/etc.); those stay sequential.

Review trust evolved after v1.13. `DECLARED_REPO_REVIEW` remains a schema/snapshot check only. Under the current default policy, triggered R2 and all R3 require `SIGNED_GUARDIAN`: a fresh external reviewer attestation signed by a Guardian key outside Worker repository authority. A2–A4 runtime isolation/merge claims are still supplied by the trusted outer runtime and are reported as such.

