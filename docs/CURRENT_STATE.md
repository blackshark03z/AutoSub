# Current State

This is the current human-readable handoff baseline. Code, tests, accepted
evidence, and live Git remain the source of truth. `project_state.json`
retains the last formal release snapshot used by the historical documentation
validator; it is not a statement that an installer is required for daily use.

## Current daily-use MVP

- The accepted product is a local, single-user Windows MVP.
- Double-click `Run AutoSub.cmd` to start the local server and open the Simple
  UI. Normal use does not require a terminal or an installer.
- The supported user path is local video and target-language selection,
  runtime preparation, source transcription, local translation, preview, and
  export.
- Runtime readiness manages AutoSubs v3.8.0 with its `small` model and local
  Argos zh→en translation. The accepted one-click Chinese→English UI smoke
  passed.
- The normal test lane is the default `python -m pytest -q` configuration,
  which excludes `release`-marked package/history validation.

## Stable E2E acceptance — 2026-09-09

The current local MVP has been re-accepted under the CADS Critical User Journey rather than inferred from isolated feature tests.

- Real rendered-UI journey PASS on the existing 15-second local fixture.
- Acceptance run: `run_20260908183651205064_2d923ddc`.
- Subtitle provenance: `provider_transcription`; the accepted ASS contained 7 dialogue events and did not use fixture subtitle injection.
- The validated output MP4 hash differs from the source hash, the completed preview is eligible, `Mở thư mục kết quả` performs a real Windows folder-open action, and `Tạo video mới` resets the setup state without deleting the prior result.
- Responsive rendered UI checks passed at 1365 px and 390 px without horizontal overflow.
- Controlled runtime-readiness failure PASS: failure remains fail-closed, exposes retry/back, preserves the useful selected source, and never exposes a fake completed result.
- Focused journey/runtime/launcher regressions, canonical-doc validation, storage preflight, and the normal full `python -m pytest -q` lane passed after the accepted repairs.
- AutoSub now uses current CADS-native Git/test/runtime engineering control. The former Build OS authority/policy/adoption files and in-repository Build OS control-plane archive are retired from the canonical tree; historical provenance remains in Git history.

## Release history and deferred lane

CP12B Full Portable is the last accepted packaging baseline. CP13A/CP13A1 are
historical release candidates and evidence, not the current daily-use product
contract. EXE, installer, external beta, and package work are intentionally
**DEFERRED** and preserved separately on
`wip/windows-release-pipeline-rebuild`.
They are not current MVP blockers.

The following release metadata is retained verbatim for provenance and the
historical validator; it must not be read as an instruction to build or use an
installer:

- Historical package path: `release\CP12B\tool_auto_sub_windows_full_portable_cp12b.zip`
- Historical package SHA-256: `9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0`
- Historical release ID: `CP12B_WINDOWS_FULL_PORTABLE_CREATIVE_IMPORT_BETA`
- Historical distribution: `Unified Full Portable`
- Historical one-click beta candidate: `CP13A1 Complete Payload Hotfix`
- CP13A1 installer path: `release\CP13A1\ToolAutoSubBetaSetup_CP13A1.exe`
- CP13A1 installer SHA-256: `9ca6f54c9d3caa440ea410f40aba819c75f7240954a316841d5da7ca4f9e317a`
- CP13A1 release ID: `CP13A1_WINDOWS_COMPLETE_PAYLOAD_HOTFIX`
- CP13A1 distribution: `Per-user Windows EXE installer with complete installed-root payload wrapping CP12B`
- CP13A1 machine validation: `CP13A1_COMPLETE_INSTALL_PAYLOAD_AND_UNINSTALL_SHORTCUT_MACHINE_PASS`
- CP13A1 evidence state: `RERUN_PASS_PENDING_ACCEPTANCE` (historical/deferred)
- Primary UI: `Simple UI`
- Advanced UI: `Operator UI`
- Backend version: `0.2.0`
- Simple UI asset version: `cp13a`
- Operator UI asset version: `cp09c`
- Database schema: `0009_subtitle_tracks`
- External-machine beta: `pending`
- Storage gate: `tiered_operation_storage_gate`
- Historical D: free-space snapshot: `5,352,738,816` bytes (`4.984` GiB)
- Storage thresholds: `run=1,073,741,824`, `media=2,147,483,648`, `package=4,294,967,296` bytes
- Current operation gate status: `run=allowed`, `media=allowed`, `package=allowed`
- Retained development project whitelist: `vertical_slice_cp07`
- Remaining project directories: `1`

## Current limits and next action

The old fixed 15 GiB global disk gate is retired. Storage is checked by
operation immediately before execution: `run`, `media`, or `package`. The
historical CP13A1 rerun still requires independent machine-evidence acceptance
if an Owner explicitly resumes the deferred release lane.

No next product scope is accepted by this handoff. A future Tech Lead should
first decide whether to continue local-MVP product work or explicitly resume
the deferred release lane; neither decision is implied by this document.

## Validation Commands

Lightweight consistency check:

```powershell
python tools\validate_canonical_docs.py
```

Historical release hash verification, intentionally not part of normal tests
and not required for the daily-use MVP:

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
