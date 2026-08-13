# Worker Packet

Generated: 2026-08-09T09:20:30+00:00
Capsule Revision: 43

## Task

- ID: AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY/r001
- Status: COMPLETED
- Goal: AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY / node FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY
- Milestone / criterion: M-001 / The canonical full media completes deterministic bounded source-only AutoSubs transcription through the normal workflow and reaches real Argos translation plus inspected preview/export.
- Risk / profile: R2 / STANDARD
- Negative path required: no
- Shipping breaker: INACTIVE (0/3 non-shipping)

## Outcome

Implement only the smallest AutoSubs full-media reliability fix supported by Task 1, preserve source-only timestamp semantics, then prove the full normal workflow through existing Argos translation and preview/export.

## Goal Context

Make the retained 366.270998-second Chinese video complete source-only AutoSubs transcription through the normal AutoSub workflow with bounded, explainable reliability semantics, then complete existing local Argos translation and preview/ex

## Scout Handoff

NONE

## Scope

- Modify: app/providers/asr/autosubs_provider.py,app/services/external_transcription.py,tests/test_external_transcription_provider.py,tests/test_external_transcription_vertical_slice.py
- Create: NONE
- External calls: NONE
- Pre-existing dirty files: 0 (not part of task unless changed again)
- Current task delta: NONE

## Acceptance

- [ ] Implement no arbitrary timeout increase; retain bounded clear failure behavior and add focused regression coverage.
- [ ] If chunking is justified, preserve absolute timestamps, source semantics, provenance, and prevent overlap duplicates.
- [ ] Run full retained-media normal workflow, real Argos translation, preview/export, and frozen regression bundle.

## Verify

1. Cheapest focused check.
2. Affected runtime/integration check.
3. Inspect final output and Git diff.
- Acceptance contract: 948a48d3857c14ec5d8223d4ed68baf842fc3d8c99e8588c3d4f0cb5397420f3 (predeclared commands=1, locked probes=0)
- Review policy: none

## Stop

- Stop/change strategy after two failed attempts without new evidence.
- Amend scope instead of widening it silently.
- Final output and task delta must be inspected before acceptance.
- If shipping breaker is ACTIVE, do not start/continue non-shipping work without an explicit override.

## Shared/Operational Context

- Product goal: A user can take a local video from timestamped source transcript through a separate translation track to preview/export with no manual ASR configuration.
- Data operation: CREATE_NEW_VERSION
- Artifact operation: CREATE_NEW_VERSION
- Rollback: revert task-scoped diff and remove new artifacts

## Relevant Decisions

- NONE_LISTED
