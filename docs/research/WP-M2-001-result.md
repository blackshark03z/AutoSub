# WP-M2-001 result — Longer local URL→en-US MP4 and reliable recovery

Status: worker evidence complete; awaiting independent Tech Lead verification (not self-authorizing).

## 1. Identity

- Repo `D:\AutoSub`; only worktree touched: `D:\AutoSub-M2-longmedia`; branch `feat/m2-longmedia-reliability`.
- Baseline HEAD `3f8ec7c8286fa40195b2a404af032122799d3fe3`; the scoped fix + this report are committed locally on `feat/m2-longmedia-reliability` (9 modified files + 2 new, §4). No merge, push or deploy performed.
- Owner-accepted POLICY_REF `caea6f255aa16ce36827a875bcdbca0f8b57cb1e`; `AGENTS.md` / `docs/kit/WORKER.md` / `docs/kit/ORCHESTRATOR.md` byte-identical to the checkout.
- Product SoT `docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md`; WP `docs/research/WP-M2-001-longmedia-reliability.md`; M1 code baseline `f16ddc21340adeab0cea915edb1ed4cf63ea9df7`.

## 2. One real user journey (A1, A7)

- Command (clean tree, resume):
  `python tools/run_m1_local_url_to_mp4.py --url https://www.bilibili.com/video/BV1bPeu6cEkG --job-dir data/m2_job --part 1 --end-seconds 1800 --max-transfer-mib 500 --resume`
- One public URL → 30-minute contiguous Part 1 → chunked hard-subtitle OCR (reused) → offline context-aware en-US (and vi-VN) → adaptive-union black plates drawn inside the `.ass` → finished H.264/AAC MP4. `M1_STATUS=NEEDS_REVIEW` (5 layout-fit flags; see A3/A4).
- Total elapsed `1719.6 s` (28.7 min) of the ≤4 h budget. One source fetch; ≤500 MiB honoured.

## 3. Acceptance oracles

### A1 URL/ingest — PASS
- Source `data/m2_job/source/source.mp4`, sha256 `402d4f3e980e664e4fbfe68baed8c406faacc57eacd5fe5f2fc7a9f4cbcb2069`, 286,820,816 B, 1800.0 s.
- `input_identity.json` binds host/`BV1bPeu6cEkG`/part 1/`end_seconds=1800`/redacted URL/sha256/size — SHA256-bound to canonical BV id, Part, window. Mismatch/invalid media fail closed (unchanged from M1).

### A2 chunk/boundary integrity — PASS
- 30 × 60 s chunks; checkpoint `master_version_id=988e40c96a34e5abcac31c0ddf274c276fd5f6aad4b7bbc2c26319e23bf25d22` (stable, content-independent), `master_extraction_hash=246bea3408b21b73c519e1b6ea8414a2f5f33ad02c7dc076ff0f91841c025532`.
- Merge: `cue_count=713`, `duplicates_removed=63`, `boundary_merges=63`. No legitimate cue lost; stable ids/geometry per M1.
- **A2 fix (approved slice):** `merge_chunk_cues` now merges a proven same-display split pair (identical normalized text + bbox IoU ≥ threshold + any temporal overlap) instead of requiring an overlap ratio ≥ 0.5, and unions the retained cue's `[start,end]` so the full display window stays covered. Seven split pairs merged in M2 (run 2 → run 4), e.g. `跑呀怎么不跑了` a=(176133,178233) b=(178000,178500) iou=0.926 ov=233 ms; `雪儿别怕` iou=0.853; `三了他` iou=0.976; `当然能` iou=0.993; `换些资源回来吧` iou=0.933; `多谢师傅` iou=0.981; `哈哈哈正有此意` iou=0.926. A single subtitle is never shown twice at once, so an overlap of the same text at the same geometry is one display, not two cues.

### A3 en-US translation — PASS for all cues (EDITORIAL PARTIAL)
- en-US: **713/713 `TRANSLATED`, 0 rejected**; `context_echo_retries=5`, `repair_attempts=2`. No missing English, no residual Han, no prompt echo.
- **A3/A4 fix (approved slice):** a residual-Han / empty / prompt-scaffolding-echo output now triggers one bounded, grounded repair inference (`build_repair_translation_prompt`, same source line, Han-free) and is never marked `TRANSLATED` while defective (`is_defective_translation`). Root cause of the old rejects was a **prompt echo**: e.g. `看人只用重瞳术` returned `Use only the specified terminology: - 重瞳 = Double Pupils Line to translate: …`; the repair pass returns `Use the heavy pupil technique to judge people`.
- vi-VN (secondary, not part of the en-US deliverable): 710/713 `TRANSLATED`, `repair_attempts=8`; 3 residual (see §7). Locale isolation holds: `en_us_rekeyed=true`, `new_miss=true`, `vi_still_hit=true`, `l1_still_hit=true`.
- EDITORIAL_QA: **PARTIAL** (spot review only; no full human editorial pass).

### A4 render/output/provenance — PASS
- Render (`render_report_en-US.json`): `cue_count=713`, statuses `{PASS: 708, NEEDS_REVIEW: 5}`, `mask_model=adaptive_union_source_and_english`, `mask_padding_px=3`, `max_mask_width_px=1158`, `safe_text_width=1152`. **All 713 cues have `covered_source_and_text=true`.**
- **F1 render blocker fixed:** the old per-cue `drawbox` graph was 66,763 chars for 718 cues → `WinError 206` (Windows argv cap). This ffmpeg (9.0.1-essentials) exposes no `-filter_script`/`-filter_complex_script`. Plates are now **ASS vector events** on layer 1 (solid `black@1.0`, no border/shadow) under the English text event on layer 2, rendered by a **single constant-size `subtitles` filter**; `-filter_script:v` is removed. `ass plates=713, text_events=713`.
- Output: `data/m2_job/longpilot_en-US.mp4`, 236,195,102 B, sha256 `76ce31488d17146eb5998dc26fa83f0e2da057d184937b84f914cb9820e11692`; 1800.0 s h264 1280×720 15 fps + aac 44100 stereo. **Audio packet MD5 `MD5=5357d9a80fd43aaf53e9ddf5d0d2de2a` = source (bit-exact copy).**
- Frame audit (source vs output, real frames): mask bands changed **83.6–90.4 % inside** and only **0.015–0.053 % outside** (H.264 noise); a genuine **time-gap frame changed 0.00 % inside / 0.005 % outside** → no plate in gaps. Uploader logo `(0,0,272,110)` and AI badge `(960,653,1279,719)` ROI diff ≤0.32 % (re-encode noise) → provenance intact. Visual spot-check `cue_027` (2-line PASS) and `cue_131` (NEEDS_REVIEW): solid black plate with white English on top, logo + badge fully visible.
- The 5 NEEDS_REVIEW cues are **layout-fit** (`translation_exceeds_safe_frame_at_equivalent_size`), all with English text drawn — not black-only, not missing English: `cue_131, cue_500, cue_506, cue_602, cue_660`. Same class M1 accepted at §8.2/§8.3.

### A5 checkpoint/resume + tamper — PASS
- Run 4 `--resume` reused all 30 OCR chunks (`chunk NN SKIP (cached)`), `L1 cache HIT`, and re-keyed only L2/L3 (extraction hash changed by the A2 merge). Checkpoint: 30 chunks `RENDERED_DONE`, `checksum=3f88b96cce4ff16d39a05cc04685f001165268c30f83e7d88ea72612414d3c24`; each chunk carries `l1_cache_key` + per-locale `translation_cache_keys`.
- Fail-closed negatives (unchanged, re-verified earlier): corrupt / master-mismatch / checksum-tamper all exit 3, never rewrite the checkpoint (`sha256 before == after`).

### A6 regression — PASS
- **M1 6-minute reconfirm:** `data/m1_reconfirm`, `M1_STATUS=DONE`, `cue_count=128`, `statuses={PASS: 128}`, `elapsed=295.8 s`; output 53,436,397 B, sha256 `5cb7864c0c17b6ee29b969b42759412bc98864870f8154048cfb7c0ea8f85973`; checkpoint `checksum=8ef4f71430f6d21a7a52d20254cfce7d58a1f95fcb6b0bbf4486abe317ae45b7`. 6/6 chunks reused.
- Focused suites pass: `tests/test_longpilot_chunking.py tests/test_e2_dynamic_sub_mask.py tests/test_hy_mt2_offline_adapter.py tests/test_subtitle_presentation_timeline.py tests/test_longpilot_translate_guards.py` (new guards added).
- Full suite: **724 tests, 713 passed, 3 failed, 8 skipped, 0 errors**. The 3 failures (`test_cp08d4_punctuation_detector`, `test_v2_start_processing_integration`, `test_visual_localization_analysis::test_adaptive_ocr_selects_changed_text_frame`) reproduce identically on the untouched baseline worktree → **pre-existing, not introduced**.

### A7 user proof — PASS
- One command (§2), real hashes and before/after frames (`m2_audit4/frames`, `frame_evidence.json`). Per-stage progress via `checkpoint.json`/`ocr_meta.json` (OCR child stdout is buffered).

## 4. Files (scoped, verified)

`app/services/clean_subtitle_render.py` (`local_sub_mask_ass_event`, `build_subtitles_filter`, `write_clean_subtitles_ass` emits the plate even without English), `app/services/e2_dynamic_sub_mask.py` (`render_dynamic_mask_window` uses the `subtitles` filter; `build_dynamic_mask_plan(review_only_ids=…)`; `load_render_cues_with_review`), `app/services/longpilot_chunking.py` (`_same_cue` split-pair merge + extent union), `app/services/hy_mt2_offline_adapter.py` (`build_repair_translation_prompt`), `tools/run_longpilot_render.py`, `tools/run_longpilot_translate.py`, plus `tests/test_e2_dynamic_sub_mask.py`, `tests/test_hy_mt2_offline_adapter.py`, `tests/test_longpilot_chunking.py`, `tests/test_longpilot_translate_guards.py` (new). 9 modified files (+291/−24) plus 2 new files (2 added).

## 5. Product delta

- Long (30-minute) URL→MP4 now completes end-to-end: the M2 blocker (`WinError 206` argv cap at render) is removed by burning plates inside the `.ass`, so the filtergraph is constant-size. Sourced from the smallest product-critical slice (reuse → wire → fix), no new subsystem.
- Removed 7 split-cue duplicates in M2 and 4 in the M1 window (132 → **128**), and eliminated the en-US CJK/prompt-echo rejects (2 → **0**). Audio stays bit-exact; provenance and time-gating preserved.

## 6. Honest residual / next blocker

- **vi-VN (secondary): 3 unresolved** — `cue_286` `势力隐隐有被擎天圣地超越的迹象`, `cue_645` `我是擎天圣地的齐光`, `cue_658` `我们擎天圣帝来拜访` retain `擎天`. These render as a source-covering plate flagged `NEEDS_REVIEW` (no leak, no fabricated text) but are **black-only in vi-VN** — i.e. not a complete vi-VN subtitle. The en-US deliverable is unaffected (713/713).
- en-US `EDITORIAL_QA` remains PARTIAL; the 5 layout-fit `NEEDS_REVIEW` cues await the Owner's M1 §8.5 ruling (bounded shrink vs review-gate).
- Downstream legacy patterns (`full_visual_pipeline.py`, `simple_workflow.py`) still use `-filter_script:v`, which this ffmpeg build rejects; they are outside M2 scope and were not modified.

## 7. Verdicts

- **M2_PRODUCT: PROPOSED_PASS (worker suggestion — requires independent Tech Lead verification).** A1/A2/A4/A5/A6/A7 evidenced on one real 30-minute URL run and a real resume; A3 holds for **all 713 en-US cues** (0 missing English, 0 leak). Caveat for the Tech Lead: if the bar is **zero unresolved cues in every locale**, the 3 vi-VN residuals (§6) are **not met**.
- TECHNICAL: PASS. EDITORIAL: PARTIAL. SECURITY: SUPERVISED_PASS (bounded public-anonymous reuse of the already-verified source; host allowlist; URL/argv redaction; input-identity and checkpoint fail-closed; no cookie/secret access, no upload). PUBLICATION: BLOCKED pending Owner.
- M3 not started; no merge/push/deploy.
