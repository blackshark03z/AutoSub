# Operations

## Development Startup

For ordinary local use, double-click **Run AutoSub.cmd** in the repository
root. It starts the same Uvicorn application as `run_app.ps1`, waits for
`/api/health`, and then opens the Simple UI. If AutoSub is already healthy, it
opens that existing UI without starting a second server. If port 8173 belongs
to another application, it stops safely with a clear message and does not kill
the other process. Startup logs are in `runtime\logs\autosub-launcher.log`.

For developer diagnostics, from the repository root:

```powershell
.\run_app.ps1
```

Open:

```text
http://127.0.0.1:8173/
```

The advanced Operator UI is available at:

```text
http://127.0.0.1:8173/operator/
```

## Tests

Use focused tests while developing:

```powershell
python -m pytest tests\test_cp12a_creative_subtitle_tracks.py -q
```

Use the full suite only after a `run` storage preflight passes:

```powershell
python tools\storage_preflight.py --operation run
python -m pytest -q
```

Use the lightweight docs/state validator anytime:

```powershell
python tools\validate_canonical_docs.py
```

## Database Migration

App startup calls `init_db()`, which runs Alembic to head. The current schema is:

```text
0009_subtitle_tracks
```

Read-only SQLite check:

```powershell
python -c "import sqlite3; con=sqlite3.connect('data/app.db'); print(con.execute('PRAGMA quick_check').fetchone()[0]); print(con.execute('SELECT version_num FROM alembic_version').fetchone()[0]); con.close()"
```

## Release Build

Do not build releases unless a `package` storage preflight passes immediately before execution. The current portable baseline is CP12B:

```text
release\CP12B\tool_auto_sub_windows_full_portable_cp12b.zip
```

The current one-click external beta candidate is CP13A:

```text
release\CP13A\ToolAutoSubBetaSetup_CP13A.exe
```

CP13A is a per-user Windows installer. It installs by default under `%LOCALAPPDATA%\Programs\ToolAutoSubBeta`, keeps user data under `%LOCALAPPDATA%\ToolAutoSubBeta\data`, opens the Simple UI by default, and keeps Gemini, ElevenLabs, upload, and publish disabled.

Build CP13A only from this repository root:

```powershell
python tools\build_cp13a_one_click_beta.py
```

The explicit release verification commands are:

```powershell
Get-FileHash release\CP12B\tool_auto_sub_windows_full_portable_cp12b.zip -Algorithm SHA256
Get-FileHash release\CP13A\ToolAutoSubBetaSetup_CP13A.exe -Algorithm SHA256
```

## Package Validation

Package validation requires enough temporary and output disk space. Run:

```powershell
python tools\storage_preflight.py --operation package
```

If a package tool can estimate temporary workspace size, pass that estimate so the gate requires the greater of 4 GiB and projected workspace plus configured safety reserve. It must not include secrets, source videos, rendered project media, development databases, browser profiles, evidence directories, or `.git`.

## Diagnostics

Portable diagnostics are collected with `COLLECT_DIAGNOSTICS.cmd`. CP13A installed diagnostics are collected from the Start Menu shortcut `Collect Diagnostics`. Diagnostics must be sanitized and must not include full imported scripts, subtitle text, API keys, tokens, browser profiles, source media, rendered media, or raw databases.

## Disk Gate And Cleanup

The active disk policy uses operation-specific gates:

- `run`: `1,073,741,824` bytes.
- `media`: `2,147,483,648` bytes.
- `package`: `4,294,967,296` bytes, or more when projected temporary workspace plus safety reserve exceeds 4 GiB.

Build and render operations must run their storage preflight immediately before execution. The old fixed 15 GiB global gate is retired because it was conservative for package-build/extraction safety but not supported as a run-only requirement by MAINT-006 measurements.

Do not scan or delete sibling projects. Prefer deleting only exact, known, project-created validation/staging directories after verifying no active process, no unique user data, source package retained, and protected hashes unchanged.

When safe cleanup cannot reach the target, stop and ask for an operator-approved archive/delete plan.

Low disk space must never trigger automatic deletion of user media.

## Retention Policy

Retain one canonical Full Portable package locally. Historical release binaries should not remain on the working drive after the newer canonical release has been hash-verified; retain their manifests, checksums, release notes, and history instead.

Delete staging and validation extractions after acceptance when the exact path is project-owned, no active process is using it, and no unique user data is present.

Retain active project media until an explicit operator archive/delete decision exists. Duplicate media requires a copy-hash-verify-remove plan rather than automatic deletion.

## Lightweight Process

Bug fix:

```text
issue -> code -> focused tests -> commit
```

Feature:

```text
issue -> optional ADR -> code -> tests -> commit
```

Release:

```text
full tests -> package -> validation -> manifest/checksum -> release notes -> cleanup staging/extraction
```

Do not require a numbered report/checkpoint/evidence directory for every ordinary change.
