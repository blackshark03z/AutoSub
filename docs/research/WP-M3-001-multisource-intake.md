# WP-M3-001 — Multi-source intake, platform selection and truthful local proof

**Status:** M3 Phase A discovery VERIFIED 2026-10-20; **PLATFORM_SELECTION_PENDING / M3_NOT_YET_PROVEN**. M3 is the last milestone permitted before the Owner-directed **STOP after M3**. No M4/M5 Cloud, upload, publisher account action or deploy.

## 1. Immutable worker context

- Local repository `D:\AutoSub`; worker checkout **only** `D:\AutoSub-M3-multisource` on `feat/m3-multisource-intake`. The relay states exact checked-out HEAD. Preflight must match path, branch, HEAD, clean status or STOP.
- Accepted `POLICY_REF=caea6f255aa16ce36827a875bcdbca0f8b57cb1e`; verify Git blobs `AGENTS.md` and `docs/kit/WORKER.md` match that ref and read both. Product SoT `docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md`, `TASK.md`; accepted M2 code candidate `5ce4c84ee7e9cb1806f00374b314885a505ef94d`, latest M2 worktree HEAD `7d4beaf569ab103cdb14d413996529baf3d6cfce`. M3 is branched from clean M2, not a merge of main or historical pilot trees.
- Reuse proven M2 Bilibili URL→1800s en-US MP4, 713/713 layout PASS, offline HY-MT2, ASS vector plate, original audio, protected attribution, 30-chunk checkpoint and input identity. Existing `app/services/ingestion_contract.py`, `tools/run_m1_local_url_to_mp4.py`, `tests/test_ingestion_gap_closure.py` are the first evidence. Bilibili remains baseline, not a second platform.

## 2. Critical source distinction and Owner selection gate

- Product SoT M3: expand same URL→media intake contract to **Owner-selected additional platform(s)**; require actual full selected episode/video or explicitly approved bounded real segment to finished local MP4, not metadata, teaser, other adaptation or app-only video. **Platform list and minimum coverage require separate Owner confirmation before M3 acceptance.**
- Historical Owner-supplied Kylin/Hongguo URL: `https://kylin.hainanyuyue.com/s/i9J6kMK62PE/`. Repo's `create_kylin_ingestion_handoff` currently marks `APP_ONLY_RESTRICTED` / `INGESTION_BLOCKED` with web preview only. Treat this as a *negative* test; do not claim full access from a 30s teaser or bypass mobile app, authentication, paywall/DRM.
- M1/M2 entrypoint allowlist is Bilibili-specific. **Never merely add another host string to that allowlist.** Use source platform identity, selected episode/part, real media availability, redacted URL, SHA256, media probe and isolated job/checkpoint keys. Explicitly reject wrong/changed redirected host, metadata-only, app-only, invalid/partial download and unsupported extractor, without creating fake MP4.

## 3. Phase A — AUTHORIZED NOW: verify candidates and propose coverage

1. Verify worktree/policy and existing intake implementation/tests. Inventory Bilibili working path and Kylin blocked path; distinguish historical hardcoded fixture classification from any actual new anonymous metadata check. Keep probes read-only and bounded; no login/cookies or downloading Kylin episodes.
2. Identify **1–3 concrete candidate alternative platforms/providers** which could serve an authorized publicly accessible Chinese hard-subtitle video. Report specific verified URL and selected episode only when actually grounded; identify what is real full-media access vs uncertain teaser/metadata, local extractor support and probable risks. Do not fabricate public links or claim availability from a title/search snippet.
3. Recommend the **smallest M3 positive coverage** (Bilibili regression + at least one distinct newly selected platform with an actual bounded media item); provide source identity, expected download/processing limits and evidence to Owner for **separate platform and minimum-coverage confirmation**. If none demonstrably viable, report a blocker and ask Owner for an authorized source URL. A second Bilibili link alone does not count.
4. Run focused/negative tests for existing Kylin `APP_ONLY_RESTRICTED`, invalid URL/host, teaser-versus-full confusion, redirect/redaction/part identity, no fake artifacts and resumability boundaries. Only change already existing intake contract/validation code if a concrete defect is proven; no new generic framework/queue or speculative platform adapter before Owner choice.
5. Write `docs/research/WP-M3-001-result.md`: exact worktree/HEAD/diff, platform classification matrix, actual evidence and limitations, new tests, Product Delta, single most important blocker, 1–3 grounded alternatives and a concise **Owner platform/coverage decision**. Commit only scoped verified changes locally. **Stop Phase A here; M3_PRODUCT=NOT_YET_PROVEN or PARTIAL_DISCOVERY, not PASS.**

## 4. Phase B — AFTER OWNER EXPLICITLY SELECTS PLATFORM/COVERAGE

Only after Owner confirms which additional supported platform(s) and minimum acceptable proof, the orchestrator pins that choice in active Product SoT/WP and relays bounded Phase B. Then reuse the existing source-bound one-job OCR→offline en-US→adaptive union black ASS plate→MP4 workflow with minimal platform-specific resolver/downloader changes. Required oracles:

- **A1**: real new-platform URL resolves to the *intended* full selected video/episode (or Owner-approved segment), not teaser/metadata, public authorized access, stable platform/id/part/interval identity, safe redirect/host and sanitized logs, bounded download, ffprobe + media SHA256.
- **A2**: source-hash-bound chunk/cue inventory, complete actual Chinese subtitle OCR, boundaries and no duplicate/missing cues.
- **A3**: en-US translation faithful to per-cue Chinese and locale-isolated caches, truthful editorial QA, no fabricated language output or false PASS.
- **A4**: Owner-approved minimal dynamic solid-black union mask, source-equivalent glyph size, no black-only cue or protected logo/AI badge overlap, original audio packet MD5 identical, playable local MP4 with real duration and SHA256.
- **A5**: source/part/host/interval mismatch and corrupt checkpoint fail closed, and valid checkpoint reuse/resume without silently attaching Bilibili media to a new provider.
- **A6**: negative matrix for Kylin app-only, preview-only, unsupported/redirected host, bad media, duplicate part selection and unavailable full video yields actionable statuses, never a fake finished MP4.
- **A7**: focused/protected/full tests, Bilibili M1/M2 regression, one real clean-committed-code new-platform URL→MP4 run, actual evidence frames/manifest/artifact hash and independent Tech Lead verification. **M3 Product PASS only for Owner-confirmed platform coverage and fully verified local artifact**, not discovery/tests alone.

## 5. Strict limits and stop rules

- **Phase A**: <=90 minutes local work; public anonymous *metadata-only* checks <=2 requests/host where needed; scratch <=500MiB; no media downloads, new models, private links, cookies, external uploads, paid APIs, public publish, git push, merge or deploy.
- Proposed **Phase B bounds** (require Owner choice first): <=6 min one verified real segment/provider unless Owner chooses otherwise; <=200 MiB fetch, <=3 GiB new job scratch, <=120 min processing, <=2 download attempts; offline OCR/HY-MT2 only. Stop and report PARTIAL if a target is unavailable under caps; don't substitute preview, another title or access bypass.
- Stop on wrong worktree/HEAD/policy, changed source or lack of verified Owner-selected new provider, unauthorized access/expense, security concerns, missing reliable artifact or two consecutive cycles without Product Delta. Do not reset/overwrite unrelated trees.
- **STOP AFTER M3** even if PASS: produce a bounded publishing-effectiveness decision dossier (actual authorized publishing pathway, output quality/editorial gaps, proposed small real publish pilot, possible observed reach/engagement/retention metrics, cost and requested Owner GO/NO-GO). No invented KPIs or audience figures. M4/M5 deferred.

This WP authorizes M3 Phase A discovery only at this point; not platform-specific full-media retrieval or final M3 acceptance without the separately required Owner platform selection.
