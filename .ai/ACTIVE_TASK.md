# Task Template — STANDARD

Task Status: COMPLETED
Task Mode: STANDARD
Task ID: AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP-SHIP_EXTERNAL_TRANSCRIPTION_MVP
Task Revision: 1
Created: 2026-08-09
Owner Authorization: APPROVED
Authorization Reference: OWNER DECISION FINAL RECOVERY/SHIP PATH C:\Users\ADMIN\.codex\attachments\d8c7582a-ece9-4fd9-a308-393080d87cf0\pasted-text.txt

## Single Outcome

Materialize the externally Git-verified AutoSubs MVP, run real Chinese-media source-only AutoSubs acceptance and the frozen 5/5 suite, then ship the intentional product delta without redesign.

## Product Link

- Milestone ID: M-001
- Success Criterion: Canonical delta matches the externally reconstructed 11 tracked Git blobs and five SHA-verified untracked files; real source-only Chinese smoke and frozen 5/5 acceptance pass.
- Goal ID: AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP
- Goal Node: SHIP_EXTERNAL_TRANSCRIPTION_MVP
- Delivery Delta: EXECUTABLE_CAPABILITY
- Demonstrable Result: runtime/output evidence listed in task evidence index
- Unlocks: next ready Goal node
- Consecutive Non-Shipping Tasks Before This Task: 0

## Risk and Execution Profile

- Risk At Start: R2
- Risk Tier: R2
- Declared Risk Tier: R2
- Risk Floor: R2
- Risk Floor Reason: R2:shared/API/data/security/background surface
- Negative path required: no
- Execution Profile: STANDARD
- Human review required: trigger-based
- Specialist reviewer trigger: UNSET
- Full suite required: only-if-gate-requires
- Review policy: none
- Acceptance Contract SHA256: 3beb89b353f6f5ffa0b4496242f38a3b3da0ace8db1c789bb4c14ceb7f61d5ae
- Acceptance Contract JSON: {"commands":["cmd /c python -m pytest -q tests/test_external_transcription_provider.py tests/test_external_transcription_vertical_slice.py tests/test_task35_offline_transcription.py tests/test_task40_source_caption_translation.py tests/test_cp10b_simple_workflow.py"],"expected_outputs":["passed"],"probe_files":[],"probe_hashes":{},"effective_risk_at_freeze":"R2","frozen_at":"2026-08-09T05:30:56+00:00","contract_sha256":"3beb89b353f6f5ffa0b4496242f38a3b3da0ace8db1c789bb4c14ceb7f61d5ae"}
- State Hazard Level: S0
- State Hazard Signals: NONE
- State Contract SHA256: 11ed7156957cf0604a027304d0b6da0f59313d984d1506f34d4fe3ecf8bb720e
- State Contract JSON: {"schema_version":1,"level":"S0","authority":"","transitions":[],"invariants":[],"dependencies":["app/api/routes.py","app/services/asr_models.py","app/services/operator_ui.py","app/services/simple_workflow.py","app/services/source_caption_translation.py","app/services/subtitle_tracks.py","app/static/simple/app.js","app/static/simple/index.html","tests/test_task38s_small_model_policy.py","tests/test_task40_source_caption_translation.py","tests/test_v1_scope_cut_gemini_rejection.py","app/providers/asr/autosubs_provider.py","app/services/external_transcription.py","docs/AUTOSUBS_ENGINE_CONTRACT.md","tests/test_external_transcription_provider.py","tests/test_external_transcription_vertical_slice.py"],"signals":[],"contract_sha256":"11ed7156957cf0604a027304d0b6da0f59313d984d1506f34d4fe3ecf8bb720e"}

## Continuity Fingerprint at Authorization

- Project ID: tool-autosub
- Branch: main
- HEAD: 62602ccc6127ae0b730127a621bd20033969feed
- Worktree: DIRTY
- Starting Snapshot SHA256: 41541b5e3b7152f34f3b547dbf1acf03be17587b5fb91f0a4b2336ba54d7cb25
- Verified Snapshot SHA256: 556a51c52ccf67c63fa3f8d12c56f0398b2561393de7cd4c8bceb83198b35bf3
- State Revision: 21
- Context Capsule Revision: 31

## Permission Matrix

### Allowed

- Read: task-relevant repository files
- Modify: app/api/routes.py,app/services/asr_models.py,app/services/operator_ui.py,app/services/simple_workflow.py,app/services/source_caption_translation.py,app/services/subtitle_tracks.py,app/static/simple/app.js,app/static/simple/index.html,tests/test_task38s_small_model_policy.py,tests/test_task40_source_caption_translation.py,tests/test_v1_scope_cut_gemini_rejection.py
- Create: app/providers/asr/autosubs_provider.py,app/services/external_transcription.py,docs/AUTOSUBS_ENGINE_CONTRACT.md,tests/test_external_transcription_provider.py,tests/test_external_transcription_vertical_slice.py
- Commands: focused checks and task-authorized commands
- Local services: NONE
- External calls: NONE
- Data operation: READ_ONLY
- Artifact operation: CREATE_NEW_VERSION
- Git: status/diff/log; commit only if explicitly authorized

### Prohibited

- Scope, production, destructive and architecture actions not listed above.

## Acceptance Criteria

- [ ] Run AutoSubs v3.8.0 small zh source-only against retained canonical Chinese media and inspect JSON cues.
- [ ] Run frozen acceptance unchanged and inspect provider/source/translation/preview output.

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
- Platform: windows
- Model Claimed: UNSPECIFIED
- Identity Verification: VERIFIED
- Session Label: autosubs-ship-worker
- Claimed At: 2026-08-09T05:30:56+00:00
- Last Heartbeat: 2026-08-09T05:49:35+00:00
- Released At: 2026-08-09T05:49:35+00:00
- Takeover From: NONE

## Lifecycle Timing

- Started At: 2026-08-09T05:30:56+00:00
- First Runnable At: NONE
- First Runnable Evidence: NONE
- Completed At: 2026-08-09T05:49:35+00:00

## Completion

- Outcome: AutoSubs v3.8.0 small now provides source-only Chinese timestamped cues through the external provider and source-track resolution, with target-language translation remaining separate.
- Evidence index: .ai/evidence/AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP-SHIP_EXTERNAL_TRANSCRIPTION_MVP/r001/EVIDENCE_INDEX.md
- Evidence Bundle: .ai/evidence/AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP-SHIP_EXTERNAL_TRANSCRIPTION_MVP/r001
- Worker report: .ai/evidence/AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP-SHIP_EXTERNAL_TRANSCRIPTION_MVP/r001/WORKER_REPORT.md
- Review report: NONE
- Ending HEAD: 62602ccc6127ae0b730127a621bd20033969feed
- Lease release: RELEASED

## Revision Stop-Loss Acknowledgement

- Prior failed-first-pass revisions: 0
- Changed root-cause hypothesis: Stop for material redesign, unreconstructable intended Git product state, or origin divergence; do not use prohibited alignment assets.
