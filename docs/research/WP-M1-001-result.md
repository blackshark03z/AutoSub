# WP-M1-001 result — Ship Local URL to en-US MP4 (Bilibili, one job)

**Status:** M1_PRODUCT_PASS_SUGGESTED (evidence below), NOT yet independently verified, NOT_PUBLICATION_AUTHORIZED.
**Verdicts:** M1_PRODUCT = PROPOSED_PASS · TECHNICAL = PASS · EDITORIAL = PARTIAL · SECURITY = SUPERVISED_PASS · PUBLICATION = BLOCKED (Owner-only).
**Product Delta (pre-WP baseline):** before this WP no single command turned a live Bilibili URL into a finished en-US captioned MP4; the pilot blocked OCR/translation and the longpilot rendered from a pre-downloaded 6-min fixture. This WP now yields one URL-originated 6-min job end-to-end from the real media.

## 1. Identity

- Worktree: `D:\AutoSub-M1-local-url-mp4` (only tree modified).
- Branch: `feat/m1-local-url-to-mp4`. Baseline HEAD (run-time `implementation_head`): `d4d55ddbbfb41b8ebd3e5cf718b42c506a5a8307`.
- POLICY_REF: `caea6f255aa16ce36827a875bcdbca0f8b57cb1e` (AGENTS.md blob `f5910fd8…`, docs/kit/WORKER.md blob `792b81af…` matched at that commit; no stale local override).
- Product SoT: `docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md`.
- Reused read-only evidence refs: `review/us-longpilot-20261017` @ `f5f8e255` (drivers/modules), `feat/pilot-e2-local-sub-overlay` @ `0d0c28a` (ingestion contract). No wholesale cherry-pick/merge; only selected modules copied.

## 2. One real user journey (A1, A7)

Single command (one human invocation, no manual intermediate assembly):

```
C:\Users\ADMIN\AppData\Local\Programs\Python\Python311\python.exe tools/run_m1_local_url_to_mp4.py \
    --url https://b23.tv/gK7gvDO --job-dir data/m1_job --end-seconds 360 --resume
```

- Input URL: `https://b23.tv/gK7gvDO` → canonical `BV1bPeu6cEkG_p1` (P1, full part duration 28312.88s); **bounded first 6 min only** (`--end-seconds 360`), 1 actual download (≤2 allowed).
- URL is redacted in the manifest (`input_url_redacted`), host allowlist enforced (`b23.tv`/`bilibili.com`), no signed playback URL persisted.
- Manifest chain: `input_url_redacted` → `source_resolution.canonical_id` → `job.job_id=job_0001_a9e63824` → `media.sha256` → `ocr_meta.master_version_id` → `render.ass` → `output.mp4_sha256` → `implementation_head`. See `data/m1_job/m1_manifest.json`.
- Terminal status: `M1_STATUS=DONE`, `finalize_exit=0`, `blocking_layouts={}`.

## 3. Acceptance oracles

### A1 URL/ingest — PASS
- `resolve_source` (yt-dlp `--dump-single-json`) → `RESOLVED`, `PUBLIC_ANONYMOUS`, `LIVE_MEDIA_VALIDATED`.
- `download_bounded`: `--download-sections *0:00:00-0:06:00`, `--max-filesize 200M`, single part, 2 attempts max.
- `probe_and_validate`: ffprobe h264 1280x720 @15fps + aac 44100 stereo, ffmpeg full decode **0 errors**.
- Source media: 63,962,107 bytes, sha256 `a9e638243bd648e7e87addb7aba2a33b23d4141b96e7af7be23a88a26b1630b5`, duration 360.0s.
- Negative: malformed scheme/host rejected pre-network (`URL_REJECTED`).

### A2 integrated OCR/translation — PASS
- Real chunked Chinese hardcoded-subtitle OCR on the A1 media, same job: `master_extraction_hash=e901f9294009c528bc77f0a7b44e76c1ed91eff8bea489b40c82c007af64c7ec`, `l1_cache_key=4681766989a353d89f156203ff3b740e3ee611d1b40fff13055a1090b2a78ad9`.
- `cue_count=137`, `duplicates_removed=6`, `boundary_merges=6`, `chunk_count=6` (boundary merging verified — no cue loss/dup at boundaries). Offline HY-MT2 llama.cpp localization to en-US, locale-keyed cache. Historical 137 dead-on match used as comparison only.
- Same media hash flows OCR → translate → render within one job (no predownloaded fixture; the source came from the live URL this run).

### A3 image/font — PASS (editorial PARTIAL)
- Before/after frame evidence (1280x720): `data/m1_job/evidence/{before,after}_{10.5,15,115}.png`.
  - t=15.0s short cue: original Chinese replaced by tight solid-black mask (`black@1.0`, padding 3px) with en-US overlay at the same source position and visually equivalent glyph height; uploader logo (top-left) and AI badge (bottom-right) intact.
  - t=10.5s another cue: same behavior.
  - t=115s silent gap: **no mask, no overlay**; logo + AI badge intact.
- Per-cue metrics in `data/m1_job/render_report_en-US.json` (font_size, text_width, mask bbox/padding, status per cue). Example cue_001: font_size 49, text 172px, mask x588 y596 w103 h57 (padding 3).
- Geometry QC: pre-clamp 130 PASS / 6 PROTECTED_ROI_INTERSECTION / 1 NEEDS_REVIEW → post-clamp **137 PASS**; 6 cues clamped (cue_027/033/034/035/037/132), 1 refit (cue_038), `unrecoverable=[]`, `max_text_width=1152`, `max_mask_width_px=793`. No full-width band, no downscaled-font fallback accepted silently.
- Protected ROI recorded: logo (0,0,272,110), AI badge (956,649,1279,719).
- EDITORIAL remains PARTIAL: not every English line is stylistic/idiomatic; editorial quality is not improved or claimed here.

### A4 output — PASS
- Output: `data/m1_job/longpilot_en-US.mp4`, 54,388,502 bytes, sha256 `93b43028ab15110077886f3028ebce32f9c0bada62f998efeb1ac83f99f6485c`.
- ffprobe: 360.0s, h264 1280x720@15fps, aac 44100 stereo; decodes clean.
- Audio preserved: source vs output audio packet MD5 identical = `MD5=1dc07eac0ffaa2ac8ebedc25c181b5f2` (stream copy). Source not mutated (source sha unchanged; render wrote a new file).
- Manifest + ASS (`longpilot_en-US.ass`) + subtitle/report artifacts present.

### A5 resume — PASS
- Resume proven: re-run with `--resume` printed `chunk 00 SKIP (cached)` and reused completed chunks rather than recomputing.
- Fail-closed negative cases (run on temp copies, good job untouched):
  - corrupted `checkpoint.json` → OCR tool exit **3** (fail-closed, no overwrite).
  - same checkpoint vs **different source sha** → exit **3** (`CheckpointMismatch`).
  - good `checkpoint.json` (5125 B) left intact afterward.

### A6 regression — PASS (focused) / PASS-with-pre-existing (full)
- Focused: exact command —
  `python -m pytest tests/test_longpilot_chunking.py tests/test_multimarket_contracts.py tests/test_multimarket_cache.py tests/test_e2_dynamic_sub_mask.py tests/test_experimental_cue_ocr_tracking.py tests/test_hy_mt2_offline_adapter.py tests/test_longpilot_mask_clamp.py tests/test_ingestion_gap_closure.py tests/test_e1_e2_crosslane_binding.py -q`
  → **117 collected, 115 passed, 2 skipped, 0 failed**.
- Full suite: `python -m pytest -q --continue-on-collection-errors` → **695 collected, 684 passed, 8 skipped, 3 failed**.
  - The 3 failures (`test_cp08d4_detects_punctuation_partially_under_plate_edge`, `test_full_voice_repairs_display_projection_after_narration_alignment`, `test_adaptive_ocr_selects_changed_text_frame`) **reproduce identically on a clean checkout of baseline `d4d55dd`** (verified in a throwaway detached worktree) → pre-existing, not introduced by M1.
  - Environment note: `operator/run_config.json` (machine-local, gitignored) was absent in this fresh worktree and is required by `app/core/config.get_settings`; adding the same local config used by the existing worktrees reduced full-suite failures from 96 to 3. No acceptance/protected test was weakened; no test was skipped to pass.
- `test_local_sub_mask_overlay.py` was **not** ported: it imports the legacy `local_sub_overlay_layout_for_interval` path that M1 does not use (M1 uses `e2_dynamic_sub_mask`), so porting it would pull an unused legacy plan path. This is disclosed, not silently dropped.

### A7 user proof — PASS
- One executable entrypoint (`tools/run_m1_local_url_to_mp4.py`) goes URL → local MP4 with no manual intermediate assembly; manifest connects URL → job ID → media SHA → OCR/translation artifacts → MP4 SHA → HEAD (see §2). Frame paths included above.

## 4. Scope, files, resources

- Changed/added (23 files, +7621/−2):
  - New: `tools/run_m1_local_url_to_mp4.py`; `app/services/{e2_dynamic_sub_mask,longpilot_chunking,multimarket_cache,multimarket_contracts,hy_mt2_offline_adapter,ingestion_contract,experimental_cue_ocr_tracking}.py`; `tools/run_longpilot_{ocr,translate,render,finalize}.py`; tests ported from `f5f8e255`.
  - Edited: `app/core/redaction.py` (`redact_url`, extended SENSITIVE_WORDS); `app/services/clean_subtitle_render.py` (`build_local_sub_mask_overlay_filter`, LOCAL_SUB_MASK_OVERLAY branch in `write_clean_subtitles_ass`); `app/services/experimental_cue_ocr_tracking.py` (configurable OCR subprocess timeout).
- Resource use: 1 download attempt, source transfer 63.96 MB (limit 200 MiB); job dir well under 3 GiB; wall clock 1325.2s (~22 min, within 120-min session). Reused the pre-existing Hy-MT2 GGUF and OCR env; no new model weights downloaded; no paid/cloud inference.

## 5. Honest gaps / next blocker

- EDITORIAL PARTIAL: line-level English style is functional, not polished; a couple of lines remain awkward. Not silently promoted to editorial PASS.
- Cloud release (M2–M5), other providers/locales, dubbing and UI rework are explicitly out of scope.
- Remaining ship blocker (next): none within M1 scope; the next product gap is M2 long-feature chunk scaling on top of this working single-job spine.

## 6. Verdict summary

- M1_PRODUCT: PROPOSED_PASS — A1–A7 each evidenced on one actual URL-originated local job with mask/font rules passing; requires independent Tech Lead verification (worker suggestion ≠ authorization).
- TECHNICAL: PASS. EDITORIAL: PARTIAL. SECURITY: SUPERVISED_PASS (bounded public anonymous download, URL redaction, host allowlist, no secret/cookie extraction). PUBLICATION: BLOCKED pending Owner.

## 7. Acceptance-closure update (2026-10-18) — supersedes §3 A3/A6 verdicts

Tech Lead review (`docs/research/WP-M1-001-techlead-review.md`, verdict `M1=PARTIAL_DELIVERY`) found the earlier `M1_STATUS=DONE` rested on a **false-PASS layout bug** and an unbound input pipeline. This section records the fix and the honest re-run.

### 7.1 Root cause fixed (P0)
- A3's "137 PASS / max_text_width=1152" was not a real fit. `fit_overlay_text` accepted a target font down to `0.72*target`, and `apply_protected_roi_clamp` promoted `NEEDS_REVIEW`→`PASS` when `text_width == 0` (no measured fit). `refit_needs_review_layouts` then shrank fonts to 16px, while text was fit against the full 0.90*frame (1152px) budget, not the narrow mask. Measured: 129/137 cues had `text_width > mask width`; 15 cues had font/height ratio < 0.90 (min 0.329); cue_034/035/037 had `text_width=0`.
- Fix (`app/services/e2_dynamic_sub_mask.py`, `app/services/clean_subtitle_render.py`): translations are now fitted against the **confirmed source-mask width** at an **equivalent glyph size** (`SOURCE_FONT_TARGET_RATIO=0.96`, `SOURCE_FONT_MIN_RATIO=0.88`). A cue that cannot fit becomes `NEEDS_REVIEW` (reason `translation_exceeds_source_mask_at_equivalent_size`); no silent downscale, no promotion from a zero-width fit. Any mask grazing a protected ROI fails closed. `refit_needs_review_layouts` removed. The clamp re-fits against the clamped width and can therefore also yield `NEEDS_REVIEW` (`translation_exceeds_clamped_source_mask`).

### 7.2 Input identity + redaction + commit binding (P0/P1)
- `tools/run_m1_local_url_to_mp4.py`: the requested URL/part/section is bound to the on-disk media in `input_identity.json` (`canonical_id`, `part_index`, `end_seconds`, `requested_host`, `source_sha256`). Existing media without a recorded identity is refused; any changed request or tampered hash raises `IMMUTABLE_JOB_INPUT_MISMATCH` (fail closed). `--skip-download` no longer adopts stale media.
- Resolved share URLs are stripped to `scheme://host/path` (`app/core/redaction.canonical_url`), and argv/yt-dlp stderr are scrubbed (`redact_text`), removing `mid`/`buvid`/`share_session_id`/`timestamp`/tokens. The manifest records `implementation_head` + `implementation_dirty` so acceptance binds to the exact executed commit.
- New tests: `tests/test_m1_input_identity.py` (identity write/verify, mismatch/parametrized, tampered hash, missing identity refused, unreadable record, canonical-id fallback, embedded-URL redaction) — all pass.

### 7.3 Re-run on the fixed code (one URL, bounded 6 min)
- Command (clean tree, HEAD `d7ac802d159d82951332ece3926a5badd7f47d42`, `implementation_dirty=false`):
  `python tools/run_m1_local_url_to_mp4.py --url https://www.bilibili.com/video/BV1bPeu6cEkG --job-dir data/m1_job --part 1 --end-seconds 360`
- Source unchanged: sha256 `a9e638243bd648e7e87addb7aba2a33b23d4141b96e7af7be23a88a26b1630b5` (63,962,107 B), bound in `data/m1_job/input_identity.json`.
- Render (`data/m1_job/render_report_en-US.json`): pre-clamp 62 NEEDS_REVIEW / 69 PASS / 6 PROTECTED_ROI_INTERSECTION → post-clamp **67 NEEDS_REVIEW, 70 PASS**, `clamped=[cue_027,033,034,035,037,132]`, `unrecoverable=[]`, `fit_budget=confirmed_source_mask_width`, `safe_text_width=1152`, `max_mask_width_px=793`.
- Output: `data/m1_job/longpilot_en-US.mp4`, 54,356,559 B, sha256 `63e26843a46704c1eafc8e582e2fabf36df4219b1ca35bad94c1f83ebf935f8c`; audio packet MD5 `MD5=1dc07eac0ffaa2ac8ebedc25c181b5f2` (identical to source — audio preserved).
- `M1_STATUS=NEEDS_REVIEW`, `blocking_layouts={NEEDS_REVIEW: 67}`.
- Evidence frames: `data/m1_job/evidence/frame_{10.5,15,83.1,86.7}s.png`. t=10.5s (cue_002, font 46px, text 201px ≤ mask 229px) = PASS; t=83.1s (cue_034) = NEEDS_REVIEW — overlay visibly wider than the narrow source mask (the exact case that previously false-PASSed). Uploader logo and AI-content badge preserved in both.

### 7.4 Regression
- Focused: `python -m pytest tests/test_e2_dynamic_sub_mask.py tests/test_longpilot_mask_clamp.py tests/test_m1_input_identity.py -q` → 33 passed, 1 skipped.
- Full suite still shows only the 3 pre-existing failures reproduced at baseline `d4d55dd` (punctuation detector, narration alignment, adaptive OCR frame selection); none introduced here. Remaining layout tests pin the no-false-PASS invariant.

### 7.5 Honest verdict (revised)
- **M1_PRODUCT: NOT_PASS.** Under the Owner rule (narrow mask tight to the Chinese bbox; English at an equivalent glyph size) **67/137 (49%)** of cues cannot fit the source mask in English and are correctly `NEEDS_REVIEW`, not silently shrunk. A1/A2/A4/A5/A7 remain evidenced; A3 (visual acceptance) does not meet a product bar.
- **Material decision needed from Owner (not a worker choice):** Chinese → English text expansion is intrinsic, so "equal glyph size inside the same tight mask" cannot be satisfied for roughly half the lines. The options are: (a) accept English that overflows the narrow mask horizontally within the safe frame (mask stays tight to the Chinese; keeps ~all PASS); (b) allow a small, bounded font downscale with a floor and mark sub-floor cues NEEDS_REVIEW; or (c) keep strict and ship as a review-gated artifact. No option is implemented speculatively; current code implements (c) and reports (a)/(b) with measured counts.
- TECHNICAL: PASS for the fixed behaviours. EDITORIAL: PARTIAL. SECURITY: SUPERVISED_PASS (bounded public anonymous download, host allowlist, URL/argv redaction, input-identity fail-closed, no cookie/secret access). PUBLICATION: BLOCKED pending Owner.
