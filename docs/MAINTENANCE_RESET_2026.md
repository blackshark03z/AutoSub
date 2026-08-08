# Maintenance Reset 2026

## Documents Consolidated

The active documentation authority chain is now:

- `README.md`
- `docs\CURRENT_STATE.md`
- `docs\ARCHITECTURE.md`
- `docs\OPERATIONS.md`
- `docs\DECISIONS\`
- `CHANGELOG.md`
- `project_state.json`

Historical numbered reports and checkpoint files remain historical acceptance evidence only.

## Contradictions Resolved

| Subject | Old conflicting statement | Resolution | Canonical replacement |
|---|---|---|---|
| CP10B meaning | CP10B was used for both Simple UI and module-boundary proposal | CP10B means Simple end-to-end workflow UI | `docs\CURRENT_STATE.md`, `CHANGELOG.md` |
| CP10B completion | Roadmap implied CP10B boundary work remained next | CP10B Simple UI is completed; boundary work is future proposal | `docs\DECISIONS\REF-001_MODULE_BOUNDARIES_PROPOSAL.md` |
| Simple UI version | Old docs referenced `cp10b` or `cp11d` | CP12B package baseline uses `cp12b` | `docs\CURRENT_STATE.md` |
| Operator UI version | Old docs referenced CP09/CP10 context inconsistently | Current package baseline records `cp09c` | `docs\CURRENT_STATE.md` |
| Canonical release | Old README pointed at CP09 local export | Current product release is CP12B Full Portable ZIP | `README.md`, `docs\CURRENT_STATE.md` |
| Database schema | Old baseline stopped at earlier migrations | Current schema is `0009_subtitle_tracks` | `docs\CURRENT_STATE.md` |
| Git baseline | CP10A baseline commit was stale | Source baseline is `e7e1a89`; maintenance commit follows it | `docs\CURRENT_STATE.md`, `project_state.json` |
| External beta | Older docs pointed to CP11 beta state | External-machine beta for CP12B remains pending | `docs\CURRENT_STATE.md` |
| OCR distribution | Older docs described add-on transition | Current distribution bundles OCR in Full Portable | `docs\ARCHITECTURE.md` |
| Storage blocker | Old docs did not reflect CP12C result | Storage remains blocked below safe threshold | `docs\CURRENT_STATE.md` |

## Documents Removed

The old multi-document authority layer under `docs\` was removed after valid current facts were merged into the living documents. Git history remains the archive.

Removed active authority documents include:

- root implementation/process/status files such as `00_READ_ME_FIRST.md`, `01_IMPLEMENTATION_SPEC_V0.2.md`, `06_PROJECT_STATUS.md`, worker task files, `CURRENT_EXECUTION_BRIEF.md`, and `CONTRIBUTING.md`
- `docs\CURRENT_BASELINE.md`
- `docs\DOCUMENT_AUTHORITY_MATRIX.md`
- `docs\TARGET_ARCHITECTURE_PROPOSAL.md`
- old numbered docs and runbooks under `docs\`

## Canonical Baseline

Canonical release: `CP12B Full Portable`.

Canonical package: `release\CP12B\tool_auto_sub_windows_full_portable_cp12b.zip`.

Canonical package SHA-256: `9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0`.

Database schema: `0009_subtitle_tracks`.

Primary UI: Simple UI.

Advanced UI: Operator UI.

## New Process

Bug fix: issue -> code -> focused tests -> commit.

Feature: issue -> optional ADR -> code -> tests -> commit.

Release: full tests -> package -> validation -> manifest/checksum -> release notes -> cleanup staging/extraction.

Routine changes do not require a numbered checkpoint/report/evidence hierarchy.

## Remaining Blockers

- D: free space remains below the safe threshold.
- CP12B external-machine beta is pending.
- Further storage recovery requires operator-approved archive/delete decisions or additional storage.
