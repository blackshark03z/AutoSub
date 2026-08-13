# Full-media AutoSubs reliability acceptance

Date: 2026-08-09 (Asia/Saigon)
Goal: `AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY`
Task: `FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY`

## Verdict

PASS.  The canonical 366.270998-second Chinese media completed the normal
Simple UI workflow with source-only AutoSubs transcription, local Argos
Chinese-to-English translation, and a verified rendered MP4.  No fallback,
engine translation, diarization, or forced-alignment asset was used.

## Minimal evidence-backed product change

The Task 1 probes established CPU-bound normal AutoSubs execution, with mean
RTF 2.690 and a predicted 985.3 seconds for the retained 366.296188-second
workflow WAV.  The fixed 900-second timeout was therefore too short.  The
provider now derives a duration-aware limit of `ceil(duration_seconds * 3.5)`,
never below the configured timeout and never above 1800 seconds.  The actual
WAV receives a 1283-second bound.

The successful direct full run also exposed a v3.8.0 response shape issue:
the presentation-oriented `segments` field contained eight zero-duration cues
(indices 53--60), whereas engine-emitted `originalSegments` contained all 171
source cues with valid timestamps.  The provider now selects the latter when
present, preserving its text and timestamps verbatim rather than repairing or
translating them.

## Runtime and provenance

| Item | Evidence |
| --- | --- |
| Source media | `C:\\Users\\ADMIN\\Downloads\\SaveTik.io_7645708844944903475_hd.mp4`; SHA-256 `67E710166D98C732D6ECADB66C71FA86E376B5971D1D96D713788723094A97DA` |
| Workflow WAV | 366.296188 seconds, PCM s16le/16 kHz/mono; SHA-256 `1EB6E2DEBFCBCEABF2FBB964E1FD5BA51C23F22399428631276380FF89E53653` |
| Engine | AutoSubs Windows x64 `3.8.0`; executable SHA-256 `7836B94D14A68D50320C9D21F28CF87D85DD5C22E011F75BFB7EBCB08F4EF52D` |
| Model | cached `small`; SHA-256 `1BE3A9B2063867B937E64E2EC7483364A79917E157FA98C5D94B5C1FFFEA987B` |
| Engine command | `autosubs.exe <run>/work/source_asr_16khz_mono.wav --model small --lang auto --no-gpu --format json` |
| Source result | detected `zh`; task `transcribe`; 171 source cues; `originalSegments` selected |
| Translation | existing supported local `offline_translation` (Argos) runtime; requested target `English` |
| Bound and runtime | 1283-second provider bound; actual AutoSubs processing `1184.678` seconds; successful exit |
| Run | `run_20260809085145764767_05c7dbfb`, under `D:\\ToolAutoSubRuntime\\e2e_reliability_final_20260809` |
| Output | `output\\final_video.mp4`, SHA-256 `D2A92B6BD792A458DEF47ECA44F90BDD287B3581B2CB0295E447AD1FF12812B8`; H.264/AAC, 1920x1080, 366.270998 seconds, 104359483 bytes |

All large binary/model/media and raw runtime artifacts remain outside Git.

## Source-track and output checks

`subtitles/resolved_active_track.json` recorded an active canonical translation
track sourced from AutoSubs.  Its provenance records `asr_provider=autosubs`,
`asr_engine_version=3.8.0`, `asr_model=small`, `asr_task=transcribe`,
`source_language=zh`, `fallback_attempts=0`, `engine_translation=false`,
`forced_alignment=false`, and `translation_provider=offline_translation`.

The 171 persisted source cue texts matched the direct engine
`originalSegments` exactly, and their rounded millisecond timestamps matched
exactly; every cue had `start_ms < end_ms` (first 530--2770 ms, last
364740--366160 ms).  Representative source-to-translation pairs were retained
as Chinese source text followed by Argos English, for example
`经常挨揍的观众朋友们都知道啊` -> `People who get beat up often know that.`
and `我们一定要逃出去` -> `We have to get out of here.`

The rendered MP4 was inspected at 10 seconds.  The frame contains the English
burned-in cue `Quick! Quick!`; its capture is retained outside Git at
`D:\\ToolAutoSubRuntime\\e2e_reliability_final_20260809\\preview_frame_10s.png`.

## Regression

```text
python -m pytest -q tests/test_external_transcription_provider.py \
  tests/test_external_transcription_vertical_slice.py \
  tests/test_task40_source_caption_translation.py \
  tests/test_subtitle_presentation_timeline.py \
  tests/test_task36_one_button_flow.py \
  tests/test_v1_scope_cut_gemini_rejection.py

46 passed, 3 skipped
```
