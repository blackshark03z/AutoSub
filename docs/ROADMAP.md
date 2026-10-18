> CURRENT PRODUCT AUTHORITY: docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md and TASK.md. The content below concerns historical local/V2 implementation or acceptance; it is not a current ship goal.

# Roadmap

## Active Owner-approved ship milestones

M1 through M5 are defined **only** in [the active Owner Decision](DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md#owner-approved-ship-milestones). Current execution status and blocker: [TASK.md](../TASK.md). All preexisting roadmap content below is historical and must not be interpreted as an alternative active ship plan.

## Historical roadmap (retained for provenance)

Roadmap is planned direction, never the authority for shipped behavior. A DONE item must be contained by the accepted baseline and reconcilable with accepted history.

## CURRENT ACCEPTED PRODUCT

- Local daily-use MVP is **PASS**.
- Normal use starts by double-clicking `Run AutoSub.cmd`, opens the Simple UI, and supports video/language selection, runtime readiness, transcription, translation, preview, and export.
- One-click Chinese-to-English UI smoke passed and the normal product suite is **GREEN**.
- CADS composed-journey acceptance is **STABLE E2E PASS**: real source selection → readiness → provider transcription → local translation → render → validated preview → real output-folder action → fresh-run reset, plus controlled fail-closed recovery.
- The retired Build OS control plane is dropped from the canonical tree; ordinary development now follows the lightweight CADS Git/test/runtime path.

## NEXT PRODUCT WORK

- UNKNOWN; no specific next product scope is currently accepted.

## DEFERRED RELEASE WORK

- EXE, installer, and release packaging remain intentionally deferred.
- Preserve the deferred work on `wip/windows-release-pipeline-rebuild`. It is not a current product blocker.
- Release-only CP11C/CP11D checks remain separate under the release lane.

## DROPPED

- None recorded.
