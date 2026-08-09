# Current State

Updated: 2026-08-09
State Revision: 29

## Continuity Fingerprint

- Project ID: tool-autosub
- Branch: main
- HEAD: 8a594d16345112ed35a2a60194869f526a839989
- Worktree: DIRTY
- Active Task ID: NONE
- Last Known Good Commit: 8a594d16345112ed35a2a60194869f526a839989
- Runtime/Data Fingerprint: NOT_CAPTURED

## Current Product Position

- Current milestone: M-001
- Success criterion: The canonical full media completes deterministic bounded source-only AutoSubs transcription through the normal workflow and reaches real Argos translation plus inspected preview/export.
- Last demonstrated behavior/capability: Canonical full Chinese media completed bounded AutoSubs source transcription, local Argos translation, and inspected MP4 export.
- Demo evidence: .ai/evidence/AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY/r001/EVIDENCE_INDEX.md
- Current user-visible limitation: MVP outcome not yet demonstrated

## Delivery Pulse

- Last completed Task ID: AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY
- Last Delivery Delta: EXECUTABLE_CAPABILITY
- Consecutive Non-Shipping Tasks: 0
- Shipping Circuit Breaker: INACTIVE
- Time since last runnable demo: 0
- Next required demo: A clean source repository with explicit stable continuity and external runtime wiring

## Active Work

- Status: IDLE
- Task ID: NONE
- Writer session: NONE
- What is changing: NOTHING
- Current checkpoint: COMPLETED

## Completed and Verified

- AUTOSUB_EXTERNAL_TRANSCRIPTION_MVP-EXTERNAL_TRANSCRIPTION_PROVIDER/r001: AutoSub has a replaceable AutoSubs v3.8.0 subprocess provider that verifies the real engine version and cached small model, normalizes timestamped source cues, and fails safely without translation or fallback. | evidence: `.ai/evidence/AUTOSUB_EXTERNAL_TRANSCRIPTION_MVP-EXTERNAL_TRANSCRIPTION_PROVIDER/r001`
- AUTOSUB_EXTERNAL_TRANSCRIPTION_MVP-ONE_CLICK_EXTERNAL_TRANSCRIPTION_SLICE/r001: One-click Simple Workflow now defaults to AutoSubs local external transcription, preserves the source track, and creates a separate translation track only when languages differ. | evidence: `.ai/evidence/AUTOSUB_EXTERNAL_TRANSCRIPTION_MVP-ONE_CLICK_EXTERNAL_TRANSCRIPTION_SLICE/r001`
- AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP-SHIP_EXTERNAL_TRANSCRIPTION_MVP/r001: AutoSubs v3.8.0 small now provides source-only Chinese timestamped cues through the external provider and source-track resolution, with target-language translation remaining separate. | evidence: `.ai/evidence/AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP-SHIP_EXTERNAL_TRANSCRIPTION_MVP/r001`
- AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-DIAGNOSE_AUTOSUBS_FULL_MEDIA_TIMEOUT/r001: Classified the 900-second full-media failure as normal CPU-bound AutoSubs small-model scaling (root-cause class A), with direct and wrapped evidence; no product files changed. | evidence: `.ai/evidence/AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-DIAGNOSE_AUTOSUBS_FULL_MEDIA_TIMEOUT/r001`
- AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY/r001: Canonical full Chinese media completed bounded AutoSubs source transcription, local Argos translation, and inspected MP4 export. | evidence: `.ai/evidence/AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY/r001`

## Current Blocker

- Problem: NONE
- Confirmed facts: NONE
- Unconfirmed assumptions: NONE
- Attempts: NONE
- Decision required: NONE

## Changed or Sensitive Files

- NONE

## Verification State

- AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY/r001 accepted.
- Snapshot: `51c53f7b7ba885e7c61479bbd7ceac3f32a00453847ab758f33b1c709094f73e`.
- Evidence: `.ai/evidence/AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY/r001`.

## Process State

- PID: NONE
- Port: NONE
- Log: NONE
- Cleanup: NONE

## Cost Efficiency State

- Expected cost range: small until evidence justifies escalation
- Actual cost signal: ledger:AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY:1
- Marginal value status: ACCEPTED
- Repeated operations: NONE
- Economic stop-loss: INACTIVE
- Next spend expected to buy: first runnable SC-001 evidence

## Next Exact Action

1. Select the next smallest milestone-linked outcome.
2. Run `python scripts/ai_os.py report` periodically to tune gates from actual data.

## Do Not Do

- Do not modify application code without a valid active task and claimed lease.
- Do not infer state from chat history.

## Open Owner Decisions

- Product Contract initialization.

## Later / Not MVP

- Anything outside the initialized SC-001 scope.

