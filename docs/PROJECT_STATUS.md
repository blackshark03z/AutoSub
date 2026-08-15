# Project Status

## Accepted baseline

- **Declared accepted ref:** `main`
- **Machine baseline:** resolve the current accepted SHA live with `git rev-parse main`; no volatile SHA is committed here.
- **Architecture authority:** [ARCHITECTURE.md](ARCHITECTURE.md)

## Accepted capabilities

- Tool Auto Sub is a local, single-user Windows application for creating dialogue-subtitle videos from local source media.
- The local daily-use MVP is **PASS**. The normal launch action is to double-click `Run AutoSub.cmd`; it starts AutoSub and opens the Simple UI.
- The normal user flow is video selection and target-language selection, runtime readiness, transcription, translation, preview, and export. Terminal interaction is not required.
- AutoSubs/Argos runtime readiness remains managed by the existing product path. The one-click Chinese-to-English UI smoke passed.
- The normal product suite is **GREEN**.

## Accepted limitations and deferred work

- Dialogue subtitles are the supported localization scope; in-scene text is preserved unless separately authorized.
- Gemini, ElevenLabs, upload, and publish calls are disabled unless a future authorized task enables them.
- Release-only CP11C/CP11D checks remain separate under the release lane.
- EXE, installer, and release packaging are intentionally **DEFERRED**. The preserved work is on `wip/windows-release-pipeline-rebuild` and is not a current product blocker.

## Direction pointers

- **Next product work:** UNKNOWN; no specific next product scope is accepted by this synchronization.
- **Deferred release work:** preserved on `wip/windows-release-pipeline-rebuild`; it is separate from the accepted local MVP.

This file describes accepted reality only. Active dirty work, partial results, raw evidence, and Worker state belong elsewhere.
