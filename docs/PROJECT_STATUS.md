# Project Status

## Accepted baseline

- **Declared accepted ref:** `main`
- **Machine baseline:** resolve the current accepted SHA live with `git rev-parse main`; no volatile SHA is committed here.
- **Architecture authority:** [ARCHITECTURE.md](ARCHITECTURE.md)

## Accepted capabilities

- Tool Auto Sub is a local, single-user Windows application for creating dialogue-subtitle videos from local source media.
- The Simple UI at `/` is the normal path; the Operator UI at `/operator/` is advanced diagnostic and recovery tooling.
- The Simple UI’s one-click Chinese-to-English external-audio workflow visibly checks local readiness, prepares AutoSubs/Argos dependencies only when missing, automatically continues after success, and offers a user-safe retry when readiness fails.
- The canonical accepted release is CP12B Full Portable. It uses a bundled, release-local OCR runtime and SQLite project state.
- Canonical cue timing is independent from wording. Translation, Creative, and Imported subtitle tracks are supported without changing source timing, source-suppression geometry, audio, or source media.

## Accepted limitations

- Dialogue subtitles are the supported localization scope; in-scene text is preserved unless separately authorized.
- Gemini, ElevenLabs, upload, and publish calls are disabled unless a future authorized task enables them.
- CP13A1 is a one-click external beta candidate whose machine-rerun evidence is pending acceptance; it is not the canonical accepted release.

## Direction pointers

- **Active initiative:** No active product initiative is recorded by this adoption.
- **Next planned milestone:** Resolve the separately tracked CP13A1 external beta evidence acceptance, or establish a new accepted product direction.

This file describes accepted reality only. Active dirty work, partial results, raw evidence, and Worker state belong elsewhere.
