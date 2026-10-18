# Current bounded ship milestone — M1 / local URL-to-MP4

**Active Product SoT:** docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md. Resolve it and POLICY_REF from the active Decision Index; this file is a bounded task summary, not an independent competing product authority.

**Milestone:** one source-video URL enters a job and produces a verified, retrievable completed localized MP4 using already accepted ingest/OCR/translation/mask/render components.

**Owner-approved sequence:** M1 local URL-to-MP4 -> M2 long-form reliability -> M3 additional sources -> M4 Cloud E2E -> M5 release candidate and ship. The single authoritative definition and gate for each is in the linked Owner Decision, section "Owner-approved ship milestones". **Active milestone M1: NOT_YET_PROVEN; M2–M5: NOT_YET_PROVEN.** Do not advance a gate without real evidence. Next orchestration action: assess accepted ingest and longpilot artifacts and scope the smallest one-job Bilibili six-minute integration.

**Verified historical technical evidence:** six-minute contiguous longpilot at review/us-longpilot-20261017 / f5f8e25 reports INTEGRATED_LONGPILOT_TECHNICAL_PASS, editorial PARTIAL, security SUPERVISED_PASS, publication BLOCKED. The underlying longpilot implementation is NOT integrated into current main. Main also retains separate historical Stable V2 machine-acceptance evidence.

**Latest M1 Product Delta (2026-10-18):** Worker M1 clean 6fddc4a corrected false PASS, source identity and redaction. Real 6-minute URL job at d7ac802 correctly reported 70 PASS/67 NEEDS_REVIEW under the former source-width-only fit criterion. Owner now accepts the smallest safe black mask covering the union of source Chinese AND translated English glyph bounds at equivalent apparent size. **M1 NOT_PRODUCT_PASS** until revised A3 and A1–A7 are demonstrated on a clean committed run. Continue only existing WP-M1-001; no M2/merge/push/deploy.

**M1 visual presentation rule (Owner revised):** Dynamic solid-black strip fits the minimal union of Chinese source and English new glyphs with tiny padding, not only old Chinese width; stays source-centred with equivalent character size, respects protected ROIs and cue times. If still unsafe, NEEDS_REVIEW. Details in current Owner Decision.

**Acceptance direction:** URL-sourced provenance, source/checkpoint integrity, cue/translation correctness, narrow subtitle mask and protected regions, preserved source audio, complete playable MP4, resumability, operator-visible artifact and operational evidence. Distinguish real Product PASS from technical, editorial, security or publication PASS.

**Historical local V2 task:** docs/research/TASK_localization_V2_historical_2026-10-18.md (not active).
