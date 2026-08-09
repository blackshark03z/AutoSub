# AutoSubs full-media timeout diagnosis

Date: 2026-08-09 (Asia/Saigon)
Task: `DIAGNOSE_AUTOSUBS_FULL_MEDIA_TIMEOUT`

## Root-cause class

`A. AUTOSUBS_NORMAL_SLOW_EXECUTION`

The retained normal-workflow WAV is 366.296188 seconds, PCM signed 16-bit,
16 kHz mono, 11,721,602 bytes, SHA-256
`1EB6E2DEBFCBCEABF2FBB964E1FD5BA51C23F22399428631276380FF89E53653`.
It was produced from the canonical media by the normal workflow before the
observed full-run timeout.

AutoSubs identity: v3.8.0 executable SHA-256
`7836B94D14A68D50320C9D21F28CF87D85DD5C22E011F75BFB7EBCB08F4EF52D`;
approved cached `small` model SHA-256
`1BE3A9B2063867B937E64E2EC7483364A79917E157FA98C5D94B5C1FFFEA987B`.

Direct probes used the same command shape as the product:

```text
autosubs.exe <wav> --model small --lang auto --no-gpu --format json
```

| WAV duration | Wall seconds | Engine-reported seconds | RTF | Source language | Segments |
| --- | ---: | ---: | ---: | --- | ---: |
| 20s | 55.239 | 52 | 2.762 | zh | 7 |
| 60s | 160.541 | 158 | 2.676 | zh | 21 |
| 120s | 315.889 | 312 | 2.632 | zh | 40 |

The mean RTF is `2.690`, predicting approximately `985.3` seconds for the
366.296188-second input. That exceeds the product's 900-second fixed timeout
by approximately `85.3` seconds. The app's earlier full run therefore timed
out at 900 seconds despite normal CPU activity; it did not demonstrate a
pathological full-media engine state.

## Liveness and buffering evidence

The direct full-media probe was deliberately capped at 300 seconds. During all
60 samples it remained active, accumulated `1167.297` CPU seconds (roughly
3.9 busy cores), and peaked at `986.1 MiB` working set. Its stderr remained:

```text
autosubs: starting (model=small)
autosubs: transcribing...
```

Stdout stayed at zero bytes until process exit on every successful short probe;
it is buffered JSON rather than incremental progress. The full probe was
terminated at its diagnostic cap by the harness, leaving no child process.
This rules out relying on stdout growth for a liveness timeout, but establishes
that the provider process itself is actively consuming CPU rather than
deadlocked.

The original normal-workflow failure recorded the same full WAV command and
`subprocess.TimeoutExpired` after 900 seconds, then correctly persisted
`autosubs_preflight_failed` with no fallback. The direct and wrapped evidence
are consistent: the defect is the product's fixed wall-clock timeout semantics
for CPU-bound full-media inference, not a different AutoSubs invocation,
preprocessing defect, or child-process hang.

## Boundaries and artifacts

Machine-local diagnostic artifacts are under
`D:\\ToolAutoSubRuntime\\autosubs_diagnosis_20260809` and are excluded from
Git. Each probe has a clipped WAV hash, redirected stdout/stderr, and a
five-second CPU/memory/output-size metrics JSON file. `pytest -q
tests/test_external_transcription_provider.py` passed (`8 passed`).

Task 2 may proceed only with a bounded, evidence-backed policy. It must not
remove timeout protection or add an arbitrary global timeout constant.
