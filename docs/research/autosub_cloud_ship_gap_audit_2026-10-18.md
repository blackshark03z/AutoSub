# AutoSub Cloud — evidence-led ship gap audit (2026-10-18)

**Basis and confidence:** MAR current worktree reads, identified commit/report. Distinguishes observed files from Owner-supplied worker acceptance. No Cloud deploy or browser-to-completed-job E2E has been evidenced by this audit.

## Current identities
- D:\AutoSub (MAR root): feat/pilot-e2e-local-sub-overlay HEAD 0d0c28a, **extensively dirty** (276 status entries before this doc update). Owner product decision in docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md is uncommitted here; do not call it GitHub main.
- D:\AutoSub-acceptance-longpilot: review/us-longpilot-20261017 HEAD f5f8e25, clean at audit, real 6-min source technical PASS.
- D:\AutoSub-live-verification: feat/live-source-verification HEAD 364c578, clean; docs/cloud/discovery_integration_plan.md is a **blueprint for metadata discovery** (not downloaded media or an integrated Cloud ship).
- GitHub public blackshark03z/AutoSub main: as checked, AGENTS.md still refers to prior standard, TASK.md to the historical local Product Monitor goal, and the Oct 18 owner decision is not on main.

## Proven sub-results / limits
- Longpilot source BV1bPeu6cEkG P1, 0–6 minutes, SHA256 c30b87bb57e6c256be839944d669ead732e415403eb9b022c201175d343058d1; 6 OCR chunks / 137 master cues; resume and mismatch fail-closed; offline en-US+vi-VN 137/137; masks clamped clear of AI badge; both MP4 renders keep original audio packet MD5. Report docs/research/opencode_us_longpilot_5_10min_acceptance_2026-10-17.md at HEAD f5f8e25. Reported pytest 560 passed / 8 skipped / 0 failures. This audit did not rerun tests. Editorial cue_012 unresolved, SECURITY_GATE=SUPERVISED_PASS, PUBLICATION_AUTHORIZATION=BLOCKED.
- The historical 1.10.0 Windows one-click OCR+Gemini and AutoSubs/Argos workflows are separate accepted local Product CUJs. They should be reused where compatible, not rebuilt from scratch.
- The Cloud source-discovery blueprint is metadata-only and explicitly labels download as future. An existing source URL → actual media ingest → OCR/translation/render → downloadable Cloud output on one job identity **has not been proven**.

## Next product milestone recommendation (not a new approval to deploy)
**Cloud E2E vertical slice on one authorized 6-minute source link, reusing the accepted longpilot media pipeline**, with a minimal entry point (API or one headless cloud-runner job) and a persisted job manifest. It must receive a source URL, derive/download source through an authorized supported path, invoke existing offline OCR→E1→HY-MT2 en-US→black-mask/FFmpeg, and expose one final playable MP4 and subtitles via a retrievable artifact reference. If a working API/UI already exists, wire that rather than building a parallel queue/platform.

**Acceptance:** 1) single job input is URL not a pre-downloaded local fixture, 2) provenance + sanitized URL/source hash bound through checkpoint, 3) OCR and translation count/cue identities intact, 4) real finished 6-minute en-US MP4 playable; source audio identity and logo/AI badge protected, 5) failure/retry resumes without redundant OCR or leaking tokens/signed URL, 6) operator has real job state and artifact access, 7) focused tests plus exact-candidate protected/full gates, 8) technical vs product vs editorial/security/publication results explicitly separated.

**Non-goals:** unrelated whole-frame inpainting, voice dubbing, new editors, a second SoT, speculative provider choice, new message queue before evidence, public video publication, or claiming the pilot proves a complete multi-hour film. After vertical slice passes, extend source-length/budget reliability on a representative full film and separately qualify deployment/operations.

## Current gates
| Gate | Observed | Next proof |
|---|---|---|
| Real 6-minute pipeline | Technical PASS (worker report) | Product run from URL cloud job |
| Actual source download from URL | Only source probe blueprint + pilot manual source retrieval | Automatic job ingest and provenance |
| Job orchestration / artifacts | Not proven integrated | One persisted cloud job manifest and retrievable MP4 |
| Multi-hour source | Not proven | Long-real-media bounded run and resume |
| Editorial QA | PARTIAL cue_012 | Human source review / explicit handling |
| Unattended security | SUPERVISED_PASS | Verified watchdog/deny, security review |
| Publication | BLOCKED | Separate Owner authorization |

This audit is a blocker map, not a license to start a worker or deploy cloud infrastructure.
