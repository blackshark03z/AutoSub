> CURRENT PRODUCT AUTHORITY: docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md and TASK.md. The content below concerns historical local/V2 implementation or acceptance; it is not a current ship goal.

# Project Status

## Accepted baseline

- **Declared accepted ref:** `main`.
- **Machine baseline:** resolve the current accepted SHA live with `git rev-parse main`; do not freeze a volatile HEAD in this status file.
- **Architecture authority:** [ARCHITECTURE.md](ARCHITECTURE.md).
- **Engineering process authority:** CADS, with root `TASK.md` as the active Goal / CUJ / Acceptance / Progress source of truth.

## Current product reality — Stable V2 engineering candidate

AutoSub Localization V2 is **machine-accepted on the local production route**. The normal Windows-first journey remains: configure -> Run once -> Production Monitor -> validated final video.

The public Simple UI now exposes all three accepted V2 modes:

- Subtitle Localization.
- Full Visual Localization.
- Full Localization + Voice, including run-level Kokoro voice/speed/preview before Run.

Canonical real full-golden run: `run_20260912101239568746_c5d9f22e`.

- Source: 458.3 s, 1920x1080 real acceptance fixture.
- Final media: H.264 + AAC, 458.300000 s, 266,319,210 bytes.
- Final SHA-256: `e48d29a56e1f7a8ee74c54df535baaa51d1181a7a4828d5f81ad8342d50fbc79`, matching the run manifest and visual acceptance binding.
- Subtitle quality: PASS, 291/291 dialogues, zero hard violations.
- Narration: PASS, 25/25 Kokoro units with the accepted immutable run snapshot.
- Full Visual: PASS, 1080 expected localized tracks / 1187 planned replacements / 0 unresolved / 0 final residual / 0 failures.
- Final Full Visual QC recovery used the already-rendered 458 s candidate rather than repeating accepted upstream work. First-pass residuals were 21; the one bounded mask-only repair pass reduced them 21 -> 0.
- A16 repeat-use isolation: PASS with fresh Run B `run_20260912152319264327_e6f07428`; no stale voice/output/track/job state leaked across runs and Run A artifacts remained immutable.
- Public Stable V2 exposure: PASS.
- Live product smoke: PASS (`Run AutoSub.cmd`, health endpoint, OCR readiness, Gemini configuration, Kokoro catalog, golden-run status, HTTP 206 partial playback).
- OCR progress observability: PASS; Production Monitor now shows truthful frame/batch/provider progress for bounded PaddleOCR work instead of treating healthy long OCR batches as stalled.

Machine-verifiable V2 gates applicable to the local route are therefore closed. Full repository regression and `git diff --check` PASS on the release candidate are recorded in root `TASK.md`.

## Remaining release-closing gate

The only open acceptance item is the explicitly subjective owner real-use watch/listen pass required by A17. It is limited to:

- visual flicker / background reconstruction naturalness;
- replacement/readability perception;
- narration naturalness;
- overall day-to-day usability.

Objective assist already PASSed: no freeze >=1.0 s, no black interval >=0.5 s, no observed audio clipping, and no intermittent residual-repair mask-drop candidate. These objective checks narrow the manual review but do not replace subjective judgment.

## Release identity

`AutoSub 1.10.0 Stable` / `v1.10.0` remains the **currently published canonical release identity** until the subjective V2 UAT pass is completed. Do not prematurely relabel `VERSION`, tag, or existing release bundles merely because the V2 engineering candidate is machine-accepted.

After subjective UAT PASS, close the V2 release as one bounded operation: choose/freeze the release version, update canonical release metadata and validators, rerun release gates, create the local tag/package/checksums, then push/publish through the authorized release path. Subsequent throughput work belongs to maintenance or a separately scoped V2.x improvement and must preserve the frozen quality gates.

## Accepted constraints

- Windows-first, local single-user product; local run/artifact state is authoritative.
- No full subtitle/video timeline editor and no mandatory manual edit queue on the golden normal path.
- Full Visual performs source-matched text replacement with closed-loop residual QC; it does not blindly translate every glyph/logo/identifier.
- Cloud acceleration remains optional and qualified. The no-paid-overage policy stays fail-closed; current Modal Starter evidence does not satisfy provider-side hard-budget verification.
- Do not weaken the 0.5 s authoritative visual sampling, subtitle hard limits, one-pass residual repair, immutable run snapshots, or local-authoritative policy merely to improve speed.

This file describes accepted product reality. Raw experiments and detailed chronological evidence remain in `TASK.md`, `evidence/`, and run artifacts.
