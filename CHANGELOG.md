# Changelog

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
