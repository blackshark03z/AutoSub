# AutoSubs external transcription contract

The current Simple UI automatic-transcription path uses the approved local
AutoSubs **v3.8.0** Windows executable through a subprocess-only adapter.
AutoSubs is MIT-licensed; the optional MMS forced-alignment path is excluded
because its asset license is noncommercial.

## Readiness and ownership

Before a normal zh→en workflow starts, `runtime_readiness` checks the
machine-local runtime. If needed, it downloads the pinned AutoSubs executable
to the per-user runtime root, verifies its SHA-256, and performs a short real
`small`-model probe through AutoSubs' public CLI. It also prepares and probes
the local Argos zh→en dependency. This is product-managed preparation, not an
installer prerequisite and not a user-configured ASR command.

AutoSubs owns its model cache. AutoSub records successful readiness in its
managed runtime root, then the adapter verifies the executable version and
cached `small` model again before actual transcription. A failed readiness or
preflight blocks the run with an actionable local error; the AutoSubs flow
does not silently fall back to another engine.

## Source transcription boundary

The adapter invokes source transcription only:

```text
--model small --lang <source|auto> --no-gpu --format json
```

It does not request engine translation or forced alignment. The adapter
accepts timestamped source cues from AutoSubs JSON, preferring
`originalSegments` when available, validates timings, and preserves source
text. Translation, source-track persistence, presentation, preview, and
export remain downstream responsibilities.

The FastAPI runtime endpoint exposes readiness and provider policy to the
Simple UI. See [EXTERNAL_RUNTIME.md](EXTERNAL_RUNTIME.md) for machine-local
ownership and [ARCHITECTURE.md](ARCHITECTURE.md) for the full workflow.
