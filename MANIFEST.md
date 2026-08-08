# Package Manifest — Senior AI Build OS v1.8.0

## Core contracts

- `.ai/PROJECT.md` — product contract + optional project-specific Risk Surface Map
- `.ai/ACTIVE_TASK.md`
- `.ai/STATE.md`
- `.ai/CONTEXT_CAPSULE.md` — compact generated worker packet
- `.ai/DECISIONS.md`
- `.ai/QUALITY_GATES.md`
- `.ai/COST_LEDGER.csv`

## Machine and immutable records

- `.ai/runtime/task_baseline.json` — generated task-start dirty fingerprints
- `.ai/runtime/*.json` — generated canonical mirrors
- `.ai/evidence/<task>/rNNN/` — immutable COMPACT/FULL evidence bundles with per-file verified fingerprints
- `.ai/history/<task>/rNNN.json` — accepted-task history
- `.ai/transactions/` — lifecycle transaction journal

## Commit-aware CI

- `init` auto-installs `.github/workflows/ai-build-os.yml` in Git repos unless `--no-ci`.
- `validate_ai_os.py --ci` binds committed application paths to v1.8 evidence file fingerprints from the same change set.
- Product checks run every change set; Build OS regression self-test is conditional on core/template changes.

## Runtime/validation

- `scripts/ai_os.py`
- `scripts/risk_support.py`
- `scripts/runtime_support.py`
- `scripts/evidence_support.py`
- `scripts/state_runtime.py`
- `scripts/validate_ai_os.py`
- `scripts/refresh_context_capsule.py`
- `scripts/project_ci.py`
- `scripts/append_cost_ledger.py`
- `scripts/self_test.py`

## Templates and guides

- LEAN / STANDARD / DEEP task templates
- Evidence/review/incident/release/cost templates
- GitHub Actions commit-provenance + OS regression + product CI template (auto-installed by `init`)
- Root quickstarts and docs 01–15
- Upgrade guides through v1.8
