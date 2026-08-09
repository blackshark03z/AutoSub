import os
import subprocess
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.preflight import run_preflight
from app.core.security import require_local_operator
from app.db.session import session_scope
from app.domain.models import MediaAsset, Project
from app.providers.translation.gemini import (
    GeminiOpenAICompatibleProvider,
    gemini_credential_status,
    load_gemini_translation_config,
)
from app.services.cp02_pipeline import run_cp02_vertical_slice
from app.services.content_transform import transform_latest_timeline
from app.services.ingest import import_local_source
from app.services.ocr_runtime import get_ocr_runtime_status
from app.providers.asr.autosubs_provider import AUTOSUBS_MODEL, AUTOSUBS_VERSION
from app.services.operator_ui import (
    accepted_preview_path,
    apply_cjk_cleanup_action,
    build_operator_project_summary,
    list_operator_projects,
    mark_issue_reviewed,
)
from app.services.production_golden_path import local_export_file_path, local_export_review_summary, manual_publication_handoff_path, run_golden_path_action
from app.services.production_intake import (
    create_project_from_local_source,
    finalize_uploaded_source,
    make_project_slug,
    start_stage_job,
    uploaded_source_destination,
    validate_source_path,
)
from app.services.simple_workflow import (
    InvalidCompletedResultError,
    SubtitleContentUnavailableError,
    accept_processing,
    approve_run,
    create_or_reuse_run,
    current_run,
    get_run,
    output_file_path,
    output_location,
    persist_unhandled_processing_failure,
    recent_runs,
    reject_run,
    save_copy,
    start_processing,
    supported_formats,
    validate_source,
)
from app.services.subtitle_tracks import (
    apply_import_candidate,
    export_creative_template,
    get_track,
    list_tracks,
    preview_import_candidate,
    resolved_cues,
    set_active_track,
    set_track_enabled,
    undo_last_import,
    update_track_item,
)
from app.services.timeline import (
    disable_segment,
    load_latest_timeline,
    merge_segments,
    save_timeline_revision,
    split_segment,
)
from app.worker.heartbeat import heartbeat

router = APIRouter(dependencies=[Depends(require_local_operator)])


class ProjectCreate(BaseModel):
    title: str = Field(default="Vertical slice")


class SourceImportRequest(BaseModel):
    source_path: str


class SegmentSplitRequest(BaseModel):
    segment_id: str
    split_ms: int


class SegmentPairRequest(BaseModel):
    first_id: str
    second_id: str


class SegmentDisableRequest(BaseModel):
    segment_id: str


class IssueReviewRequest(BaseModel):
    issue_id: str


class CJKCleanupActionRequest(BaseModel):
    action: str
    issue_id: str | None = None


class SourcePreflightRequest(BaseModel):
    source_path: str
    slug: str | None = None


class ProductionProjectCreateRequest(BaseModel):
    name: str
    slug: str
    source_path: str
    source_language: str = "zh-CN"
    target_language: str = "en-US"
    content_mode: str = "sentence-level narrated localization"
    localization_scope: str = "dialogue_subtitles_only"
    voice: str = "Production voice configured"
    elevenlabs_model: str = "eleven_multilingual_v2"
    source_audio_policy: str = "replace source speech with generated narration"
    subtitle_style_preset: str = "CP07A compact plate"
    output_resolution: str = "1280x720"
    provenance_acknowledged: bool = False
    notes: str = ""


class StageActionRequest(BaseModel):
    stage: str


class GoldenPathActionRequest(BaseModel):
    action: str
    artifact_path: str | None = None


class CreativeImportRequest(BaseModel):
    content: str
    format: str = "txt"
    filename: str = "creative_script.txt"
    mode: str = "cue_id"


class CreativeApplyRequest(CreativeImportRequest):
    track_type: str = "creative"
    display_name: str | None = None
    fallback_policy: str = "fallback_to_translation"


class ActiveTrackRequest(BaseModel):
    track_id: str
    fallback_policy: str = "fallback_to_translation"


class TrackEnabledRequest(BaseModel):
    enabled: bool


class TrackItemUpdateRequest(BaseModel):
    cue_id: str
    text: str


class SimpleSourceRequest(BaseModel):
    source_path: str
    settings: dict | None = None


class SimpleRetryRequest(BaseModel):
    source_path: str
    retry_parent_run_id: str
    settings: dict | None = None


class SimpleSaveCopyRequest(BaseModel):
    destination_folder: str


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "bind": "127.0.0.1", "ocr_runtime": get_ocr_runtime_status()}


@router.get("/worker/heartbeat")
def worker_heartbeat() -> dict:
    return heartbeat()


@router.get("/preflight")
def preflight() -> dict:
    return run_preflight()


@router.get("/operator/projects")
def operator_projects() -> dict:
    return list_operator_projects()


@router.get("/operator/runtime-build")
def operator_runtime_build() -> dict:
    commit = os.environ.get("TOOL_AUTO_SUB_BUILD_COMMIT", "").strip() or "unknown"
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=Path.cwd(), text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        pass
    simple_asset = os.environ.get("TOOL_AUTO_SUB_SIMPLE_UI_VERSION", "").strip() or "cp12b"
    operator_asset = os.environ.get("TOOL_AUTO_SUB_OPERATOR_UI_VERSION", "").strip() or "cp09c"
    return {
        "git_commit": commit,
        "backend_version": "0.2.0",
        "frontend_asset_version": operator_asset,
        "simple_frontend_asset_version": simple_asset,
        "operator_frontend_asset_version": operator_asset,
    }


@router.get("/simple/capabilities")
def simple_capabilities() -> dict:
    return {
        "supported_formats": supported_formats(),
        "default_settings": {
            "target_language": "English",
            "subtitle_mode": "burned into video",
            "localization_scope": "dialogue subtitles only",
            "copy_source_into_workspace": False,
        },
        "provider_calls_on_ui_load": {"gemini": 0, "elevenlabs": 0, "youtube": 0},
        "gemini_translation": gemini_credential_status(),
        "automatic_transcription": {
            "provider": "autosubs",
            "engine_version": AUTOSUBS_VERSION,
            "model": AUTOSUBS_MODEL,
            "model_source": "AutoSubs managed local cache",
            "model_policy": "preflight_requires_cached_small_model",
            "local_inference": True,
            "fallback_enabled": False,
            "implicit_download_enabled": False,
        },
    }


@router.post("/simple/source/validate")
def simple_validate_source(payload: SimpleSourceRequest) -> dict:
    return validate_source(payload.source_path)


@router.post("/simple/source/upload")
async def simple_source_upload(request: Request) -> dict:
    filename = request.headers.get("x-filename", "")
    try:
        temp_path, final_path = uploaded_source_destination(filename)
        try:
            with temp_path.open("wb") as handle:
                async for chunk in request.stream():
                    if chunk:
                        handle.write(chunk)
            uploaded = finalize_uploaded_source(temp_path, final_path)
            uploaded["validation"] = validate_source(uploaded["uploaded_path"])
            return uploaded
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"title": "Upload failed", "message": str(exc)}) from exc


@router.post("/simple/runs")
def simple_create_run(payload: SimpleSourceRequest) -> dict:
    try:
        return {"run": create_or_reuse_run(payload.source_path, settings=payload.settings)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"title": "Unsupported video format", "message": str(exc)}) from exc


@router.post("/simple/runs/retry")
def simple_retry_run(payload: SimpleRetryRequest) -> dict:
    try:
        return {"run": create_or_reuse_run(payload.source_path, settings=payload.settings, retry_parent_run_id=payload.retry_parent_run_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"title": "Processing was interrupted", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/start")
def simple_start_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    x_idempotency_key: str | None = Header(default=None),
) -> dict:
    try:
        accepted = accept_processing(run_id, x_idempotency_key)
        if accepted.get("start_accepted"):
            background_tasks.add_task(_run_simple_processing_background, run_id)
        return {"run": accepted}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"title": "Processing was interrupted", "message": str(exc)}) from exc
    except SubtitleContentUnavailableError as exc:
        raise HTTPException(status_code=400, detail={"title": "Không thể tạo phụ đề", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"title": "Processing was interrupted", "message": str(exc)}) from exc


def _run_simple_processing_background(run_id: str) -> None:
    try:
        start_processing(run_id, accepted=True)
    except Exception as exc:
        # The worker persists a safe blocked/failed state for polling. Background
        # exceptions must not invalidate the already accepted HTTP response.
        persist_unhandled_processing_failure(run_id, exc)
        return


@router.get("/simple/runs/current")
def simple_current_run() -> dict:
    return current_run()


@router.get("/simple/runs/recent")
def simple_recent_runs() -> dict:
    return recent_runs()


@router.get("/simple/runs/{run_id}")
def simple_run_status(run_id: str) -> dict:
    try:
        return {"run": get_run(run_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"title": "Processing was interrupted", "message": str(exc)}) from exc


@router.get("/simple/runs/{run_id}/output")
def simple_run_output(run_id: str) -> FileResponse:
    try:
        path = output_file_path(run_id)
    except InvalidCompletedResultError as exc:
        raise HTTPException(status_code=409, detail={"title": "Kết quả không hợp lệ", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"title": "Video is not ready", "message": str(exc)}) from exc
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/simple/runs/{run_id}/output-location")
def simple_output_location(run_id: str) -> dict:
    try:
        return output_location(run_id)
    except InvalidCompletedResultError as exc:
        raise HTTPException(status_code=409, detail={"title": "Kết quả không hợp lệ", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"title": "Video is not ready", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/approve")
def simple_approve_run(run_id: str) -> dict:
    try:
        return {"run": approve_run(run_id)}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"title": "Output verification failed", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/reject")
def simple_reject_run(run_id: str) -> dict:
    try:
        return {"run": reject_run(run_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"title": "Processing was interrupted", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/save-copy")
def simple_save_copy(run_id: str, payload: SimpleSaveCopyRequest) -> dict:
    try:
        return save_copy(run_id, payload.destination_folder)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"title": "Output verification failed", "message": str(exc)}) from exc


@router.get("/simple/runs/{run_id}/creative/template")
def simple_export_creative_template(run_id: str, format: str = "txt") -> dict:
    try:
        return export_creative_template(run_id, format)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"title": "Template export failed", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/creative/import/preview")
def simple_preview_creative_import(run_id: str, payload: CreativeImportRequest) -> dict:
    try:
        return preview_import_candidate(run_id, content=payload.content, fmt=payload.format, filename=payload.filename, mode=payload.mode)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"title": "Import preview failed", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/creative/import/apply")
def simple_apply_creative_import(run_id: str, payload: CreativeApplyRequest) -> dict:
    try:
        return apply_import_candidate(
            run_id,
            content=payload.content,
            fmt=payload.format,
            filename=payload.filename,
            mode=payload.mode,
            track_type=payload.track_type,
            display_name=payload.display_name,
            fallback_policy=payload.fallback_policy,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"title": "Import apply failed", "message": str(exc)}) from exc


@router.get("/simple/runs/{run_id}/tracks")
def simple_list_tracks(run_id: str) -> dict:
    try:
        return list_tracks(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"title": "Run not found", "message": str(exc)}) from exc


@router.get("/simple/runs/{run_id}/tracks/resolved")
def simple_resolved_track(run_id: str) -> dict:
    try:
        return resolved_cues(run_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"title": "Track resolution failed", "message": str(exc)}) from exc


@router.get("/simple/runs/{run_id}/tracks/{track_id}")
def simple_get_track(run_id: str, track_id: str) -> dict:
    try:
        return get_track(run_id, track_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"title": "Track not found", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/tracks/active")
def simple_set_active_track(run_id: str, payload: ActiveTrackRequest) -> dict:
    try:
        return set_active_track(run_id, payload.track_id, payload.fallback_policy)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"title": "Track selection failed", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/tracks/{track_id}/enabled")
def simple_set_track_enabled(run_id: str, track_id: str, payload: TrackEnabledRequest) -> dict:
    try:
        return set_track_enabled(run_id, track_id, payload.enabled)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"title": "Track update failed", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/tracks/{track_id}/items")
def simple_update_track_item(run_id: str, track_id: str, payload: TrackItemUpdateRequest) -> dict:
    try:
        return update_track_item(run_id, track_id, payload.cue_id, payload.text)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"title": "Track edit failed", "message": str(exc)}) from exc


@router.post("/simple/runs/{run_id}/tracks/undo-import")
def simple_undo_track_import(run_id: str) -> dict:
    try:
        return undo_last_import(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"title": "Run not found", "message": str(exc)}) from exc


@router.get("/operator/slug")
def operator_slug(name: str) -> dict:
    return {"slug": make_project_slug(name)}


@router.post("/operator/source/preflight")
def operator_source_preflight(payload: SourcePreflightRequest) -> dict:
    return validate_source_path(payload.source_path, slug=payload.slug)


@router.post("/operator/source/upload")
async def operator_source_upload(request: Request) -> dict:
    filename = request.headers.get("x-filename", "")
    try:
        temp_path, final_path = uploaded_source_destination(filename)
        try:
            with temp_path.open("wb") as handle:
                async for chunk in request.stream():
                    if chunk:
                        handle.write(chunk)
            return finalize_uploaded_source(temp_path, final_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/operator/projects/{project_id}/summary")
def operator_project_summary(project_id: str) -> dict:
    try:
        return build_operator_project_summary(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Accepted operator project data not found") from exc


@router.post("/operator/projects/create")
def operator_create_project(payload: ProductionProjectCreateRequest) -> dict:
    try:
        return create_project_from_local_source(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Source file not found") from exc


@router.post("/operator/projects/{project_id}/stage/start")
def operator_start_stage(project_id: str, payload: StageActionRequest) -> dict:
    try:
        return start_stage_job(project_id, payload.stage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/operator/projects/{project_id}/golden-path/action")
def operator_golden_path_action(project_id: str, payload: GoldenPathActionRequest) -> dict:
    try:
        return run_golden_path_action(project_id, payload.action, artifact_path=payload.artifact_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/operator/projects/{project_id}/local-export/review")
def operator_local_export_review(project_id: str) -> dict:
    try:
        return local_export_review_summary(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/operator/projects/{project_id}/local-export/files/{file_name}")
def operator_local_export_file(project_id: str, file_name: str) -> FileResponse:
    try:
        path = local_export_file_path(project_id, file_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_types = {
        ".mp4": "video/mp4",
        ".json": "application/json",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".ass": "text/plain; charset=utf-8",
        ".srt": "text/plain; charset=utf-8",
    }
    return FileResponse(path, media_type=media_types.get(path.suffix.lower(), "application/octet-stream"), filename=path.name)


@router.get("/operator/projects/{project_id}/manual-publication-handoff")
def operator_manual_publication_handoff(project_id: str) -> FileResponse:
    try:
        path = manual_publication_handoff_path(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Manual publication handoff is not available") from exc
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)


@router.post("/operator/projects/{project_id}/issues/review")
def operator_mark_issue_reviewed(project_id: str, payload: IssueReviewRequest) -> dict:
    try:
        return mark_issue_reviewed(project_id, payload.issue_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Accepted operator project data not found") from exc


@router.post("/operator/projects/{project_id}/cleanup/action")
def operator_cjk_cleanup_action(project_id: str, payload: CJKCleanupActionRequest) -> dict:
    try:
        return apply_cjk_cleanup_action(project_id, payload.action, payload.issue_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Accepted operator project data not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/operator/projects/{project_id}/accepted-preview")
def operator_accepted_preview(project_id: str) -> FileResponse:
    try:
        path = accepted_preview_path(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Accepted preview not found") from exc
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.post("/projects")
def create_project(payload: ProjectCreate) -> dict:
    project_id = f"proj_{uuid4().hex[:12]}"
    with session_scope() as session:
        project = Project(project_id=project_id, title=payload.title)
        session.add(project)
    return {"project_id": project_id, "title": payload.title}


@router.get("/projects")
def list_projects() -> dict:
    with session_scope() as session:
        projects = session.query(Project).order_by(Project.created_at.asc()).all()
        return {
            "projects": [
                {"project_id": p.project_id, "title": p.title, "created_at": p.created_at.isoformat()}
                for p in projects
            ]
        }


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
    with session_scope() as session:
        project = session.query(Project).filter(Project.project_id == project_id).one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"project_id": project.project_id, "title": project.title, "created_at": project.created_at.isoformat()}


@router.post("/projects/{project_id}/source/import")
def import_source(project_id: str, payload: SourceImportRequest) -> dict:
    settings = get_settings()
    source_path = Path(payload.source_path)
    if not source_path.is_absolute():
        source_path = settings.root / source_path
    with session_scope() as session:
        project = session.query(Project).filter(Project.project_id == project_id).one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
    return import_local_source(project_id=project_id, source_path=source_path)


@router.post("/projects/{project_id}/transcribe")
def transcribe_vertical_slice(project_id: str) -> dict:
    with session_scope() as session:
        media = (
            session.query(MediaAsset)
            .filter(MediaAsset.project_id == project_id)
            .order_by(MediaAsset.id.desc())
            .first()
        )
        if media is None:
            raise HTTPException(status_code=409, detail="Project source has not been imported")
        source_path = Path(media.path)
    try:
        return run_cp02_vertical_slice(project_id=project_id, source_path=source_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/segments/split")
def split_project_segment(project_id: str, payload: SegmentSplitRequest) -> dict:
    return _save_segment_edit(project_id, split_segment, payload.segment_id, payload.split_ms)


@router.post("/projects/{project_id}/segments/merge")
def merge_project_segments(project_id: str, payload: SegmentPairRequest) -> dict:
    return _save_segment_edit(project_id, merge_segments, payload.first_id, payload.second_id)


@router.post("/projects/{project_id}/segments/disable")
def disable_project_segment(project_id: str, payload: SegmentDisableRequest) -> dict:
    return _save_segment_edit(project_id, disable_segment, payload.segment_id)


@router.post("/projects/{project_id}/content/transform")
def transform_project_content(project_id: str) -> dict:
    try:
        provider = GeminiOpenAICompatibleProvider(load_gemini_translation_config())
        return transform_latest_timeline(project_id, provider)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Required local translation input is missing") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Translation provider failed: {type(exc).__name__}") from exc


def _save_segment_edit(project_id: str, operation, *args) -> dict:
    try:
        timeline = load_latest_timeline(project_id)
        return save_timeline_revision(project_id, operation(timeline, *args))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
