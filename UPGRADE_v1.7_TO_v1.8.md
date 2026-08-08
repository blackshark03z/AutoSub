# Upgrade v1.7 → v1.8

## What changes

1. `init` now installs `.github/workflows/ai-build-os.yml` by default in Git repositories. Use `--no-ci` only when CI is managed elsewhere.
2. Evidence schema v4 adds `task_delta_file_hashes`, allowing commit-aware provenance checks.
3. CI should run `python scripts/validate_ai_os.py --ci`, not `--template` or local `check`, because commit HEAD naturally differs from the pre-commit verified working-tree HEAD.
4. Two consecutive revisions of the same task with `first_pass_accepted=no` trigger a start-time stop-loss. The next revision needs `--stop-loss-ack "..."`.
5. Shipping-breaker overrides are persisted into immutable history and surfaced by `report`.
6. `PROJECT.md` gains optional `Sensitive business terms`; matching changed lines raise acceptance-time actual risk to R2.
7. `done` gains optional repeatable `--expected-output` assertions over stored verification stdout/stderr.

## Project Contract addition

Add under `## Risk Surface Map` if upgrading an existing project:

```text
- Sensitive business terms: NONE
```

Use domain-specific identifiers/phrases only, for example `wallet_balance,refund_amount,inventory_quantity`. Keep generic/noisy terms out unless they are genuinely sensitive in your domain.

## GitHub Actions

For existing projects, either rerun `init` safely with the same project metadata only if your operating procedure allows it, or copy the v1.8 `templates/CI_GITHUB_ACTIONS.yml` to `.github/workflows/ai-build-os.yml` once. New projects get it automatically.

Required branch protection is recommended so the CI gate cannot simply be ignored at merge time.

## Compatibility

Existing v1.7 evidence remains locally verifiable. However, `--ci` provenance for application files changed in a new change set requires v1.8 evidence containing `task_delta_file_hashes`; old evidence cannot authorize a new commit delta.
