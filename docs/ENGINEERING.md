# Engineering Contract

## Operating standard

AutoSub follows current Convergent AI Development System (CADS) as an engineering reasoning standard, not as a second lifecycle runtime. Normal development is native:

`understand -> inspect -> edit -> focused test -> ordinary commit -> continue`

For new/stale context, reconstruct live Git/runtime reality first. For a new/changed material product outcome, keep one bounded Product Goal with a representative Critical User Journey and observable acceptance in root `TASK.md`. Use root `ARCHITECTURE.md` for durable architecture/invariants and `docs/DECISIONS/` only for material decisions that must survive turnover.

Feature/subsystem PASS does not establish Journey/Product PASS. A completion claim for a multi-step user-facing Goal requires evidence from the composed supported product journey, tied to identified source/config/runtime, plus relevant independent regression evidence.

## Code and dependency expectations

Keep the FastAPI modular monolith, local-only provider boundaries, SQLite project/run isolation, and explicit runtime readiness/error handling unless current Goal evidence materially requires a change. Prefer reuse/wiring/fix/replacement over adding new abstractions or parallel paths.

## Testing and executable quality gates

Use focused tests for changed behavior, then run integrated relevant verification. Before the normal full suite, run the documented `run` storage preflight.

Normal product regression:

```powershell
python tools\storage_preflight.py --operation run
python -m pytest -q
```

Canonical documentation consistency:

```powershell
python tools\validate_canonical_docs.py
```

Release/package validation remains explicitly separate with `python -m pytest -m release` after the applicable release preparation. Do not treat release-only failures as blockers for the local daily-use Goal unless that lane is explicitly resumed.

## User-facing verification

When the Goal changes task flow, discoverability, screen interaction, status, or recovery:

- shape the UI around the user's job rather than backend modules;
- expose only controls with a real effect;
- keep the primary action and current state/next action obvious;
- exercise the actual rendered Simple UI when live verification is available;
- verify ready/loading/success/error/recovery states that can actually occur;
- prioritize journey blockers/high usability issues and defer cosmetic alternatives after acceptance is met.

## Safety boundaries

- **Runtime:** Windows x64 local application and machine-local runtimes only; no global PATH, service, or shell-handler changes are ordinary work.
- **Production:** no production deployment/cloud publication is enabled; release packaging/external beta is separately scoped.
- **Data:** user-selected source media and project data stay local; source media is not mutated and low disk space never authorizes automatic deletion.
- **Secrets:** do not commit secrets, API keys, tokens, browser profiles, user media, runtime binaries, models, caches, or live databases.
- **External effects:** use explicit authorization/safety for destructive, privileged/security-sensitive, external, or explicitly high-cost effects.

## Change and closure discipline

Keep one active workline by default. Experiments should be disposable. Before changing direction or handing off, preserve unique value through a commit or explicit retained path. At Goal closure, converge unique Goal value into one canonical Product HEAD, remove only proven Goal-created disposable residue, run the predefined acceptance journey and regressions, and push the accepted clean HEAD.

Do not expand a local defect into unrelated refactoring. Apply: if it directly blocks the current Goal, fix it; if it threatens a must-preserve invariant, fix it; otherwise defer it.
