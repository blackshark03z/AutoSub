# Machine-local runtime ownership

The source tree deliberately excludes runtime payloads, model binaries, and
user data. The supported daily-use path prepares its own local dependencies;
they are not source-controlled release artifacts.

## Daily-use runtime

`Run AutoSub.cmd` launches the local application. Before a Simple UI workflow
uses automatic transcription, the product's runtime-readiness path checks or
prepares the following machine-local dependencies:

- AutoSubs **v3.8.0** executable under
  `%LOCALAPPDATA%\ToolAutoSub\runtime\autosubs\autosubs.exe`.
- The AutoSubs `small` model in the cache owned by AutoSubs itself, verified by
  a real local probe.
- Argos Translate **1.9.6** and the local `translate-zh_en-1_9` package under
  `%LOCALAPPDATA%\ToolAutoSub\runtime\translation`.

The runtime root can be overridden with `TOOL_AUTO_SUB_RUNTIME_ROOT` for tests
or controlled integration environments. `TOOL_AUTO_SUB_AUTOSUBS_BINARY` is an
advanced override for the AutoSubs adapter, not a normal-user setting. The
development tree may also retain a compatible legacy
`addons\autosubs\autosubs.exe`, but new daily-use preparation uses the
per-user runtime root above.

## Boundaries

- Do not commit, copy, or rebuild runtime payloads, model caches, virtual
  environments, user media, or project databases as part of ordinary source
  work.
- Faster-Whisper modules and older machine-specific paths remain compatibility
  or historical implementation material; they are not the default automatic
  transcription contract for the current Simple UI MVP.
- EXE/installer packaging is a separate deferred release lane. It is not
  required to launch or use the daily-use local MVP.

For the runtime preparation and transcription contract, see
[AUTOSUBS_ENGINE_CONTRACT.md](AUTOSUBS_ENGINE_CONTRACT.md). For normal launch
and diagnostics, see [OPERATIONS.md](OPERATIONS.md).
