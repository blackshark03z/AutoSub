# Project Contract

Updated: 2026-08-09
Project ID: tool-autosub
Owner: Product Owner
Project Status: ACTIVE

## Product Problem

Users need a low-friction local Windows workflow that turns a video with audible speech into usable timed source subtitles and a separate requested translation without manually operating ASR or OCR tooling.

## Target User

Windows users creating subtitle videos from local, single-language speech video.

## Primary User Workflow

1. Select a local video and requested target language.
2. AutoSub obtains a timestamped source-language transcript through an approved local external transcription provider and resolves it as the source track.
3. AutoSub produces the requested translation as a separate track, preserves existing subtitle layout/timeline behavior, and reaches preview/export.

## MVP Goal

A user can take a local video from timestamped source transcript through a separate translation track to preview/export with no manual ASR configuration.

## Success Criteria

### SC-001
- User: Windows user creating subtitles from a local video.
- Action: Select a local video and target language, then start the product workflow.
- Observable result: Timestamped source-language cues, separate translated cues when needed, and a preview/export artifact are produced through the existing subtitle pipeline.
- Acceptance threshold: The frozen external-transcription Goal acceptance contract passes for Chinese and English representative local-speech fixtures, source/translation separation, one-click behavior, and regressions.
- Demo method: Deterministic provider/vertical-slice tests plus selected-engine local integration smoke evidence.

## In Scope

- Local video input to a replaceable external local transcription provider.
- Timestamped source transcript normalization, source-track preservation, existing requested translation semantics, subtitle presentation/timeline, preview, and export.
- Initial Chinese source and English source acceptance cases on Windows.
- Managed detection/preparation of the selected local transcription runtime/model when required for the one-click MVP.

## Out of Scope / Later

- Speaker diarization, overlapping-speaker optimization, hard-sub OCR improvement, pathological/noisy-audio optimization, custom model training, cloud ASR, collaborative editing, and wholesale ASR architecture rewrite.
- Deleting the existing internal Faster-Whisper path; it remains a control/fallback during evaluation.

## Supported Cases

- Local video files with audible single-language speech.
- Chinese source with English target translation.
- English source as an anti-hard-code case.
- Source language equal to target, or equivalent no-translation behavior.
- Windows local operation.

## Explicitly Unsupported Cases

- Noncommercial-only alignment/model assets in the commercializable MVP path.
- Manual ASR-parameter configuration as a required user step.
- Source-track substitution by translated ASR output.
- Any case listed as out of scope unless it already works without new scope.

## Current Milestone

- Milestone ID: AUTOSUB_EXTERNAL_TRANSCRIPTION_MVP
- User outcome: A local video reaches a timestamped source track, separate requested translation, preview, and export through an approved external local transcription provider.
- Success Criterion: SC-001
- Demonstrable outcome: One-click local video to source transcript, translation track, subtitle layout/timeline, and preview/export artifact.
- Time-to-first-demo expectation: Selected-engine normalized transcription capability before product wiring.
- Maximum consecutive non-shipping tasks: 3

## Technical Baseline

- Language/runtime: Python 3.11
- Framework: FastAPI
- Database: SQLAlchemy with SQLite
- Package manager: pip
- Entry point: app/main.py
- Run command: .\run_app.ps1
- Install command: python -m pip install -r requirements.txt
- Test command: python -m pytest -q
- Lint command: NONE_CURRENTLY_AVAILABLE
- Typecheck command: NONE_CURRENTLY_AVAILABLE
- Build command: NONE_REQUIRED_FOR_LOCAL_DEVELOPMENT
- CI quality command: python -m pytest -q
- CI quality capabilities: test
- Important directories: app, tests, tools

## Risk Surface Map

- R2 paths: app/api/**,app/services/**,app/domain/**,app/providers/**,app/db/**,app/static/**,tests/**
- R3 paths: alembic/**,release/**,installer/**,.github/workflows/**
- Sensitive business terms: source_track,translation_track,source_language,target_language,transcription_provider

## Architecture Budget

- Default architecture: modular monolith
- New deployables without owner approval: 0
- New databases without owner approval: 0
- New framework requires owner approval: yes
- New production dependency requires justification: yes
- Abstraction requires real variation or tested boundary: yes

## Codebase Health Policy

- Health mode: RATCHET
- Architecture boundaries config: config/codebase_health.json
- Architecture decision: Existing modular-monolith boundaries are documented, but enforceable import-direction rules require a later dedicated decision.
- Tracked build/cache artifacts: prohibited unless explicitly allowlisted
- Large new binary threshold: 5 MB
- New runtime dependency: structured capability / alternatives / removal-cost decision required
- Cleanup budget: bounded to the touched area; broad rewrites require a separate Goal
- Refactor priority: change-frequency × rework/defect hotspot, not file size alone

## Quality Priorities

1. Functional acceptance and source/translation semantic safety.
2. Local-data and licensing safety.
3. Deterministic evidence appropriate to actual risk.
4. Maintainability sufficient for the next product milestone.

## Owner Authorization Policy

### Pre-authorized

- Read-only inspection and upstream evaluation.
- Focused local tests using non-canonical fixtures.
- Local external-engine probes and managed model/runtime preparation required by the approved Goal.
- Create-new-version evidence and artifacts within an authorized task.
- Commits and a non-force push to origin/main after the control-plane and Goal acceptance requirements are met.

### Explicit approval required

- Scope beyond the approved Goal.
- Production mutation, migration, delete/overwrite outside the authorized task.
- Secrets, paid provider use, cloud ASR, deploy/publish, new deployables, or a new framework.
- A noncommercial dependency or model asset in the commercializable MVP path.

## Data and Artifact Policy

- Canonical data: User-selected local videos and project records; do not mutate source media.
- Test/clone data: Public authoritative fixtures and local derived test artifacts only.
- Default operation: READ_ONLY
- In-place mutation: explicit approval required
- Delete: explicit approval required
- Regeneration overwrite: prohibited unless explicitly authorized
- Backup/rollback: Revert only task delta; preserve source media and create new preview/export artifacts.

## Constraints

- Time: Optimize for the smallest accepted vertical slice.
- Financial: No paid provider or cloud ASR.
- Privacy: Local processing; do not upload user video or transcript content.
- Platform: Windows is primary.
- Licensing: Provider and required runtime/model path must be permissive and commercializable.
- External services: Local subprocess/API only for the approved transcription engine; cloud providers remain disabled.

## Scope Guard

Do not expand the MVP for a pathological fixture. AutoSub owns a replaceable provider boundary, not another ASR implementation. Manual correction may remain valid product behavior.
