# WP-M3-001 — Independent Tech Lead Phase A acceptance (2026-10-20)

**Verdict:** M3_PHASE_A_DISCOVERY=VERIFIED_COMPLETE; M3_PRODUCT=NOT_YET_PROVEN; PHASE_B=OWNER_DECISION_PENDING. This is verification of the bounded discovery/test scope, not authorization to download a new platform's media or publish. After successful M3 technical acceptance, STOP per Owner's effective-publishing hold before M4/M5.

## Verified Git identity and scope

- Independent MAR read of worktree `D:/AutoSub-M3-multisource`, branch `feat/m3-multisource-intake`, clean HEAD `7a258e237812edda9395add4153c678ac69b4cb5`, from baseline `4198664c8138bf373bf17b2e1d37368639136300`. Exactly 2 added tracked files: `docs/research/WP-M3-001-result.md` and `tests/test_m3_multisource_intake.py`; no product source/allowlist/renderer changes.
- Git `AGENTS.md` blob `f5910fd8b62da3bd18e79fd0ee127cac154d5c0d` and `docs/kit/WORKER.md` blob `792b81afb67976881490e6ba1d105066d61b727f` match accepted POLICY_REF `caea6f255aa16ce36827a875bcdbca0f8b57cb1e`. Worker baseline exact checkout is verified.
- Tech Lead independently reran `python -m pytest -q tests/test_m3_multisource_intake.py tests/test_ingestion_gap_closure.py tests/test_m1_input_identity.py` at that M3 tree: exit 0, **31/31 PASS**. These are fail-closed/data-contract tests, not proof of new-platform actual ingest.

## Evidence and limitations

- **Accepted working baseline:** Bilibili M1/M2 real media+full en-US MP4 proof is verified from earlier stages, not newly rerun for Phase A.
- **Restricted negative:** Kylin/Hongguo existing handoff `APP_ONLY_RESTRICTED` / `INGESTION_BLOCKED`; 30s preview cannot count as full episode. No new live Kylin request was performed; history and contract/test evidence support this negative classification, not proof of current full-media access.
- **Candidates:** Worker reports public anonymous yt-dlp metadata probes for YouTube IDs `2TeSCz4vcSQ` and `x0nK3i6aEvA`, format counts and absent soft/auto caption tracks. No YouTube video bytes were downloaded and Tech Lead did not re-probe those URLs. `yt-dlp` metadata indicates potential downloadability, **not** tested extraction success for 0–6 min; absent subtitle tracks does not alone prove pixel-burned Chinese. Historical frames for the first candidate are untracked and were not reverified in this Phase A worker tree. Treat subtitle reality and source safety as Phase B acceptance conditions.
- **Other platforms:** Dailymotion gave no verified match in two worker-reported searches; iQiyi/Youku/WeTV/MangoTV access claims are structural notes, not independent live source-specific checks. Do not mark all those providers universally inaccessible or treat them as a positive pass.
- **Known implementation delta to fix after Owner platform selection:** `tools/run_m1_local_url_to_mp4.py` is Bilibili-only in allowlist and source id; generic canonical URL redaction would drop the YouTube `v=` video identifier. Build platform-specific canonical identity safely (preserve only validated video ID), bind source/part/interval/checkpoints to provider, and calibrate protected ROIs for actual source. No need for another ingestion framework.

## One proposed Owner decision (NOT YET APPROVED)

Approve **YouTube** as ONE additional M3 platform; primary URL `https://www.youtube.com/watch?v=2TeSCz4vcSQ`; first 0:00–6:00, <=720p, <=200 MiB media transfer, <=2 fetch attempts, <=120 min local processing, local scratch <=3 GiB; Bilibili M1/M2 regression + one genuine YouTube URL→en-US MP4 end-to-end is the smallest M3 positive coverage. Backup only if separately selected: `https://www.youtube.com/watch?v=x0nK3i6aEvA`. No user-provided credentials, model downloads, private access, DRM circumvention, media upload, remote git push or deployment. Worker must stop or return PARTIAL_DELIVERY if public anonymous 0–6 min extraction, actual Chinese burned captions, source identity or safe geometry fails. Metadata is not a substitute for frames/artifacts.

**Decision boundary:** The SoT explicitly requires Owner selection of both platform and minimum coverage. Pending that, do not start Phase B, widen any allowlist, run an external media download or mark M3 PASS. After real Phase B proof, Tech Lead and Owner review; **STOP after M3 for actual publishing-effectiveness plan and separate GO/NO-GO**, without automatically starting Cloud E2E M4 or M5.
