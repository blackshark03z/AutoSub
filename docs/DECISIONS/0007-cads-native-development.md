# 0007 — CADS-native development control

**Status:** Accepted  
**Date:** 2026-09-09  
**Scope:** AutoSub engineering control, project context, and completion semantics

## Context

AutoSub previously carried an external Build OS v1.22 lifecycle authority plus an in-repository v1.16 historical control-plane archive. That model added lifecycle/adoption/validation authority outside ordinary product Git/test/runtime work. The Owner explicitly directed AutoSub to drop Build OS and update to current Convergent AI Development System (CADS).

Current CADS defines a lightweight control model (`Reality -> Intent/Design -> Change -> Acceptance -> Consequence`) and explicitly keeps ordinary development native to Git, editors, tests, CI/Worker, and identified runtime evidence. It requires composed Critical User Journey evidence for multi-step user-facing Product Goals rather than inferring Product PASS from isolated feature tests.

## Decision

1. AutoSub uses current CADS as its AI-assisted engineering standard and root `AGENTS.md` as the minimum project activation surface.
2. Build OS lifecycle authority/policy/current-tree archives are retired from AutoSub; Git history is sufficient provenance for historical control-plane material.
3. Ordinary AutoSub work requires no separate lifecycle/adoption/grant/close runtime. Source edits, focused tests, real journey evidence, normal regression, ordinary commits, and Git convergence remain the execution path.
4. One bounded Product Goal and representative CUJ define completion. Tests are supporting verification evidence, not a substitute for the product journey.
5. Consequential/destructive/external effects still require appropriate explicit authorization/safety; dropping Build OS does not weaken those boundaries.

## Why

This removes a redundant second development-control runtime, reduces ceremony and stale authority, and aligns the repository with CADS's current convergence model while preserving product safety and evidence requirements.

## Consequences

- `AGENTS.md`, `TASK.md`, root `ARCHITECTURE.md`, and `docs/DECISIONS/` carry durable/current engineering context.
- Product docs should describe product/runtime truth rather than Build OS task state.
- Historical Build OS material is recovered from Git history if ever needed, not kept active in the current source tree.
- A future Worker must not require Build OS commands to edit/test/commit AutoSub.

## Revisit When

Revisit only if CADS itself materially changes its universal control model, or if dropping the prior control plane demonstrably causes a serious/repeated failure that current CADS rules cannot handle.
