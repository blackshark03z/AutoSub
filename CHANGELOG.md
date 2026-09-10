# Changelog

## AutoSub 1.10.0 Stable — 2026-09-10

- Replaced the opaque long-running processing card with a truthful Production Monitor using existing run evidence rather than synthetic telemetry.
- Added live OCR/frame/provider/cache/retry/subtitle-inspector evidence and persisted FFmpeg render progress with final QC counts.
- Preserved simultaneous source captions in distinct visual lanes while retaining overlap trimming within the same lane; real OCR acceptance renders 292/292 cues with zero caption loss.
- Corrected pixel-coverage validation to measure the actual source-caption mask window including render padding.
- Added durable render-only retry: a child of a `render_failed` run reuses a validated matching resolved-caption checkpoint and does not repeat OCR/Gemini or speech transcription.
- Added safe result-state reconciliation for previously downgraded `invalid_completed_result` rows when current output validation and stored output hash both prove the artifact valid again.
- Real monitored OCR+Gemini CUJ PASS: `run_20260909163453377807_558c472f`, 292 resolved cues -> 292 ASS Dialogue events, final validation PASS.
- Real local speech CUJ remains PASS: `run_20260909112831523056_b7d74d66`, 7/7 ASS Dialogue events, Gemini calls 0.
- Full regression and run/media/package storage gates PASS before release metadata closure.

## AutoSub 1.9.0 Stable — 2026-09-09

- Promoted two truthful production journeys: local AutoSubs+Argos speech mode and PaddleOCR+Gemini embedded-caption mode.
- Added mode-aware readiness and fail-fast OCR/Gemini preflight before expensive frame analysis.
- Added professional Simple UI redesign with a dedicated Settings view, Windows/Fluent-oriented hierarchy and WCAG-oriented interaction behavior.
- Added 1-n Gemini key management: append/deduplicate local ignored file storage, sanitized counts, and bounded sticky failover across keys on auth/quota failures.
- Completed canonical migration to `D:\\AutoSub` and retired active Build OS authority in favor of CADS + native Git/test/runtime evidence.
- Fixed OCR render-plan contract mismatch (`render_cues`) found by real E2E acceptance.
- Fixed adjacent OCR interval overlap so valid captions are boundary-trimmed rather than silently dropped during ASS normalization.
- Real speech CUJ PASS: `run_20260909112831523056_b7d74d66`, 7 ASS Dialogue events.
- Real OCR+Gemini CUJ PASS: `run_20260909114149833349_fcd7afff`, 2 OCR intervals -> 2 ASS Dialogue events.
- Live Gemini call evidence preserved from `run_20260909112225050772_05bb7b7a`; accepted retry used the exact cache entry.

## Historical release notes

- CP12B Full Portable was a historical portable release baseline.
- CP13A/CP13A1 were historical one-click beta candidates and are not relabeled as 1.9.0.
- CP12A added Creative Subtitle Script Import with Translation, Creative, and Imported tracks.
- CP11D established the earlier unified Full Portable distribution with bundled OCR.
- CP10B introduced the Simple end-to-end workflow UI as the primary user path.
