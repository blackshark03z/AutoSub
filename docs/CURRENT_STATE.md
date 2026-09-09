# Current State

## Canonical release

- Name: `AutoSub 1.9.0 Stable`
- Release ID: `AUTOSUB_1_9_0_STABLE`
- Version: `1.9.0`
- Git tag: `v1.9.0`
- Canonical root: `D:\\AutoSub`
- Source bundle: `release\\AutoSub-1.9.0\\AutoSub-1.9.0-source.zip`
- Primary UI: Simple UI at `http://127.0.0.1:8173/`
- Simple UI asset: `fluent-settings-v1`
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

Final acceptance run: `run_20260909114149833349_fcd7afff`.

- Local PaddleOCR found `2` caption intervals.
- Both intervals are preserved through render: `2` intervals -> `2` ASS Dialogue events.
- Gemini correction/translation model: `gemini-2.5-flash`.
- Completed with `result_eligible=true` and final validation `PASS`.
- Final MP4 exists and has SHA-256 `ad37deef979acd6c4f0113dceaa53bb0fb036f2d5536791d40844e265448966f`.
- Subtitle provenance: `source_caption_gemini_translation`.
- Final run used one exact Gemini provider cache hit. Live-provider evidence is preserved by earlier run `run_20260909112225050772_05bb7b7a`, which made `1` real Gemini request and exposed the renderer contract defect subsequently fixed.

The renderer fixes added during acceptance are material: source-caption render plans now expose `render_cues`, and adjacent OCR intervals that overlap slightly are boundary-trimmed rather than silently dropping a caption.

## UI / Settings

The Simple UI separates frequent creation choices from application Settings. Gemini Settings accepts 1-n API keys, one per line, appends only new unique values to ignored local `secrets\\gemini_api.txt`, reports added/duplicate/total counts, and never echoes plaintext keys through product APIs. Multi-key Gemini execution uses bounded sticky failover for credential/quota failures.

The UI follows Windows/Fluent-oriented settings hierarchy and WCAG-oriented focus, visible labels/helper text, target sizing, reflow, reduced-motion and error-feedback behavior.

## Verification

- Focused OCR/render overlap regression: PASS.
- Professional UI/Settings regression: PASS.
- Normal `python -m pytest -q`: PASS on the accepted candidate before release metadata closure; release closure reruns it on the final Product HEAD.
- Canonical docs validator must PASS on final Product HEAD.
- Storage preflight measured on `D:\AutoSub`: `10,379,632,640` bytes free.
  - `run`: threshold `1073741824` bytes — allowed.
  - `media`: threshold `2147483648` bytes — allowed.
  - `package`: threshold `4294967296` bytes — allowed.
- The old fixed 15 GiB global gate is retired.

## Stable release identity

- Canonical release: `AutoSub 1.9.0 Stable`.
- Release ID: `AUTOSUB_1_9_0_STABLE`.
- Version: `1.9.0`.
- Git tag: `v1.9.0`.
- Source bundle: `release\AutoSub-1.9.0\AutoSub-1.9.0-source.zip`.
- The source bundle and SHA-256 are generated from the exact tagged commit after Git closure; historical CP packages are not relabeled.

## Architecture / process authority

Current engineering control is CADS: `Reality -> Intent/Design -> Change -> Acceptance -> Consequence`, using native Git, tests and runtime evidence. Legacy Build OS lifecycle/control-plane files are retired from authority.

## Historical distribution provenance

CP12B Full Portable, CP13A and CP13A1 remain historical package/evidence lines only. Their external-machine beta state is not a prerequisite for this local stable release, and none of those binaries is relabeled as AutoSub 1.9.0 Stable.
