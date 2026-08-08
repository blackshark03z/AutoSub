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

## User Interfaces

The Simple UI at `/` is the default product path for a normal single user.

The Operator UI at `/operator/` is advanced tooling for project review, diagnostics, release inspection, and recovery.

## Distribution

The portable baseline distribution is a unified Full Portable ZIP. CP12B includes the application, release-local Python runtime, bundled OCR runtime, launchers, diagnostics, documentation, migrations through `0009_subtitle_tracks`, and no private source media or secrets.

The current one-click external beta candidate is CP13A. It wraps CP12B in a per-user Windows EXE installer, adds a Simple UI first-run path, bundles release-local FFmpeg and ffprobe, installs under `%LOCALAPPDATA%\Programs\ToolAutoSubBeta`, and stores user projects, logs, diagnostics, and SQLite state under `%LOCALAPPDATA%\ToolAutoSubBeta`. It does not register global PATH, Python, OCR, shell handlers, services, or machine-wide state.

## Subtitle Model

Canonical cue timing is independent from subtitle wording. Implemented content tracks are:

- `translation`: canonical generated/default wording.
- `creative`: optional user-authored wording.
- `imported`: optional imported wording.

Creative and Imported tracks do not control cue timing, source subtitle suppression geometry, audio, or source media. Missing creative cues default to `fallback_to_translation` unless the user selects a different policy.

## OCR And Render Pipeline

The implemented media pipeline stores source references, OCR/source-subtitle detection artifacts, subtitle tracks, render outputs, and run manifests under project/run directories. CP12B portable startup discovers the bundled OCR runtime from release-local configuration; it does not require a separate OCR installation.

## Isolation

Project/run data is isolated under:

```text
data\projects\<project_id>\runs\<run_id>\
```

Imported creative scripts are copied into the owning run directory. Track metadata and items are stored in SQLite and are scoped by `run_id`.

## Provider Boundary

Gemini, ElevenLabs, upload, and publish calls are disabled unless a future authorized task explicitly enables them. Tests and maintenance checks must use fake providers or local state.

CP13A launchers set provider/upload disable flags at startup so external beta use remains local-only by default.

## Future Proposal

Module boundary cleanup is tracked as `REF-001_MODULE_BOUNDARIES_PROPOSAL` in `docs\DECISIONS`. It is proposed, not implemented, and is not CP10B.
