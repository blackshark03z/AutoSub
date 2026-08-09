# Real AutoSubs engine smoke evidence

Recorded: 2026-08-09

## Engine and model provenance

- Engine: AutoSubs Windows x64 `v3.8.0`.
- Engine path: `C:\ToolAutoSub\AutoSub\addons\autosubs\autosubs.exe`.
- Engine SHA-256: `7836B94D14A68D50320C9D21F28CF87D85DD5C22E011F75BFB7EBCB08F4EF52D`.
- Engine command: `<autosubs.exe> <clip> --model small --lang zh --no-gpu --format json`.
- Version command: `<autosubs.exe> --version` -> exit `0`, `autosubs 3.8.0`.
- Model: Whisper GGML multilingual `small`, cached by AutoSubs at
  `C:\Users\ADMIN\AppData\Local\com.autosubs\models\models--ggerganov--whisper.cpp\snapshots\5359861c739e955e79d9a303bcbc70fb988958b1\ggml-small.bin`.
- Model size / SHA-256: `487601967` bytes (465.01 MiB) /
  `1BE3A9B2063867B937E64E2EC7483364A79917E157FA98C5D94B5C1FFFEA987B`.
- No `--translate`, `--target-language`, `--diarize`, or `--forced-alignment`
  flag was supplied.

The engine's `--list-models` command returns supported model names, including
`small`; it is not treated as cryptographic cache evidence. The successful
real invocation and the cached GGML file above provide the runtime evidence.

## Input and external-engine result

- Retained media: `C:\Users\ADMIN\Downloads\SaveTik.io_7645708844944903475_hd.mp4`.
- Retained media SHA-256 / duration: `67E710166D98C732D6ECADB66C71FA86E376B5971D1D96D713788723094A97DA` /
  `366.270998` seconds.
- Smoke interval: source `260.000--280.000` seconds; extracted clip duration
  `20.066992` seconds; clip SHA-256
  `9A9E1ED08513EAE4C40785AB20007280757B718BF545366185AB482DBDEB40B8`.
- Direct external command exit: `0`; detected language: `zh`; engine-reported
  processing time: `35` seconds; seven timestamped segments.

The source transcript is preserved below as Unicode escapes:

1. `\u600e\u4e48\u5440\u6765\u6211\u518d\u5b66\u4e60\u5462`
2. `\u7b2c\u4e00\u5f97\u4e00,\u4e00\u4e8c\u5f97\u4e8c,`
3. `\u4e09\u516b\u5987\u5973\u59d0,\u4e94\u4e00,\u52b3\u52a8\u59d0\u516d`
4. `\u4e00,\u513f\u7ae5\u59d0\u4e0d\u6211\u5728\u5b66\u5462,\u6211\u5728\u5b66,`
5. `\u6211\u5728\u5b66\u56de\u53bb\u770b\u7535\u89c6\u5427,`
6. `\u96f7\u9706\u59d0\u4f60\u8d76\u7d27\u53bb\u5427\u8fd8\u5e26\u4e86\u4e2a\u5c0f\u9e7f\u811a,`
7. `\u73a9\u5f97\u633a\u597d\u54c8\u54ce\u5440,\u5e78\u4e8f\u6211\u5728\u5b66`

## AutoSub boundary result

An isolated AutoSub run used the same clip, `target_language=English`, and
`source_language=auto`:

- `ensure_external_transcription_track`: `PASS`.
- Provider metadata: AutoSubs `3.8.0`, `small`, `task=transcribe`, detected
  source and subtitle language `zh`, `fallback_attempts=0`, engine translation
  `false`, forced alignment `false`.
- Extracted audio SHA-256:
  `6dc1ea1dfb07113a14a67902fb9043939b7cc213db6d292e9ee1783e6399ff8d`.
- AutoSub external-provider processing time: `62.539` seconds.
- Raw provider cue texts equal resolved source cue texts: `true`.
- Resolved track type: `source`; every resolved translation text was empty.

This is a product smoke test only. The retained media has no authoritative
source-language reference transcript, so no CER/WER or recognition-quality
claim is made.

## Frozen acceptance and environment note

The five frozen Goal commands passed. The final 35-test regression bundle
initially failed only because caching the 465 MiB model left C: with 1.615 GiB
free, below that legacy test's 2 GiB media-preflight threshold. The exact model
file was moved to validated `D:\ToolAutoSubRuntimeStaging` for the test and
restored afterward with the same SHA-256; all 35 tests then passed. No model or
runtime binary was added to Git.
