# Engineering Contract

## Code and dependency expectations

Keep the FastAPI modular monolith, local-only provider boundaries, SQLite project isolation, and explicit runtime readiness/error handling. Keep product code separate from Build OS control artifacts.

## Testing and executable quality gates

Use focused tests for changed behavior and the full pytest suite only after the documented storage preflight. Documentation-only control-plane work runs `python tools\validate_canonical_docs.py`.

Run validation as a coherent slice: inspect, implement, focused tests, fix relevant failures, integrated relevant validation, and one final acceptance pass. Persist detailed command output once, then retain a short summary and pointer.

### Normal versus release validation

Normal product regression is `python -m pytest -q` and excludes tests marked
`release`. Release/package and retained historical-fixture validation remains
explicitly executable with `python -m pytest -m release`; run the applicable
CP08F/CP09, CP12B, or CP13A generation/bootstrap command first. The marker does
not skip or weaken assertions; it keeps release-only checks out of the normal
product suite while preserving a separately runnable gate.

## Safety boundaries

- **Runtime:** Windows x64 local application and machine-local runtimes only; no global PATH, service, or shell-handler changes are ordinary work.
- **Production:** No production deployment or cloud publication is enabled; release packaging and external beta activity require separately authorized work.
- **Data:** User-selected source media and project data remain local; source media is not mutated and low disk space never authorizes automatic deletion.
- **Secrets:** Do not commit secrets, API keys, tokens, browser profiles, user media, runtime binaries, models, or caches.

## Workflow convention

Goal worker owns execution until terminal state or a genuine Owner-only blocker. Routine task transitions, validation retries, Scouts, Workers and Reviewers are internal execution and must not require Owner relay.

Treat a large feature as one Codex Goal and keep that Goal in one thread by default. Compact natively before rollover; use a Context Epoch successor only when the governor requires it. Keep detailed worker evidence in files while the active model retains bounded summaries and pointers. Perform a Field Study eligibility check when terminal work ends, before task context is discarded.

The control-plane task `BUILD_OS_V122_LIFECYCLE_ADOPTION/r001` is documentation-only. Its immutable evidence validates the v1.22 transition and does not represent a product behavior change.

## Avoid

- Do not bypass configured quality gates or copy active state into canonical docs.
