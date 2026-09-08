# System Purpose

AutoSub is a local, single-user Windows application that converts local source video into an inspectable English-subtitled MP4 while keeping user media and processing local by default.

# Architecture

The current product is a FastAPI modular monolith with static browser UIs, SQLite state, machine-local managed runtimes, and project/run artifacts under the configured data root.

- `app/main.py` creates the FastAPI app, serves the browser surfaces, and initializes database migrations.
- `app/api/routes.py` exposes local-only HTTP endpoints guarded for the local operator.
- `app/services/` owns workflow, readiness, transcription/translation composition, subtitle tracks, render, validation, and operator behavior.
- `app/providers/` owns external/local provider adapters. The active V1 normal path uses AutoSubs for speech transcription and Argos for offline zh->en translation.
- `app/static/simple/` is the primary product surface.
- `app/static/operator/` is advanced diagnostics/recovery tooling, not the normal user journey.
- `tools/local_launcher.py` + `Run AutoSub.cmd` own the normal Windows launch/reuse path.

# Data / Control Flow

Normal V1 CUJ:

`Run AutoSub.cmd -> verified local FastAPI server -> Simple UI -> source validation -> run identity -> runtime readiness -> AutoSubs transcription -> Argos zh->en translation -> resolved subtitle track -> ffmpeg subtitle render -> final result validation -> browser preview/output folder`

Project/run artifacts are isolated under `data/projects/<project_id>/runs/<run_id>/`. Source media is referenced by default and is copied only when the explicit working-copy option is used. Completed output is published to the UI only after validation marks it eligible.

# Authority / State Boundaries

- `main` Git/source is implementation authority.
- SQLite + run directories are live product state for the identified data root.
- The source media path/hash identifies the input; source media is not mutated.
- `runtime_readiness` owns machine-local AutoSubs/Argos readiness identity and validation.
- Run manifests/results are evidence only for the run/source/configuration they identify.
- Tests are verification evidence; they do not supersede live product behavior.
- `TASK.md` is temporary current-Goal context, not runtime authority.
- CADS is an engineering standard/control model only; AutoSub has no second development lifecycle runtime.

# Stable Invariants

- Local product endpoints bind to `127.0.0.1`.
- User source media is never modified and never automatically deleted to satisfy storage pressure.
- Invalid/empty/unverified subtitle results must not be surfaced as completed output.
- Processing is idempotently admitted so duplicate UI starts do not create duplicate active work for the same run.
- Project/run data remains isolated by run identity.
- Runtime/provider failures produce actionable, sanitized product errors rather than raw secrets/tracebacks.
- Gemini, ElevenLabs, upload/publish effects are disabled for the active V1 normal journey.
- Generated media, user data, secrets, runtime binaries/models/caches, and databases are not canonical Git source.

# Important Tradeoffs / Decisions

- V1 optimizes for a single coherent daily-use journey rather than exposing every historical capability in the primary UI.
- AutoSubs + Argos are managed/validated locally so the user does not need manual runtime setup during normal operation; first preparation may require network access.
- The normal output contract is intentionally fixed and simple. Configurability that has no verified backend effect must not be presented as a working control.
- EXE/installer/portable packaging remains a separate deferred release concern; repository-local double-click launch is the accepted daily-use entry point for this Goal.

# External Boundaries

- AutoSubs release download/model preparation and Argos package/model preparation may use network access when a required managed runtime is missing.
- ffmpeg is a required local media execution dependency.
- No cloud publication/deployment is part of the active Goal.

# Deprecated / Legacy Notes

The former AutoSub Build OS v1.16/v1.22 lifecycle/control-plane adoption is retired. Historical evidence remains recoverable from Git history; Build OS authority records, lifecycle instructions, and archived control-plane source are not current architecture and must not be treated as active development authority.

The older `docs/ARCHITECTURE.md` may remain as a compatibility/detail document while references are migrated, but this root file is the CADS durable architecture authority going forward.
