# Current bounded ship milestone — M1 / local URL-to-MP4

**Active Product SoT:** docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md. Resolve it and POLICY_REF from the active Decision Index; this file is a bounded task summary, not an independent competing product authority.

**Milestone:** one source-video URL enters a job and produces a verified, retrievable completed localized MP4 using already accepted ingest/OCR/translation/mask/render components.

**Owner-approved sequence:** M1 local URL-to-MP4 -> M2 long-form reliability -> M3 additional sources -> M4 Cloud E2E -> M5 release candidate and ship. The single authoritative definition and gate for each is in the linked Owner Decision, section "Owner-approved ship milestones". **Active milestone M1: LOCAL_FUNCTIONAL_PASS (2026-10-19), editorial PARTIAL; M2–M5: NOT_YET_PROVEN.** Do not advance a gate without real evidence. Next orchestration action: assess accepted ingest and longpilot artifacts and scope the smallest one-job Bilibili six-minute integration.

**Verified historical technical evidence:** six-minute contiguous longpilot at review/us-longpilot-20261017 / f5f8e25 reports INTEGRATED_LONGPILOT_TECHNICAL_PASS, editorial PARTIAL, security SUPERVISED_PASS, publication BLOCKED. The underlying longpilot implementation is NOT integrated into current main. Main also retains separate historical Stable V2 machine-acceptance evidence.

**Independent M1 acceptance (2026-10-19):** Local functional M1 PASS on feat/m1-local-url-to-mp4 commit f16ddc21340adeab0cea915edb1ed4cf63ea9df7, clean tree. Via MAR, Tech Lead reran the existing URL-entrypoint on committed code using verified source/checkpoints: M1_STATUS=DONE, 132/132 cue PASS, 0 blockers, implementation_dirty=false, matching code HEAD. Source SHA256 a9e638243bd648e7e87addb7aba2a33b23d4141b96e7af7be23a88a26b1630b5; final MP4 SHA256 e535b521ce286cf85dfb35ac2ed18d869f75212b964245adac1557efd4d0586b, 360s; unchanged source/output AAC MD5 1dc07eac0ffaa2ac8ebedc25c181b5f2. All mask coverage true; font/source ratios 0.949-0.973; cue_132 end 360.36s clipped to 360s video. Focused OCR and mask/input tests PASS. Three historical full-suite failures remain disclosed. Editorial PARTIAL; security SUPERVISED_PASS; publication BLOCKED. Next critical milestone M2 long-media reliability; no worker launched or code merged.

**M1 visual presentation rule (Owner revised):** Dynamic solid-black strip fits the minimal union of Chinese source and English new glyphs with tiny padding, not only old Chinese width; stays source-centred with equivalent character size, respects protected ROIs and cue times. If still unsafe, NEEDS_REVIEW. Details in current Owner Decision.

**Acceptance direction:** URL-sourced provenance, source/checkpoint integrity, cue/translation correctness, narrow subtitle mask and protected regions, preserved source audio, complete playable MP4, resumability, operator-visible artifact and operational evidence. Distinguish real Product PASS from technical, editorial, security or publication PASS.

**Historical local V2 task:** docs/research/TASK_localization_V2_historical_2026-10-18.md (not active).
