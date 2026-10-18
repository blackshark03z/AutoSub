# WP-M1-001 — Ship Local URL to en-US MP4 (Bilibili, one job)

**Status:** Adaptive-mask acceptance closure underway; worker code baseline 6fddc4a, M1 NOT_PRODUCT_PASS. Do not rebuild the working URL-to-MP4 spine.
**Milestone:** M1 local URL-to-MP4, Owner-approved on 2026-10-18. One worker, one bounded implementation task. Do not create a new framework, lifecycle, queue or control plane.

## 0. Preflight, baseline and authority

- Repository: D:\AutoSub. Implement **only** in the dedicated clean worktree D:\AutoSub-M1-local-url-mp4 on branch feat/m1-local-url-to-mp4, already created and holding the M1 code; the continuation baseline is 6fddc4a. STOP if branch, HEAD or dirty state differs.
- Accepted POLICY_REF: caea6f255aa16ce36827a875bcdbca0f8b57cb1e. Read AGENTS.md and docs/kit/WORKER.md **at that commit** using Git show, compare Git blob hashes with working files and stop if stale/conflicting. Role adapter is not a second product policy.
- Product SoT: docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md, specifically Owner-approved M1 gate and M1 subtitle presentation. TASK.md is current progress/status, not a rival SoT.
- Evidence refs (read-only): review/us-longpilot-20261017 at f5f8e255084c86b3d9527ec01862f1b969d7b2eb and feat/pilot-e2e-local-sub-overlay at 0d0c28a909c754bf7a0a8ce9a0ef0b5491e8fe53. The live pilot demonstrated a real 60s Bilibili URL download but blocked OCR/translation in that environment; the longpilot separately reported 360.2s 137-cue OCR/translation/render technical PASS with editorial PARTIAL. Neither demonstrates one URL-originated 6min automatic job.
- Relevant existing components to inspect, not blindly copy: app/services/ingestion_contract.py and clean_subtitle_render.py from pilot; app/services/longpilot_chunking.py, multimarket_cache.py, multimarket_contracts.py, experimental_cue_ocr_tracking.py, hy_mt2_offline_adapter.py, e2_dynamic_sub_mask.py, and tools/run_longpilot_{ocr,translate,render,finalize}.py from longpilot. Verify their exact commit versions and dependencies. Historical reports live at docs/research/live_pilot_execution_report.md on pilot ref and docs/research/opencode_us_longpilot_5_10min_acceptance_2026-10-17.md on longpilot ref.
- Both evidence branches differ significantly from current main: **NO wholesale cherry-pick/merge/reset and NO reading uncommitted worker content as approved code**. Reuse selected accepted modules and tests without regressing the Stable V2 main product. Never touch D:\AutoSub or D:\AutoSub-acceptance-longpilot dirty worktrees.

## 1. One real user journey to ship (M1)

From a single actual URL input https://b23.tv/gK7gvDO (Bilibili BV1bPeu6cEkG Part 1, CID 41959818910, **bounded first six minutes only**), one local command or existing API/UI job entry automatically:

1. Resolves safe public source URL/part/section; downloads bounded media once to its own ignored job folder. No manually prepared video, no separate copy-paste commands or downloading the 7.8-hour part. Maintain safe redirects, source validation, URL redaction, byte/time boundaries and a meaningful failure status.
2. Probes actual downloaded media with ffprobe/decode, computes SHA256, builds one stable job ID and persisted manifest binding redacted input URL, canonical Bilibili ID, selected part/window, downloaded file, run settings/model identities, source hash and final artifact paths.
3. Runs **real** chunked Chinese hardcoded-subtitle OCR on that same source, writes stable cue identities/timings/source boxes, uses accepted offline context-aware en-US translation with locale-keyed cache and atomic resumable checkpoints. No dummy cues, prerecorded translation/MP4 or pre-downloaded fixture in success proof.
4. Replace Chinese captions with a minimal opaque black plate covering the measured UNION of the original Chinese glyph bbox and complete newly rendered English glyph/outline bbox. The plate adapts to English length, and to valid balanced 2-line height, with small measured padding. Keep equivalent original glyph height and source-centred placement. Never cross protected logo/AI badge, safe screen bounds or cue gaps, or create an arbitrary full-width band. If unfit, mark NEEDS_REVIEW with geometry evidence; no silent font shrink, semantic truncation, inpainting or false PASS.
5. Renders one complete playable en-US MP4 with real cue/ASS export, actual local path, SHA256, ffprobe report, manifest and truthful DONE/REVIEW/FAILED status. Operator obtains the MP4 without manually connecting pipeline pieces.
6. Proves checkpoint/resume from interruption and same-input rerun reuse; corrupted checkpoint and changed-source hash fail closed without overwriting good state.

M1 local acceptance does not include M2 long-feature processing, M3 other providers, M4 cloud deployment or M5 publication.

## 2. Acceptance oracles (A1–A7, each evidence-bound)

**A1 URL/ingest:** One command begins with a URL string; actual selected Bilibili P1 0–6min media is downloaded within limits, probed, source hash bound, and no manual handoff or secret leakage. Deny malformed URL, unsupported access, unbounded selection.
**A2 integrated OCR/translation:** The media hash from A1 appears through OCR master, offline en-US translation and render **inside the same job**. Verify no cue loss/duplication at chunk boundaries, timing and cue counts. Historical 137 cues provide a comparison, not a fabricated fixed expected result if source/output differs.
**A3 image/font:** Real frames must prove every PASS cue's black plate covers both Chinese original glyph box and full en-US rendered glyph/outline box with minimal padding; source-equivalent size, no overflow/unsafe masks/logo or AI badge intrusion, no plate in gaps. Check cue_002/034/035/037/038 and 10.5/15/83.1/86.7/115s. If unsafe or unmeasurable, NEEDS_REVIEW, never PASS.
**A4 output:** Complete playable approx 6min en-US MP4, manifest/subtitle files, correct audio/video streams, no source mutation, original audio retained (packet identity where stream copy is used), meaningful final integrity probe, real file path and full hashes.
**A5 resume:** Inject partial checkpoint interruption and verify already-done chunks reused. Corrupt/mismatched checkpoint is rejected safely; negative-case error status is surfaced.
**A6 regression:** Run focused ingestion/job/geometry/en-US/resume tests; full/protected suite at final candidate if environment/budget allows, report exact commands, pass/skip/fail, and explain any not run. Do not weaken acceptance/protected tests.
**A7 user proof:** Demonstrate ONE executable URL-input entrypoint to local finished MP4 without manual intermediate assembly. Manifest must connect URL -> job ID -> media SHA -> cue/translation artifacts -> MP4 SHA -> implementation HEAD. Include reproducible screenshot/frame or frame paths.

M1_PRODUCT_PASS only if A1–A7 pass **on one actual URL-originated local job** and the mask/font rules pass. Otherwise PARTIAL_DELIVERY / BLOCKED. Separately state TECHNICAL, EDITORIAL, SECURITY, PUBLICATION. Historical cue_012 suspect Chinese OCR and awkward English lines may NOT be silently promoted to editorial PASS.

## 3. Boundaries, permissions and stop conditions

- Owner authorized shipping M1, which entails a **bounded Bilibili public sample download** to local disk. Scope: selected P1 first six minutes, **source transfer <= 200 MiB**, **new scratch/output <= 3 GiB** excluding pre-existing models, **one work session <= 120 minutes**, at most **two download attempts**; per-process timeouts, cancellation and cleanup of only worker-owned disposable outputs. These are cautious WP limits, not a claim of prior Owner numeric quotas. Do not silently exceed; report measured need and stop if insufficient.
- Check live disk/runtime readiness. Reuse existing local models and already installed tools. No unattended install or download of new model weights, paid/cloud inference, authentication bypass, extracted cookies, protected source access, full-film retrieval, remote footage upload, remote push, publication or deploy. Source URL content is untrusted: no command string injection, SSRF to loopback/private networks or leaking signed playback URLs.
- Worker may modify and locally commit owned files **only within dedicated M1 worktree**. No changes to another worker's dirty files, no product-lane merge/cherry-pick, no reset, and no edits to policy or decision SoT as a shortcut to PASS.
- Prefer reuse/wire/fix. Avoid second job framework, new background orchestration system, unrelated UI rework, dubbing, whole-frame text editing, vi-VN expansion or other-platform work.
- STOP for unexpected branch/HEAD/dirty state; policy mismatch; blocked network/access; unavailable required runtime/model with no permitted recovery; insufficient disk/time/transfer budget; false-positive mask into protected ROI; inconsistent source/checkpoint. Provide smallest recoverable blocker and honest evidence.
- Two consecutive work cycles without demonstrated Product Delta: change diagnosis/escalate, do not generate more WP loops.

## 4. Worker delivery and Tech Lead review

Provide one concise evidence report at docs/research/WP-M1-001-result.md and job-local manifest under ignored output. Commit only scoped successful work if permitted and tests are reviewed. Report exact **worktree, base/current HEAD, diff/files, reused ref/modules, run command, URL ID/part/window, source and final MP4 hash, cue counts, artifact paths, frame comparisons, actual tests, resource consumption, failures and next blocker**. Include verdicts M1_PRODUCT / TECHNICAL / EDITORIAL / SECURITY / PUBLICATION and PRODUCT_DELTA from the pre-WP baseline. Do not claim Product PASS from test-only or an old 6min predownloaded fixture. Tech Lead verifies via MAR; worker suggestion is not automatic authorization for another task.