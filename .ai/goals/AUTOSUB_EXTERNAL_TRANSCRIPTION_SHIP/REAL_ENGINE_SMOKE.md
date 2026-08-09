# AutoSubs real-engine acceptance

Recorded: 2026-08-09

## Provenance

- Engine: AutoSubs Windows x64 `v3.8.0`, `addons/autosubs/autosubs.exe`.
- Engine SHA-256: `7836B94D14A68D50320C9D21F28CF87D85DD5C22E011F75BFB7EBCB08F4EF52D`.
- Model: cached Whisper GGML multilingual `small`, SHA-256
  `1BE3A9B2063867B937E64E2EC7483364A79917E157FA98C5D94B5C1FFFEA987B`.
- Canonical retained media SHA-256:
  `67E710166D98C732D6ECADB66C71FA86E376B5971D1D96D713788723094A97DA`.
- The real provider command is
  `autosubs.exe <extracted-wav> --model small --lang zh --no-gpu --format json`.
  It uses `task=transcribe`; it does not request translation, diarization, or
  forced alignment.

## Result

- The full retained-media app path ran the engine for `572.055` seconds and
  returned from provider transcription; the ad-hoc harness then failed only
  because it had not created the database workflow run needed to persist a
  track.
- A fresh 260--280 second clip extracted from the same retained media had SHA-256
  `50A863412C3F3BB852BB4C744CA511CC3C2E5FE51CDFB51758F0B1DE1B19DC5B`.
- The standard temporary-DB workflow run completed `external_asr.json` with
  `status=PASS`, AutoSubs `3.8.0`, model `small`, task `transcribe`, detected
  and subtitle language `zh`, requested target `en`, seven timestamped cues,
  zero fallback attempts, `engine_translation=false`, and
  `forced_alignment=false`.
- Processing time for the app-bound clip was `22.236` seconds. The raw provider
  cue texts exactly equaled resolved source-track texts. The active track was
  `source`; all seven resolved translation texts were empty.

The fixture has no authoritative reference transcript. This is a product smoke
test and makes no CER/WER or recognition-quality claim.
