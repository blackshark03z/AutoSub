# Task Template — STANDARD

Task Status: COMPLETED
Task Mode: STANDARD
Task ID: AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY
Task Revision: 1
Created: 2026-08-09
Owner Authorization: APPROVED
Authorization Reference: Owner directive e91e4ae4-92d0-41bb-85be-4d5507b11f15; Task 1 class A diagnosis

## Single Outcome

Implement only the smallest AutoSubs full-media reliability fix supported by Task 1, preserve source-only timestamp semantics, then prove the full normal workflow through existing Argos translation and preview/export.

## Product Link

- Milestone ID: M-001
- Success Criterion: The canonical full media completes deterministic bounded source-only AutoSubs transcription through the normal workflow and reaches real Argos translation plus inspected preview/export.
- Goal ID: AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY
- Goal Node: FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY
- Delivery Delta: EXECUTABLE_CAPABILITY
- Demonstrable Result: runtime/output evidence listed in task evidence index
- Unlocks: next ready Goal node
- Consecutive Non-Shipping Tasks Before This Task: 0

## Risk and Execution Profile

- Risk At Start: R2
- Risk Tier: R2
- Declared Risk Tier: R2
- Risk Floor: R2
- Risk Floor Reason: R2:persistent data version creation
- Negative path required: no
- Execution Profile: STANDARD
- Human review required: trigger-based
- Specialist reviewer trigger: UNSET
- Full suite required: only-if-gate-requires
- Review policy: none
- Acceptance Contract SHA256: 948a48d3857c14ec5d8223d4ed68baf842fc3d8c99e8588c3d4f0cb5397420f3
- Acceptance Contract JSON: {"commands":["python -m pytest -q tests/test_external_transcription_provider.py tests/test_external_transcription_vertical_slice.py tests/test_task40_source_caption_translation.py tests/test_subtitle_presentation_timeline.py tests/test_task36_one_button_flow.py tests/test_v1_scope_cut_gemini_rejection.py"],"expected_outputs":["passed"],"probe_files":[],"probe_hashes":{},"effective_risk_at_freeze":"R2","frozen_at":"2026-08-09T08:10:06+00:00","contract_sha256":"948a48d3857c14ec5d8223d4ed68baf842fc3d8c99e8588c3d4f0cb5397420f3"}
- State Hazard Level: S1
- State Hazard Signals: explicit:S1, isolated D: workflow run and preview/export artifacts
- State Contract SHA256: 3cfedf07ed8c7de26111378ea4d752cf5b52d69569bca6af3594ac4a7a70dc72
- State Contract JSON: {"schema_version":1,"level":"S1","authority":"","transitions":[],"invariants":[],"dependencies":["app/providers/asr/autosubs_provider.py,app/services/external_transcription.py,app/services/simple_workflow.py,app/services/offline_translation.py,app/services/subtitle_tracks.py"],"signals":["explicit:S1","isolated D: workflow run and preview/export artifacts"],"contract_sha256":"3cfedf07ed8c7de26111378ea4d752cf5b52d69569bca6af3594ac4a7a70dc72"}

## Continuity Fingerprint at Authorization

- Project ID: tool-autosub
- Branch: main
- HEAD: 8a594d16345112ed35a2a60194869f526a839989
- Worktree: CLEAN
- Starting Snapshot SHA256: 1ae36ab8834161a73f7d75f03778b39ad1e8c9a1564803901cf293f659dede12
- Verified Snapshot SHA256: 51c53f7b7ba885e7c61479bbd7ceac3f32a00453847ab758f33b1c709094f73e
- State Revision: 28
- Context Capsule Revision: 41

## Permission Matrix

### Allowed

- Read: task-relevant repository files
- Modify: app/providers/asr/autosubs_provider.py,app/services/external_transcription.py,tests/test_external_transcription_provider.py,tests/test_external_transcription_vertical_slice.py
- Create: NONE
- Commands: focused checks and task-authorized commands
- Local services: NONE
- External calls: NONE
- Data operation: CREATE_NEW_VERSION
- Artifact operation: CREATE_NEW_VERSION
- Git: status/diff/log; commit only if explicitly authorized

### Prohibited

- Scope, production, destructive and architecture actions not listed above.

## Acceptance Criteria

- [ ] Implement no arbitrary timeout increase; retain bounded clear failure behavior and add focused regression coverage.
- [ ] If chunking is justified, preserve absolute timestamps, source semantics, provenance, and prevent overlap duplicates.
- [ ] Run full retained-media normal workflow, real Argos translation, preview/export, and frozen regression bundle.

## Verification Plan

1. Cheapest focused check.
2. Affected runtime/integration check.
3. Inspect final output and Git diff.

## Before-Execution Preflight

- Inputs: task-relevant source and fixtures
- Outputs: accepted outcome and evidence
- Files created: NONE
- Files overwritten: NONE
- Data mutated: NONE
- External/provider calls: NONE
- Expected provider cost: 0.0
- Disk requirement: MINIMAL
- RAM/GPU requirement: MINIMAL
- Process/port: NONE
- Cache/artifact lineage: source inputs and evidence manifest
- Rollback: revert task-scoped diff and remove new artifacts

## Cost Efficiency Plan

- Outcome value: UNSET
- Expected cost range: small; investigate repeated attempts without evidence
- Primary cost drivers: implementation, verification and output inspection
- Cheapest evidence-first sequence: focused → affected regression/runtime → acceptance contract → diff review
- Initial execution profile: STANDARD
- Escalation conditions: risk exceeds profile or same approach fails twice
- Marginal value checkpoint: before repeated expensive operation
- Continue spending when: next spend buys evidence, lower uncertainty, acceptance or safety proof
- Split/change strategy when: same approach fails twice or no new evidence
- Quality gates that may not be reduced: acceptance, safety, authorization, output, regression, rollback, cleanup

## Economic Stop-Loss Conditions

- Same approach failed twice.
- Repeated test/provider/context/review without relevant change.
- Scope expansion or two work cycles without new evidence.

## Relevant Decisions

- NONE_LISTED

## Execution Lease

- Lease Status: RELEASED
- Writer Role: WORKER
- Platform: ChatGPT
- Model Claimed: UNSPECIFIED
- Identity Verification: VERIFIED
- Session Label: autosub-full-media-reliability-worker
- Claimed At: 2026-08-09T08:10:07+00:00
- Last Heartbeat: 2026-08-09T09:20:30+00:00
- Released At: 2026-08-09T09:20:30+00:00
- Takeover From: NONE

## Lifecycle Timing

- Started At: 2026-08-09T08:10:07+00:00
- First Runnable At: NONE
- First Runnable Evidence: NONE
- Completed At: 2026-08-09T09:20:30+00:00

## Completion

- Outcome: Canonical full Chinese media completed bounded AutoSubs source transcription, local Argos translation, and inspected MP4 export.
- Evidence index: .ai/evidence/AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY/r001/EVIDENCE_INDEX.md
- Evidence Bundle: .ai/evidence/AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY/r001
- Worker report: .ai/evidence/AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY-FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY/r001/WORKER_REPORT.md
- Review report: NONE
- Ending HEAD: 8a594d16345112ed35a2a60194869f526a839989
- Lease release: RELEASED
