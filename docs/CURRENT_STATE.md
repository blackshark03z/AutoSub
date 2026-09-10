# Current State

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
