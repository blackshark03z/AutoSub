# Quality Gates

> Generated from `config/gates.json`. Edit the machine-readable policy, not this file.

## R0 — LEAN

Docs, read-only analysis, rename, format, metadata; cheapest relevant verification.

| Gate | Policy |
|---|---|
| Focused check | `required` |
| Negative/failure path | `none` |
| Affected integration | `none` |
| Frozen acceptance | `none` |
| Rollback/recovery | `none` |
| Broader/full suite | `none` |
| Review | `none` |

## R1 — LEAN

Pure/local behavior and isolated bug fixes; negative verification only when a meaningful failure path exists.

| Gate | Policy |
|---|---|
| Focused check | `required` |
| Negative/failure path | `when_failure_behavior` |
| Affected integration | `none` |
| Frozen acceptance | `none` |
| Rollback/recovery | `none` |
| Broader/full suite | `none` |
| Review | `none` |

## R2 — STANDARD

Shared/API/persistence/integration work; frozen Goal acceptance when Goal-linked; review only on elevated triggers.

| Gate | Policy |
|---|---|
| Focused check | `required` |
| Negative/failure path | `required` |
| Affected integration | `required` |
| Frozen acceptance | `required_for_goal` |
| Rollback/recovery | `when_recovery_relevant` |
| Broader/full suite | `when_shared_critical_core` |
| Review | `triggered_signed_guardian` |

## R3 — DEEP

Production/destructive/payment/migration/deploy/critical paths; externally attested review required.

| Gate | Policy |
|---|---|
| Focused check | `required` |
| Negative/failure path | `required` |
| Affected integration | `required` |
| Frozen acceptance | `required_for_goal` |
| Rollback/recovery | `required` |
| Broader/full suite | `required` |
| Review | `signed_guardian` |

## Deterministic close gate

`COMPLETED` requires a released lease, valid task-scoped evidence bound to the verified snapshot, accepted outcome/history, and synchronized state.

## Visual/media/artifact gate

Representative output must be opened/inspected when the artifact can be meaningfully inspected; risk-proportional evidence still applies.

## Milestone gate

A milestone closes only when its observable success criterion has demo/acceptance evidence; test count or document count alone is insufficient.
