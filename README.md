# Tool Auto Sub

Tool Auto Sub is a local, single-user Windows application for creating English dialogue-subtitle videos from local source media.

The current canonical product release is **CP12B Full Portable**:

- Package: `release\CP12B\tool_auto_sub_windows_full_portable_cp12b.zip`
- SHA-256: `9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0`
- Distribution: unified Full Portable ZIP with bundled OCR runtime
- Database schema: `0009_subtitle_tracks`

## Scope

The tool is intentionally scoped to dialogue subtitles. It preserves non-dialogue in-scene text unless a future issue explicitly changes that behavior.

## Normal Launch

For development/local operation from this repository:

```powershell
.\run_app.ps1
```

Then open:

- Simple UI: `http://127.0.0.1:8173/`
- Operator UI: `http://127.0.0.1:8173/operator/`

For beta users, extract the CP12B ZIP and double-click `START_TOOL.cmd`.

## Primary Workflow

The Simple UI is the primary interface. It supports selecting a video, creating dialogue subtitles, previewing the result, saving a copy, and using optional Creative Import.

Creative Import lets a user export a cue-based template, edit only the subtitle wording outside the tool, import it back, preview validation results, and apply it as a separate Creative or Imported subtitle track. It does not change canonical timing, audio, OCR, source suppression geometry, or source media.

The Operator UI is the advanced console for diagnostics, release review, recovery, and historical project inspection.

## Living Documentation

- [Current State](docs/CURRENT_STATE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Decisions](docs/DECISIONS)
- [Changelog](CHANGELOG.md)
- [Maintenance Reset Note](docs/MAINTENANCE_RESET_2026.md)

Historical numbered reports and checkpoint files are retained as acceptance evidence, not as the active authority chain.
