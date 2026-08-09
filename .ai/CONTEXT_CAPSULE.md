# Worker Packet

Generated: 2026-08-09T05:49:35+00:00
Capsule Revision: 33

## Task

- ID: AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP-SHIP_EXTERNAL_TRANSCRIPTION_MVP/r001
- Status: COMPLETED
- Goal: AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP / node SHIP_EXTERNAL_TRANSCRIPTION_MVP
- Milestone / criterion: M-001 / Canonical delta matches the externally reconstructed 11 tracked Git blobs and five SHA-verified untracked files; real source-only Chinese smoke and frozen 5/5 acceptance pass.
- Risk / profile: R2 / STANDARD
- Negative path required: no
- Shipping breaker: INACTIVE (0/3 non-shipping)

## Outcome

Materialize the externally Git-verified AutoSubs MVP, run real Chinese-media source-only AutoSubs acceptance and the frozen 5/5 suite, then ship the intentional product delta without redesign.

## Goal Context

Validate and ship the preserved AutoSubs external-transcription MVP using the already verified Git-object reconstruction, real Chinese-media execution, and frozen product acceptance.

## Scout Handoff

NONE

## Scope

- Modify: app/api/routes.py,app/services/asr_models.py,app/services/operator_ui.py,app/services/simple_workflow.py,app/services/source_caption_translation.py,app/services/subtitle_tracks.py,app/static/simple/app.js,app/static/simple/index.html,tests/test_task38s_small_model_policy.py,tests/test_task40_source_caption_translation.py,tests/test_v1_scope_cut_gemini_rejection.py
- Create: app/providers/asr/autosubs_provider.py,app/services/external_transcription.py,docs/AUTOSUBS_ENGINE_CONTRACT.md,tests/test_external_transcription_provider.py,tests/test_external_transcription_vertical_slice.py
- External calls: NONE
- Pre-existing dirty files: 18 (not part of task unless changed again)
- Current task delta: NONE

## Acceptance

- [ ] Run AutoSubs v3.8.0 small zh source-only against retained canonical Chinese media and inspect JSON cues.
- [ ] Run frozen acceptance unchanged and inspect provider/source/translation/preview output.

## Verify

1. Cheapest focused check.
2. Affected runtime/integration check.
3. Inspect final output and Git diff.
- Acceptance contract: 3beb89b353f6f5ffa0b4496242f38a3b3da0ace8db1c789bb4c14ceb7f61d5ae (predeclared commands=1, locked probes=0)
- Review policy: none

## Stop

- Stop/change strategy after two failed attempts without new evidence.
- Amend scope instead of widening it silently.
- Final output and task delta must be inspected before acceptance.
- If shipping breaker is ACTIVE, do not start/continue non-shipping work without an explicit override.

## Shared/Operational Context

- Product goal: A user can take a local video from timestamped source transcript through a separate translation track to preview/export with no manual ASR configuration.
- Data operation: READ_ONLY
- Artifact operation: CREATE_NEW_VERSION
- Rollback: revert task-scoped diff and remove new artifacts

## Relevant Decisions

- NONE_LISTED
