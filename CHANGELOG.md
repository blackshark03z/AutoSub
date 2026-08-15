# Changelog

## Current Baseline

- The local daily-use MVP is **PASS**: double-clicking `Run AutoSub.cmd` starts
  AutoSub and opens the Simple UI for the normal readiness, transcription,
  translation, preview, and export flow. One-click Chinese-to-English UI smoke
  passed, and the normal product suite is **GREEN**.
- EXE, installer, and release packaging remain intentionally deferred on
  `wip/windows-release-pipeline-rebuild`; release-only CP11C/CP11D checks remain
  separate under the release lane.

## Historical Release Notes

- CP12B Full Portable was the canonical release baseline.
- CP13A1 Complete Payload Hotfix was a one-click external beta candidate; its
  release evidence remains historical and does not change the accepted local
  MVP state.
- CP12A added Creative Subtitle Script Import with Translation, Creative, and Imported tracks.
- CP11D established the unified Full Portable distribution with bundled OCR.
- CP10B introduced the Simple end-to-end workflow UI as the primary user path.
- CP09 completed the production golden path, local export package, and manual publication handoff.
- CP08G locked localization scope to dialogue subtitles only.

## Historical Notes

Detailed checkpoint reports remain in Git history and numbered root reports. They are historical acceptance evidence, not the active documentation authority chain.
