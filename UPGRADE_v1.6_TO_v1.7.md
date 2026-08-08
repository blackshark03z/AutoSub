# Upgrade v1.6 → v1.7

## What changed

1. `done`/`close` now reconcile authorized risk against actual task-delta paths and changed lines.
2. Shipping Circuit Breaker is visible and enforced at `begin`.
3. Shipping Delivery Delta is reconciled against actual application delta.
4. Evidence risk/profile must match the current amended task.
5. Optional project-specific risk path floors can be configured in `PROJECT.md`.

## Existing repositories

Existing v1.6 task files remain compatible. Add this optional section to `.ai/PROJECT.md` when your repository uses sensitive directory names not covered by generic heuristics:

```text
## Risk Surface Map

- R2 paths: src/security/**,src/persistence/**
- R3 paths: migrations/**,infra/production/**,billing/**
```

If omitted, v1.7 uses built-in actual-path and changed-line heuristics only.

## Behavior change

A task that began as R0/R1 may now fail at `done` with `Actual task delta requires R2/R3`. This is intentional: run `ai_os.py amend --risk ... --reason ...`, provide R3 authorization if needed, then rerun the gates required by the escalated tier.

When the Shipping Circuit Breaker is ACTIVE, non-shipping `begin` commands are rejected unless they include both `--breaker-override` and `--breaker-override-reason`.
