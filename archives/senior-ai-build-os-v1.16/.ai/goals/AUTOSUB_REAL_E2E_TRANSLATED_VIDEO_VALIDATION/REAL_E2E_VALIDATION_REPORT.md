# Real E2E translated-video validation report

Date: 2026-08-09 (Asia/Saigon)
Goal: `AUTOSUB_REAL_E2E_TRANSLATED_VIDEO_VALIDATION`
Task: `REAL_E2E_TRANSLATED_VIDEO_VALIDATION`
Verdict: blocked at the first product boundary, before translation.

## Normal workflow exercised

An isolated instance of the real FastAPI/Simple UI application was started on
`127.0.0.1:8788`, with its data and SQLite database rooted outside Git at
`D:\\ToolAutoSubRuntime\\e2e_validation_20260809\\data`. The server process
was explicitly launched with:

- `TOOL_AUTO_SUB_ROOT=C:\\ToolAutoSub\\AutoSub`
- `TOOL_AUTO_SUB_DATA_DIR=D:\\ToolAutoSubRuntime\\e2e_validation_20260809\\data`
- `TOOL_AUTO_SUB_DB_PATH=D:\\ToolAutoSubRuntime\\e2e_validation_20260809\\data\\app.db`
- `TOOL_AUTO_SUB_TRANSLATION_RUNTIME_CONFIG=D:\\ToolAutoSubRuntime\\translation\\operator\\translation_runtime_config.local.json`

The normal Simple UI API flow was used, not a mock or direct translation
harness:

1. `POST /api/simple/runs` selected the retained media
   `C:\\Users\\ADMIN\\Downloads\\SaveTik.io_7645708844944903475_hd.mp4`.
2. Settings requested English, `external_audio_transcription`, burned-in
   subtitles, and `copy_source_into_workspace=false`.
3. `POST /api/simple/runs/{run_id}/start` began background processing with an
   idempotency key.

The selected source was 366.270998 seconds, 1920x1080, 64,463,645 bytes, and
SHA-256 `67E710166D98C732D6ECADB66C71FA86E376B5971D1D96D713788723094A97DA`.

## Runtime identity and actual result

AutoSubs executable SHA-256 was
`7836B94D14A68D50320C9D21F28CF87D85DD5C22E011F75BFB7EBCB08F4EF52D`; the
cached small model SHA-256 was
`1BE3A9B2063867B937E64E2EC7483364A79917E157FA98C5D94B5C1FFFEA987B`.

The actual application subprocess was:

```text
autosubs.exe <run>/work/source_asr_16khz_mono.wav --model small --lang auto --no-gpu --format json
```

It used the normal auto-detect setting (no user ASR arguments), requested no
engine translation, diarization, or forced alignment. The extracted WAV was
created successfully, then the provider exceeded its configured 900-second
timeout. The persisted first failure was:

```text
autosubs_preflight_failed
AutoSubs transcription timed out; no fallback engine was used.
```

The local run evidence is retained outside Git at
`D:\\ToolAutoSubRuntime\\e2e_validation_20260809\\data\\projects\\simple-savetik-io-7645708844944903475-h-67e710166d\\runs\\run_20260809072935168862_b5eaf1a9`.
In particular, `logs/external_asr_error.log` records `subprocess.TimeoutExpired`
for the exact command after 900 seconds; `logs/subtitle_source_block.json`
records the fail-closed condition; and `run_manifest.json` is `blocked` at
`Create subtitles`.

No fallback provider ran. Because no real source cues were returned, the
workflow correctly did not reach the existing Argos translation call despite
the server process receiving the supported translation-runtime configuration.
Therefore there are no real source-to-English pairs, no resolved source or
translation tracks, and no preview/export artifact to inspect. This is a
failure at `video -> AutoSubs`, not an Argos, timeline, layout, or export
failure.

## Regression

The frozen regression command passed with `43 passed, 3 skipped`:

```text
python -m pytest -q tests/test_external_transcription_provider.py \
  tests/test_external_transcription_vertical_slice.py \
  tests/test_task40_source_caption_translation.py \
  tests/test_subtitle_presentation_timeline.py \
  tests/test_task36_one_button_flow.py \
  tests/test_v1_scope_cut_gemini_rejection.py
```

No product file, model, runtime, or configuration was changed by this Goal.
