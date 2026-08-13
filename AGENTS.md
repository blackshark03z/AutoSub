# AutoSub Build OS v1.22 lifecycle operating guidance

## Active control plane

AutoSub uses one verified external **Build OS v1.22** transactional control
plane. It is intentionally outside this repository at:

`C:\ToolAutoSub\.build-os-v1.22-lifecycle-kit-1.1.2-continuity-1.1.0-context-epoch-1.0.1`

Package provenance:

- archive: `D:\Youtube\_packages\Senior_AI_Build_OS_Stable_v1.22_lifecycle_v1.1.2_continuity_v1.1.0_context_epoch_v1.0.1.zip`
- archive SHA-256: `0749714490B946F3C1C66AFC10533ACF03E6F900A2A7246B721969574A8A9C78`
- frozen source commit: `e41ca10826b32b2d46a3b859345f734c113e00ae`
- lifecycle kit: `1.1.2`; continuity skill: `1.1.0`; context epoch capability: `1.0.1`
- authority record: `.buildos-authority.json`

Use the normal Worker facade with `--root C:\ToolAutoSub\AutoSub`:

```powershell
python C:\ToolAutoSub\.build-os-v1.22-lifecycle-kit-1.1.2-continuity-1.1.0-context-epoch-1.0.1\scripts\ai.py --root C:\ToolAutoSub\AutoSub status
```

Before Worker takeover and before lifecycle operations, run the package-native
execution-authority preflight. Use the `project-lifecycle-bootstrap` and
`documentation-handoff-continuity` Skills for their documented lifecycle and
continuity steps. The local `.buildos/` directory is an excluded runtime, not a
tracked product artifact. The default-on Field Study is external at
`C:\ToolAutoSub\.buildos-field-study\AutoSub` and is append-only,
evidence-backed, and non-blocking.

## Lifecycle and proof

The single lifecycle authority is the validated immutable generation selected
by `.buildos/control/CURRENT`. Generated packets are guidance only. Normal
flow is `bootstrap` → ordinary Git commit → `record-commit` → `validate` →
`close`; `recover` repairs only control-plane pointer/receipt/projection state.
There is no destructive reopen: a post-validation product change needs
`new-revision`, which preserves prior evidence.

Risk is derived from actual side effects and cannot be downgraded:

- R0: read-only.
- R1: write/create.
- R2: mutation or type change.
- R3: delete; requires explicit Owner approval/reference, independent review,
  and a distinct rollback/recovery check before validation.

Every product task must use a bounded allow/prohibit scope and immutable
validation evidence. The kernel does not treat Owner acceptance as a substitute
for required R3 independent review. It does not implement legacy Goal budgets,
delivery-delta breakers, Guardian attestations, or task-finalization ceremonies;
do not infer those v1.16 rules. Product Goals, decomposition, and worktree
orchestration remain project decisions outside the v1.22 kernel, with a
cooperative single product writer per worktree.

Context governance is supervisory: use current prompt P against measured window
W (warn 50%, compact in the same chat at 70%, evidence-gated rollover at 80%,
hard stop at W minus the configured reserve). Request counts and historical
peaks are observational only. Missing telemetry/window measurements must remain
truthfully unmeasured; a labeled 128k fallback is not a claim about the active
model.

## Workflow convention

Goal worker owns execution until terminal state or a genuine Owner-only
blocker. Routine task transitions, validation retries, Scouts, Workers and
Reviewers are internal execution and must not require Owner relay.

Use one Codex Goal for a large feature and keep it in one thread by default.
Compact natively before rollover; use a Context Epoch successor only when the
governor requires it. Keep detailed worker evidence in files while active model
context retains bounded summaries and pointers. Check Field Study eligibility
at terminal tasks before discarding task context.

## Preserved legacy record

`archives/senior-ai-build-os-v1.16/` is the read-only historical record of the
retired legacy control plane. It is provenance only, is not discoverable as
lifecycle authority, and must not be edited to emulate the new kernel. AutoSub
application code, providers,
translation, UI, runtime binaries, and product behavior are outside Build OS
adoption scope.
