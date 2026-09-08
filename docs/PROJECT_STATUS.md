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
- The composed CADS Critical User Journey is **STABLE E2E PASS** on a real 15-second source using managed runtime readiness, provider transcription, local zh→en translation, ffmpeg render, validated preview, real output-folder opening, and fresh-run reset.
- Controlled readiness failure is fail-closed and recoverable from the rendered Simple UI without reconstructing the selected source.
- Build OS lifecycle/control-plane authority is retired from the canonical repository tree; current engineering control is CADS-native Git/test/runtime work described by root `AGENTS.md`, `TASK.md`, `ARCHITECTURE.md`, and `docs/DECISIONS/`.

## Accepted limitations and deferred work

- Dialogue subtitles are the supported localization scope; in-scene text is preserved unless separately authorized.
- Gemini, ElevenLabs, upload, and publish calls are disabled unless a future authorized task enables them.
- Release-only CP11C/CP11D checks remain separate under the release lane.
- EXE, installer, and release packaging are intentionally **DEFERRED**. The preserved work is on `wip/windows-release-pipeline-rebuild` and is not a current product blocker.

## Direction pointers

- **Next product work:** UNKNOWN; no specific next product scope is accepted by this synchronization.
- **Deferred release work:** preserved on `wip/windows-release-pipeline-rebuild`; it is separate from the accepted local MVP.

This file describes accepted reality only. Active dirty work, partial results, raw evidence, and Worker state belong elsewhere.
