# AutoSub

AutoSub is a local, single-user Windows application that turns local video into a validated English-subtitled MP4.

## AutoSub 1.9.0 Stable

Release ID: `AUTOSUB_1_9_0_STABLE`
Git tag: `v1.9.0`

The canonical daily-use installation is `D:\\AutoSub`. Historical CP12B/CP13A/CP13A1 packages are provenance only and are not relabeled as this release.

## Normal use

Double-click `Run AutoSub.cmd`. The Simple UI opens at `http://127.0.0.1:8173/`; normal use does not require a terminal.

Two accepted subtitle workflows are available:

- **Speech:** AutoSubs local transcription -> Argos local Chinese-to-English translation -> subtitle render -> validated MP4.
- **Embedded captions:** PaddleOCR local reading -> Gemini correction/translation -> subtitle render -> validated MP4.

The create-video screen contains frequent per-run choices. Infrequent provider configuration lives under **Cài đặt**. Gemini Settings accepts 1-n keys, one per line, appends only new unique keys to ignored local `secrets\\gemini_api.txt`, preserves existing keys, and exposes only sanitized counts/status.

## Acceptance

AutoSub 1.9.0 Stable is accepted from real product journeys, not inferred from isolated tests:

- Speech CUJ: `run_20260909112831523056_b7d74d66` — completed, result eligible, validation PASS, 7 ASS Dialogue events.
- OCR+Gemini CUJ: `run_20260909114149833349_fcd7afff` — completed, result eligible, validation PASS, 2 OCR intervals -> 2 ASS Dialogue events.
- Live Gemini provider evidence: `run_20260909112225050772_05bb7b7a` made a real Gemini request; the final OCR acceptance reused the exact provider cache entry.
- Normal full regression: PASS.
- Storage preflight: run PASS; package PASS.

The source release bundle is generated after commit closure from the exact `v1.9.0` tagged commit at `release\\AutoSub-1.9.0\\AutoSub-1.9.0-source.zip`; its SHA-256 is recorded in release evidence and `SHA256SUMS.txt`.

## Engineering authority

AutoSub uses CADS as an engineering control model with native Git/tests/runtime evidence. The former Build OS lifecycle/control-plane is retired and is not an authority for normal work.

Living documents:

- `TASK.md`
- `AGENTS.md`
- `ARCHITECTURE.md`
- `docs/CURRENT_STATE.md`
- `docs/OPERATIONS.md`
- `docs/DECISIONS/`
- `CHANGELOG.md`
