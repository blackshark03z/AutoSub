# AI Tech Lead MVP Continuity Protocol

## Purpose

This protocol keeps the Tool Auto Sub MVP coherent across long-running worker sessions. It prevents accidental promotion of unverified claims, accidental release-status changes, and mixed commits that combine project-control documentation with implementation work.

## Authority Order

Use this order when sources disagree:

1. Live repository state measured in the current session.
2. Files committed in Git under `.ai/` and `docs/continuity/`.
3. Current active docs such as `README.md`, `CHANGELOG.md`, `docs/CURRENT_STATE.md`, `docs/ARCHITECTURE.md`, and `docs/OPERATIONS.md`.
4. Recent worker reports, only as `WORKER_REPORTED` until independently verified.
5. Historical checkpoint reports as evidence, not active authority.

## Evidence Labels

- `VERIFIED_SOURCE`: The claim is directly supported by committed project authority files.
- `WORKER_REPORTED`: The claim came from a prior worker report and has not been independently rerun in this task.
- `LIVE_VERIFIED`: The claim was verified in the current live repository/session.
- `LIVE_UNKNOWN`: The current task did not verify the claim.
- `NOT_ACCEPTED`: The claim is explicitly not accepted or not complete.

Do not upgrade a claim from `WORKER_REPORTED` to `LIVE_VERIFIED` unless the current task actually checks it.

## Task Discipline

- Execute one small task at a time.
- Respect the task's explicit file scope.
- Do not build, render, publish, upload, or call providers from a documentation-only task.
- Commit only the intended scope.
- Preserve unrelated working-tree changes.
- If a blocker is encountered, record exact evidence and stop.

## Release Discipline

- CP12B Full Portable remains canonical until a replacement has complete evidence.
- CP13A machine-pass evidence does not transfer to CP13A1.
- CP13A1 needs its own installer, manifest, checksums, release notes, clean install validation, and external-machine acceptance.
- Package storage preflight must pass immediately before package build.

## Resume Discipline

Before resuming implementation:

1. Read `.ai/PROJECT.md`, `.ai/STATE.md`, and `.ai/DECISIONS.md`.
2. Verify branch, HEAD, and working tree.
3. Verify storage gate live if the next task is package work.
4. Confirm whether uncommitted files belong to the task being resumed.
5. Continue only after the next exact action is concrete.
