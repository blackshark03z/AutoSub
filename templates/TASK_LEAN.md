# Task Template — LEAN Fast Lane

Task Status: READY
Task Mode: LEAN
Task ID: TASK-XXX
Task Revision: 1
Created: YYYY-MM-DD
Owner Authorization: NOT_REQUIRED
Authorization Reference: NONE

## Single Outcome

[One observable outcome.]

## Product Link

- Milestone ID: M-XXX
- Success Criterion: SC-XXX
- Delivery Delta: EXECUTABLE_CAPABILITY

## Risk and Execution Profile

- Risk Tier: R0_OR_R1
- Declared Risk Tier: auto
- Risk Floor: R0
- Risk Floor Reason: none
- Negative path required: no
- Execution Profile: LEAN
- Human review required: no
- Full suite required: no

## Continuity Fingerprint at Authorization

- Project ID: UNSET
- Branch: UNSET
- HEAD: UNSET
- Worktree: CLEAN
- Starting Snapshot SHA256: UNSET
- Verified Snapshot SHA256: NONE
- State Revision: 0
- Context Capsule Revision: 0

## Permission Matrix

### Allowed

- Read: task-relevant repository files
- Modify: UNSET
- Create: NONE
- Commands: focused checks and task-authorized commands
- External calls: NONE
- Data operation: READ_ONLY
- Artifact operation: CREATE_NEW_VERSION

### Prohibited

- Production, destructive, architecture and out-of-scope actions not explicitly listed.

## Acceptance Criteria

- [ ] Observable outcome exists.
- [ ] Critical negative path is checked when `Negative path required: yes`.
- [ ] Final output and task-scoped diff are inspected.

## Verification Plan

1. Focused check.
2. Negative-path check only when the task has a real failure behavior.
3. Runtime/output and diff inspection.

## Before-Execution Preflight

- External/provider calls: NONE
- Files overwritten: NONE
- Data mutated: NONE
- Expected provider cost: UNKNOWN
- Process/port: NONE
- Rollback: revert task-scoped diff and remove new artifacts

## Execution Lease

- Lease Status: UNCLAIMED
- Writer Role: WORKER
- Platform: UNSET
- Model Claimed: UNSPECIFIED
- Identity Verification: PENDING
- Session Label: UNSET
- Claimed At: NONE
- Last Heartbeat: NONE
- Released At: NONE

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
