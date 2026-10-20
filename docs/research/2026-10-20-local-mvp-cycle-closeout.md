# AutoSub — local MVP M1–M3 development cycle closeout (2026-10-20)

**Scope:** Owner-requested **temporary engineering stop**, non-destructive cleanup and explicit Git remote archival of accepted milestone branches. **Not** a product release, source-media publication, deployment or approval to scale Cloud. This note does not replace the active Product SoT: `docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md`.

## Verified product delta (local MVP only)

- **M1 / Bilibili URL → en-US MP4:** independently re-run on committed code `f16ddc21340adeab0cea915edb1ed4cf63ea9df7`; six-minute MP4, 132/132 caption layouts PASS, audio packets preserved. Output SHA-256 `e535b521ce286cf85dfb35ac2ed18d869f75212b964245adac1557efd4d0586b`.
- **M2 / 30-minute URL job:** independently checked clean code `5ce4c84ee7e9cb1806f00374b314885a505ef94d`; 30 OCR chunks, resume evidence, 713/713 layout PASS, original audio preserved. Output SHA-256 `74b3e6374bca9b0957c168ed0794fafddd1ff2cc17cb02742f779edf9d109e0e`. Owner viewed and accepted the real 30-minute MP4.
- **M3 / Hongguo *local file* → en-US MP4:** independently re-run on clean code `ed72ec7c702b4b8a6341fe80deb07e5b94cb7bfa`; series `7690432295777340441`, episode 001, original SHA-256 `070c8e0c91c46288b8d3831e906b7349c0d60462967620759da6e79645afb186`, 93.855s finished MP4, 35/35 layout PASS, audio preserved. Output SHA-256 `287bb090891c2a441045948db311819292d7036ed3f47f6802438834be28bdd4`. Owner viewed and accepted.
- **Owner decision A:** M3 local MVP source-type scope PASS = Bilibili URL plus Hongguo *pre-downloaded local file*. **Hongguo URL direct ingestion NOT_PROVEN/NOT_SUPPORTED**. This does not prove an integrated installer, publishable release or public distribution. Editorial still PARTIAL (especially Hongguo `cue_010`), publication authorization BLOCKED and Cloud E2E NOT_PROVEN.

## Git/source-code handoff

- **POLICY_REF:** `caea6f255aa16ce36827a875bcdbca0f8b57cb1e`; active source of truth remains the current accepted owner decision above.
- Source repository: `D:\AutoSub` Git metadata; canonical SoT checkout `D:\AutoSub-policy-main` on `main`.
- M1 worktree `D:\AutoSub-M1-local-url-mp4`, branch `feat/m1-local-url-to-mp4`, HEAD `f16ddc21340adeab0cea915edb1ed4cf63ea9df7`.
- M2 worktree `D:\AutoSub-M2-longmedia`, branch `feat/m2-longmedia-reliability`, HEAD `7d4beaf569ab103cdb14d413996529baf3d6cfce` (docs-only after functional code proof).
- M3 worktree `D:\AutoSub-M3-multisource`, branch `feat/m3-multisource-intake`, HEAD `183f4027cb5a23502d43c63a4e1aee173b9b8354` (docs-only after functional code proof).
- **Owner authorized committing and pushing** local `main` and the three accepted feature branches to GitHub `origin` as a **source-code backup/hand-off**. Pushes must be normal non-force, individually verified against remote refs; no branch merges, releases, tags, GitHub publication actions or changes to other branches are approved.
- **Do not confuse remote source preservation with production ship:** implementation resides in feature histories, not merged into `main`. Do not promise an integrated main-branch build. The original dirty `D:\AutoSub` pilot tree and other historical/lane worktrees must remain untouched.

## Reproducible artifacts / retention

- `D:\AutoSub-M1-local-url-mp4\data\m1_job_rev2\longpilot_en-US.mp4` (6 minutes).
- `D:\AutoSub-M2-longmedia\data\m2_job\longpilot_en-US.mp4` (30 minutes).
- `D:\AutoSub-M3-multisource\data\m3_hongguo_job\longpilot_en-US.mp4` (93.855 s).
- Hongguo original pre-downloaded MP4s and metadata: `D:\Youtube\Youtube Package Crawl\output_hongguo`. These are **ignored local data**, NOT Git-tracked, NOT included in `git push`. Preserve them without deletion or remote media upload.
- Acceptance provenance and focus/full-test caveats are in `docs/research/WP-M1-001-techlead-review.md`, `WP-M2-001-techlead-review.md`, and `WP-M3-001-techlead-review.md` on their appropriate worktrees; 3 historical full-suite failures remain disclosed, not closed by this documentation freeze.

## Closeout / next Owner gate

After remote ref verification, **STOP engineering**. Do not launch workers, resume M4/M5, merge to main, run Hongguo crawler/decryption, deploy or upload media. Any optional disk reclamation must first establish that data is recoverable; no destructive cleanup of job outputs/model caches or dirty checkout is permitted in this cycle.

The **single next product gate** is a separately approved *small real publishing-effectiveness pilot*: confirm distribution rights, choose a publishing account/channel and eligible corrected/edited sample video, set an actual measurement window and outcome thresholds with Owner, cap time/cost, publish only after distinct explicit authorization, then observe real reach/engagement/results before deciding if Cloud E2E is worth scaling.
