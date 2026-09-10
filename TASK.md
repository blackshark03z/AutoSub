# Goal

Release the next stable AutoSub version from baseline `v1.9.0` / `ac271c1144ac1c0bd8e033e0cdf628f2f2bc49f8` with a truthful **Production Monitor** for long-running subtitle work.

The product remains one-click automation: **Setup -> Production Monitor -> Result/QC**. AutoSub must not become a full subtitle editor. The monitor exists so the owner can answer, without opening raw logs: what is running now, whether it is making material progress, what subtitle artifacts have been found/generated, whether Gemini/cache/retries are involved, whether rendering is advancing, and whether final subtitle integrity checks passed.

# Critical User Journeys

## CUJ-A — OCR + Gemini monitored production

1. Select an OCR + Gemini source and press Run once.
2. Production Monitor stays visible for the whole run and shows the truthful current operation, elapsed time, mode/provider identity, worker heartbeat/activity and meaningful counters.
3. During image analysis, the monitor exposes real sampled-frame / crop / OCR-batch progress when totals are known. It never invents a total percentage from unrelated stages.
4. Once caption intervals exist, the monitor exposes a bounded live subtitle inspector with time range, source OCR text, translated text when available, OCR confidence and attention state. Raw provider payloads and secrets remain hidden.
5. During Gemini resolution, the monitor surfaces bounded provider evidence: request/retry/cache counters and the selected model when known. Exact cached work is labelled as cache reuse rather than a new provider call.
6. During rendering, the monitor shows real processed media time / source duration and speed when FFmpeg reports it.
7. Result/QC shows integrity evidence such as detected/generated caption count, ASS Dialogue count, output validation and whether caption loss/overlap checks passed.

## CUJ-B — Local audio monitored production

1. Select local speech mode and press Run once.
2. Production Monitor truthfully shows runtime readiness, recognition/subtitle creation/render stages, elapsed activity and subtitle artifacts as soon as the canonical track exists.
3. Gemini-specific metrics remain absent or explicitly not applicable; the monitor never implies an external call for the local path.
4. Render and final QC evidence use the same monitor/result language as OCR mode.

# Acceptance

- **A1 — One-click preserved:** no mandatory review/edit step is inserted between Run and final output. The monitor is observational; technical logs remain advanced disclosure.
- **A2 — Evidence-bearing status:** long-running processing visibly exposes current operation, elapsed duration, last material progress/heartbeat and stalled/abnormal warning when appropriate. A generic busy message alone is insufficient.
- **A3 — Determinate only when truthful:** counters/percentages are shown only when numerator and denominator come from runtime evidence. Unknown total work remains stage/indeterminate rather than fake precision.
- **A4 — OCR live metrics:** OCR mode surfaces real frame/crop/OCR-batch counts from `analysis_progress`, caption interval count, Gemini request/retry/cache evidence, and selected model when available.
- **A5 — Live subtitle inspector:** the monitor displays a bounded, scrollable list of current caption artifacts with timing, source text, translated/resolved text when available, OCR confidence and warnings/attention state. The list is informational only and never exposes secrets or raw provider response bodies.
- **A6 — Render progress:** FFmpeg render progress is persisted/read through the existing run directory and surfaced with processed media time, duration and speed. Render progress never regresses and reaches the source duration before successful completion within normal FFmpeg rounding tolerance.
- **A7 — Result/QC evidence:** completion view states caption input/resolved/rendered counts, ASS Dialogue count, result validation and output eligibility. Caption-count mismatch or invalid render remains fail-closed.
- **A8 — Stalled truth:** the UI can distinguish active heartbeat/material progress from an apparently stalled worker and does not report a failed/stalled process as healthy progress.
- **A9 — Accessibility/responsive:** Production Monitor preserves WCAG-oriented focus visibility, keyboard access, semantic live status, reduced-motion behavior and usable desktop/small-width reflow. Desktop uses available horizontal space instead of a large empty single card.
- **A10 — Regression:** focused monitor tests, existing OCR/audio tests, full `python -m pytest -q`, canonical-doc validation and storage preflight pass from the candidate Product HEAD.
- **A11 — Real E2E:** one real OCR+Gemini fixture and one real local-audio fixture complete through the monitor with truthful evidence and validated MP4 outputs.
- **A12 — Stable release:** accepted work is committed/pushed from one clean canonical `main`, versioned as the next feature release after 1.9.0, tagged, and accompanied by exact-tag source/checksum evidence. `v1.9.0` remains immutable historical baseline.

# Constraints

- Windows-first, local single-user product.
- No new observability subsystem, queue, editor timeline engine or Build OS lifecycle.
- Reuse existing `analysis_progress`, subtitle track resolver, provider metadata, run validation and run-directory evidence files.
- Add only the minimal persisted render/monitor evidence needed to make the current run observable.
- Secrets never enter progress files, UI payloads, logs, Git or release archives.
- Prefer `REUSE -> WIRE -> FIX -> ADD`; no speculative metrics or fake percentage.
- CADS core remains unchanged unless the same failure class is independently observed in another project.

# Baseline / Findings

- Baseline release: `v1.9.0`, commit `ac271c1144ac1c0bd8e033e0cdf628f2f2bc49f8`.
- Current Simple UI processing view is a centered stage card with raw JSON hidden under details; it does not expose enough evidence for the owner to judge correctness/progress.
- Existing backend already exposes `analysis_progress`, subtitle tracks, provider usage, timestamps and final validation. `resolved_cues` exposes timing/source/translation/OCR confidence once a track exists.
- OCR analysis already persists frame/crop/OCR-batch heartbeat and stage history; these should be surfaced rather than duplicated.
- FFmpeg render currently runs as one opaque `subprocess.run`; bounded render progress persistence is the main backend addition required.

# Release Closure

Do not open additional feature scope after the monitor acceptance criteria pass. Final closure is: focused acceptance -> real OCR/audio journeys -> full regression/docs/storage gates -> version/docs identity -> commit/push/tag -> exact-tag source bundle/checksum -> runtime smoke from tagged HEAD.
