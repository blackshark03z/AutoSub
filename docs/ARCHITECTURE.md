# Architecture

This document describes implemented behavior only. Proposed future work lives in short ADRs under `docs\DECISIONS`.

## Runtime Shape

Tool Auto Sub is a local FastAPI application with static browser UIs, SQLite state, and project files under `data\projects`.

- `app\main.py` creates the FastAPI app, serves static UI files, and runs database migrations at startup.
- `app\api\routes.py` exposes local-only API endpoints.
- `app\services\*` contains workflow, operator, import, OCR, render, provider-boundary, and release services.
- `app\db\session.py` configures SQLite and runs Alembic migrations.
- `app\domain\models.py` defines SQLAlchemy models.
- `app\static\simple` is the primary Simple UI.
- `app\static\operator` is the advanced Operator UI.

## Launcher and User Interfaces

Double-clicking `Run AutoSub.cmd` starts the local AutoSub server and opens the Simple UI after startup readiness. The Simple UI at `/` is the default product path for a normal single user; the normal flow covers video and target-language selection, runtime readiness, transcription, translation, preview, and export.

The normal external-audio workflow uses the existing `runtime_readiness` service inside the accepted background processing job. AutoSubs/Argos readiness is managed through that product path and surfaced through the existing run-status contract.

The Operator UI at `/operator/` is advanced tooling for project review, diagnostics, release inspection, and recovery.

## Distribution

The accepted product state is the local daily-use MVP. The SQLite schema remains at `0009_subtitle_tracks`. EXE, installer, and release packaging are intentionally deferred to `wip/windows-release-pipeline-rebuild`; release-only CP11C/CP11D checks remain separate from normal product validation.

## Subtitle Model

Canonical cue timing is independent from subtitle wording. Implemented content tracks are:

- `translation`: canonical generated/default wording.
- `creative`: optional user-authored wording.
- `imported`: optional imported wording.

Creative and Imported tracks do not control cue timing, source subtitle suppression geometry, audio, or source media. Missing creative cues default to `fallback_to_translation` unless the user selects a different policy.

## OCR And Render Pipeline

The implemented media pipeline stores source references, OCR/source-subtitle detection artifacts, subtitle tracks, render outputs, and run manifests under project/run directories.

## Isolation

Project/run data is isolated under:

```text
data\projects\<project_id>\runs\<run_id>\
```

Imported creative scripts are copied into the owning run directory. Track metadata and items are stored in SQLite and are scoped by `run_id`.

## Provider Boundary

Gemini, ElevenLabs, upload, and publish calls are disabled unless a future authorized task explicitly enables them. Tests and maintenance checks must use fake providers or local state.

## Future Proposal

Module boundary cleanup is tracked as `REF-001_MODULE_BOUNDARIES_PROPOSAL` in `docs\DECISIONS`. It is proposed, not implemented.
