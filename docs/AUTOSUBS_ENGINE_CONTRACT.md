# AutoSubs external transcription contract

AutoSub integrates the approved local AutoSubs **v3.8.0** Windows executable through a subprocess-only adapter. AutoSubs is MIT-licensed; the optional MMS forced-alignment path is excluded because its asset license is noncommercial.

The adapter invokes only source transcription: `--model small --lang <source|auto> --no-gpu --format json`. It does not pass translation or forced-alignment flags and does not silently fall back to Faster-Whisper. Before transcribing it asks the real AutoSubs executable for `--version` and `--list-models`; it refuses to start unless the executable reports exactly v3.8.0 and the approved `small` model is already listed as cached. The installer/package owns those bundle artifacts; an operator does not configure an ASR command manually.

AutoSubs itself owns its Tauri application cache and can download missing assets when directly invoked. AutoSub never uses that implicit path: it blocks before transcription if `small` is absent. AutoSubs' own source does not expose a supported offline/cache override, so this preflight is deliberately performed through its public CLI instead of inventing environment variables the engine does not consume.

The JSON boundary is `segments[{start,end,text}]`, measured in seconds. The adapter validates each timestamp and preserves source `text` verbatim. Translation, source-track persistence, presentation, and export remain downstream responsibilities.
