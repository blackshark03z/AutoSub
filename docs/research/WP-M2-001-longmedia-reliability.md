# WP-M2-001 — Longer local URL-to-MP4 and reliable recovery

Status: LOCAL_M2_FUNCTIONAL_PASS (independent MAR verification, 2026-10-20), EDITORIAL_PARTIAL, PUBLICATION_BLOCKED; Owner video viewing gate pending before M3. Product milestone M2, not M3 or Cloud. Work must reuse the accepted M1 vertical slice, not create another framework.

## Immutable context and policy

- Repo D:\AutoSub; only edit dedicated clean worktree D:\AutoSub-M2-longmedia on branch feat/m2-longmedia-reliability. The relay specifies exact post-WP baseline HEAD. Stop on incorrect path/branch/HEAD or a dirty tree.
- Owner-accepted POLICY_REF: caea6f255aa16ce36827a875bcdbca0f8b57cb1e. Read AGENTS.md and docs/kit/WORKER.md at that ref, compare Git blobs with checkout and follow current role instructions. Missing/conflicting policy: STOP.
- Product SoT: docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md; task/status TASK.md. The accepted Owner M3 STOP rule is authoritative: M2 -> M3 -> stop, no automatic M4 Cloud or M5 publication.
- Starting implementation is M1 clean functional PASS at f16ddc21340adeab0cea915edb1ed4cf63ea9df7, selected as M2 code baseline. Its six-minute Bilibili URL-originated output has 132/132 PASS cues, unchanged AAC audio and independently checked source/MP4 hashes. Main's SoT-only worktree does not yet have M1 implementation; do not wholesale merge main or historical longpilot branches.

## Bounded vertical slice

Prove the same existing one-command Bilibili URL -> real Chinese hard-subtitle OCR -> offline context-aware en-US -> minimal adaptive black union plate -> finished playable MP4 runs reliably for materially longer contiguous media. Select the first 30 minutes of existing validated Bilibili BV1bPeu6cEkG Part 1 as a *provisional task fixture*, subject to readiness, access and resource caps. This task-specific candidate is not a permanent product-wide duration threshold. Do not download the entire long compilation. If 30 minutes cannot fit caps, run a smaller diagnostic 12–15 minutes and report PARTIAL_DELIVERY, never M2_PRODUCT_PASS.

Reuse the tools/run_m1_local_url_to_mp4.py entrypoint, run_longpilot_ocr/translate/render/finalize, app/services/longpilot_chunking.py, source identity, checkpoint, locale-isolated caches and M1 mask/ROI rules. Do not create a new queue, job database or alternative production path.

## Required product acceptance A1–A7

A1 — Actual supported URL and bounded contiguous 30-minute source captured via normal public access, probed/decoded and SHA256-bound to canonical BV ID, Part 1, exact time window, model/config versions, job ID and code HEAD. URL data sanitized. Source/interval mismatch and invalid media fail closed.

A2 — All 60-second chunks have deterministic identities and atomic checkpoints. Inspect boundary windows at start/middle/end; all legitimate Chinese subtitle cues retained without duplicate/lost cue IDs; no false scene-text cues. Compare chunk cue inventories and overlap joins using actual source frames.

A3 — Offline en-US translated from the verified source cues with stable context and locale-specific cache identities. Review representative beginning/middle/end lines, fast transitions, complex card/rank scenes; list mistranslation, missing English, hallucination, repetition or stylistic issues. Honest EDITORIAL status required.

A4 — Every PASS cue has the Owner-approved minimal black plate covering actual original Chinese and entire English glyph outline at visually equivalent source font height; no unnecessary full-width region or time-gap plate, no overlay on logo/AI badge or other protected scene regions. No black-only missing English cues. Full-length H264/AAC MP4 playable, correct duration/source audio hash, per-cue geometry verified, manifest/source/render/MP4 hashes consistent.

A5 — Demonstrate controlled stop after at least one successfully checkpointed chunk and a real resume reusing proven completed chunks without rerunning them; verify source hash/part/window/config and corrupted checkpoint fail closed, preserve good artifacts and log what was reused. Never replace valid checkpoint or silently relabel corrupted data.

A6 — Measure elapsed/CPU/memory/disk/per-stage time and transfer bytes; show observable per-chunk progress so high CPU is not mistaken for a stuck worker. Enforce timeouts, cancellation, scratch limits and bounded retries; stop and report on resource exhaustion.

A7 — Focused and negative tests for >6-minute bounded ingest, cross-chunk cue integrity, resume/tamper/recovery, cache locale isolation, subtitles/ROI/time gaps and output validation. Run full/protected suite on the committed candidate, disclose inherited failures versus regressions. Reconfirm M1 6-minute compatibility. Report one real URL-to-output execution command, real hashes and before/after samples.

M2_PRODUCT_PASS only if all seven oracles are grounded on a clean committed candidate's representative long-media run with no blocking cues and demonstrated failure recovery; a synthetic or shorter diagnostic is PARTIAL_DELIVERY. Technical/Editorial/Security/Publication verdicts are independent; no publishing permission is conferred.

## Hard limits, stop conditions and deliverable

- Conservative LOCAL limits for this WP: one selected source interval at most 30 minutes; media download <=500 MiB; new job scratch/output <=8 GiB (existing GGUF/cache excluded); cumulative processing time <=4 hours; at most two fetch attempts; timeouts per process. Before any media request inspect live disk (D: currently ~50 GiB free) and installed OCR/HY-MT2 runtimes. These are bounded work authorization, not a global product policy. If infeasible, stop, quantify and ask for scope adjustment.
- No new downloaded models, paid inference, video upload, cookies/DRM bypass, entire-part retrieval, work outside assigned tree, release/push/merge/deploy, M3 or Cloud feature expansion. Avoid polluting git with job artifacts. Preserve existing M1 implementation and historical artifacts. If there are two successive cycles without product progress, stop for rediagnosis.
- Write a concise result at docs/research/WP-M2-001-result.md including worktree/HEAD, exact command/URL/interval, source/output SHA256, cue counts, chunk/boundary audit, sample quality, source-output audio packet MD5, interruption/resume checks, manifest, MP4/evidence paths, tests, resources, product delta and top blocker; locally commit only scoped verified changes. Report honest M2_PRODUCT / TECHNICAL / EDITORIAL / SECURITY / PUBLICATION verdicts and wait for Tech Lead verification via MAR.
- When M2 is proven, M3 may be scoped separately. AFTER M3 STOP for publishing-effectiveness evidence and Owner GO/NO-GO. Do not start M4/M5 without explicit later authorization.

## Tech Lead's verified 30-minute partial acceptance and closure (2026-10-19)

**Actual status:** M2=PARTIAL_DELIVERY / NOT_PRODUCT_PASS, despite a genuine, decoded 1800s en-US MP4. The code fixes render scaling, cue merges and en-US translation-repair, but five en-US cues are still NEEDS_REVIEW and several contain translation context bleed from adjacent cues. The worker's final manifest was produced with implementation_dirty=true at old HEAD 3f8ec7c, not its later committed 05ad4ae candidate.

**Exact scope for continuation:** Follow docs/research/WP-M2-001-techlead-review.md. Fix only evidenced original-Chinese/en-US consistency and safe-layout defects in cue_131, cue_500, cue_506, cue_602, cue_660 plus immediately implicated neighbors (notably cue_132/501/603); prove actual interruption-then-resume, final 30-min artifact and manifest on clean committed HEAD, preserve M1 compatibility. Do not silently shrink, falsely PASS, build new infrastructure or start M3. vi-VN remains reported partial but is not part of the en-US M2 Product PASS bar.
