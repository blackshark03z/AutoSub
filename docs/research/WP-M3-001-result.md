# WP-M3-001 result — Phase A multi-source discovery

**Status:** `PHASE_A_COMPLETE / PARTIAL_DISCOVERY`; `M3_PRODUCT=NOT_YET_PROVEN`; `PLATFORM_SELECTION_PENDING`.
**Phase B:** NOT_STARTED (requires explicit Owner platform/coverage selection).
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
