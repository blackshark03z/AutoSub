# Current State

This document is the current human-readable baseline. It should agree with `project_state.json`.

## Canonical Facts

- Repository source baseline: `e7e1a89`
- Current product release: `CP12B Full Portable`
- Package path: `release\CP12B\tool_auto_sub_windows_full_portable_cp12b.zip`
- Package SHA-256: `9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0`
- Release ID: `CP12B_WINDOWS_FULL_PORTABLE_CREATIVE_IMPORT_BETA`
- Distribution: `Unified Full Portable`
- Current one-click beta candidate: `CP13A1 Complete Payload Hotfix`
- CP13A1 installer path: `release\CP13A1\ToolAutoSubBetaSetup_CP13A1.exe`
- CP13A1 installer SHA-256: `9ca6f54c9d3caa440ea410f40aba819c75f7240954a316841d5da7ca4f9e317a`
- CP13A1 release ID: `CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX`
- CP13A1 distribution: `Per-user Windows EXE installer with complete installed-root payload wrapping CP12B`
- CP13A1 machine validation: `CP13A1_COMPLETE_INSTALL_PAYLOAD_AND_UNINSTALL_SHORTCUT_MACHINE_PASS`
- CP13A1 evidence state: `RERUN_PASS_PENDING_ACCEPTANCE`
- Primary UI: `Simple UI`
- Advanced UI: `Operator UI`
- Backend version: `0.2.0`
- Simple UI asset version: `cp13a`
- Operator UI asset version: `cp09c`
- Database schema: `0009_subtitle_tracks`
- External-machine beta: `pending`
- Storage gate: `tiered_operation_storage_gate`
- D: current measured free: `5,352,738,816` bytes (`4.984` GiB)
- Storage thresholds: `run=1,073,741,824`, `media=2,147,483,648`, `package=4,294,967,296` bytes
- Current operation gate status: `run=allowed`, `media=allowed`, `package=allowed`
- Retained development project whitelist: `vertical_slice_cp07`
- Remaining project directories: `1`

## Current Blocker

The old fixed 15 GiB global disk gate is retired. MAINT-006 showed it was a conservative package-build/extraction target, not a measured run-only requirement. Storage readiness is now checked by operation immediately before execution: `run`, `media`, or `package`. CP13A1 has been rebuilt after the uninstall-shortcut product fix and the local lifecycle rerun passed, but the rebuilt evidence still requires independent machine-evidence acceptance before external-machine beta.

## Next Operational Action

Run independent acceptance of the rebuilt CP13A1 installer, release artifacts, and new evidence. Do not start external-machine beta until the rebuilt evidence is accepted.

## Short Roadmap

1. Run operation-specific storage preflight.
2. Accept or reject the rebuilt CP13A1 machine evidence.
3. Run external beta of CP13A1 only after evidence acceptance.
4. Fix reproduced beta issues and issue a maintenance release.

## Validation Commands

Lightweight consistency check:

```powershell
python tools\validate_canonical_docs.py
```

Explicit release hash verification, intentionally not part of normal tests:

```powershell
Get-FileHash release\CP12B\tool_auto_sub_windows_full_portable_cp12b.zip -Algorithm SHA256
Get-FileHash release\CP13A1\ToolAutoSubBetaSetup_CP13A1.exe -Algorithm SHA256
```

Storage preflight examples:

```powershell
python tools\storage_preflight.py --operation run
python tools\storage_preflight.py --operation media
python tools\storage_preflight.py --operation package
```
