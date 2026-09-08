# Goal

Make the current local AutoSub V1 genuinely stable end-to-end for its normal single-user Windows job: a user can double-click AutoSub, choose a local Chinese-dialogue video, create a verified English-subtitled MP4 with one primary action, inspect the result, reach the actual output folder, and recover from an applicable failure without terminal knowledge.

This Goal also completes the engineering-control transition from the retired AutoSub Build OS lifecycle/control plane to current CADS-native Git/test/runtime development.

# Critical User Journey

1. User double-clicks `Run AutoSub.cmd`.
2. AutoSub reuses or starts exactly one verified local server and opens the Simple UI.
3. User chooses/drops a supported local video. AutoSub validates the source and shows its identity/metadata without creating or starting processing prematurely.
4. User sees only meaningful working choices, then clicks the single primary action `Tạo video có phụ đề`.
5. AutoSub verifies/prepares the managed AutoSubs + Argos runtime as needed and exposes real processing stages.
6. AutoSubs produces real source speech transcription; Argos produces Chinese-to-English subtitle content; ffmpeg renders the subtitle video.
7. AutoSub exposes the completed state only when result validation passes. User can preview the real MP4.
8. `Mở thư mục kết quả` actually opens the output folder. User can then create a new video without stale run state leaking into the next cycle.
9. If runtime/source/render preparation fails, AutoSub fails closed, preserves the useful selection/context when safe, explains the problem in user terms, and provides the relevant retry/back path.

# Acceptance

- **A1 — CADS activation / Build OS retirement:** root `AGENTS.md`, `TASK.md`, `ARCHITECTURE.md`, and decision index describe current CADS-native control; active Build OS authority/policy/adoption artifacts and the retired in-repo Build OS archive are absent from the canonical working tree. No external Build OS lifecycle command is required for ordinary development.
- **A2 — Launch:** from the canonical Product HEAD on the supported Windows machine, `Run AutoSub.cmd` reaches a healthy `127.0.0.1:8173` Simple UI without terminal interaction; a healthy existing AutoSub process is reused and an unrelated port occupant is never killed.
- **A3 — Setup:** the representative fixture can be selected/validated from the rendered Simple UI; only controls that have a real product effect are presented as working controls; exactly one obvious primary create action advances the normal journey.
- **A4 — Real processing:** with test-fixture subtitle injection disabled, the representative fixture completes through real managed runtime readiness, provider transcription, local translation, subtitle rendering, and final validation. The resulting MP4 exists, is not the source bytes, contains non-empty subtitle rendering, reports eligible completion, and has non-fixture provenance.
- **A5 — Result/useful outcome:** the completed UI loads the output preview; `Mở thư mục kết quả` triggers a real Windows folder-open action; `Tạo video mới` returns to a fresh setup state without deleting prior results.
- **A6 — Recovery:** at least one controlled runtime-readiness failure remains fail-closed and offers a retry/back path without exposing an invalid completed result or requiring the user to reconstruct the selected source manually.
- **A7 — Regression:** focused journey/runtime/launcher tests pass, `python tools\validate_canonical_docs.py` passes, storage preflight permits the run, and the normal `python -m pytest -q` regression passes. Release-only tests are not part of this Goal.
- **A8 — Convergence:** one clean canonical `main` Product HEAD contains the accepted change, no competing Goal-created implementation/worktree remains, and the accepted HEAD is pushed to `origin/main`.

Acceptance is not weakened merely because a subsystem test passes. A2-A6 require source/runtime/config identity tied to the exercised evidence.

# Acceptance Fixture / Golden Input

Use the existing 15-second real local fixture already present in AutoSub's operator-upload store:

`C:\ToolAutoSub\AutoSub\data\operator_uploads\8f5454dd0b448583a7a497b79bcc34f8903d017090cd4f8fb2e8308fdaa5a442.mp4`

Observed baseline evidence before implementation: a prior completed run exists for this source with `result_eligible=true` and subtitle provenance `provider_transcription`. The media itself remains ignored/local and is never committed.

# Non-goals

- EXE, installer, portable-package, or external-beta/release-lane work.
- New language pairs beyond the current Chinese-to-English V1.
- Enabling Gemini, ElevenLabs, YouTube upload/publish, or other paid/cloud product paths.
- Broad module/architecture refactoring unrelated to a direct CUJ blocker or must-preserve invariant.
- Improving OCR unless the normal audio-transcription CUJ demonstrates that OCR is a direct blocker.
- Cosmetic redesign after journey blockers/high usability defects are resolved.

# Constraints

- Windows-first, local single-user product.
- Preserve source/user media; low disk space never authorizes automatic deletion.
- Fail closed on invalid subtitle/output evidence.
- Keep current FastAPI modular-monolith + SQLite project/run isolation unless real evidence forces a change.
- Generated media, runtime/models/caches, secrets, and databases stay outside Git.
- The historical release lane stays separate from normal regression.

# Material Decisions

- CADS replaces the AutoSub Build OS lifecycle/control-plane model; ordinary development is native Git/test/runtime work.
- V1 stability is defined by the composed CUJ, not by the sum of feature tests.
- Misleading no-op controls are removed from the normal UI rather than implementing speculative options that the current Goal does not need.
- The default V1 output contract remains one verified `final_video.mp4`; advanced export naming/destination configurability is deferred until a real product requirement exists.

See `docs/DECISIONS/0007-cads-native-development.md`.

# Progress

- CADS local checkout verified at `a3e24a1`; AutoSub is migrated to CADS-native project context and engineering control.
- Build OS authority/policy/adoption artifacts, canonical archive source, local `.buildos` runtime residue, and AutoSub-specific external Build OS packages/field-study residue were retired as directed.
- Result-folder action now performs the real Windows folder-open consequence; misleading no-op Simple UI controls were removed from the primary journey.
- Upload dedupe ownership was hardened so a failed preflight cannot delete an already-existing hash-deduplicated source.
- Focused journey/runtime/launcher regression: PASS after repair.
- Real rendered Playwright CUJ: PASS on `run_20260908183651205064_2d923ddc`; provenance `provider_transcription`; 7 real ASS dialogue events; output hash differs from source; preview/result eligible; real folder-open PASS; fresh-video reset PASS; 1365 px and 390 px layouts PASS.
- Controlled `runtime_readiness_failed` journey: PASS fail-closed with retry/back, no fake preview, `result_eligible=false`, and selected-source preservation.
- Storage preflight: PASS. Canonical docs validator: PASS before final evidence sync. Normal full `python -m pytest -q`: PASS with exit code 0; release-only/deferred tests remain intentionally outside this Goal.

# Discoveries / Blockers

No product or Owner-only blocker remains. Product acceptance A1-A7 is satisfied by bounded source/config/runtime identity and rendered/runtime evidence.

# Next Safe Action

Perform final Git convergence only: rerun the lightweight final gates on the synchronized tree, stage the bounded Goal diff, commit to `main`, push to `origin/main`, and verify a clean `HEAD == origin/main`. After that A8 is satisfied and this Goal requires no further product work.
