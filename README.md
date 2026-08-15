# Tool Auto Sub

Tool Auto Sub is a local, single-user Windows application for creating dialogue-subtitle videos from local source media.

CP12B Full Portable remains the last accepted release baseline; current daily use is documented as the local MVP below.

## Normal Launch

**Double-click `Run AutoSub.cmd`.** AutoSub starts, the Simple UI opens, and normal use does not require terminal interaction.

## Primary Workflow

In the Simple UI, select a local video and target language. AutoSub manages AutoSubs/Argos runtime readiness through the existing product path, then runs transcription, translation, preview, and export. The accepted one-click Chinese-to-English UI smoke passed.

`run_app.ps1` remains available for developer diagnostics. The Operator UI at `/operator/` is advanced tooling for diagnostics, recovery, and release review.

## Product State

The local daily-use MVP is **PASS**, and the normal product suite is **GREEN**. EXE, installer, and release packaging are intentionally deferred; that work is preserved on `wip/windows-release-pipeline-rebuild` and is not a current product blocker. Release-only CP11C/CP11D checks remain separate under the release lane.

The tool is intentionally scoped to dialogue subtitles. It preserves non-dialogue in-scene text unless a future issue explicitly changes that behavior.

## Living Documentation

- [Project Status](docs/PROJECT_STATUS.md)
- [Roadmap](docs/ROADMAP.md)
- [Current State](docs/CURRENT_STATE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Engineering](docs/ENGINEERING.md)
- [Operations](docs/OPERATIONS.md)
- [Changelog](CHANGELOG.md)
