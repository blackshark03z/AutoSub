# Task Template — STANDARD

Task Status: READY
Task Mode: STANDARD
Task ID: TASK-XXX
Task Revision: 1
Created: YYYY-MM-DD
Owner Authorization: REQUIRED_IF_R3
Authorization Reference: REQUIRED_IF_APPROVED

## Single Outcome

[One observable outcome.]

## Product Link

- Milestone ID: M-XXX
- Success Criterion: SC-XXX
- Goal ID: NONE
- Goal Node: NONE
- Delivery Delta: EXECUTABLE_CAPABILITY
- Demonstrable Result: UNSET
- Unlocks: UNSET
- Consecutive Non-Shipping Tasks Before This Task: 0

## Risk and Execution Profile

- Risk At Start: R0
- Risk Tier: R1_OR_R2
- Declared Risk Tier: auto
- Risk Floor: R0
- Risk Floor Reason: none
- Negative path required: no
- Execution Profile: STANDARD
- Human review required: trigger-based
- Specialist reviewer trigger: UNSET
- Full suite required: only-if-gate-requires
- Review policy: auto
- Acceptance Contract SHA256: NONE
- Acceptance Contract JSON: {}
- State Hazard Level: S0
- State Hazard Signals: NONE
- State Contract SHA256: NONE
- State Contract JSON: {}

## Continuity Fingerprint at Authorization

- Project ID: UNSET
- Branch: UNSET
- HEAD: UNSET
- Worktree: CLEAN
- Starting Snapshot SHA256: UNSET
- Verified Snapshot SHA256: NONE
- State Revision: UNSET
- Context Capsule Revision: UNSET

## Permission Matrix

### Allowed

- Read: UNSET
- Modify: UNSET
- Create: UNSET
- Commands: UNSET
- Local services: UNSET
- External calls: NONE_UNLESS_LISTED
- Data operation: READ_ONLY
- Artifact operation: CREATE_NEW_VERSION
- Git: status/diff/log; commit only if explicitly authorized

### Prohibited

- Scope, production, destructive and architecture actions not listed above.

## Acceptance Criteria

- [ ] Observable outcome exists.
- [ ] Critical negative path checked.
- [ ] Output and side effects match preflight.

## Verification Plan

1. Cheapest focused check.
2. Affected runtime/integration check.
3. Inspect final output and Git diff.

## Before-Execution Preflight

- Inputs: UNSET
- Outputs: UNSET
- Files created: UNSET
- Files overwritten: NONE
- Data mutated: NONE
- External/provider calls: NONE
- Expected provider cost: UNKNOWN
- Disk requirement: UNSET
- RAM/GPU requirement: UNSET
- Process/port: NONE
- Cache/artifact lineage: UNSET
- Rollback: UNSET

## Cost Efficiency Plan

- Outcome value: UNSET
- Expected cost range: UNSET
- Primary cost drivers: UNSET
- Cheapest evidence-first sequence: UNSET
- Initial execution profile: STANDARD
- Escalation conditions: UNSET
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

- Lease Status: UNCLAIMED
- Writer Role: WORKER
- Platform: UNSET
- Model Claimed: UNSPECIFIED
- Identity Verification: UNSET
- Session Label: UNSET
- Claimed At: NONE
- Last Heartbeat: NONE
- Released At: NONE
- Takeover From: NONE

## Lifecycle Timing

- Started At: NONE
- First Runnable At: NONE
- First Runnable Evidence: NONE
- Completed At: NONE

## Completion

- Outcome: NONE
- Evidence index: NONE
- Evidence Bundle: NONE
- Worker report: NONE
- Review report: NONE
- Ending HEAD: UNSET
- Lease release: PENDING
