# WP-M2-001 result — 30-minute local URL→en-US MP4 and reliable recovery

Status: worker evidence complete; awaiting independent Tech Lead verification (not self-authorizing).

## 1. Identity

- Repo `D:\AutoSub`; only worktree touched: `D:\AutoSub-M2-longmedia`; branch `feat/m2-longmedia-reliability`.
- Code/test HEAD for the final acceptance: `5ce4c84ee7e9cb1806f00374b314885a505ef94d` (clean; `implementation_dirty=false`).
- Two local commits: `5ee5d34` (grounded dual inference + bleed/divergence/fit guards) and `5ce4c84` (peach-seed and face/reputation termbase fixes). No merge, push, deploy, or publication.
- Owner-accepted POLICY_REF `caea6f255aa16ce36827a875bcdbca0f8b57cb1e`; HEAD `AGENTS.md` / `docs/kit/WORKER.md` blobs match POLICY_REF. The raw worktree files use CRLF, so raw file hashes differ; HEAD blobs match.
- Product SoT `docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md`; WP `docs/research/WP-M2-001-longmedia-reliability.md`; M1 code baseline `f16ddc21340adeab0cea915edb1ed4cf63ea9df7`.
- Final source: `data/m2_job/source/source.mp4`, sha256 `402d4f3e980e664e4fbfe68baed8c406faacc57eacd5fe5f2fc7a9f4cbcb2069`, 286,820,816 B, 1800.0 s, 1280×720 h264/15 fps + aac 44.1 kHz stereo.
- Model: `Hy-MT2-1.8B-Q4_K_M`, sha256 `dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699`.

## 2. One real user journey

Final acceptance command (clean tree; existing 30-minute source reused):

```powershell
python tools/run_m1_local_url_to_mp4.py `
  --url https://www.bilibili.com/video/BV1bPeu6cEkG `
  --job-dir data/m2_job --end-seconds 1800 --resume --skip-download
```

- URL resolve + source identity verified → 30 OCR chunks reused → en-US/vi-VN L3 translations reused from the immediately preceding same-HEAD force run → adaptive-union black plates rendered → finalize.
- `M1_STATUS=DONE`; `implementation_head=5ce4c84…`; `implementation_dirty=false`; `blocking_layouts={}`.
- Final render/entrypoint elapsed `132.4 s` with all OCR chunks and both locale translations cached. The forced translation pass that populated the cache also ran at the same clean HEAD; it completed 713 en-US + 713 vi-VN cues.
- Two URL resolves total (M1 regression + final acceptance), no media re-download; source reused.

## 3. Acceptance oracles

### A1 URL/ingest — PASS

- Source SHA-256/size/duration above; input identity binds host `www.bilibili.com`, canonical id `BV1bPeu6cEkG`, part 1, `end_seconds=1800`, redacted URL, source SHA-256 and size.
- Mismatch/invalid media still fail closed (unchanged M1 behavior).

### A2 chunk/boundary integrity — PASS

- 30 × 60 s chunks; checkpoint master version `988e40c96a34e5abcac31c0ddf274c276fd5f6aad4b7bbc2c26319e23bf25d22`.
- Master extraction hash `246bea3408b21b73c519e1b6ea8414a2f5f33ad02c7dc076ff0f91841c025532`.
- Merge: 713 cues, 63 duplicates removed, 63 boundary merges. No cue loss or duplicate simultaneous display observed.
- Final checkpoint checksum `5482efa2fdd62e2cd9cc0a8f869d65a5bda7457f91203b3db040762738e6d4fd`, status `RENDERED_DONE`.

### A3 en-US translation — PASS for all 713 cues (EDITORIAL PARTIAL)

Forced run at HEAD `5ce4c84` used `CONTEXT_PROMPT_VERSION=hy-mt2-contextual-v4-grounded-2026-10-19` and `glossary-en-US-v6-context`.

- 713/713 `TRANSLATED`; modes: 675 contextual, 38 grounded.
- en-US counters: `grounded_inferences=713`, `neighbor_bleed_fallbacks=8`, `divergence_fallbacks=26`, `fit_fallbacks=3`, `repair_attempts=0`.
- No missing English, residual Han, or prompt echo in en-US.
- L3 key `a635e584421c2cf6dc26b0da9ca9cff6c24bb75cb712c57cc50704d483cc6751`, `live_cache_ok=true`.
- Locale isolation: `en_us_rekeyed=true`, `new_miss=true`, `vi_still_hit=true`, `l1_still_hit=true`.

Required defect cues verified in the final artifact:

| Cue | Final en-US text | Mode |
|---|---|---|
| `cue_131` | `System` | grounded |
| `cue_132` | `Plant these 50 peach tree seeds in the back mountain.` | contextual |
| `cue_133` | `Use 4 white character summon cards` | contextual |
| `cue_500` | `I am the deputy tower master of the Shadow Tower` | contextual |
| `cue_501` | `The tower master sent me to capture a child who possesses the Supreme Bone.` | contextual |
| `cue_506` | `How many people are there in the Shadow Tower currently?` | contextual |
| `cue_602` | `Ding! Congratulations, host. The Shadow Tower mission has been completed.` | contextual |
| `cue_603` | `The reward package has been distributed.` | contextual |
| `cue_660` | `Is it not giving me any face at all in the Qingtian Sacred Land?` | contextual |
| `cue_662` | `Do you really have such a strong reputation in the Qingtian Sacred Land?` | contextual |

- The previous 5 layout-fit `NEEDS_REVIEW` cues (`cue_131, cue_500, cue_506, cue_602, cue_660`) no longer include neighbor text and now fit at the source-equivalent size.
- `EDITORIAL_QA=PARTIAL`: no full human editorial pass. Two awkward-but-intelligible surviving literalisms are disclosed below (`cue_657`, `cue_662`/`cue_663`).
- Direct phrase checks used a termbase, not per-cue overrides: `暗影楼`, `楼主`, `副楼主`, `至尊骨`, `擎天圣地`, `擎天圣帝`, `蟠桃树种`, `种在`, and face/reputation idioms. `apply_rule_postedit` normalizes only when the source term is present.

### A4 render/output/provenance — PASS

- Render report: `cue_count=713`, `statuses={PASS: 713}`, `non_pass=[]`, `mask_model=adaptive_union_source_and_english`, `mask_padding_px=3`, `safe_text_width=1152`, `max_mask_width_px=1146`.
- Output: `data/m2_job/longpilot_en-US.mp4`, 236,355,071 B, sha256 `74b3e6374bca9b0957c168ed0794fafddd1ff2cc17cb02742f779edf9d109e0e`.
- Streams: 1800.0 s, h264 1280×720 15 fps, aac 44.1 kHz stereo.
- **Audio packet MD5 `MD5=5357d9a80fd43aaf53e9ddf5d0d2de2a` = source (bit-exact stream copy).**
- Frame audit at `385.8 s` (source vs output, H.264 re-encode noise included): mean absolute pixel difference logo ROI `(0,0)-(272,110)` = `0.938`, AI badge ROI `(960,653)-(1280,720)` = `2.544`, caption band `(0,540)-(1280,720)` = `18.194`, full frame = `5.367`. Uploader logo and AI badge remain visible; black plate is confined to the source caption strip.
- Visually inspected output frames at cues `132, 500, 506, 602, 660`: solid black plate, white source-equivalent English, no source Chinese visible, logo/badge clear.

### A5 checkpoint/resume + tamper — PASS

Resume demo, independent of the 30-minute job:

- Excerpt `data/m2_resume_demo/source/excerpt.mp4`, 180.133 s, sha256 `08014405f31ade9ef8339e265833d87f0fd876d78d79b935e9a1ea561eeb3300`.
- 3 chunks; first run `--stop-after-chunk 1` completed chunks 0 and 1 (22 and 26 cues), left chunk 2 pending.
- `--resume` printed `chunk 00 SKIP (cached, 22 cues)`, `chunk 01 SKIP (cached, 26 cues)`, processed only chunk 2 (15 cues); merged 62 cues, `duplicates_removed=1`, `boundary_merges=1`.
- Final demo checkpoint checksum `42117645cb5fcb1f4dc002cc6b09ecc538ece5472cb0bdd8af2fcd5252aca526`, master extraction hash `7b26df044c9f9d915c495f875aa361ffa78f7714b763559d53c465fedb058207`.
- Corrupt checkpoint (`checkpoint.json` = `{not-json`) → `CHECKPOINT_FAIL_CLOSED: CheckpointCorrupt`, exit `3`.
- Checkpoint bound to the 180 s excerpt, run with the 1800 s source → `CHECKPOINT_FAIL_CLOSED: CheckpointMismatch`, exit `3`.
- Neither negative rewrote the checkpoint.

### A6 regression and tests — PASS with 3 pre-existing full-suite failures

M1 6-minute reconfirm (`data/m1_reconfirm`):

- `M1_STATUS=DONE`, 128 cues, `{PASS: 128}`, elapsed `455.6 s`, 6/6 chunks reused.
- Source sha256 `a9e638243bd648e7e87addb7aba2a33b23d4141b96e7af7be23a88a26b1630b5`.
- Output 53,476,955 B, sha256 `0f8e5cce696fb7fe4022bc2c268016b116ac5996741b02360b59172b44211c65`.
- Output audio packet MD5 `1dc07eac0ffaa2ac8ebedc25c181b5f2` = source.
- `max_mask_width_px=1059`; `blocking_layouts={}`.

Full suite at HEAD `5ce4c84`:

- 732 tests: **721 passed, 3 failed, 8 skipped, 0 errors**.
- Failures:
  - `tests/test_cp08d4_punctuation_detector.py::test_cp08d4_detects_punctuation_partially_under_plate_edge`
  - `tests/test_v2_start_processing_integration.py::test_full_voice_repairs_display_projection_after_narration_alignment`
  - `tests/test_visual_localization_analysis.py::test_adaptive_ocr_selects_changed_text_frame`
- All three also fail on an untouched `92466ee` archive (baseline check). The v2 integration case fails earlier at baseline because the archive lacks the untracked `operator/run_config.json`, so its exact failure mode is environment-dependent; it is still not caused by this change.
- Focused suites pass: `tests/test_hy_mt2_offline_adapter.py`, `tests/test_longpilot_translate_guards.py`, `tests/test_longpilot_chunking.py`, `tests/test_e2_dynamic_sub_mask.py`, `tests/test_subtitle_presentation_timeline.py`.

### A7 user proof — PASS

- One final command produced the 30-minute MP4 from the authorized URL/source.
- Manifest, render report, translation metadata, checkpoint and output hashes are present under `data/m2_job/`.
- Visual proof frames and ROI measurements are recorded above; no manual stage handoff was used.

## 4. Scoped files changed

Code/tests (two local commits):

- `app/services/hy_mt2_offline_adapter.py` — grounded prompt, termbase, bleed/divergence helpers, source-gated post-edit rules.
- `tools/run_longpilot_translate.py` — dual contextual/grounded inference, bleed/divergence guards, renderer-exact fit guard, L3 prompt version.
- `tests/test_hy_mt2_offline_adapter.py`, `tests/test_longpilot_translate_guards.py` — new guard/termbase/fit coverage.

Documentation: this result report (docs-only commit after final acceptance).

## 5. Product delta

- 30-minute URL→MP4 now finishes with **713/713 en-US cues PASS** and `M1_STATUS=DONE`; the 5 previous layout-fit review flags are eliminated without shrinking fonts or forcing PASS.
- Neighbor-context copying is independently checked against a grounded same-source reference; 8 en-US bleed fallbacks and 26 divergence fallbacks were applied.
- Names/titles are stable in en-US (`Shadow Tower`, `tower master`, `deputy tower master`, `Supreme Bone`, `Qingtian Sacred Land`, `Qingtian Holy Emperor`).
- Audio remains bit-exact; logo/AI badge and uploader attribution remain visible; plates cover only the source caption strip.
- Resume/checkpoint behavior is demonstrated on a real 3-chunk excerpt, including two fail-closed exit-3 negatives.

## 6. Honest residual / next blocker

- vi-VN is secondary and not part of the en-US deliverable: 711/713 translated, `repair_attempts=2`, 2 `QUALITY_REJECTED` residual-Han cues (`cue_286`, `cue_645`, both retain `擎天`). No vi-VN render was produced in this WP.
- en-US editorial residual:
  - `cue_657` `贵宗真是好大的脸面` → `Your family indeed has great pride` is awkward; `贵宗` is a sect, not a family, and the tone is off.
  - `cue_662` `Do you really have such a strong reputation in the Qingtian Sacred Land?` is intelligible but less natural than a human edit.
  - `cue_663` `Why should I give you face?` is acceptable but blunt.
  - `cue_072` OCR is `真不害` (actual frame text `真不害臊`, missing `臊`); `cue_162` OCR `不三你们` is likely `不杀你们`. These are source-OCR residuals; translations inherit them and are not overfitted per cue.
- Full suite retains 3 pre-existing failures listed in A6; no protected test was edited.
- `EDITORIAL_QA=PARTIAL`; a full human editorial review is still required before publication.
- M3/Cloud expansion was not started; no merge/push/deploy/publication.

## 7. Verdicts

- **M2 technical/product candidate: PROPOSED_PASS (worker evidence; independent Tech Lead verification required).**
- TECHNICAL: PASS on the final 30-minute artifact, final manifest, render report, audio identity, ROI checks, resume demo and M1 regression.
- EDITORIAL: PARTIAL (en-US technical completeness proven; human editorial review and the residuals above remain).
- SECURITY: SUPERVISED_PASS (public anonymous source reuse, host allowlist, redacted URL/argv, input-identity and checkpoint fail-closed, no cookies/secrets/upload).
- PUBLICATION: BLOCKED pending Owner authorization.
