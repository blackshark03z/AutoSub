# Current bounded ship milestone — M1 / local URL-to-MP4

**Active Product SoT:** docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md. Resolve it and POLICY_REF from the active Decision Index; this file is a bounded task summary, not an independent competing product authority.

**Milestone:** one source-video URL enters a job and produces a verified, retrievable completed localized MP4 using already accepted ingest/OCR/translation/mask/render components.

**Owner-approved sequence:** M1 local URL-to-MP4 -> M2 long-form reliability -> M3 additional sources -> M4 Cloud E2E -> M5 release candidate and ship. The single authoritative definition and gate for each is in the linked Owner Decision, section "Owner-approved ship milestones". **Active milestone M1: NOT_YET_PROVEN; M2–M5: NOT_YET_PROVEN.** Do not advance a gate without real evidence. Next orchestration action: assess accepted ingest and longpilot artifacts and scope the smallest one-job Bilibili six-minute integration.

**Verified historical technical evidence:** six-minute contiguous longpilot at review/us-longpilot-20261017 / f5f8e25 reports INTEGRATED_LONGPILOT_TECHNICAL_PASS, editorial PARTIAL, security SUPERVISED_PASS, publication BLOCKED. The underlying longpilot implementation is NOT integrated into current main. Main also retains separate historical Stable V2 machine-acceptance evidence.

**Latest independent M1 review (2026-10-18):** Worker commit 7f8730d produced a real local URL -> 360s en-US MP4, with actual matching source/output hashes and packet-identical audio. **Product Delta proven, but M1=PARTIAL_DELIVERY / NOT_PRODUCT_PASS.** The highest blocker is false-PASS subtitle layout / non-equivalent font size on material cues; next is source URL/part/section resume identity and sanitized commit-bound provenance. **Exact evidence + bounded closure scope:** docs/research/WP-M1-001-techlead-review.md. Three full-suite failures remain reported, not independently baseline-proven. No M2 start until M1 acceptance.

**M1 visual presentation rule:** Owner reconfirmed a tight solid-black mask *only* over the original Chinese subtitle, with en-US text placed at the same location and at equivalent apparent source font size. Do not use a full-width plate, inpainting or silent font shrinking; mismatched/overflowed cues must fail into evidence-bearing review. Normative details are in the linked Owner Decision. Existing ~88% size fallback must be checked before PASS.

**Acceptance direction:** URL-sourced provenance, source/checkpoint integrity, cue/translation correctness, narrow subtitle mask and protected regions, preserved source audio, complete playable MP4, resumability, operator-visible artifact and operational evidence. Distinguish real Product PASS from technical, editorial, security or publication PASS.

**Historical local V2 task:** docs/research/TASK_localization_V2_historical_2026-10-18.md (not active).
