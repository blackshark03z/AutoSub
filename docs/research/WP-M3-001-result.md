# WP-M3-001 result — Phase A discovery and Phase B-1 Hongguo local proof

**Status:** Phase A `PHASE_A_COMPLETE / PARTIAL_DISCOVERY`; Phase B-1 `M3_HONGGUO_LOCAL_PROCESSING=PROPOSED_PASS`; `M3_PRODUCT=NOT_PASS / URL_INGEST_UNPROVEN`.
**Phase B:** Phase B-1 local-file processing complete for Hongguo `Tap_001`; genuine selected-platform URL→MP4 ingestion remains NOT_STARTED.
**Publication:** BLOCKED. No upload, push, merge, deploy or external media/model operation was performed.

## 1. Identity and preflight

- Worktree: `D:\AutoSub-M3-multisource` only.
- Branch: `feat/m3-multisource-intake`.
- Baseline HEAD: `4198664c8138bf373bf17b2e1d37368639136300`; clean before Phase A work.
- POLICY_REF `caea6f255aa16ce36827a875bcdbca0f8b57cb1e` exists in this Git history; blobs at that ref match this worktree:
  - `AGENTS.md` = `f5910fd8b62da3bd18e79fd0ee127cac154d5c0d`
  - `docs/kit/WORKER.md` = `792b81afb67976881490e6ba1d105066d61b727f`
- Product SoT: `docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md` (M3 = multi-source intake, platform list and minimum coverage require separate Owner confirmation).
- WP: `docs/research/WP-M3-001-multisource-intake.md`.
- Accepted baseline reused read-only: M2 candidate `5ce4c84ee7e9cb1806f00374b314885a505ef94d`, Bilibili `BV1bPeu6cEkG` Part 1 (`cid=41959818910`), 1800 s en-US MP4.
- Scoped changes: `tests/test_m3_multisource_intake.py` (new negative/discovery tests) and this report. No product code, allowlist, pipeline, OCR, translation, renderer, checkpoint or M2 artifact was modified.

## 2. Existing intake audit

### Bilibili — working baseline

- Entrypoint `tools/run_m1_local_url_to_mp4.py` enforces `ALLOWED_HOSTS = {b23.tv, www.b23.tv, bilibili.com, www.bilibili.com, m.bilibili.com}` before any network call.
- Bilibili resolution uses `yt-dlp --flat-playlist --playlist-items <part>`; media download is bounded by section, duration, format height and `--max-filesize`.
- Input identity binds requested host, canonical Bilibili ID, part index, bounded interval, redacted URL and source SHA-256; a changed URL/part/interval or tampered media fails closed.
- M1/M2 accepted artifacts: source SHA-256 `402d4f3e980e664e4fbfe68baed8c406faacc57eacd5fe5f2fc7a9f4cbcb2069` (1800 s), 30 OCR chunks, 713/713 en-US layout PASS, output SHA-256 `74b3e6374bca9b0957c168ed0794fafddd1ff2cc17cb02742f779edf9d109e0e`, audio packet MD5 unchanged.
- Phase A made no new Bilibili run and downloaded no media; this baseline is inventory/regression reference only.

### Kylin / Hongguo — restricted negative, not a candidate

- `create_kylin_ingestion_handoff` returns `discovery_status=APP_ONLY_RESTRICTED`, `access_status=APP_ONLY`, `ingestion_status=INGESTION_BLOCKED`, `error_code=APP_ONLY_RESTRICTED` for `https://kylin.hainanyuyue.com/s/i9J6kMK62PE/`.
- The recorded web preview is a `30 s` teaser (`parts_or_chapters` type `web_teaser`), while the series reports 113 app-only episodes. `attach_and_validate_local_media` returns the blocked record unchanged, so a teaser file cannot be promoted to a full episode.
- No new Kylin request was made in Phase A. The classification rests on the committed contract/tests plus the historical 2026-10-17 evidence (public HTML 200, 30 s web preview, app-only full episodes). It remains a negative test, not an approved workaround.

### Critical distinction preserved

`METADATA_ONLY`, `LOCAL_FIXTURE_VALIDATED`, `LIVE_MEDIA_VALIDATED` and `INGESTION_BLOCKED` remain independent states. A metadata-only record cannot dispatch an ingestion job; a missing/corrupt file yields `INGESTION_FAILED`, never a fake MP4.

## 3. Platform classification matrix

| Source | Classification | Evidence | Full-media access | Fit for M3 |
|---|---|---|---|---|
| Bilibili `BV1bPeu6cEkG` | `VERIFIED_BASELINE` | Accepted M1/M2 source SHA, 1800 s artifact, 713 cues | Yes (bounded part/section) | Regression baseline |
| Kylin/Hongguo share link | `APP_ONLY_RESTRICTED` | Committed contract/tests; historical 30 s web teaser, 113 app episodes | No | Negative only |
| YouTube `2TeSCz4vcSQ` | `PUBLIC_FULL_MEDIA_VERIFIED` (metadata) | Public, 78 082 s, 19 formats, max 1080p, no soft/auto subtitle tracks; uploader `迷妹漫剧丨AI Animation`; upload 2026-07-04 | Yes (full video public; bounded section recommended) | **Primary candidate** |
| YouTube `x0nK3i6aEvA` | `PUBLIC_FULL_MEDIA_VERIFIED` (metadata) | Public, 60 909 s, 42 formats, max 720p, no soft/auto subtitle tracks; uploader `Kitten Comics`; upload 2026-06-25 | Yes (full video public; bounded section recommended) | **Backup candidate** |
| Dailymotion | `NO_VERIFIED_MATCH` | Two bounded anonymous searches (`短剧 中文字幕`, `chinese drama`) returned 0 entries | Not established | Do not propose |
| iQiyi / Youku / WeTV / MangoTV | `NOT_VIABLE_WITHOUT_AUTH_DRM` | Structural platform access model; no anonymous full-media probe attempted | No (account/DRM/region) | Do not propose |

Both YouTube titles are compilations of the same novel adaptation family as the historical YouTube sample (`《随便建个宗怎么就无敌了》` chapters 1–178 / 1–181). They are **different works/adaptations from the Bilibili 3D CGI baseline** and must never be presented as the same episode or as a mirror of the Bilibili video.

### YouTube subtitle evidence

- Metadata exposes **no soft subtitles and no automatic captions** for either candidate, so any caption source is burned into the video pixels, matching the OCR pipeline.
- Historical visual evidence from the 2026-10-17 reverse-engineering audit recorded yellow burned-in Chinese subtitle frames from `2TeSCz4vcSQ` at `D:\AutoSub\docs\research\evidence\evidence_youtube_mimei_frame_{2s,10s}.png` (untracked in the main tree, not re-verified in this worktree). Phase A did not download media, so the current burned-in subtitle layout of these long compilations is **not newly verified**.

## 4. Phase A probes actually run

Metadata-only, anonymous, no cookies, no media download, no DRM bypass. Scratch JSON was written outside the repo to `C:\Users\ADMIN\AppData\Local\Temp\opencode\`.

```powershell
yt-dlp --no-warnings --skip-download --dump-single-json --no-playlist https://www.youtube.com/watch?v=2TeSCz4vcSQ
yt-dlp --no-warnings --skip-download --dump-single-json --no-playlist https://www.youtube.com/watch?v=x0nK3i6aEvA
yt-dlp --no-warnings --skip-download --flat-playlist --dump-single-json https://www.dailymotion.com/search/<query>/videos
```

Observed results:

- `2TeSCz4vcSQ`: `availability=public`, `live_status=not_live`, `age_limit=0`, `duration=78082`, 19 formats, max height 1080, `subtitles=[]`, `automatic_captions=[]`.
- `x0nK3i6aEvA`: `availability=public`, `live_status=not_live`, `age_limit=0`, `duration=60909`, 42 formats, max height 720, `subtitles=[]`, `automatic_captions=[]`.
- Dailymotion: `playlist_count=0` for both queries; no URL is claimed.
- Total public metadata/search requests: 2 to YouTube, 2 to Dailymotion. No request was made to Kylin or any account/DRM-protected platform.

## 5. Reuse assessment for Phase B

| Component | Reuse | Required Phase B change |
|---|---|---|
| URL validation / allowlist | Host allowlist + scheme check already fail closed | Add only the Owner-selected platform host(s); keep Kylin and lookalike hosts rejected |
| Resolver | Generic `yt-dlp` JSON resolve, canonical ID from `entry.id` | `resolve_source` is named/structured for Bilibili; make platform identity explicit and preserve YouTube ID |
| Canonical provenance | `canonical_url` strips query string | **Concrete gap:** `https://www.youtube.com/watch?v=<id>` becomes `https://www.youtube.com/watch`, losing the video ID. Platform-aware canonical URL is needed before binding YouTube provenance |
| Downloader | Generic `download_bounded` with section, ≤720p, 200 MiB cap, 2 attempts | Reuse as-is with Owner-approved bounds; verify section cutting on long compilations |
| Handoff/job identity | `IngestionHandoffRecord`, dedup by canonical id + item + media SHA | `create_bilibili_ingestion_handoff` and M1 entrypoint hardcode `platform="bilibili"` / `source_id` prefix; add a selected-platform handoff, no new framework |
| Input identity / checkpoint | Fields are platform-neutral; checkpoints bind source hash | Reuse unchanged; ensure new platform source hash never attaches to Bilibili checkpoints |
| OCR / en-US / offline HY-MT2 | Source-hash driven, platform-neutral | Reuse; verify burned-in subtitle geometry and language on the real segment |
| Mask/render | Adaptive union black plate, 1280×720 renderer | Reuse; protected ROI (uploader logo / AI badge) must be re-measured for the new source. Do not assume Bilibili ROIs |
| Audio/attribution | Stream copy and artifact checks | Preserve original audio; preserve whatever protected marks exist; never invent an AI badge |

No new queue, resolver framework or platform adapter is justified before Owner selection.

## 6. Negative-case tests

New `tests/test_m3_multisource_intake.py` (8 tests) covers:

1. YouTube URL is rejected by the current M1 allowlist (no silent expansion).
2. Kylin host is rejected by the M1 allowlist.
3. Lookalike hosts `bilibili.com.evil.example` and `evilbilibili.com` are rejected.
4. Non-HTTP scheme is rejected before network.
5. A 30 s Kylin teaser cannot be promoted to a full episode; the blocked record keeps no media path/hash.
6. A metadata-only handoff cannot dispatch a job.
7. Missing media yields `INGESTION_FAILED` and creates no file.
8. Failed validation cannot dispatch a job.

Focused verification command and result:

```powershell
python -m pytest tests/test_m3_multisource_intake.py tests/test_ingestion_gap_closure.py tests/test_m1_input_identity.py -q
# 31 passed
```

Existing coverage still proves four distinct ingestion states, missing/corrupt media, wrong part index, arbitrary BV ID and URL credential redaction. No protected test was edited. The full suite was not run because Phase A produced no product candidate and no product-code change.

## 7. Product Delta

- Established a grounded platform matrix that separates verified public full media (YouTube), restricted app-only content (Kylin/Hongguo), unverified search results (Dailymotion) and auth/DRM platforms.
- Proved that Phase A did not widen the Bilibili allowlist and that unsupported/lookalike hosts, metadata-only records and teasers still fail closed.
- Identified one concrete reuse gap before Phase B: canonical URL handling drops the YouTube video ID, and platform identity/ROI are still Bilibili-specific.
- No new platform URL→MP4 artifact exists. M3 remains `PRODUCT_NOT_YET_PROVEN`.

## 8. Recommended smallest M3 positive coverage

**Option A (recommended):** Bilibili M1/M2 regression + one bounded real YouTube segment from `2TeSCz4vcSQ`.

- Source: `https://www.youtube.com/watch?v=2TeSCz4vcSQ` (uploader `迷妹漫剧丨AI Animation`, public, 78 082 s, 19 formats, max 1080p, no soft subtitle tracks).
- Segment: `0:00–6:00` at ≤720p, one download, ≤200 MiB, ≤2 attempts.
- Processing: existing OCR → offline HY-MT2 en-US → adaptive union mask → MP4; ≤120 min; no cookies, paid API or external upload.
- Why: public anonymous metadata verified now; burned-in Chinese subtitle evidence exists from the historical sample; yt-dlp already supports the host; 720p matches the accepted 1280×720 renderer.
- Caveat: this is a different adaptation from the Bilibili baseline. It must be labeled as such in the manifest and acceptance report.

**Option B (backup):** the same bounded `0:00–6:00` segment from `x0nK3i6aEvA` (public, 60 909 s, 42 formats, max 720p, uploader `Kitten Comics`).

**Option C:** Owner supplies a specific authorized public URL from another platform. Phase A cannot claim viability without a real URL; a search result, title or teaser is not enough.

**Not proposed:** Kylin/Hongguo (app-only), Dailymotion (no verified match), iQiyi/Youku/WeTV/MangoTV (account/DRM/region), or any second Bilibili link counted as a new platform.

## 9. Risks, limitations and blocker

- **Single most important blocker:** no Owner-selected, verified additional platform/URL and no approved coverage. YouTube is technically viable per metadata, but platform authorization/terms, content ownership and the exact bounded segment remain Owner decisions.
- Phase A did not download media, so burned-in subtitle presence/geometry on the current YouTube compilations is historical evidence, not a fresh observation.
- Full-length candidates are 16–22 h compilations; full-video acceptance is impractical. A bounded segment must be explicitly approved.
- `canonical_url` loses the YouTube `v=` identifier; this must be fixed in Phase B before provenance binding.
- Protected ROI, subtitle safe zone and AI/uploader marks differ by source; renderer calibration is required.
- YouTube extraction can change; no guarantee of future availability. No publication or redistribution rights are implied.
- Editorial quality remains `PARTIAL` from M2; a new platform does not change that.

## 10. Owner decision requested

1. Approve or reject **YouTube** as the additional M3 platform.
2. Select the exact source: **`2TeSCz4vcSQ` (recommended)** or `x0nK3i6aEvA`, or provide an authorized URL from another platform.
3. Approve the bounded coverage: **first 360 s, ≤720p, ≤200 MiB, ≤2 download attempts, ≤120 min local processing**, or specify a different segment/limit.
4. Confirm that M3 minimum coverage = **Bilibili regression + one bounded real segment from one new platform**, not a full 16–22 h compilation.
5. Authorize Phase B local retrieval/processing only; publication, upload, merge, push and deploy remain separate approvals.

**Tóm tắt cho Owner:** Phase A đã xác minh YouTube là nguồn bổ sung khả thi công khai (2 video dài, có định dạng tải được, không có phụ đề mềm), Kylin/Hongguo vẫn là app-only/teaser và không được tính. Đề xuất M3 = Bilibili regression + một đoạn thật 6 phút từ `2TeSCz4vcSQ` (≤720p, ≤200 MiB). Cần Owner chọn nền tảng/URL/độ dài đoạn và cấp phép Phase B; chưa có M3 Product PASS.

## 11. Verdicts

- **M3 Phase A:** `PARTIAL_DISCOVERY` (complete for the authorized discovery scope; platform selection pending).
- **M3 Product:** `NOT_YET_PROVEN`.
- **TECHNICAL:** focused tests PASS (31); no new-platform artifact.
- **EDITORIAL:** unchanged from M2 (`PARTIAL`).
- **SECURITY:** public anonymous metadata only; no cookies, DRM bypass, secrets or uploads; allowlist unchanged.
- **PUBLICATION:** `BLOCKED`.
- **Next:** Tech Lead verifies this report and presents the platform/coverage choice to Owner. Stop before Phase B, M4 or M5.

---

# Phase B-1 — Hongguo local episode acceptance (Owner-approved 2026-10-20)

**Worktree:** `D:\AutoSub-M3-multisource`; **branch:** `feat/m3-multisource-intake`; **baseline HEAD:** `249bd5491708a2299b9ca44a7e6d12633498f60e`; POLICY_REF `caea6f255aa16ce36827a875bcdbca0f8b57cb1e` (worktree `AGENTS.md` blob `f5910fd8…`, `docs/kit/WORKER.md` blob `792b81af…`). This Phase B-1 proof was produced on a dirty tree; the scoped changes are committed after the run.

## 1. Scope and identity

- Provider is explicitly `HONGGUO_LOCAL_FILE`, `input_mode=LOCAL_INPUT`, `url_ingest=false`. No Hongguo host was added to any allowlist and no URL-like identity was fabricated.
- Series `7690432295777340441`, episode `001`, title `隐世魔尊，女帝带娃找上门来`, interval `0–93855 ms`.
- Original source (read-only): `D:\Youtube\Youtube Package Crawl\output_hongguo\[隐世魔尊，女帝带娃找上门来] [7690432295777340441]\[隐世魔尊，女帝带娃找上门来]_Tap_001.mp4`.
- Source SHA-256 `070c8e0c91c46288b8d3831e906b7349c0d60462967620759da6e79645afb186`; 10,106,862 bytes; HEVC 1920×1080/30 fps; AAC 44.1 kHz stereo; 93.855011 s.
- Original hash was verified before and after copy/copy-reuse; the original was not modified. Gitignored job scratch: `data/m3_hongguo_job/` (60,854,498 bytes including output/evidence).
- Identity file `data/m3_hongguo_job/local_input_identity.json` binds provider, series, episode, interval, source SHA-256, code HEAD/dirty state, OCR/sampling config and empty source-specific ROI map. A mismatched episode or source hash fails closed.

## 2. Implementation (reuse-first, smallest local adapter)

- New `app/services/hongguo_local_intake.py` and `tools/run_m3_hongguo_local.py` implement the local-file handoff only. They validate extension, non-empty file, video/audio streams, positive duration, expected SHA-256, and full decodability; a metadata-valid but truncated file is rejected as `TRUNCATED_MEDIA`.
- `create_or_reuse_ingestion_job` now accepts the explicit `LOCAL_SOURCE_VALIDATED` state; no allowlist or URL resolver was changed.
- `app/services/experimental_cue_ocr_tracking.py` was fixed for real 1080p media: cue construction now passes actual frame dimensions to `compute_narrow_plate`; the previous 1280×720 default clipped the bottom to 719 px and raised `ValueError` for captions at y≈943–1024. Added regression coverage.
- `app/services/e2_dynamic_sub_mask.py` and `tools/run_longpilot_render.py` accept a source-specific protected-ROI JSON map; the render report now exposes `center_x`, `anchor_y` and `source_bbox` for independent cue auditing.
- `app/core/media.py` now reads ffprobe JSON as UTF-8 with replacement, so Chinese metadata/paths do not crash probing.
- `app/services/clean_subtitle_render.py` strips Markdown `**`/backtick artifacts from model output before rendering. This fixed a visible `**measures**` defect without rewriting the translation cache.
- `docs/research/WP-M3-001-result.md` header was updated; Phase A content is preserved.

## 3. Pipeline evidence

- OCR: 2 chunks, 22 + 17 cues, merged to **35 cues** (`duplicates_removed=4`, `boundary_merges=4`); `master_extraction_hash=54224d884f0ad2e1ba64298503fd5c78f7f7d0669a7af7868bc1c3db01eb03f4`; `l1_key=92a1d550b408883986f735f677bb99331a161daf8b009ce16dd65896b145d25c`; `roundtrip_ok=true`.
- Translation: offline HY-MT2 `tencent/Hy-MT2-1.8B-Q4_K_M`, model SHA-256 `dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699`; **35/35 en-US** (`TRANSLATED` 35, `fit_fallbacks=0`, one divergence fallback); en-US L3 `37caaa185f37ddf443140a524aec783830c1ae15633dbff5c2203d25b5f3e7c5`. vi-VN secondary also 35/35, L3 `0a593b0556e541162edb194f38fb8f6f2ef4e92187e22f2ab2cab61a6c5d65ec`; locale isolation PASS.
- Mask/render: `protected_rois={}` because sampled frames show no Bilibili-style uploader/AI badge; the retained left-side series branding (`仙界女帝`, character name) is outside every plate. `statuses={"PASS": 35}`, max mask width 1674 px vs safe 1728 px, no full-width band, no black-only cue.
- Cue audit: **35/35** cues have a non-empty overlay, a mask covering both source box and measured English text, and no protected-ROI hit; `blocking_cues=[]`; largest inter-cue gap probe at 31.0 s has no active ASS event.
- Output: `data/m3_hongguo_job/longpilot_en-US.mp4`, SHA-256 `287bb090891c2a441045948db311819292d7036ed3f47f6802438834be28bdd4`, 29,861,535 bytes, H.264 1920×1080/30 fps + AAC 44.1 kHz stereo, duration 93.855011 s. Full decode succeeds (2812 frames, 93.855 s).
- Audio preservation: source and output AAC packet MD5 both `MD5=722e0d3294ee37942416fb8df55f0475`; equality PASS.
- Evidence: before/after frames under `data/m3_hongguo_job/evidence/{source_frames,after_frames}/` at 3, 5, 25, 43, 50, 80 and 89 s. Inspected frames show source Chinese captions fully covered, readable source-equivalent English, preserved scene imagery/branding, and a clean 5 s gap with no overlay.
- Checkpoint: finalized 2 chunks as `RENDERED_DONE`, checksum `654afc537590fe0ae5a12a17fcc99274a3d5d2fc30352755e44ab8ff03aa856a`; OCR reported container duration 93733 ms vs ffprobe 93855 ms (122 ms frame-count rounding) while the rendered/final output uses the full 93.855 s.

## 4. Negative and regression tests actually run

```powershell
python -m pytest tests/test_m3_hongguo_local.py -q          # 11 passed
python -m pytest tests/test_ingestion_gap_closure.py tests/test_m3_multisource_intake.py tests/test_e2_dynamic_sub_mask.py tests/test_experimental_cue_ocr_tracking.py tests/test_longpilot_mask_clamp.py tests/test_longpilot_translate_guards.py tests/test_longpilot_chunking.py tests/test_task38_clean_subtitle_render.py tests/test_m3_hongguo_local.py -q
# 104 passed, 1 skipped
python -m pytest -q                                         # 743 passed, 7 skipped, 3 failed
```

The 3 full-suite failures are the previously disclosed unrelated failures: `test_cp08d4_detects_punctuation_partially_under_plate_edge`, `test_full_voice_repairs_display_projection_after_narration_alignment`, and `test_adaptive_ocr_selects_changed_text_frame`. The full suite required the existing gitignored `operator/run_config.json` runtime config; it was not committed. Full suite is **not** PASS.

Observed fail-closed negatives:

- Wrong expected hash: `SOURCE_HASH_MISMATCH`, exit 1; no job/media promotion.
- Wrong episode on an existing identity: `LOCAL_INPUT_MISMATCH: episode '001' != '002'`, exit 4.
- Truncated media with still-valid container metadata: `TRUNCATED_MEDIA: decoded 5.900s of 93.855s`, exit 4; no output MP4.
- No captions (2 s black clip): OCR `cue_count=0`; adapter `NO_CAPTIONS_DETECTED`, exit 6; no render/output.
- Stale checkpoint from the real job against a different source: `CHECKPOINT_FAIL_CLOSED: CheckpointMismatch`, exit 3.
- Missing path/unsupported extension/empty file/video-without-audio: unit-level `LocalSourceError` rejections.
- Gap probe: no ASS event and no mask at the largest inter-cue gap (31.0 s); source caption strip is masked only during cues.
- Bilibili M1/M2 regression: focused ingestion, mask, OCR-tracking, chunking and translation-guard tests passed; no protected test was edited.

## 5. Verdicts and remaining work

- **`M3_HONGGUO_LOCAL_PROCESSING`:** `PROPOSED_PASS` (technical local-file evidence complete).
- **`M3_PRODUCT`:** `NOT_PASS / URL_INGEST_UNPROVEN`; a real selected-platform URL→MP4 proof is still absent.
- **TECHNICAL:** local-file path PASS; full suite not PASS (3 unrelated disclosed failures).
- **EDITORIAL_QA:** `PARTIAL`. cue_010 `傻子才等你开大` became “Only fools wait for you to speak up.”, losing `开大` (“use an ultimate/skill”); cue_006 is awkward, and cue_031 has punctuation drift. The visible Markdown artifact was fixed. No false technical PASS is claimed for editorial quality.
- **SECURITY_GATE:** `PASS` for this scoped local run: read-only original, no network download, no cookies/account/DRM bypass, no secret extraction, no upload/merge/push/deploy.
- **PUBLICATION_AUTHORIZATION:** `BLOCKED`.
- **Next evidenced blocker:** a genuine, authorized Hongguo URL→MP4 ingestion path (not local file) under the current SoT, plus an Owner editorial decision on whether the local processing quality is sufficient. Stop after M3; M4/M5 remain deferred.
