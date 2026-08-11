# AutoSub Build OS v1.22 operating guidance

## Active control plane

AutoSub uses the verified external **Build OS v1.22** transactional control
plane. It is intentionally outside this repository at:

`C:\ToolAutoSub\.build-os-v1.22-portable-field-proven\build-os-v1.22-portable`

Package provenance:

- archive: `D:\build-os-v1.22-portable-field-proven.zip`
- archive SHA-256: `A48DD4CEA972FEEA10B1587FCD38A88E00F89CFE92BFEAB986B5308D1FD0E463`
- frozen source commit: `e41ca10826b32b2d46a3b859345f734c113e00ae`
- edition: `PROJECTED_HEADROOM_ONE_CHAT_HYBRID`, field-proven

Use the normal Worker facade with `--root C:\ToolAutoSub\AutoSub`:

```powershell
python C:\ToolAutoSub\.build-os-v1.22-portable-field-proven\build-os-v1.22-portable\build-os\scripts\ai.py --root C:\ToolAutoSub\AutoSub status
```

Use the administrative facade only for `new-revision`, `abort`, or telemetry
ingest. The local `.buildos/` directory is an excluded runtime, not a tracked
product artifact. The default-on Field Study is external at
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

## Preserved legacy record

`.ai/` is the read-only historical record of the retired Senior AI Build OS
v1.16: completed goals, task history, evidence, and prior project context are
preserved for provenance. It is not lifecycle authority for v1.22 and must not
be edited to emulate the new kernel. AutoSub application code, providers,
translation, UI, runtime binaries, and product behavior are outside Build OS
adoption scope.
