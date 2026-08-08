# Upgrade v1.5 → v1.6

## Recommended boundary

Upgrade **between tasks**, when `STATE Active Task ID: NONE`. v1.6 scope enforcement needs a task-start baseline; an already-running v1.5 task has no trustworthy historical baseline to reconstruct.

## Preserve

Keep project-specific contents of:

- `.ai/PROJECT.md`
- `.ai/DECISIONS.md`
- `.ai/COST_LEDGER.csv`
- `.ai/evidence/`
- `.ai/history/`

Replace scripts/templates/guides with v1.6, then run:

```bash
python -m py_compile scripts/*.py
python scripts/ai_os.py check
python scripts/self_test.py
```

## Behavior changes

1. `begin` auto-claims by default. Use `--ready` for the old unclaimed behavior.
2. `--risk` is optional; default `auto`. Manual risk may raise but cannot lower inferred risk floor.
3. Scope validation is based on task delta from `.ai/runtime/task_baseline.json`, not all dirty files.
4. R1 no longer universally requires a negative command. Use `--negative-required` when failure behavior belongs to acceptance.
5. R1+ `done` requires `--output-inspected-by`.
6. Use `amend` for adjacent scope discovery; use `pause/resume` instead of hand-editing lifecycle state.
7. R0/R1 evidence is COMPACT; R2/R3 evidence remains FULL.

Existing accepted v1.5 evidence remains historical data; do not rewrite it to schema v3.
