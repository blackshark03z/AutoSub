import json
import os
import re
import shutil
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.paths import ensure_dir
from app.core.preflight import RUN_ONLY_MIN_FREE_BYTES, storage_preflight
from app.db.session import session_scope
from app.domain.models import Project, SimpleWorkflowRun
from app.services.production_intake import SUPPORTED_VIDEO_EXTENSIONS
from app.services.local_transcription import ensure_local_transcription_track
from app.services.external_transcription import ensure_external_transcription_track
from app.services.asr_models import SIMPLE_UI_MODEL_NAME, normalize_simple_ui_settings
from app.services.offline_translation import OfflineTranslationError, translate_source_captions
from app.services.runtime_readiness import RuntimeReadinessError, ensure_product_runtime_ready
from app.services.clean_subtitle_render import (
    build_source_replacement_filter,
    build_source_replacement_plan,
    write_clean_subtitles_ass,
)
from app.services.source_caption_translation import (
    EXTERNAL_AUDIO_MODE,
    LOCAL_AUDIO_MODE,
    SOURCE_CAPTION_GEMINI_MODE,
    SOURCE_CAPTION_HUMAN_REVIEW_MODE,
    SOURCE_CAPTION_MODE,
    SOURCE_CAPTION_MODES,
    SourceCaptionUnavailableError,
    build_source_caption_render_plan,
    create_source_caption_translation,
)
from app.services.caption_analysis_runtime import (
    CaptionAnalysisError,
    load_caption_analysis_progress,
    run_caption_analysis_worker,
)
from app.services.subtitle_tracks import (
    SubtitleContentUnavailableError,
    active_track_provenance,
    create_provider_translation_track,
    create_source_caption_translation_track,
    resolved_cues,
    test_fixture_context_enabled,
    validate_resolved_subtitle_content,
)

USER_STAGES = [
    ("checking_video", "Checking video"),
    ("analysing_dialogue", "Analysing dialogue"),
    ("preparing_english_subtitles", "Preparing English subtitles"),
    ("cleaning_dialogue_subtitles", "Cleaning original dialogue subtitles"),
    ("rendering_video", "Rendering video"),
    ("verifying_result", "Verifying result"),
]
DEFAULT_SETTINGS = {
    "target_language": "English",
    "subtitle_mode": "burned_into_video",
    "localization_scope": "dialogue_subtitles_only",
    "copy_source_into_workspace": False,
    "include_ass_sidecar": True,
    "subtitle_style": "compact readable plate",
    "output_filename": "final_video.mp4",
    "caption_mode": EXTERNAL_AUDIO_MODE,
}
PROTECTED_DESTINATION_NAMES = {"windows", "program files", "program files (x86)", "users"}
INVALID_COMPLETED_RESULT = "invalid_completed_result"
INVALID_SUBTITLE_CONTENT_MESSAGE = (
    "Kết quả không hợp lệ. Nội dung phụ đề chỉ là bản mẫu hoặc không có nguồn xác định."
)
INVALID_RESULT_MESSAGE = "Kết quả không hợp lệ. Không có phụ đề. Hãy tạo lượt chạy mới."


RENDER_FAILED_MESSAGE = (
    "Không thể xuất video có phụ đề. "
    "Video chưa được hoàn tất; hãy xem chi tiết kỹ thuật và thử lại."
)


class InvalidCompletedResultError(ValueError):
    pass


class RuntimeReadinessBlockedError(SubtitleContentUnavailableError):
    """The run is already persisted as blocked with a setup-specific reason."""


def supported_formats() -> list[str]:
    return sorted(SUPPORTED_VIDEO_EXTENSIONS)


def validate_source(path_value: str) -> dict[str, Any]:
    try:
        source = _resolve_source(path_value)
    except ValueError as exc:
        return _user_error("Unsupported video format" if "format" in str(exc).lower() else "Video file is no longer available", str(exc), retry=True)
    try:
        metadata = _source_metadata(source)
    except Exception as exc:
        return _user_error("Unsupported video format", f"Video metadata could not be read: {type(exc).__name__}.", retry=True)
    disk = _disk_status(source)
    status = "PASS" if disk["ok"] else "FAIL"
    return {
        "status": status,
        "source": metadata,
        "supported_formats": supported_formats(),
        "disk": disk,
        "error": None
        if disk["ok"]
        else {
            "title": "Not enough disk space",
            "message": "The input is safe, but this run needs more local working space before processing.",
            "input_safe": True,
            "retry_possible": True,
            "recommended_next_action": "Free disk space or choose a smaller source video.",
        },
    }


def _settings_compatible_for_reuse(existing_run: SimpleWorkflowRun, requested: dict[str, Any]) -> bool:
    """Check if existing run settings are compatible with requested settings for reuse.
    
    Returns False if settings differ in ways that would produce different output.
    Output-affecting settings include caption_mode, target_language, subtitle_mode,
    localization_scope, and subtitle/render settings.
    """
    try:
        existing_settings = json.loads(existing_run.requested_settings_json)
    except (json.JSONDecodeError, TypeError):
        # If we can't parse existing settings, don't reuse
        return False
    
    # Critical settings that must match for compatible output
    output_affecting_keys = [
        "caption_mode",
        "target_language",
        "subtitle_mode",
        "localization_scope",
        "include_ass_sidecar",
        "subtitle_style",
    ]
    
    for key in output_affecting_keys:
        existing_value = existing_settings.get(key)
        requested_value = requested.get(key)
        if existing_value != requested_value:
            return False
    
    return True


def create_or_reuse_run(path_value: str, settings: dict[str, Any] | None = None, retry_parent_run_id: str | None = None) -> dict[str, Any]:
    source = _resolve_source(path_value)
    source_hash = sha256_file(source)
    metadata = _source_metadata(source, source_hash=source_hash)
    requested = normalize_simple_ui_settings(DEFAULT_SETTINGS | (settings or {}))
    
    # V1 PRE-CREATE GUARD: Reject unverified Gemini source-caption mode before run creation
    requested_mode = str(requested.get("caption_mode") or SOURCE_CAPTION_MODE)
    if requested_mode in {SOURCE_CAPTION_GEMINI_MODE, SOURCE_CAPTION_HUMAN_REVIEW_MODE}:
        raise ValueError(
            "Gemini source-caption translation is not available in V1. "
            "This feature requires verification infrastructure not included in this release. "
            "Please use the default OCR translation mode."
        )
    
    project_id = _project_id_for_source(source, source_hash)
    with session_scope() as session:
        project = session.query(Project).filter(Project.project_id == project_id).one_or_none()
        if project is None:
            session.add(Project(project_id=project_id, title=f"Simple workflow - {source.stem}"))
            session.flush()
        if not retry_parent_run_id:
            existing = (
                session.query(SimpleWorkflowRun)
                .filter(SimpleWorkflowRun.source_hash == source_hash, SimpleWorkflowRun.internal_state.in_(["selected", "processing", "completed", "approved"]))
                .order_by(SimpleWorkflowRun.created_at.desc())
                .first()
            )
            if existing and Path(existing.source_path) == source:
                # V1 FIX: Check settings compatibility before reusing run
                if _settings_compatible_for_reuse(existing, requested):
                    return _serialize_run(existing, reused=True)
        run_id = _new_run_id(project_id)
        run_dir = _run_dir(project_id, run_id)
        _prepare_run_layout(run_dir)
        row = SimpleWorkflowRun(
            run_id=run_id,
            project_id=project_id,
            source_path=str(source),
            source_hash=source_hash,
            source_metadata_json=json.dumps(metadata, ensure_ascii=False),
            requested_settings_json=json.dumps(requested, ensure_ascii=False),
            current_phase="Select video",
            internal_state="selected",
            run_directory=str(run_dir),
            approval_state="not_reviewed",
            retry_parent_run_id=retry_parent_run_id,
            is_test_fixture=False,
        )
        session.add(row)
        session.flush()
        _write_source_reference(run_dir, metadata, requested)
        if requested.get("copy_source_into_workspace"):
            _copy_source_into_work(run_dir, source)
        _write_manifest(run_dir, row, status="selected")
        return _serialize_run(row, reused=False)


def accept_processing(run_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
    with session_scope() as session:
        row = _get_run(session, run_id)
        requested = json.loads(row.requested_settings_json or "{}")
        
        # V1 SCOPE-CUT: Reject unverified Gemini source-caption mode
        requested_mode = str(requested.get("caption_mode") or LOCAL_AUDIO_MODE)
        if requested_mode in {SOURCE_CAPTION_GEMINI_MODE, SOURCE_CAPTION_HUMAN_REVIEW_MODE}:
            row.failure_category = "feature_unavailable"
            row.internal_state = "blocked"
            row.current_phase = "Create subtitles"
            row.updated_at = datetime.now(timezone.utc)
            _write_manifest(Path(row.run_directory), row, status="blocked")
            raise ValueError(
                "Gemini source-caption translation is not available in V1. "
                "This feature requires verification infrastructure not included in this release. "
                "Please use the default OCR translation mode."
            )
        
        existing_key = str(requested.get("start_idempotency_key") or "")
        if row.internal_state in {"processing", "completed", "approved"}:
            return _serialize_run(
                row,
                start_accepted=False,
                duplicate_prevented=True,
                idempotency_reused=bool(idempotency_key and existing_key == idempotency_key),
            )
        if not Path(row.source_path).exists():
            row.failure_category = "source_missing"
            row.internal_state = "blocked"
            row.current_phase = "Create subtitles"
            row.updated_at = datetime.now(timezone.utc)
            _write_manifest(Path(row.run_directory), row, status="blocked")
            raise ValueError("Video file is no longer available. The input is safe; choose the file again or create a new run.")
        disk = _disk_status(Path(row.source_path))
        if not disk["ok"]:
            row.failure_category = "insufficient_disk_space"
            row.internal_state = "blocked"
            row.current_phase = "Create subtitles"
            row.updated_at = datetime.now(timezone.utc)
            _write_manifest(Path(row.run_directory), row, status="blocked")
            raise ValueError("Not enough disk space for this run.")
        if idempotency_key:
            requested["start_idempotency_key"] = idempotency_key
            row.requested_settings_json = json.dumps(requested, ensure_ascii=False)
        row.started_at = datetime.now(timezone.utc)
        row.updated_at = row.started_at
        row.current_phase = "Prepare audio"
        row.internal_state = "processing"
        row.failure_category = None
        _write_manifest(Path(row.run_directory), row, status="processing")
        return _serialize_run(row, start_accepted=True, duplicate_prevented=False)


def start_processing(run_id: str, *, accepted: bool = False) -> dict[str, Any]:
    try:
        if not accepted:
            accepted_run = accept_processing(run_id)
            if not accepted_run.get("start_accepted"):
                return accepted_run
        with session_scope() as session:
            row = _get_run(session, run_id)
            if row.internal_state != "processing":
                return _serialize_run(row, duplicate_prevented=True)
            source_path = Path(row.source_path)
            run_directory = Path(row.run_directory)
            source_metadata = json.loads(row.source_metadata_json or "{}")
            requested = json.loads(row.requested_settings_json or "{}")
            normalized_requested = normalize_simple_ui_settings(requested)
            if normalized_requested != requested:
                requested = normalized_requested
                row.requested_settings_json = json.dumps(requested, ensure_ascii=False)
                _write_manifest(Path(row.run_directory), row, status="processing")

        provenance = active_track_provenance(run_id)
        if not test_fixture_context_enabled() and provenance not in {
            "user_import",
            "user_authored",
            "local_transcription",
            "provider_transcription",
        }:
            _set_processing_phase(run_id, "Prepare audio")
            requested_mode = str(requested.get("caption_mode") or LOCAL_AUDIO_MODE)
            if requested_mode in SOURCE_CAPTION_MODES:
                _set_processing_phase(run_id, "Read embedded captions")
                try:
                    source_caption = run_caption_analysis_worker(source_path, run_directory)
                except CaptionAnalysisError as exc:
                    _persist_processing_failure(run_id, exc.code, "Read embedded captions", exc)
                    raise
                except Exception as exc:
                    _persist_processing_failure(run_id, "EMBEDDED_CAPTION_ANALYSIS_FAILED", "Read embedded captions", exc)
                    raise
                create_source_caption_translation_track(
                    run_id,
                    cues=source_caption["cues"],
                    metadata={
                        **source_caption["metadata"],
                        "source_filename": source_path.name,
                        "source_sha256": row.source_hash,
                    },
                )
                _persist_resolved_mode(
                    run_id,
                    str(source_caption["metadata"].get("mode") or SOURCE_CAPTION_GEMINI_MODE),
                )
            elif requested_mode == EXTERNAL_AUDIO_MODE:
                try:
                    ensure_product_runtime_ready(
                        get_settings().root,
                        progress=lambda _state, message: _set_processing_phase(run_id, message),
                    )
                except RuntimeReadinessError as exc:
                    _persist_runtime_readiness_block(run_id, exc)
                    raise RuntimeReadinessBlockedError(str(exc)) from exc
                _set_processing_phase(run_id, "Recognize speech")
                external_result = ensure_external_transcription_track(
                    run_id,
                    source_path=source_path,
                    run_directory=run_directory,
                    source_duration_seconds=float(source_metadata.get("duration_seconds") or 0),
                    target_language=str(requested.get("target_language") or "English"),
                    source_language=requested.get("source_language"),
                )
                if external_result.get("status") == "PASS" and not _same_language(
                    external_result.get("metadata", {}).get("source_language"), requested.get("target_language"),
                ):
                    _create_external_translation_track(run_id, external_result, str(requested.get("target_language") or "English"))
                _persist_resolved_mode(run_id, EXTERNAL_AUDIO_MODE)
            elif requested_mode == LOCAL_AUDIO_MODE:
                _set_processing_phase(run_id, "Recognize speech")
                ensure_local_transcription_track(
                    run_id,
                    source_path=source_path,
                    run_directory=run_directory,
                    source_duration_seconds=float(source_metadata.get("duration_seconds") or 0),
                    target_language=str(requested.get("target_language") or "English"),
                    source_language=requested.get("source_language"),
                    model_name=SIMPLE_UI_MODEL_NAME,
                )
                _persist_resolved_mode(run_id, LOCAL_AUDIO_MODE)
            else:
                raise SourceCaptionUnavailableError("Unsupported caption workflow mode")
        _set_processing_phase(run_id, "Create subtitles")
        resolved = resolved_cues(run_id)
        (run_directory / "subtitles" / "resolved_active_track.json").write_text(
            json.dumps(resolved, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _set_processing_phase(run_id, "Render video")
        try:
            with session_scope() as session:
                row = _get_run(session, run_id)
                _bounded_subtitle_process(row, resolved)
                row.completed_at = datetime.now(timezone.utc)
                row.updated_at = row.completed_at
                row.current_phase = "Preview"
                row.internal_state = "completed"
                row.output_path = str(Path(row.run_directory) / "output" / "final_video.mp4")
                row.output_hash = sha256_file(Path(row.output_path))
                validation = _completed_result_validation(row)
                if not validation["eligible"]:
                    _invalidate_completed_result(row, validation)
                    raise InvalidCompletedResultError(validation["message"])
                _write_manifest(Path(row.run_directory), row, status="completed")
                return _serialize_run(row)
        except InvalidCompletedResultError:
            raise
        except Exception as exc:
            _persist_processing_failure(run_id, "render_failed", "Render video", exc)
            raise ValueError(RENDER_FAILED_MESSAGE) from exc
    except RuntimeReadinessBlockedError:
        raise
    except (SubtitleContentUnavailableError, SourceCaptionUnavailableError) as exc:
        _persist_subtitle_source_block(run_id, exc)
        raise


def _same_language(source_language: Any, target_language: Any) -> bool:
    aliases = {"english": "en", "en-us": "en", "en-gb": "en", "chinese": "zh", "zh-cn": "zh", "zh-hans": "zh"}
    source = aliases.get(str(source_language or "").strip().lower(), str(source_language or "").strip().lower())
    target = aliases.get(str(target_language or "").strip().lower(), str(target_language or "").strip().lower())
    return bool(source and source == target)


def _create_external_translation_track(run_id: str, external_result: dict[str, Any], target_language: str) -> dict[str, Any]:
    source_cues = list(external_result.get("cues") or [])
    try:
        translated = translate_source_captions([str(cue.get("text") or "") for cue in source_cues])
    except OfflineTranslationError as exc:
        raise SubtitleContentUnavailableError(
            f"Local translation to {target_language} is unavailable. Install the existing local translation runtime and retry."
        ) from exc
    return create_provider_translation_track(
        run_id,
        source_cues=source_cues,
        translated_texts=[str(item.get("translated_text") or "") for item in translated],
        metadata={**dict(external_result.get("metadata") or {}), "translation_provider": "offline_translation", "target_language": target_language},
    )


def get_run(run_id: str) -> dict[str, Any]:
    repair_invalid_completed_results()
    with session_scope() as session:
        return _serialize_run(_get_run(session, run_id))


def current_run() -> dict[str, Any]:
    repair_invalid_completed_results()
    with session_scope() as session:
        row = (
            session.query(SimpleWorkflowRun)
            .filter(
                SimpleWorkflowRun.is_test_fixture.is_(False),
                (SimpleWorkflowRun.failure_category.is_(None)) | (SimpleWorkflowRun.failure_category != INVALID_COMPLETED_RESULT),
            )
            .order_by(SimpleWorkflowRun.updated_at.desc(), SimpleWorkflowRun.id.desc())
            .first()
        )
        return {"run": _serialize_run(row) if row else None}


def recent_runs(limit: int = 5) -> dict[str, Any]:
    repair_invalid_completed_results()
    with session_scope() as session:
        rows = (
            session.query(SimpleWorkflowRun)
            .filter(SimpleWorkflowRun.is_test_fixture.is_(False))
            .order_by(SimpleWorkflowRun.updated_at.desc(), SimpleWorkflowRun.id.desc())
            .limit(limit)
            .all()
        )
        return {"runs": [_serialize_run(row) for row in rows]}


def approve_run(run_id: str) -> dict[str, Any]:
    with session_scope() as session:
        row = _get_run(session, run_id)
        if row.internal_state != "completed":
            raise ValueError("Video is not ready for approval.")
        validation = _completed_result_validation(row)
        if not validation["eligible"]:
            _invalidate_completed_result(row, validation)
            raise InvalidCompletedResultError(validation["message"])
        row.approval_state = "approved"
        row.internal_state = "approved"
        row.current_phase = "Save result"
        row.updated_at = datetime.now(timezone.utc)
        _write_manifest(Path(row.run_directory), row, status="approved")
        return _serialize_run(row)


def reject_run(run_id: str) -> dict[str, Any]:
    with session_scope() as session:
        row = _get_run(session, run_id)
        row.approval_state = "needs_adjustment"
        row.updated_at = datetime.now(timezone.utc)
        _write_manifest(Path(row.run_directory), row, status="needs_adjustment")
        return _serialize_run(row)


def output_file_path(run_id: str) -> Path:
    with session_scope() as session:
        row = _get_run(session, run_id)
        validation = _completed_result_validation(row)
        if validation["status"] == "FAIL":
            if row.internal_state in {"completed", "approved"}:
                _invalidate_completed_result(row, validation)
            raise InvalidCompletedResultError(validation["message"])
        path = Path(row.output_path or "")
        if not path.exists() or Path(row.run_directory).resolve() not in path.resolve().parents:
            raise FileNotFoundError("Output is not available for this run.")
        return path


def output_location(run_id: str) -> dict[str, Any]:
    path = output_file_path(run_id)
    return {
        "status": "PASS",
        "folder": str(path.parent),
        "file": str(path),
        "filename": path.name,
    }


def save_copy(run_id: str, destination_folder: str) -> dict[str, Any]:
    output = output_file_path(run_id)
    destination_dir = _resolve_safe_destination(destination_folder)
    metadata = get_run(run_id)["source"]
    friendly = f"{Path(metadata['filename']).stem}_subbed_en.mp4"
    destination = _collision_safe_path(destination_dir / friendly)
    shutil.copy2(output, destination)
    source_hash = sha256_file(output)
    copy_hash = sha256_file(destination)
    if source_hash != copy_hash:
        destination.unlink(missing_ok=True)
        raise ValueError("Output verification failed.")
    return {
        "status": "PASS",
        "destination": str(destination),
        "source_hash": source_hash,
        "copy_hash": copy_hash,
        "byte_identical": True,
        "overwrote_existing": False,
    }


def _resolve_source(path_value: str) -> Path:
    if not path_value or ".." in Path(path_value).parts:
        raise ValueError("Source path traversal is not allowed.")
    source = Path(path_value).expanduser()
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("Video file is no longer available.") from exc
    if not source.is_file():
        raise ValueError("Source path must be a regular file.")
    if source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        raise ValueError("Unsupported video format.")
    return source


def _source_metadata(source: Path, source_hash: str | None = None) -> dict[str, Any]:
    media = media_summary(source)
    return {
        "path": str(source),
        "filename": source.name,
        "size_bytes": source.stat().st_size,
        "sha256": source_hash or sha256_file(source),
        "duration_seconds": media["duration_seconds"],
        "resolution": {
            "width": media["video"].get("width"),
            "height": media["video"].get("height"),
        },
        "media": media,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }


def _disk_status(source: Path) -> dict[str, Any]:
    settings = get_settings()
    target = settings.db_path.parent if settings.db_path.parent.exists() else settings.data_dir
    size = source.stat().st_size
    estimated = max(size * 3, RUN_ONLY_MIN_FREE_BYTES)
    margin = 512 * 1024 * 1024
    required = estimated + margin
    storage = storage_preflight("run", target)
    free = storage["current_free_bytes"] or 0
    return {
        "free_bytes": storage["current_free_bytes"],
        "estimated_working_bytes": estimated,
        "safe_margin_bytes": margin,
        "required_bytes": required,
        "operation": storage["operation"],
        "required_minimum_bytes": storage["required_minimum_bytes"],
        "margin_bytes": storage["margin_bytes"],
        "measured_at": storage["measured_at"],
        "recommendation": storage["recommendation"],
        "ok": storage["passed"] and free >= required,
    }


def _project_id_for_source(source: Path, source_hash: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", source.stem.lower()).strip("-")[:32] or "video"
    return f"simple-{stem}-{source_hash[:10]}"


def _new_run_id(project_id: str) -> str:
    settings = get_settings()
    while True:
        nonce = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        run_id = f"run_{nonce}_{uuid4().hex[:8]}"
        if not (settings.data_dir / "projects" / project_id / "runs" / run_id).exists():
            return run_id


def _run_dir(project_id: str, run_id: str) -> Path:
    return get_settings().data_dir / "projects" / project_id / "runs" / run_id


def _prepare_run_layout(run_dir: Path) -> None:
    if run_dir.exists():
        raise ValueError("Run directory already exists.")
    for child in ["work", "subtitles", "output", "logs"]:
        ensure_dir(run_dir / child)


def _write_source_reference(run_dir: Path, metadata: dict[str, Any], requested: dict[str, Any]) -> None:
    payload = {
        "schema_version": 1,
        "source": metadata,
        "storage_policy": {
            "copy_source_by_default": False,
            "source_modified": False,
            "copy_source_into_workspace": bool(requested.get("copy_source_into_workspace")),
        },
    }
    (run_dir / "source_reference.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy_source_into_work(run_dir: Path, source: Path) -> None:
    destination = run_dir / "work" / f"source_copy{source.suffix.lower()}"
    shutil.copy2(source, destination)


def _write_manifest(run_dir: Path, row: SimpleWorkflowRun, status: str, result_validation: dict[str, Any] | None = None) -> None:
    requested = json.loads(row.requested_settings_json or "{}")
    payload = {
        "schema_version": 1,
        "run_id": row.run_id,
        "project_id": row.project_id,
        "status": status,
        "phase": row.current_phase,
        "internal_state": row.internal_state,
        "source_hash": row.source_hash,
        "output_path": row.output_path,
        "output_hash": row.output_hash,
        "provider_calls": _provider_calls_for_run(row.run_id),
        "upload_publish": {"upload": "not_performed", "publish": "not_performed"},
        "workflow_mode": requested.get("resolved_mode") or requested.get("caption_mode"),
        "result_validation": result_validation or _completed_result_validation(row),
        "subtitle_track_lineage": "canonical cue timing -> active content track resolver -> English layout -> presentation plate -> existing source-suppressed visual -> final render",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _bounded_subtitle_process(row: SimpleWorkflowRun, resolved: dict[str, Any]) -> None:
    run_dir = Path(row.run_directory)
    source = Path(row.source_path)
    log_path = run_dir / "logs" / "processing.log"
    log_path.write_text("\n".join(label for _, label in USER_STAGES), encoding="utf-8")
    subtitles_dir = run_dir / "subtitles"
    ass_path = subtitles_dir / "dialogue_subtitles_en.ass"
    if resolved.get("subtitle_provenance") in SOURCE_CAPTION_MODES:
        plan = build_source_caption_render_plan(
            source,
            resolved.get("cues", []),
            font_path=get_settings().font_path,
            subtitle_provenance=str(resolved.get("subtitle_provenance") or SOURCE_CAPTION_GEMINI_MODE),
        )
    else:
        plan = build_source_replacement_plan(source, resolved.get("cues", []), font_path=get_settings().font_path)
    use_source_mask = bool(plan["intervals"] and plan["layouts"])
    if use_source_mask:
        write_clean_subtitles_ass(
            plan["render_cues"],
            plan["layouts"],
            ass_path,
            font_size=plan["font_size"],
            output_width=plan["output_width"],
            output_height=plan["output_height"],
        )
    else:
        _write_ass_subtitles(ass_path, resolved)
    subtitles_dir.mkdir(parents=True, exist_ok=True)
    (subtitles_dir / "render_plan.json").write_text(
        json.dumps(
            {
                "intervals": plan["intervals"],
                "interval_stats": plan["interval_stats"],
                "plate_stats": plan["plate_stats"],
                "review_times": plan["review_times"],
                "subtitle_provenance": resolved.get("subtitle_provenance"),
                "ocr_model": (resolved.get("active_track") or {}).get("metadata", {}).get("ocr_model"),
                "translation_model": (resolved.get("active_track") or {}).get("metadata", {}).get("translation_model"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    expected_dialogues = len(plan.get("render_cues", [])) if use_source_mask else sum(
        1 for cue in resolved.get("cues", []) if str(cue.get("resolved_text") or cue.get("text") or "").strip()
    )
    if expected_dialogues <= 0:
        raise ValueError("Subtitle render blocked: resolved cues produced no ASS Dialogue lines.")
    output = run_dir / "output" / "final_video.mp4"
    temp_output = output.with_name(f"{output.stem}.tmp{output.suffix}")
    filter_script_path: Path | None = None
    if use_source_mask:
        filter_value = build_source_replacement_filter(plan["intervals"], plan["layouts"], ass_path)
        filter_script_path = subtitles_dir / "ffmpeg_filter_script.txt"
        filter_script_path.write_text(filter_value, encoding="utf-8")
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-filter_script:v",
            str(filter_script_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            str(temp_output),
        ]
    else:
        command = _subtitle_render_command(source, ass_path, temp_output)
    subprocess.run(command, check=True, capture_output=True, text=True)
    _validate_subtitle_render(
        source=source,
        output=temp_output,
        ass_path=ass_path,
        expected_dialogues=expected_dialogues,
        render_command=command,
        expect_masked=use_source_mask,
        filter_script_path=filter_script_path,
    )
    os.replace(temp_output, output)


def _write_ass_subtitles(ass_path: Path, resolved: dict[str, Any]) -> int:
    cues = [cue for cue in resolved.get("cues", []) if _cue_text(cue).strip() and int(cue.get("end_ms", 0)) > int(cue.get("start_ms", 0))]
    lines = [
        "[Script Info]",
        "Title: Tool Auto Sub simple workflow subtitles",
        "ScriptType: v4.00+",
        "PlayResX: 1280",
        "PlayResY: 720",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,44,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,1,2,1,2,36,36,34,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        lines.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=_ass_time(int(cue["start_ms"])),
                end=_ass_time(int(cue["end_ms"])),
                text=_ass_escape_text(_cue_text(cue)),
            )
        )
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(cues)


def _subtitle_render_command(source: Path, ass_path: Path, output: Path) -> list[str]:
    filter_value = f"subtitles='{_ffmpeg_filter_path(ass_path)}'"
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vf",
        filter_value,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(output),
    ]


def _validate_subtitle_render(
    *,
    source: Path,
    output: Path,
    ass_path: Path,
    expected_dialogues: int,
    render_command: list[str],
    expect_masked: bool = False,
    filter_script_path: Path | None = None,
) -> None:
    if not output.exists() or output.stat().st_size <= 0:
        raise ValueError("Subtitle render validation failed: output video was not created.")
    ass_text = ass_path.read_text(encoding="utf-8")
    dialogue_count = sum(1 for line in ass_text.splitlines() if line.startswith("Dialogue:"))
    if expected_dialogues > 0 and dialogue_count != expected_dialogues:
        raise ValueError("Subtitle render validation failed: ASS Dialogue count does not match resolved cues.")
    searchable = " ".join(render_command)
    if filter_script_path and filter_script_path.exists():
        searchable += " " + filter_script_path.read_text(encoding="utf-8")
    if "subtitles=" not in searchable:
        raise ValueError("Subtitle render validation failed: render command did not include subtitle burn-in.")
    if expect_masked and "delogo=" not in searchable:
        raise ValueError("Subtitle render validation failed: render command did not include source subtitle suppression.")
    if sha256_file(source) == sha256_file(output):
        raise ValueError("Subtitle render validation failed: output video is byte-identical to input.")
    source_media = media_summary(source)
    output_media = media_summary(output)
    if not output_media["video"].get("width") or not output_media["video"].get("height"):
        raise ValueError("Subtitle render validation failed: output video stream is missing.")
    if source_media["audio"].get("codec") and not output_media["audio"].get("codec"):
        raise ValueError("Subtitle render validation failed: source audio was dropped.")


def _cue_text(cue: dict[str, Any]) -> str:
    return str(cue.get("resolved_text") or cue.get("translation_text") or cue.get("text") or "")


def _ass_time(milliseconds: int) -> str:
    total_centiseconds = max(0, milliseconds) // 10
    centiseconds = total_centiseconds % 100
    total_seconds = total_centiseconds // 100
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _ass_escape_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\r\n", r"\N").replace("\n", r"\N")


def _ffmpeg_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _get_run(session, run_id: str) -> SimpleWorkflowRun:
    row = session.query(SimpleWorkflowRun).filter(SimpleWorkflowRun.run_id == run_id, SimpleWorkflowRun.is_test_fixture.is_(False)).one_or_none()
    if row is None:
        raise FileNotFoundError("Run was not found.")
    return row


def repair_invalid_completed_results() -> dict[str, Any]:
    """Downgrade legacy false-completed rows without deleting their audit artifacts."""
    repaired: list[str] = []
    with session_scope() as session:
        rows = (
            session.query(SimpleWorkflowRun)
            .filter(
                SimpleWorkflowRun.is_test_fixture.is_(False),
                SimpleWorkflowRun.internal_state.in_(["completed", "approved"]),
            )
            .all()
        )
        for row in rows:
            validation = _completed_result_validation(row)
            if validation["eligible"]:
                continue
            _invalidate_completed_result(row, validation)
            repaired.append(row.run_id)
    return {"status": "PASS", "repaired_count": len(repaired), "repaired_run_ids": repaired}


def _persist_subtitle_source_block(run_id: str, exc: Exception) -> None:
    with session_scope() as session:
        row = _get_run(session, run_id)
        requested = json.loads(row.requested_settings_json or "{}")
        is_external_asr = requested.get("caption_mode") == EXTERNAL_AUDIO_MODE
        failure = {
            "code": "autosubs_preflight_failed" if is_external_asr else "subtitle_source_unavailable",
            "message": _safe_user_failure_message(exc),
        }
        row.failure_category = "real_subtitle_content_unavailable"
        row.internal_state = "blocked"
        row.current_phase = "Create subtitles"
        row.output_path = None
        row.output_hash = None
        row.updated_at = datetime.now(timezone.utc)
        failure_path = Path(row.run_directory) / "logs" / "subtitle_source_block.json"
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_manifest(Path(row.run_directory), row, status="blocked")


def _persist_runtime_readiness_block(run_id: str, exc: Exception) -> None:
    with session_scope() as session:
        row = _get_run(session, run_id)
        failure = {"code": "runtime_readiness_failed", "message": _safe_user_failure_message(exc)}
        row.failure_category = "runtime_readiness_failed"
        row.internal_state = "blocked"
        row.current_phase = "Preparing local runtime"
        row.output_path = None
        row.output_hash = None
        row.updated_at = datetime.now(timezone.utc)
        failure_path = Path(row.run_directory) / "logs" / "runtime_readiness.json"
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_manifest(Path(row.run_directory), row, status="blocked")


def _safe_user_failure_message(exc: Exception) -> str:
    message = re.sub(
        r"(?i)(api[_-]?key|xi-api-key|authorization)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        " ".join(str(exc).split()),
    )
    return message[:500] or "Subtitle source is unavailable. Check the local transcription engine and try again."


def _persist_processing_failure(run_id: str, category: str, phase: str, exc: Exception) -> None:
    with session_scope() as session:
        row = _get_run(session, run_id)
        row.failure_category = category
        row.internal_state = "failed"
        row.current_phase = phase
        row.output_path = None
        row.output_hash = None
        row.updated_at = datetime.now(timezone.utc)
        log_path = Path(row.run_directory) / "logs" / "simple_workflow_error.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        raw_message = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        safe_message = re.sub(
            r"(?i)(api[_-]?key|xi-api-key|authorization)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            raw_message,
        )
        log_path.write_text(f"{type(exc).__name__}: {safe_message}\n", encoding="utf-8")
        _write_manifest(Path(row.run_directory), row, status="failed")


def persist_unhandled_processing_failure(run_id: str, exc: Exception) -> None:
    with session_scope() as session:
        row = _get_run(session, run_id)
        if row.internal_state != "processing":
            return
        phase = row.current_phase or "Processing"
    _persist_processing_failure(run_id, "BACKGROUND_WORKER_FAILED", phase, exc)


def _set_processing_phase(run_id: str, phase: str) -> None:
    with session_scope() as session:
        row = _get_run(session, run_id)
        row.internal_state = "processing"
        row.current_phase = phase
        row.updated_at = datetime.now(timezone.utc)
        _write_manifest(Path(row.run_directory), row, status="processing")


def _persist_resolved_mode(run_id: str, mode: str) -> None:
    with session_scope() as session:
        row = _get_run(session, run_id)
        requested = json.loads(row.requested_settings_json or "{}")
        requested["resolved_mode"] = mode
        row.requested_settings_json = json.dumps(requested, ensure_ascii=False)
        row.updated_at = datetime.now(timezone.utc)
        _write_manifest(Path(row.run_directory), row, status="processing")


def _completed_result_validation(row: SimpleWorkflowRun) -> dict[str, Any]:
    requested = json.loads(row.requested_settings_json or "{}")
    subtitle_required = requested.get("subtitle_mode") == "burned_into_video"
    is_result = row.internal_state in {"completed", "approved"} or row.failure_category == INVALID_COMPLETED_RESULT
    if not is_result:
        return {
            "status": "NOT_APPLICABLE",
            "eligible": False,
            "reason_code": None,
            "message": None,
            "subtitle_required": subtitle_required,
            "ass_dialogue_count": None,
        }
    output = Path(row.output_path) if row.output_path else None
    source = Path(row.source_path)
    ass_path = Path(row.run_directory) / "subtitles" / "dialogue_subtitles_en.ass"
    dialogue_count = _valid_ass_dialogue_count(ass_path) if subtitle_required else None

    reason_code = None
    if output is None or not output.is_file() or output.stat().st_size <= 0:
        reason_code = "output_missing"
    elif Path(row.run_directory).resolve() not in output.resolve().parents:
        reason_code = "output_outside_run"
    else:
        try:
            output_media = media_summary(output)
            if not output_media["video"].get("width") or not output_media["video"].get("height"):
                reason_code = "output_unreadable"
        except Exception:
            reason_code = "output_unreadable"

    if reason_code is None and subtitle_required and not dialogue_count:
        reason_code = "subtitle_dialogue_missing"
    if reason_code is None and subtitle_required:
        source_hash = row.source_hash.lower() if row.source_hash else (sha256_file(source).lower() if source.is_file() else "")
        output_hash = sha256_file(output).lower() if output else ""
        if source_hash and source_hash == output_hash:
            reason_code = "output_identical_to_input"
    content_validation = None
    if reason_code is None and subtitle_required:
        content_validation = _subtitle_content_validation(row, ass_path)
        if not content_validation["eligible"]:
            reason_code = content_validation["reason_code"]

    eligible = reason_code is None
    content_failure = reason_code in {
        "subtitle_content_empty",
        "subtitle_content_placeholder",
        "subtitle_provenance_invalid",
        "subtitle_timing_invalid",
        "subtitle_timing_synthetic",
    }
    return {
        "status": "PASS" if eligible else "FAIL",
        "eligible": eligible,
        "reason_code": reason_code,
        "message": None if eligible else (INVALID_SUBTITLE_CONTENT_MESSAGE if content_failure else INVALID_RESULT_MESSAGE),
        "subtitle_required": subtitle_required,
        "ass_dialogue_count": dialogue_count,
        "subtitle_content_validation": content_validation,
    }


def _subtitle_content_validation(row: SimpleWorkflowRun, ass_path: Path) -> dict[str, Any]:
    metadata = json.loads(row.source_metadata_json or "{}")
    duration_ms = max(int(float(metadata.get("duration_seconds") or 0) * 1000), 1)
    resolved_path = Path(row.run_directory) / "subtitles" / "resolved_active_track.json"
    if resolved_path.is_file():
        try:
            payload = json.loads(resolved_path.read_text(encoding="utf-8-sig"))
            if "subtitle_provenance" not in payload:
                payload["subtitle_provenance"] = _legacy_payload_provenance(payload)
            return validate_resolved_subtitle_content(
                payload,
                duration_ms=duration_ms,
                allow_test_fixture=test_fixture_context_enabled(),
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    payload = {
        "subtitle_provenance": "test_fixture" if test_fixture_context_enabled() else "unknown",
        "cues": _ass_cues(ass_path),
    }
    return validate_resolved_subtitle_content(
        payload,
        duration_ms=duration_ms,
        allow_test_fixture=test_fixture_context_enabled(),
    )


def _legacy_payload_provenance(payload: dict[str, Any]) -> str:
    active = payload.get("active_track") if isinstance(payload.get("active_track"), dict) else {}
    metadata = active.get("metadata") if isinstance(active.get("metadata"), dict) else {}
    if metadata.get("created_by") == "cp12a_default_translation_track" or active.get("source_type") in {
        "canonical",
        "test_fixture",
    }:
        return "test_fixture"
    if active.get("track_type") == "imported":
        return "user_import"
    if active.get("track_type") == "creative":
        return "user_authored"
    return "unknown"


def _ass_cues(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return []
    cues = []
    for index, line in enumerate(lines):
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            continue
        start_cs = _ass_timestamp_centiseconds(fields[1])
        end_cs = _ass_timestamp_centiseconds(fields[2])
        if start_cs is None or end_cs is None:
            continue
        text = re.sub(r"\{[^}]*\}", "", fields[9]).replace(r"\N", " ").strip()
        cues.append(
            {
                "cue_id": f"ASS_{index:04d}",
                "start_ms": start_cs * 10,
                "end_ms": end_cs * 10,
                "resolved_text": text,
            }
        )
    return cues


def _valid_ass_dialogue_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return 0
    count = 0
    for line in lines:
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        start = _ass_timestamp_centiseconds(fields[1]) if len(fields) == 10 else None
        end = _ass_timestamp_centiseconds(fields[2]) if len(fields) == 10 else None
        if len(fields) == 10 and fields[9].strip() and start is not None and end is not None and end > start:
            count += 1
    return count


def _ass_timestamp_centiseconds(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+):(\d{2}):(\d{2})\.(\d{2})\s*", value)
    if not match:
        return None
    hours, minutes, seconds, centiseconds = (int(part) for part in match.groups())
    if minutes > 59 or seconds > 59:
        return None
    return (((hours * 60) + minutes) * 60 + seconds) * 100 + centiseconds


def _invalidate_completed_result(row: SimpleWorkflowRun, validation: dict[str, Any]) -> None:
    row.internal_state = "blocked"
    row.current_phase = "Preview"
    row.approval_state = "needs_adjustment"
    row.failure_category = INVALID_COMPLETED_RESULT
    row.updated_at = datetime.now(timezone.utc)
    if Path(row.run_directory).is_dir():
        _write_manifest(Path(row.run_directory), row, status=INVALID_COMPLETED_RESULT, result_validation=validation)


def _serialize_run(row: SimpleWorkflowRun | None, **extra: Any) -> dict[str, Any]:
    if row is None:
        return {}
    source = json.loads(row.source_metadata_json)
    requested = json.loads(row.requested_settings_json)
    validation = _completed_result_validation(row)
    failure_detail = _load_failure_detail(Path(row.run_directory))
    output_url = f"/api/simple/runs/{row.run_id}/output" if row.output_path and validation["eligible"] else None
    payload = {
        "run_id": row.run_id,
        "project_id": row.project_id,
        "source": source,
        "settings": requested,
        "phase": row.current_phase,
        "internal_state": row.internal_state,
        "run_directory": row.run_directory,
        "layout": {
            "source_reference": str(Path(row.run_directory) / "source_reference.json"),
            "work": str(Path(row.run_directory) / "work"),
            "subtitles": str(Path(row.run_directory) / "subtitles"),
            "output": str(Path(row.run_directory) / "output"),
            "logs": str(Path(row.run_directory) / "logs"),
            "manifest": str(Path(row.run_directory) / "run_manifest.json"),
        },
        "stages": [{"id": stage_id, "label": label} for stage_id, label in USER_STAGES],
        "progress": _stage_progress(row.internal_state, row.current_phase),
        "output": {
            "path": row.output_path,
            "url": output_url,
            "hash": row.output_hash,
            "filename": Path(row.output_path).name if row.output_path else None,
        },
        "approval_state": row.approval_state,
        "failure_category": row.failure_category,
        "failure_detail": failure_detail,
        "result_validation": validation,
        "result_eligible": validation["eligible"],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "retry_parent_run_id": row.retry_parent_run_id,
        "provider_calls": _provider_calls_for_run(row.run_id),
        "upload_publish": {"upload": "not_performed", "publish": "not_performed"},
        "subtitle_tracks": _subtitle_track_summary(row.run_id),
        "analysis_progress": load_caption_analysis_progress(Path(row.run_directory)),
    }
    payload.update(extra)
    return payload


def _load_failure_detail(run_directory: Path) -> dict[str, str] | None:
    path = run_directory / "logs" / "subtitle_source_block.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    code = payload.get("code")
    message = payload.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        return None
    return {"code": code, "message": message}


def _subtitle_track_summary(run_id: str) -> dict[str, Any]:
    try:
        from app.services.subtitle_tracks import list_tracks

        payload = list_tracks(run_id)
        active_id = payload.get("active_track_id")
        active = next(
            (track for track in payload.get("tracks", []) if track.get("track_id") == active_id),
            None,
        )
        active_metadata = (active or {}).get("metadata") or {}
        return {
            "active_track_id": active_id,
            "operator_notice": active_metadata.get("operator_notice"),
            "automatic_caption_count": int(active_metadata.get("automatic_caption_count") or 0),
            "human_reviewed_caption_count": int(active_metadata.get("human_reviewed_caption_count") or 0),
            "tracks": [
                {
                    "track_id": track["track_id"],
                    "track_type": track["track_type"],
                    "display_name": track["display_name"],
                    "active": track["active"],
                    "fallback_policy": track["fallback_policy"],
                }
                for track in payload.get("tracks", [])
            ],
        }
    except Exception:
        return {"active_track_id": None, "tracks": []}


def _provider_calls_for_run(run_id: str) -> dict[str, int]:
    try:
        from app.services.subtitle_tracks import list_tracks

        tracks = list_tracks(run_id)
        active_id = tracks.get("active_track_id")
        active = next(
            (item for item in tracks.get("tracks", []) if item.get("track_id") == active_id),
            None,
        )
        metadata = (active or {}).get("metadata") or {}
        usage = metadata.get("provider_usage") or {}
        return {
            "gemini": int(usage.get("request_count") or 0),
            "elevenlabs": 0,
            "youtube": 0,
        }
    except Exception:
        return {"gemini": 0, "elevenlabs": 0, "youtube": 0}


def _stage_progress(state: str, phase: str | None = None) -> dict[str, Any]:
    if state in {"completed", "approved"}:
        current = "verifying_result"
        completed = [stage_id for stage_id, _ in USER_STAGES]
        status_label = None
    elif state == "processing":
        phase_map = {
            "Prepare audio": ("analysing_dialogue", ["checking_video"], "Đang chuẩn bị âm thanh"),
            "Recognize speech": ("analysing_dialogue", ["checking_video"], "Đang nhận dạng lời nói"),
            "Create subtitles": (
                "preparing_english_subtitles",
                ["checking_video", "analysing_dialogue"],
                "Đang tạo phụ đề",
            ),
            "Render video": (
                "rendering_video",
                ["checking_video", "analysing_dialogue", "preparing_english_subtitles", "cleaning_dialogue_subtitles"],
                "Đang xuất video",
            ),
        }
        current, completed, status_label = phase_map.get(
            phase,
            ("analysing_dialogue", ["checking_video"], "Đang xử lý"),
        )
    else:
        current = "checking_video"
        completed = []
        status_label = None
    return {
        "mode": "stage",
        "current_stage": current,
        "completed_stages": completed,
        "percentage": None,
        "status_label": status_label,
    }


def _resolve_safe_destination(destination_folder: str) -> Path:
    if not destination_folder or ".." in Path(destination_folder).parts:
        raise ValueError("Output destination is not safe.")
    destination = Path(destination_folder).expanduser().resolve()
    if destination.anchor and destination == Path(destination.anchor):
        raise ValueError("System root cannot be used as an output destination.")
    if destination.name.lower() in PROTECTED_DESTINATION_NAMES:
        raise ValueError("Protected system folders cannot be used as output destinations.")
    ensure_dir(destination)
    return destination


def _collision_safe_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("Could not create a collision-safe output filename.")


def _user_error(title: str, message: str, *, retry: bool) -> dict[str, Any]:
    return {
        "status": "FAIL",
        "error": {
            "title": title,
            "message": message,
            "input_safe": True,
            "retry_possible": retry,
            "recommended_next_action": "Choose another supported video file." if retry else "Open technical details.",
        },
        "supported_formats": supported_formats(),
    }
