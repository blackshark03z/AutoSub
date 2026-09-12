# Current State

## Current V2 release candidate

AutoSub Localization V2 has reached **machine-verifiable Stable V2 engineering acceptance** on `main`, but it is not yet promoted over the currently published `1.10.0` release identity. Version/tag/package promotion is intentionally deferred until the owner completes the remaining subjective watch/listen pass.

Canonical full-golden evidence is run `run_20260912101239568746_c5d9f22e` on the real 458.3 s / 1920x1080 source. The accepted final MP4 is H.264 + AAC, exactly 458.300000 s, 266,319,210 bytes, SHA-256 `e48d29a56e1f7a8ee74c54df535baaa51d1181a7a4828d5f81ad8342d50fbc79`. Subtitle validation PASSed all 291 dialogues; narration PASSed 25/25 Kokoro units; Full Visual acceptance is 1080 expected localized tracks / 1187 planned replacements / 0 unresolved replacements / 0 residual source tracks / 0 failures after the single bounded residual-repair pass.

Repeat-use isolation A16 also PASSed with fresh Run B `run_20260912152319264327_e6f07428`: no stale voice/output/track/job state crossed from Run A and Run A artifacts remained byte-identical. Public Simple UI exposure for Subtitle Localization / Full Visual Localization / Full Localization + Voice is enabled; the live product smoke and partial-playback endpoint PASSed. Objective owner-UAT assist found no >=1.0 s freeze, no >=0.5 s black interval, no audio clipping, and no intermittent residual-repair mask-drop candidate.

The only release-closing gate still open is the explicitly subjective owner watch/listen check for visual flicker/background reconstruction, readability, narration naturalness, and overall usability. Machine PASS labels do not replace that A17 judgment. Until that pass is complete, do not change the canonical released VERSION/tag/package identity and do not mutate the accepted golden artifacts.

## Canonical release

- Name: `AutoSub 1.10.0 Stable`
- Release ID: `AUTOSUB_1_10_0_STABLE`
- Version: `1.10.0`
- Git tag: `v1.10.0`
- Canonical root: `D:\\AutoSub`
- Source bundle: `release\\AutoSub-1.10.0\\AutoSub-1.10.0-source.zip`
- Primary UI: Simple UI at `http://127.0.0.1:8173/`
- Simple UI asset: `production-monitor-v1`
- Database schema: `0009_subtitle_tracks`

The source bundle is generated from the exact tagged Product HEAD after commit closure. Its SHA-256 belongs to post-commit release evidence/`SHA256SUMS.txt`, avoiding circular source metadata.

## Accepted product contract

AutoSub is a local, single-user Windows application with two accepted Critical User Journeys.

### Speech CUJ — PASS

`run_20260909112831523056_b7d74d66`

- AutoSubs local speech recognition.
- Argos local Chinese-to-English translation.
- Completed with `result_eligible=true` and final validation `PASS`.
- Final MP4 exists and has SHA-256 `180053ba31b49eba8277b80b7c0781ca83fbdf32d1f6b08b5a261cb155e8027b`.
- Subtitle provenance: `provider_transcription`.
- ASS Dialogue count: `7`.
- Gemini calls: `0`.

### Embedded-caption OCR + Gemini CUJ — PASS

Final monitored acceptance run: `run_20260909163453377807_558c472f`.

- Local PaddleOCR + Gemini produced `292` resolved caption cues.
- Source-caption spatial lanes preserve simultaneous upper/lower captions rather than dropping the second cue.
- Render integrity is `292` resolved cues -> `292` ASS Dialogue events; caption loss is `false`.
- Gemini correction/translation model: `gemini-2.5-flash`; monitor evidence records `26` requests, `10` retries and `17` cache hits for the accepted run.
- FFmpeg render progress reached `458.233 / 458.3s` at approximately `2.78x` before validation.
- Completed with `result_eligible=true` and final validation `PASS`.
- Final MP4 exists and has SHA-256 `c1f4f0f560c02c405c4d31640ebe82e8667849f18e38660560ef7f20e68661da`.
- Subtitle provenance: `source_caption_gemini_translation`.

Renderer acceptance now distinguishes visual lanes: overlap in different spatial lanes is preserved, while overlap within the same lane remains boundary-trimmed. Pixel-coverage validation checks the actual masked interval including render padding rather than raw English cue span. A `render_failed` retry may reuse only a validated matching resolved-caption checkpoint, preserving the failed parent as immutable evidence and avoiding unnecessary OCR/Gemini repetition.

## UI / Settings

The Simple UI separates frequent creation choices from application Settings. Gemini Settings accepts 1-n API keys, one per line, appends only new unique values to ignored local `secrets\\gemini_api.txt`, reports added/duplicate/total counts, and never echoes plaintext keys through product APIs. Multi-key Gemini execution uses bounded sticky failover for credential/quota failures.

The UI follows Windows/Fluent-oriented settings hierarchy and WCAG-oriented focus, visible labels/helper text, target sizing, reflow, reduced-motion and error-feedback behavior. The primary processing view is now a Production Monitor that exposes real operation/heartbeat, OCR/provider/cache/retry evidence, a bounded live subtitle inspector, FFmpeg progress and final QC without exposing raw technical logs by default.

## Verification

- Focused OCR/render overlap regression: PASS.
- Professional UI/Settings regression: PASS.
- Normal `python -m pytest -q`: PASS on the accepted candidate before release metadata closure; release closure reruns it on the final Product HEAD.
- Canonical docs validator must PASS on final Product HEAD.
- Storage preflight measured on `D:\AutoSub` at `2026-09-10T03:41:03Z`: `12,296,216,576` bytes free.
  - `run`: threshold `1073741824` bytes — allowed; margin `11222474752` bytes.
  - `media`: threshold `2147483648` bytes — allowed; margin `10148732928` bytes.
  - `package`: threshold `4294967296` bytes — allowed; margin `8001249280` bytes.
- The old fixed 15 GiB global gate is retired.

## Stable release identity

- Canonical release: `AutoSub 1.10.0 Stable`.
- Release ID: `AUTOSUB_1_10_0_STABLE`.
- Version: `1.10.0`.
- Git tag: `v1.10.0`.
- Source bundle: `release\AutoSub-1.10.0\AutoSub-1.10.0-source.zip`.
- The source bundle and SHA-256 are generated from the exact tagged commit after Git closure; historical CP packages are not relabeled.

## Architecture / process authority

Current engineering control is CADS: `Reality -> Intent/Design -> Change -> Acceptance -> Consequence`, using native Git, tests and runtime evidence. Legacy Build OS lifecycle/control-plane files are retired from authority.

## Historical distribution provenance

CP12B Full Portable, CP13A and CP13A1 remain historical package/evidence lines only. Their external-machine beta state is not a prerequisite for this local stable release, and none of those binaries is relabeled as AutoSub 1.10.0 Stable.
