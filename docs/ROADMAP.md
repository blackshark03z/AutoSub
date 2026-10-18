# Roadmap

Roadmap is planned direction, never the authority for shipped behavior. A DONE item must be contained by the accepted baseline and reconcilable with accepted history.

## CURRENT ACCEPTED PRODUCT

- Local daily-use MVP is **PASS**.
- Normal use starts by double-clicking `Run AutoSub.cmd`, opens the Simple UI, and supports video/language selection, runtime readiness, transcription, translation, preview, and export.
- One-click Chinese-to-English UI smoke passed and the normal product suite is **GREEN**.
- historical local-product composed-journey acceptance is **STABLE E2E PASS**: real source selection → readiness → provider transcription → local translation → render → validated preview → real output-folder action → fresh-run reset, plus controlled fail-closed recovery.
- The retired Build OS control plane is dropped from the canonical tree; ordinary development now follows Owner-selected Kit v1.2.2 (exact package/version verification still pending).

## NEXT PRODUCT WORK

- **AutoSub Cloud ship:** one source-video URL -> automated ingestion -> US-first subtitle localization -> validated complete video output, with resumable long-media processing. This direction is an Owner-confirmed target, not yet shipped. See `docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md`.

## DEFERRED RELEASE WORK

- EXE, installer, and release packaging remain intentionally deferred.
- Preserve the deferred work on `wip/windows-release-pipeline-rebuild`. It is not a current product blocker.
- Release-only CP11C/CP11D checks remain separate under the release lane.

## DROPPED

- None recorded.
