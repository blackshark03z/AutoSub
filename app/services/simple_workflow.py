import json
import os
import re
import shutil
import subprocess
import time
import traceback
from dataclasses import replace
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
from app.providers.asr.compute_whisper_provider import ComputeWhisperASRProvider
from app.providers.compute.modal_provider import ModalComputeProvider, discover_modal_compute_config
from app.services.compute_router import choose_compute_route
from app.services.asr_models import SIMPLE_UI_MODEL_NAME, normalize_simple_ui_settings
from app.services.localization_run_config import (
    FULL_LOCALIZATION_VOICE,
    FULL_VISUAL_LOCALIZATION,
    SUBTITLE_LOCALIZATION,
    build_localization_run_snapshot,
    normalize_localization_v2_settings,
    settings_compatible_for_output,
)
from app.services.localization_quality import evaluate_english_dialogue_cues, repair_english_dialogue_cues
from app.services.offline_translation import OfflineTranslationError, translate_source_captions
from app.providers.translation.gemini import (
    GeminiCaptionTranslationError,
    GeminiCaptionTranslator,
    ensure_gemini_caption_ready,
    load_gemini_translation_config,
)
from app.services.ocr_runtime import get_ocr_runtime_status
from app.services.runtime_readiness import RuntimeReadinessError, ensure_product_runtime_ready
from app.services.full_visual_pipeline import VisualLocalizationError, execute_full_visual_localization
from app.services.narration_pipeline import NarrationFitError, generate_kokoro_narration
from app.services.narration_repair import NarrationProjectionRepairError, repair_narration_spoken_projection
from app.services.subtitle_projection_repair import SubtitleProjectionRepairError, repair_subtitle_display_projection
from app.services.visual_ocr_runner import build_routed_whole_frame_ocr_runner, run_local_whole_frame_ocr
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
    ("checking_runtime", "Checking local capabilities"),
    ("downloading_autosubs", "Downloading AutoSubs"),
    ("preparing_autosubs_model", "Preparing AutoSubs small model"),
    ("preparing_translation", "Preparing offline Chinese to English translation"),
    ("runtime_ready", "Local capabilities ready"),
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
    "localization_mode": SUBTITLE_LOCALIZATION,
    "translation_policy": "quality_auto",
    "subtitle_quality_profile": "english_v2",
    "compute_policy": "auto",
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
    
    return settings_compatible_for_output(existing_settings, requested)


def create_or_reuse_run(path_value: str, settings: dict[str, Any] | None = None, retry_parent_run_id: str | None = None) -> dict[str, Any]:
    source = _resolve_source(path_value)
    source_hash = sha256_file(source)
    metadata = _source_metadata(source, source_hash=source_hash)
    requested = normalize_localization_v2_settings(
        normalize_simple_ui_settings(DEFAULT_SETTINGS | (settings or {}))
    )
    requested_localization_mode = str(requested.get("localization_mode") or SUBTITLE_LOCALIZATION)
    if requested_localization_mode in {FULL_VISUAL_LOCALIZATION, FULL_LOCALIZATION_VOICE} and os.environ.get(
        "AUTOSUB_ENABLE_EXPERIMENTAL_LOCALIZATION_V2", ""
    ).strip() != "1":
        raise ValueError(
            "Full Visual/Voice localization is not exposed yet because the composed visual replacement and final QC path "
            "is still being implemented. Subtitle Localization remains available."
        )
    
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


def _load_render_resume_checkpoint(run_id: str, requested: dict[str, Any]) -> dict[str, Any] | None:
    """Reuse only a validated render-ready checkpoint from a failed parent run.

    The failed parent remains immutable evidence. A retry child may skip source
    analysis, subtitle repair, and narration only when all render inputs were
    already accepted and remain hash/settings compatible with the child.
    """
    with session_scope() as session:
        row = _get_run(session, run_id)
        parent_id = str(row.retry_parent_run_id or "").strip()
        if not parent_id:
            return None
        parent = session.query(SimpleWorkflowRun).filter(SimpleWorkflowRun.run_id == parent_id).one_or_none()
        if parent is None:
            return None
        if parent.project_id != row.project_id or parent.source_hash != row.source_hash:
            return None
        if parent.failure_category not in {"render_failed", "runtime_interrupted", "BACKGROUND_WORKER_FAILED", "visual_localization_failed"}:
            return None
        if not _settings_compatible_for_reuse(parent, requested):
            return None
        child_project_id = str(row.project_id)
        child_source_hash = str(row.source_hash)
        parent_run_directory = Path(parent.run_directory).resolve()
        parent_metadata = json.loads(parent.source_metadata_json or "{}")

    checkpoint_path = parent_run_directory / "subtitles" / "resolved_active_track.json"
    if not checkpoint_path.is_file():
        return None
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    cues = list(payload.get("cues") or []) if isinstance(payload, dict) else []
    if not isinstance(payload, dict) or not cues:
        return None
    duration_ms = max(int(float(parent_metadata.get("duration_seconds") or 0) * 1000), 1)
    validation = validate_resolved_subtitle_content(
        payload,
        duration_ms=duration_ms,
        allow_test_fixture=test_fixture_context_enabled(),
    )
    if validation.get("eligible") is not True:
        return None

    localization_mode = str(requested.get("localization_mode") or SUBTITLE_LOCALIZATION)
    resume_checkpoint: dict[str, Any] = {
        "kind": "render_only",
        "parent_run_id": parent_id,
        "source_hash": child_source_hash,
    }
    if localization_mode in {FULL_VISUAL_LOCALIZATION, FULL_LOCALIZATION_VOICE}:
        quality_path = parent_run_directory / "subtitles" / "localization_quality_evidence.json"
        quality = _read_json_object(quality_path)
        if not quality or quality.get("status") != "PASS" or quality.get("eligible") is not True:
            return None
        if int(quality.get("cue_count") or 0) != len(cues):
            return None
        resume_checkpoint["subtitle_quality"] = {
            "path": str(quality_path),
            "status": "PASS",
            "cue_count": len(cues),
        }

    if localization_mode == FULL_LOCALIZATION_VOICE:
        manifest_path = parent_run_directory / "audio" / "narration_manifest.json"
        narration = _read_json_object(manifest_path)
        expected_mix = (parent_run_directory / "audio" / "narration_mix.wav").resolve()
        if not narration or narration.get("status") != "PASS" or not expected_mix.is_file():
            return None
        try:
            manifest_mix = Path(str(narration.get("mix_path") or "")).resolve()
        except (OSError, RuntimeError):
            return None
        if manifest_mix != expected_mix:
            return None
        mix_sha256 = str(narration.get("mix_sha256") or "").lower()
        if not mix_sha256 or mix_sha256 != sha256_file(expected_mix).lower():
            return None
        if str(narration.get("provider") or "") != "kokoro_local":
            return None
        if str(narration.get("voice_id") or "") != str(requested.get("voice_id") or ""):
            return None
        try:
            speed_matches = abs(float(narration.get("base_speed")) - float(requested.get("voice_speed"))) <= 0.0005
        except (TypeError, ValueError):
            speed_matches = False
        if not speed_matches:
            return None
        expected_cues = int(narration.get("expected_retimed_cue_count") or 0)
        actual_cues = int(narration.get("retimed_cue_count") or 0)
        if expected_cues != len(cues) or actual_cues != expected_cues:
            return None
        resume_checkpoint["narration"] = {
            "manifest_path": str(manifest_path),
            "mix_path": str(expected_mix),
            "mix_sha256": mix_sha256,
        }

    resumed = dict(payload)
    resumed["run_id"] = run_id
    active_track = resumed.get("active_track")
    if isinstance(active_track, dict):
        rebound_track = dict(active_track)
        rebound_track["run_id"] = run_id
        rebound_track["project_id"] = child_project_id
        resumed["active_track"] = rebound_track
    resumed["content_validation"] = validation
    resumed["resume_checkpoint"] = resume_checkpoint
    return resumed


def _materialize_render_resume_narration(
    run_directory: Path,
    resolved: dict[str, Any],
    requested: dict[str, Any],
) -> Path:
    checkpoint = dict(resolved.get("resume_checkpoint") or {})
    narration_resume = dict(checkpoint.get("narration") or {})
    if checkpoint.get("kind") != "render_only" or not narration_resume:
        raise ValueError("Render resume narration checkpoint is missing.")
    source_manifest_path = Path(str(narration_resume.get("manifest_path") or "")).resolve()
    source_mix = Path(str(narration_resume.get("mix_path") or "")).resolve()
    expected_hash = str(narration_resume.get("mix_sha256") or "").lower()
    source_manifest = _read_json_object(source_manifest_path)
    if not source_manifest or source_manifest.get("status") != "PASS" or not source_mix.is_file():
        raise ValueError("Render resume narration artifact is unavailable.")
    if not expected_hash or sha256_file(source_mix).lower() != expected_hash:
        raise ValueError("Render resume narration hash changed after checkpoint validation.")
    if str(source_manifest.get("voice_id") or "") != str(requested.get("voice_id") or ""):
        raise ValueError("Render resume narration voice does not match the child run.")
    try:
        speed_matches = abs(float(source_manifest.get("base_speed")) - float(requested.get("voice_speed"))) <= 0.0005
    except (TypeError, ValueError):
        speed_matches = False
    if not speed_matches:
        raise ValueError("Render resume narration speed does not match the child run.")

    audio_dir = Path(run_directory) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    child_mix = audio_dir / "narration_mix.wav"
    shutil.copy2(source_mix, child_mix)
    child_hash = sha256_file(child_mix).lower()
    if child_hash != expected_hash:
        child_mix.unlink(missing_ok=True)
        raise ValueError("Copied render resume narration failed hash verification.")
    child_manifest = dict(source_manifest)
    child_manifest.pop("acceptance_binding", None)
    child_manifest["mix_path"] = str(child_mix.resolve())
    child_manifest["mix_sha256"] = child_hash
    child_manifest["resume_checkpoint"] = {
        "kind": "parent_narration_artifact",
        "parent_run_id": checkpoint.get("parent_run_id"),
        "source_mix_sha256": expected_hash,
    }
    (audio_dir / "narration_manifest.json").write_text(
        json.dumps(child_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    repair_source = source_manifest_path.parent / "narration_repair_evidence.json"
    if repair_source.is_file():
        shutil.copy2(repair_source, audio_dir / "narration_repair_evidence.json")
    logs_dir = Path(run_directory) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "render_resume_evidence.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "kind": "render_only",
                "parent_run_id": checkpoint.get("parent_run_id"),
                "narration_reused": True,
                "narration_mix_sha256": child_hash,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return child_mix


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
        _write_localization_run_snapshot(Path(row.run_directory), row, requested)
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
            normalized_requested = normalize_localization_v2_settings(normalize_simple_ui_settings(requested))
            if normalized_requested != requested:
                requested = normalized_requested
                row.requested_settings_json = json.dumps(requested, ensure_ascii=False)
                _write_manifest(Path(row.run_directory), row, status="processing")

        resolved = _load_render_resume_checkpoint(run_id, requested)
        if resolved is None:
            provenance = active_track_provenance(run_id)
            if (
                not test_fixture_context_enabled()
                and provenance not in {"user_import", "user_authored", "local_transcription", "provider_transcription"}
                and provenance not in SOURCE_CAPTION_MODES
            ):
                requested_mode = str(requested.get("caption_mode") or LOCAL_AUDIO_MODE)
                if requested_mode == SOURCE_CAPTION_MODE:
                    _set_processing_phase(run_id, "Check OCR runtime")
                    ocr_status = get_ocr_runtime_status()
                    if ocr_status.get("available") is not True:
                        _persist_ocr_runtime_block(run_id, ocr_status)
                        raise RuntimeReadinessBlockedError(
                            str(ocr_status.get("actionable_fix_message") or "Bộ đọc phụ đề OCR chưa sẵn sàng.")
                        )
                    _set_processing_phase(run_id, "Check Gemini")
                    try:
                        ensure_gemini_caption_ready()
                    except GeminiCaptionTranslationError as exc:
                        _persist_gemini_runtime_block(run_id, exc)
                        raise RuntimeReadinessBlockedError(str(exc)) from exc
                    _set_processing_phase(run_id, "Read and translate embedded captions")
                    try:
                        source_caption = run_caption_analysis_worker(source_path, run_directory)
                    except CaptionAnalysisError as exc:
                        _persist_processing_failure(run_id, exc.code, "Read and translate embedded captions", exc)
                        raise
                    except Exception as exc:
                        _persist_processing_failure(
                            run_id,
                            "EMBEDDED_CAPTION_ANALYSIS_FAILED",
                            "Read and translate embedded captions",
                            exc,
                        )
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
                    _set_processing_phase(run_id, "Prepare audio")
                    try:
                        ensure_product_runtime_ready(
                            get_settings().root,
                            progress=lambda state, message: _set_runtime_readiness_phase(run_id, state, message),
                        )
                    except RuntimeReadinessError as exc:
                        _persist_runtime_readiness_block(run_id, exc)
                        raise RuntimeReadinessBlockedError(str(exc)) from exc
                    _set_processing_phase(run_id, "Recognize speech")
                    external_result = _ensure_external_transcription_with_quality_route(
                        run_id,
                        source_path=source_path,
                        run_directory=run_directory,
                        source_duration_seconds=float(source_metadata.get("duration_seconds") or 0),
                        target_language=str(requested.get("target_language") or "English"),
                        source_language=requested.get("source_language"),
                        requested=requested,
                    )
                    if external_result.get("status") == "PASS" and not _same_language(
                        external_result.get("metadata", {}).get("source_language"), requested.get("target_language"),
                    ):
                        _create_source_result_translation_track(
                            run_id,
                            external_result,
                            str(requested.get("target_language") or "English"),
                            requested,
                        )
                    _persist_resolved_mode(run_id, EXTERNAL_AUDIO_MODE)
                elif requested_mode == LOCAL_AUDIO_MODE:
                    _set_processing_phase(run_id, "Prepare audio")
                    _set_processing_phase(run_id, "Recognize speech")
                    local_result = ensure_local_transcription_track(
                        run_id,
                        source_path=source_path,
                        run_directory=run_directory,
                        source_duration_seconds=float(source_metadata.get("duration_seconds") or 0),
                        target_language=str(requested.get("target_language") or "English"),
                        source_language=requested.get("source_language"),
                        model_name=SIMPLE_UI_MODEL_NAME,
                    )
                    if (
                        _quality_translation_v2_enabled(requested)
                        and local_result.get("status") == "PASS"
                        and not _same_language(
                            local_result.get("metadata", {}).get("source_language"), requested.get("target_language"),
                        )
                    ):
                        _create_source_result_translation_track(
                            run_id,
                            local_result,
                            str(requested.get("target_language") or "English"),
                            requested,
                        )
                    _persist_resolved_mode(run_id, LOCAL_AUDIO_MODE)
                else:
                    raise SourceCaptionUnavailableError("Unsupported caption workflow mode")
            resolved = resolved_cues(run_id)
        localization_mode = str(requested.get("localization_mode") or SUBTITLE_LOCALIZATION)
        resume_checkpoint = dict(resolved.get("resume_checkpoint") or {}) if isinstance(resolved, dict) else {}
        render_resume = resume_checkpoint.get("kind") == "render_only"
        source_duration_ms = max(1, round(float(source_metadata.get("duration_seconds") or 0) * 1000))
        render_resolved = resolved
        quality_repair_actions: list[dict[str, Any]] = []
        subtitle_projection_overrides: dict[str, dict[str, Any]] = {}
        quality_enforced = _subtitle_quality_v2_enforced(requested)
        pre_repair = (
            _bounded_quality_repair(render_resolved, requested, source_duration_ms=source_duration_ms, allow_split=True)
            if quality_enforced and not render_resume
            else None
        )
        if pre_repair is not None:
            render_resolved, repair_result = pre_repair
            quality_repair_actions.extend(list(repair_result.get("actions") or []))
            if repair_result.get("eligible") is not True:
                projection_path = run_directory / "subtitles" / "subtitle_projection_repair_evidence.json"
                projection_error: Exception | None = None
                try:
                    if str(requested.get("translation_policy") or "quality_auto").strip().lower() != "quality_auto":
                        raise SubtitleProjectionRepairError(
                            "subtitle_projection_repair_policy_blocked",
                            "Display condensation requires translation_policy=quality_auto.",
                        )
                    readiness = ensure_gemini_caption_ready()
                    selected_model = str(readiness.get("selected_model") or "").strip()
                    if not selected_model:
                        raise SubtitleProjectionRepairError(
                            "subtitle_projection_repair_provider_unavailable",
                            "No compatible Gemini model is ready for bounded subtitle display repair.",
                        )
                    projection_translator = GeminiCaptionTranslator(
                        replace(load_gemini_translation_config(), model=selected_model)
                    )
                    projection = repair_subtitle_display_projection(
                        render_resolved,
                        dict(repair_result.get("evaluation") or {}),
                        translator=projection_translator,
                    )
                    projection_path.parent.mkdir(parents=True, exist_ok=True)
                    projection_path.write_text(
                        json.dumps(projection["evidence"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    render_resolved = projection["resolved"]
                    subtitle_projection_overrides = {
                        str(cue.get("cue_id") or cue.get("id") or ""): {
                            key: cue.get(key)
                            for key in ("resolved_text", "subtitle_text", "render_text", "subtitle_projection_repaired")
                            if key in cue
                        }
                        for cue in list(render_resolved.get("cues") or [])
                        if cue.get("subtitle_projection_repaired")
                    }
                    projection_repair = _bounded_quality_repair(
                        render_resolved,
                        requested,
                        source_duration_ms=source_duration_ms,
                        allow_split=True,
                    )
                    if projection_repair is not None:
                        render_resolved, repair_result = projection_repair
                        quality_repair_actions.extend(list(repair_result.get("actions") or []))
                except (SubtitleProjectionRepairError, GeminiCaptionTranslationError, ValueError) as exc:
                    projection_error = exc
                    evidence = getattr(exc, "evidence", None)
                    projection_path.parent.mkdir(parents=True, exist_ok=True)
                    projection_path.write_text(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "status": "FAIL",
                                "code": getattr(exc, "code", type(exc).__name__),
                                "message": _safe_user_failure_message(exc),
                                "evidence": evidence if isinstance(evidence, dict) else {},
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                if repair_result.get("eligible") is not True:
                    _write_localization_quality_evidence(
                        run_directory,
                        render_resolved,
                        requested,
                        enforced=True,
                        repair_actions=quality_repair_actions,
                        evaluation_override=repair_result.get("evaluation"),
                    )
                    exc = ValueError("Subtitle quality remained outside the frozen V2 hard gates after bounded repair.")
                    if projection_error is not None:
                        exc.__cause__ = projection_error
                    _persist_processing_failure(run_id, "subtitle_quality_failed", "Create subtitles", exc)
                    raise exc
        narration_audio: Path | None = None
        if localization_mode == FULL_LOCALIZATION_VOICE and render_resume:
            narration_audio = _materialize_render_resume_narration(run_directory, render_resolved, requested)
        if localization_mode == FULL_LOCALIZATION_VOICE and not render_resume:
            _set_processing_phase(run_id, "Generate narration")
            narration_input = resolved
            try:
                narration = generate_kokoro_narration(
                    project_id=row.project_id,
                    resolved=narration_input,
                    source_duration_ms=source_duration_ms,
                    voice_id=str(requested.get("voice_id") or ""),
                    base_speed=float(requested.get("voice_speed") or 1.0),
                    run_directory=run_directory,
                )
            except NarrationFitError as initial_fit_error:
                try:
                    readiness = ensure_gemini_caption_ready()
                    selected_model = str(readiness.get("selected_model") or "").strip()
                    if not selected_model:
                        raise NarrationProjectionRepairError(
                            "narration_repair_provider_unavailable",
                            "No compatible Gemini model is ready for bounded narration repair.",
                        )
                    repair_translator = GeminiCaptionTranslator(
                        replace(load_gemini_translation_config(), model=selected_model)
                    )
                    repair = repair_narration_spoken_projection(
                        narration_input,
                        initial_fit_error.failures,
                        source_duration_ms=source_duration_ms,
                        translator=repair_translator,
                    )
                    audio_dir = run_directory / "audio"
                    audio_dir.mkdir(parents=True, exist_ok=True)
                    (audio_dir / "narration_repair_evidence.json").write_text(
                        json.dumps(repair["evidence"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    narration_input = repair["resolved"]
                    narration = generate_kokoro_narration(
                        project_id=row.project_id,
                        resolved=narration_input,
                        source_duration_ms=source_duration_ms,
                        voice_id=str(requested.get("voice_id") or ""),
                        base_speed=float(requested.get("voice_speed") or 1.0),
                        run_directory=run_directory,
                    )
                except (NarrationFitError, NarrationProjectionRepairError, GeminiCaptionTranslationError, ValueError) as exc:
                    evidence = getattr(exc, "evidence", None)
                    if isinstance(evidence, dict) and evidence:
                        audio_dir = run_directory / "audio"
                        audio_dir.mkdir(parents=True, exist_ok=True)
                        (audio_dir / "narration_repair_evidence.json").write_text(
                            json.dumps({"schema_version": 1, "status": "FAIL", **evidence}, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    _persist_processing_failure(run_id, "narration_quality_failed", "Generate narration", exc)
                    raise ValueError("Generated narration did not satisfy the run quality contract.") from exc
            except ValueError as exc:
                _persist_processing_failure(run_id, "narration_quality_failed", "Generate narration", exc)
                raise ValueError("Generated narration did not satisfy the run quality contract.") from exc
            render_resolved = dict(narration["resolved"])
            narration_audio = Path(narration["mix_path"])
            if subtitle_projection_overrides:
                merged_cues: list[dict[str, Any]] = []
                for narration_cue in list(render_resolved.get("cues") or []):
                    merged = dict(narration_cue)
                    cue_id = str(merged.get("cue_id") or merged.get("id") or "")
                    override = subtitle_projection_overrides.get(cue_id)
                    if override:
                        merged.update(override)
                    merged_cues.append(merged)
                render_resolved = {**render_resolved, "cues": merged_cues}
            post_repair = _bounded_quality_repair(
                render_resolved,
                requested,
                source_duration_ms=source_duration_ms,
                allow_split=False,
            )
            if post_repair is not None:
                render_resolved, repair_result = post_repair
                quality_repair_actions.extend(list(repair_result.get("actions") or []))
                if repair_result.get("eligible") is not True:
                    _write_localization_quality_evidence(
                        run_directory,
                        render_resolved,
                        requested,
                        enforced=True,
                        repair_actions=quality_repair_actions,
                        evaluation_override=repair_result.get("evaluation"),
                    )
                    exc = ValueError("Narration-aligned subtitles remained outside the frozen V2 hard gates.")
                    _persist_processing_failure(run_id, "subtitle_quality_failed", "Create subtitles", exc)
                    raise exc

        _set_processing_phase(run_id, "Create subtitles")
        (run_directory / "subtitles" / "resolved_active_track.json").write_text(
            json.dumps(render_resolved, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _write_localization_quality_evidence(
            run_directory,
            render_resolved,
            requested,
            enforced=quality_enforced,
            repair_actions=quality_repair_actions,
        )

        visual_translator: GeminiCaptionTranslator | None = None
        if localization_mode in {FULL_VISUAL_LOCALIZATION, FULL_LOCALIZATION_VOICE}:
            visual_translator = _prepare_full_visual_runtime(run_id, requested)

        failure_phase = "Render video"
        _set_processing_phase(run_id, failure_phase)
        try:
            with session_scope() as session:
                row = _get_run(session, run_id)
                if localization_mode == SUBTITLE_LOCALIZATION:
                    _bounded_subtitle_process(row, render_resolved)
                else:
                    subtitle_stage = run_directory / "work" / "subtitle_stage.mp4"
                    _bounded_subtitle_process(
                        row,
                        render_resolved,
                        output_override=subtitle_stage,
                        audio_override=narration_audio,
                    )
                    assert visual_translator is not None
                    failure_phase = "Localize visual text"
                    _set_processing_phase(run_id, failure_phase)
                    visual_ocr_runner = build_routed_whole_frame_ocr_runner(
                        root=get_settings().root,
                        run_directory=run_directory,
                        compute_policy=str(requested.get("compute_policy") or "auto"),
                    )
                    try:
                        execute_full_visual_localization(
                            subtitle_stage,
                            run_directory,
                            run_directory / "output" / "final_video.mp4",
                            ocr_runner=visual_ocr_runner,
                            translator=visual_translator,
                            font_path=get_settings().font_path,
                            render_runner=lambda command, duration: _run_ffmpeg_with_progress(
                                command,
                                run_dir=run_directory,
                                duration_seconds=duration,
                            ),
                            target_language=str(requested.get("target_language") or "English"),
                        )
                    finally:
                        visual_ocr_runner.close()
                failure_phase = "Verify result"
                row.completed_at = datetime.now(timezone.utc)
                row.updated_at = row.completed_at
                row.current_phase = "Preview"
                row.internal_state = "completed"
                row.output_path = str(Path(row.run_directory) / "output" / "final_video.mp4")
                row.output_hash = sha256_file(Path(row.output_path))
                _bind_localization_v2_acceptance_evidence(row, requested)
                validation = _completed_result_validation(row)
                if not validation["eligible"]:
                    _invalidate_completed_result(row, validation)
                    raise InvalidCompletedResultError(validation["message"])
                _write_manifest(Path(row.run_directory), row, status="completed")
                return _serialize_run(row)
        except InvalidCompletedResultError:
            raise
        except Exception as exc:
            category = "render_failed"
            phase = failure_phase
            user_message = RENDER_FAILED_MESSAGE
            if isinstance(exc, GeminiCaptionTranslationError):
                category = str(exc.reason_code or "GEMINI_CAPTION_PROVIDER_UNAVAILABLE")
                phase = "Localize visual text" if failure_phase == "Localize visual text" else failure_phase
                user_message = (
                    "Gemini tạm thời không khả dụng; AutoSub đã dừng có kiểm soát và giữ nguyên dữ liệu cục bộ."
                    if category == "GEMINI_CAPTION_PROVIDER_UNAVAILABLE"
                    else "Bước dịch bằng Gemini không vượt qua kiểm tra an toàn; AutoSub đã dừng thay vì xuất kết quả sai."
                )
            elif isinstance(exc, VisualLocalizationError):
                category = str(exc.code or "visual_localization_failed")
                phase = "Localize visual text"
                user_message = "Bản địa hóa chữ trong khung hình chưa đạt điều kiện chất lượng; dữ liệu đã được giữ để chẩn đoán hoặc thử lại."
            elif failure_phase == "Localize visual text":
                category = "visual_localization_failed"
                user_message = "Bản địa hóa chữ trong khung hình chưa hoàn tất; dữ liệu trung gian đã được giữ để thử lại."
            elif failure_phase == "Verify result":
                category = "result_validation_failed"
                user_message = "Video đã xử lý nhưng chưa vượt qua kiểm tra kết quả cuối, nên AutoSub không đánh dấu hoàn tất."
            _persist_processing_failure(run_id, category, phase, exc)
            raise ValueError(user_message) from exc
    except RuntimeReadinessBlockedError:
        raise
    except (SubtitleContentUnavailableError, SourceCaptionUnavailableError) as exc:
        _persist_subtitle_source_block(run_id, exc)
        raise


def _prepare_full_visual_runtime(
    run_id: str,
    requested: dict[str, Any],
) -> GeminiCaptionTranslator:
    if str(requested.get("translation_policy") or "quality_auto").strip().lower() != "quality_auto":
        raise ValueError("Full Visual V2 currently requires translation_policy=quality_auto.")
    _set_processing_phase(run_id, "Check OCR runtime")
    ocr_status = get_ocr_runtime_status()
    if ocr_status.get("available") is not True:
        _persist_ocr_runtime_block(run_id, ocr_status)
        raise RuntimeReadinessBlockedError(
            str(ocr_status.get("actionable_fix_message") or "Bộ đọc chữ trong khung hình chưa sẵn sàng.")
        )
    _set_processing_phase(run_id, "Check Gemini")
    try:
        readiness = ensure_gemini_caption_ready()
    except GeminiCaptionTranslationError as exc:
        _persist_gemini_runtime_block(run_id, exc)
        raise RuntimeReadinessBlockedError(str(exc)) from exc
    selected_model = str(readiness.get("selected_model") or "").strip()
    if not selected_model:
        raise RuntimeReadinessBlockedError("No compatible Gemini model is ready for visual localization.")
    config = replace(load_gemini_translation_config(), model=selected_model)
    return GeminiCaptionTranslator(config)


def _same_language(source_language: Any, target_language: Any) -> bool:
    aliases = {"english": "en", "en-us": "en", "en-gb": "en", "chinese": "zh", "zh-cn": "zh", "zh-hans": "zh"}
    source = aliases.get(str(source_language or "").strip().lower(), str(source_language or "").strip().lower())
    target = aliases.get(str(target_language or "").strip().lower(), str(target_language or "").strip().lower())
    return bool(source and source == target)


def _quality_asr_v2_enabled(requested: dict[str, Any]) -> bool:
    mode = str(requested.get("localization_mode") or SUBTITLE_LOCALIZATION)
    return mode in {FULL_VISUAL_LOCALIZATION, FULL_LOCALIZATION_VOICE} or os.environ.get(
        "AUTOSUB_ENABLE_QUALITY_ASR_V2", ""
    ).strip() == "1"


def _write_asr_compute_route(run_directory: Path, payload: dict[str, Any]) -> None:
    path = Path(run_directory) / "logs" / "asr_compute_route.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _ensure_external_transcription_with_quality_route(
    run_id: str,
    *,
    source_path: Path,
    run_directory: Path,
    source_duration_seconds: float,
    target_language: str,
    source_language: str | None,
    requested: dict[str, Any],
) -> dict[str, Any]:
    base_kwargs = {
        "source_path": source_path,
        "run_directory": run_directory,
        "source_duration_seconds": source_duration_seconds,
        "target_language": target_language,
        "source_language": source_language,
    }
    if not _quality_asr_v2_enabled(requested):
        return ensure_external_transcription_track(run_id, **base_kwargs)

    duration = max(0.0, float(source_duration_seconds or 0))
    modal_provider = ModalComputeProvider(discover_modal_compute_config(get_settings().root))
    capabilities = modal_provider.capabilities()
    # Conservative E2E estimates derived from the real 45 s qualification run.
    # They include cold/provider overhead rather than comparing GPU inference alone.
    local_estimated = max(5.0, duration * 0.96)
    cloud_estimated = 42.0 + duration * 0.17
    decision = choose_compute_route(
        policy=str(requested.get("compute_policy") or "auto"),
        workload="asr",
        local_estimated_seconds=local_estimated,
        cloud_estimated_seconds=cloud_estimated,
        provider_capabilities=[capabilities],
    )
    route_evidence: dict[str, Any] = {
        "schema_version": 1,
        "workload": "asr",
        "policy": str(requested.get("compute_policy") or "auto"),
        "route": decision.route,
        "provider": decision.provider,
        "reason": decision.reason,
        "local_estimated_seconds": decision.local_estimated_seconds,
        "cloud_estimated_seconds": decision.cloud_estimated_seconds,
        "no_paid_overage_verified": bool(capabilities.no_paid_overage_verified),
        "fallback": False,
    }
    if decision.route != "cloud":
        _write_asr_compute_route(run_directory, route_evidence)
        return ensure_external_transcription_track(run_id, **base_kwargs)

    try:
        result = ensure_external_transcription_track(
            run_id,
            **base_kwargs,
            provider_factory=lambda: ComputeWhisperASRProvider(
                modal_provider,
                Path(run_directory) / "work" / "compute" / "asr",
            ),
        )
        route_evidence["actual_provider"] = "whisper_compute"
        _write_asr_compute_route(run_directory, route_evidence)
        return result
    except SubtitleContentUnavailableError as exc:
        # Provider outage is not run corruption. Retry the same semantic source
        # operation locally and record the downgrade rather than hiding it.
        route_evidence.update(
            route="local",
            actual_provider="autosubs",
            fallback=True,
            fallback_reason=type(exc).__name__,
        )
        _write_asr_compute_route(run_directory, route_evidence)
        return ensure_external_transcription_track(run_id, **base_kwargs)


def _subtitle_quality_v2_enforced(requested: dict[str, Any]) -> bool:
    mode = str(requested.get("localization_mode") or SUBTITLE_LOCALIZATION)
    return mode in {FULL_VISUAL_LOCALIZATION, FULL_LOCALIZATION_VOICE} or os.environ.get(
        "AUTOSUB_ENABLE_SUBTITLE_QUALITY_V2", ""
    ).strip() == "1"


def _quality_translation_v2_enabled(requested: dict[str, Any]) -> bool:
    mode = str(requested.get("localization_mode") or SUBTITLE_LOCALIZATION)
    return mode in {FULL_VISUAL_LOCALIZATION, FULL_LOCALIZATION_VOICE} or os.environ.get(
        "AUTOSUB_ENABLE_QUALITY_TRANSLATION_V2", ""
    ).strip() == "1"


def _create_source_result_translation_track(
    run_id: str,
    source_result: dict[str, Any],
    target_language: str,
    requested: dict[str, Any],
) -> dict[str, Any]:
    source_cues = list(source_result.get("cues") or [])
    source_texts = [str(cue.get("text") or cue.get("source_text") or "").strip() for cue in source_cues]
    if not source_cues or any(not text for text in source_texts):
        raise SubtitleContentUnavailableError("Source transcript cannot be translated because one or more cues are empty.")

    policy = str(requested.get("translation_policy") or "quality_auto").strip().lower()
    target_is_english = str(target_language or "").strip().lower() in {"english", "en", "en-us", "en-gb"}
    gemini_failure: str | None = None
    if policy == "quality_auto" and target_is_english and _quality_translation_v2_enabled(requested):
        try:
            readiness = ensure_gemini_caption_ready()
            selected_model = str(readiness.get("selected_model") or "").strip()
            if not selected_model:
                raise GeminiCaptionTranslationError("GEMINI_MODEL_UNAVAILABLE", "No compatible Gemini translation model is ready.")
            config = replace(load_gemini_translation_config(), model=selected_model)
            translator = GeminiCaptionTranslator(config)
            captions = []
            for index, (cue, text) in enumerate(zip(source_cues, source_texts)):
                start_ms = max(0, int(cue.get("start_ms") or 0))
                end_ms = max(start_ms + 1, int(cue.get("end_ms") or start_ms + 1))
                duration_seconds = max(0.001, (end_ms - start_ms) / 1000.0)
                captions.append({
                    "id": str(cue.get("cue_id") or cue.get("id") or f"ASR_{index + 1:05d}"),
                    "source_text": text,
                    "previous_source_text": source_texts[index - 1] if index > 0 else "",
                    "next_source_text": source_texts[index + 1] if index + 1 < len(source_texts) else "",
                    "duration_ms": str(end_ms - start_ms),
                    "preferred_max_characters": str(max(1, int(duration_seconds * 17.0))),
                    "hard_max_characters": str(max(1, int(duration_seconds * 20.0))),
                })
            result = translator.translate(captions)
            translated_texts = [str(item.get("english") or "").strip() for item in result.translations]
            return create_provider_translation_track(
                run_id,
                source_cues=source_cues,
                translated_texts=translated_texts,
                metadata={
                    **dict(source_result.get("metadata") or {}),
                    "translation_provider": translator.provider_name,
                    "translation_model": result.model,
                    "translation_policy": policy,
                    "translation_fallback": False,
                    "translation_request_count": result.request_count,
                    "translation_retry_count": result.retry_count,
                    "translation_cache_hits": result.cache_hits,
                    "translation_cache_misses": result.cache_misses,
                    "target_language": target_language,
                },
            )
        except GeminiCaptionTranslationError as exc:
            gemini_failure = exc.reason_code
        except (FileNotFoundError, ValueError):
            gemini_failure = "GEMINI_NOT_CONFIGURED"

    try:
        translated = translate_source_captions(source_texts)
    except OfflineTranslationError as exc:
        if gemini_failure:
            raise SubtitleContentUnavailableError(
                f"Quality translation was unavailable ({gemini_failure}) and the explicit local fallback is also unavailable."
            ) from exc
        raise SubtitleContentUnavailableError(
            f"Local translation to {target_language} is unavailable. Install the existing local translation runtime and retry."
        ) from exc
    return create_provider_translation_track(
        run_id,
        source_cues=source_cues,
        translated_texts=[str(item.get("translated_text") or "") for item in translated],
        metadata={
            **dict(source_result.get("metadata") or {}),
            "translation_provider": "offline_translation",
            "translation_policy": policy,
            "translation_fallback": bool(gemini_failure),
            "translation_fallback_reason": gemini_failure,
            "target_language": target_language,
        },
    )


def _create_external_translation_track(run_id: str, external_result: dict[str, Any], target_language: str) -> dict[str, Any]:
    """Compatibility wrapper for older callers; public V2 paths use the policy-aware helper."""
    return _create_source_result_translation_track(
        run_id,
        external_result,
        target_language,
        {"localization_mode": SUBTITLE_LOCALIZATION, "translation_policy": "local_only"},
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


def _launch_output_folder(folder: Path) -> None:
    if os.name != "nt" or not hasattr(os, "startfile"):
        raise OSError("Opening the output folder is supported by the Windows product runtime only.")
    os.startfile(str(folder))


def open_output_folder(run_id: str) -> dict[str, Any]:
    location = output_location(run_id)
    folder = Path(location["folder"])
    try:
        _launch_output_folder(folder)
    except OSError as exc:
        raise ValueError("Không thể mở thư mục kết quả trên Windows. Bạn vẫn có thể sao chép đường dẫn ở phần tùy chọn kết quả.") from exc
    return {**location, "opened": True}


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


def _write_localization_run_snapshot(run_dir: Path, row: SimpleWorkflowRun, requested: dict[str, Any]) -> dict[str, Any]:
    snapshot = build_localization_run_snapshot(requested)
    path = run_dir / "run_snapshot.json"
    payload = {
        "schema_version": 1,
        "run_id": row.run_id,
        "project_id": row.project_id,
        "source_hash": row.source_hash,
        "settings_sha256": snapshot.sha256,
        "settings": snapshot.settings,
        "frozen_at_start": True,
    }
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Run snapshot is unreadable; refusing to change an accepted run") from exc
        if (
            existing.get("run_id") != row.run_id
            or existing.get("source_hash") != row.source_hash
            or existing.get("settings_sha256") != snapshot.sha256
        ):
            raise ValueError("Run snapshot identity mismatch; refusing to change an accepted run")
        return existing
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _load_localization_run_snapshot(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "run_snapshot.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _bounded_quality_repair(
    resolved: dict[str, Any],
    requested: dict[str, Any],
    *,
    source_duration_ms: int,
    allow_split: bool,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    target = str(requested.get("target_language") or "").strip().lower()
    profile = str(requested.get("subtitle_quality_profile") or "").strip().lower()
    if target not in {"english", "en", "en-us", "en-gb"} or profile != "english_v2":
        return None
    result = repair_english_dialogue_cues(
        list(resolved.get("cues") or []),
        source_duration_ms=source_duration_ms,
        allow_split=allow_split,
    )
    updated = {
        **resolved,
        "cues": list(result.get("cues") or []),
        "quality_repair": {
            "profile_id": "english_v2",
            "repair_count": int(result.get("repair_count") or 0),
            "allow_split": bool(allow_split),
        },
    }
    return updated, result


def _write_localization_quality_evidence(
    run_dir: Path,
    resolved: dict[str, Any],
    requested: dict[str, Any],
    *,
    enforced: bool = False,
    repair_actions: list[dict[str, Any]] | None = None,
    evaluation_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = str(requested.get("target_language") or "").strip().lower()
    profile = str(requested.get("subtitle_quality_profile") or "").strip().lower()
    if target not in {"english", "en", "en-us", "en-gb"} or profile != "english_v2":
        payload = {
            "schema_version": 1,
            "status": "NOT_APPLICABLE",
            "eligible": True,
            "profile": profile or None,
            "reason": "quality_profile_not_applicable",
        }
    else:
        result = evaluation_override or evaluate_english_dialogue_cues(list(resolved.get("cues") or []))
        payload = {
            "schema_version": 1,
            **result,
            "profile_id": "english_v2",
            "enforced": bool(enforced),
            "repair_count": len(repair_actions or []),
            "repair_actions": list(repair_actions or []),
            "enforcement_note": (
                "Frozen V2 hard gates are enforced after bounded meaning-preserving repair."
                if enforced
                else "Evidence-only compatibility call; the production V2 path enables enforcement explicitly."
            ),
        }
    path = run_dir / "subtitles" / "localization_quality_evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _load_localization_quality_evidence(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "subtitles" / "localization_quality_evidence.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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
        "provider_calls": _provider_calls_for_run(row.run_id, run_dir),
        "upload_publish": {"upload": "not_performed", "publish": "not_performed"},
        "workflow_mode": requested.get("resolved_mode") or requested.get("caption_mode"),
        "result_validation": result_validation or _completed_result_validation(row),
        "subtitle_track_lineage": "canonical cue timing -> active content track resolver -> English layout -> presentation plate -> existing source-suppressed visual -> final render",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_progress_path(run_dir: Path) -> Path:
    return run_dir / "diagnostics" / "render_progress.json"


def _write_render_progress(run_dir: Path, payload: dict[str, Any]) -> bool:
    """Persist non-critical render telemetry without risking the render itself.

    Windows can briefly deny replacing a file while another process is reading it.
    Production Monitor polls this JSON frequently, so a transient sharing violation must
    never turn a healthy FFmpeg render into ``render_failed``.
    """
    path = _render_progress_path(run_dir)
    safe_payload = {
        key: value
        for key, value in payload.items()
        if key in {
            "state",
            "processed_seconds",
            "duration_seconds",
            "percentage",
            "speed",
            "frame",
            "expected_dialogues",
            "ass_dialogue_count",
            "updated_at",
            "error_code",
        }
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return True
            except PermissionError:
                if attempt >= 4:
                    return False
                time.sleep(0.02 * (2 ** attempt))
            except OSError:
                return False
        return False
    except OSError:
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _load_render_progress(run_dir: Path) -> dict[str, Any] | None:
    path = _render_progress_path(run_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"state": "progress_unavailable", "error_code": "RENDER_PROGRESS_READ_FAILED"}
    return payload if isinstance(payload, dict) else None


def _ffmpeg_out_time_seconds(value: str) -> float | None:
    try:
        hours, minutes, seconds = value.strip().split(":", 2)
        return max(0.0, int(hours) * 3600 + int(minutes) * 60 + float(seconds))
    except (TypeError, ValueError):
        return None


def _run_ffmpeg_with_progress(
    command: list[str],
    *,
    run_dir: Path,
    duration_seconds: float,
) -> None:
    duration = max(0.0, float(duration_seconds or 0))
    progress_command = [*command[:-1], "-progress", "pipe:1", "-nostats", command[-1]]
    state: dict[str, Any] = {
        "state": "starting",
        "processed_seconds": 0.0,
        "duration_seconds": duration,
        "percentage": 0.0 if duration > 0 else None,
        "speed": None,
        "frame": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_render_progress(run_dir, state)
    process = subprocess.Popen(
        progress_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    last_processed = 0.0
    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key == "out_time":
                parsed = _ffmpeg_out_time_seconds(value)
                if parsed is not None:
                    last_processed = max(last_processed, parsed)
                    state["processed_seconds"] = round(last_processed, 3)
                    if duration > 0:
                        state["percentage"] = round(min(100.0, (last_processed / duration) * 100.0), 1)
            elif key == "speed":
                state["speed"] = value.strip() or None
            elif key == "frame":
                try:
                    state["frame"] = max(int(state.get("frame") or 0), int(value))
                except ValueError:
                    pass
            elif key == "progress":
                state["state"] = "rendering" if value.strip() != "end" else "ffmpeg_completed"
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                _write_render_progress(run_dir, state)
        stderr_text = process.stderr.read()
        return_code = process.wait()
        if return_code != 0:
            state.update(
                state="failed",
                error_code="FFMPEG_RENDER_FAILED",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            _write_render_progress(run_dir, state)
            raise subprocess.CalledProcessError(
                return_code,
                progress_command,
                output="",
                stderr=stderr_text,
            )
        state.update(
            state="ffmpeg_completed",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        _write_render_progress(run_dir, state)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise
    finally:
        process.stdout.close()
        process.stderr.close()


def _bounded_subtitle_process(
    row: SimpleWorkflowRun,
    resolved: dict[str, Any],
    *,
    source_override: Path | None = None,
    output_override: Path | None = None,
    audio_override: Path | None = None,
) -> Path:
    run_dir = Path(row.run_directory)
    source = Path(source_override).resolve() if source_override is not None else Path(row.source_path)
    narration_audio = Path(audio_override).resolve() if audio_override is not None else None
    if narration_audio is not None and not narration_audio.is_file():
        raise ValueError("Subtitle render audio override is missing.")
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
    output = Path(output_override).resolve() if output_override is not None else run_dir / "output" / "final_video.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
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
        ]
        if narration_audio is not None:
            command.extend(["-i", str(narration_audio)])
        command.extend([
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
        ])
        if narration_audio is not None:
            command.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"])
        else:
            command.extend(["-c:a", "copy"])
        command.append(str(temp_output))
    else:
        command = _subtitle_render_command(source, ass_path, temp_output, audio_override=narration_audio)
    source_duration_seconds = float(json.loads(row.source_metadata_json or "{}").get("duration_seconds") or 0)
    _run_ffmpeg_with_progress(
        command,
        run_dir=run_dir,
        duration_seconds=source_duration_seconds,
    )
    _validate_subtitle_render(
        source=source,
        output=temp_output,
        ass_path=ass_path,
        expected_dialogues=expected_dialogues,
        render_command=command,
        expect_masked=use_source_mask,
        filter_script_path=filter_script_path,
    )
    render_progress = _load_render_progress(run_dir) or {}
    _write_render_progress(
        run_dir,
        {
            **render_progress,
            "state": "validated",
            "expected_dialogues": expected_dialogues,
            "ass_dialogue_count": _valid_ass_dialogue_count(ass_path),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    os.replace(temp_output, output)
    return output


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


def _subtitle_render_command(
    source: Path,
    ass_path: Path,
    output: Path,
    *,
    audio_override: Path | None = None,
) -> list[str]:
    filter_value = f"subtitles='{_ffmpeg_filter_path(ass_path)}'"
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
    ]
    if audio_override is not None:
        command.extend(["-i", str(audio_override)])
    command.extend([
        "-vf",
        filter_value,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ])
    if audio_override is not None:
        command.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"])
    else:
        command.extend(["-c:a", "copy"])
    command.append(str(output))
    return command


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
    """Reconcile result state against current fail-closed validation without deleting audit artifacts."""
    repaired: list[str] = []
    restored: list[str] = []
    with session_scope() as session:
        rows = session.query(SimpleWorkflowRun).filter(SimpleWorkflowRun.is_test_fixture.is_(False)).all()
        for row in rows:
            if row.internal_state in {"completed", "approved"}:
                validation = _completed_result_validation(row)
                if validation["eligible"]:
                    continue
                _invalidate_completed_result(row, validation)
                repaired.append(row.run_id)
                continue
            if row.internal_state != "blocked" or row.failure_category != INVALID_COMPLETED_RESULT:
                continue
            validation = _completed_result_validation(row)
            if validation["eligible"] is not True or not row.output_path or not row.output_hash:
                continue
            output = Path(row.output_path)
            if not output.is_file() or sha256_file(output).lower() != str(row.output_hash).lower():
                continue
            row.internal_state = "completed"
            row.current_phase = "Preview"
            row.approval_state = "not_reviewed"
            row.failure_category = None
            row.updated_at = datetime.now(timezone.utc)
            if row.completed_at is None:
                row.completed_at = row.updated_at
            _write_manifest(Path(row.run_directory), row, status="completed", result_validation=validation)
            restored.append(row.run_id)
    return {
        "status": "PASS",
        "repaired_count": len(repaired),
        "repaired_run_ids": repaired,
        "restored_count": len(restored),
        "restored_run_ids": restored,
    }


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


def _persist_ocr_runtime_block(run_id: str, status: dict[str, Any]) -> None:
    with session_scope() as session:
        row = _get_run(session, run_id)
        message = str(status.get("actionable_fix_message") or "Bộ đọc phụ đề OCR chưa sẵn sàng.")
        failure = {"code": "CAPTION_OCR_RUNTIME_FAILED", "message": _safe_user_failure_message(RuntimeError(message))}
        row.failure_category = "CAPTION_OCR_RUNTIME_FAILED"
        row.internal_state = "blocked"
        row.current_phase = "Check OCR runtime"
        row.output_path = None
        row.output_hash = None
        row.updated_at = datetime.now(timezone.utc)
        failure_path = Path(row.run_directory) / "logs" / "ocr_runtime_readiness.json"
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_manifest(Path(row.run_directory), row, status="blocked")


def _persist_gemini_runtime_block(run_id: str, exc: Exception) -> None:
    with session_scope() as session:
        row = _get_run(session, run_id)
        failure = {"code": "gemini_readiness_failed", "message": _safe_user_failure_message(exc)}
        row.failure_category = "gemini_readiness_failed"
        row.internal_state = "blocked"
        row.current_phase = "Check Gemini"
        row.output_path = None
        row.output_hash = None
        row.updated_at = datetime.now(timezone.utc)
        failure_path = Path(row.run_directory) / "logs" / "gemini_readiness.json"
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
        logs_dir = Path(row.run_directory) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        raw_message = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        safe_message = re.sub(
            r"(?i)(api[_-]?key|xi-api-key|authorization)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            raw_message,
        )
        (logs_dir / "simple_workflow_error.log").write_text(
            f"{type(exc).__name__}: {safe_message}\n",
            encoding="utf-8",
        )
        failure: dict[str, Any] = {
            "schema_version": 1,
            "code": category,
            "phase": phase,
            "message": _safe_user_failure_message(exc),
            "error_type": type(exc).__name__,
        }
        if isinstance(exc, GeminiCaptionTranslationError):
            failure["provider"] = "gemini"
            failure["http_status"] = exc.http_status
            failure["provider_usage"] = {
                "request_count": int(exc.request_count or 0),
                "retry_count": int(exc.retry_count or 0),
            }
            failure["failed_item_ids"] = list(exc.failed_caption_ids[:20])
        elif isinstance(exc, NarrationFitError):
            failure["narration_fit_failures"] = list(exc.failures[:10])
        elif isinstance(exc, NarrationProjectionRepairError):
            failure["narration_repair"] = {
                "code": exc.code,
                "evidence": dict(exc.evidence),
            }
        (logs_dir / "processing_failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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


def _set_runtime_readiness_phase(run_id: str, state: str, message: str) -> None:
    """Persist the existing readiness event so polling UI can render real work."""
    with session_scope() as session:
        row = _get_run(session, run_id)
        row.internal_state = "processing"
        row.current_phase = f"runtime:{state}"
        requested = json.loads(row.requested_settings_json or "{}")
        requested["runtime_readiness_message"] = _safe_user_failure_message(RuntimeError(message))
        row.requested_settings_json = json.dumps(requested, ensure_ascii=False)
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


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _expected_v2_acceptance_binding(row: SimpleWorkflowRun, output_hash: str) -> dict[str, str] | None:
    snapshot = _load_localization_run_snapshot(Path(row.run_directory))
    if not snapshot:
        return None
    settings_sha = str(snapshot.get("settings_sha256") or "").strip().lower()
    run_id = str(snapshot.get("run_id") or "").strip()
    source_sha = str(snapshot.get("source_hash") or "").strip().lower()
    if run_id != str(row.run_id) or source_sha != str(row.source_hash or "").strip().lower() or len(settings_sha) != 64:
        return None
    return {
        "run_id": str(row.run_id),
        "source_sha256": source_sha,
        "settings_sha256": settings_sha,
        "output_sha256": str(output_hash).strip().lower(),
    }


def _bind_localization_v2_acceptance_evidence(row: SimpleWorkflowRun, requested: dict[str, Any]) -> None:
    mode = str(requested.get("localization_mode") or SUBTITLE_LOCALIZATION)
    if mode == SUBTITLE_LOCALIZATION or not row.output_path:
        return
    output = Path(row.output_path)
    if not output.is_file():
        return
    output_hash = sha256_file(output).lower()
    binding = _expected_v2_acceptance_binding(row, output_hash)
    if binding is None:
        return
    run_dir = Path(row.run_directory)

    if mode in {FULL_VISUAL_LOCALIZATION, FULL_LOCALIZATION_VOICE}:
        path = run_dir / "visual" / "visual_quality_evidence.json"
        payload = _read_json_object(path)
        if payload and payload.get("status") == "PASS" and payload.get("eligible") is True:
            payload["acceptance_binding"] = binding
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if mode == FULL_LOCALIZATION_VOICE:
        path = run_dir / "audio" / "narration_manifest.json"
        payload = _read_json_object(path)
        if not payload or payload.get("status") != "PASS":
            return
        mix_path = Path(str(payload.get("mix_path") or ""))
        try:
            in_run = run_dir.resolve() in mix_path.resolve().parents
        except (OSError, RuntimeError):
            in_run = False
        if not in_run or not mix_path.is_file():
            return
        if str(payload.get("mix_sha256") or "").lower() != sha256_file(mix_path).lower():
            return
        if str(payload.get("provider") or "") != "kokoro_local":
            return
        if str(payload.get("voice_id") or "") != str(requested.get("voice_id") or ""):
            return
        try:
            speed_matches = abs(float(payload.get("base_speed")) - float(requested.get("voice_speed"))) <= 0.0005
        except (TypeError, ValueError):
            speed_matches = False
        if not speed_matches:
            return
        expected = int(payload.get("expected_retimed_cue_count") or 0)
        actual = int(payload.get("retimed_cue_count") or 0)
        if expected <= 0 or actual != expected:
            return
        payload["acceptance_binding"] = binding
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_v2_acceptance_evidence(
    row: SimpleWorkflowRun,
    requested: dict[str, Any],
    output_hash: str,
) -> dict[str, Any]:
    mode = str(requested.get("localization_mode") or SUBTITLE_LOCALIZATION)
    if mode == SUBTITLE_LOCALIZATION:
        return {"eligible": True, "mode": mode, "visual": None, "narration": None}
    binding = _expected_v2_acceptance_binding(row, output_hash)
    if binding is None:
        return {"eligible": False, "mode": mode, "reason_code": "v2_run_snapshot_binding_invalid"}
    run_dir = Path(row.run_directory)

    visual = _read_json_object(run_dir / "visual" / "visual_quality_evidence.json")
    if not visual:
        return {"eligible": False, "mode": mode, "reason_code": "visual_quality_evidence_missing"}
    if visual.get("status") != "PASS" or visual.get("eligible") is not True:
        return {"eligible": False, "mode": mode, "reason_code": "visual_quality_failed", "visual": visual}
    if visual.get("acceptance_binding") != binding:
        return {"eligible": False, "mode": mode, "reason_code": "visual_quality_binding_invalid", "visual": visual}

    narration: dict[str, Any] | None = None
    if mode == FULL_LOCALIZATION_VOICE:
        narration = _read_json_object(run_dir / "audio" / "narration_manifest.json")
        if not narration:
            return {"eligible": False, "mode": mode, "reason_code": "narration_evidence_missing", "visual": visual}
        if narration.get("status") != "PASS" or narration.get("acceptance_binding") != binding:
            return {"eligible": False, "mode": mode, "reason_code": "narration_binding_invalid", "visual": visual, "narration": narration}
        if str(narration.get("provider") or "") != "kokoro_local" or str(narration.get("voice_id") or "") != str(requested.get("voice_id") or ""):
            return {"eligible": False, "mode": mode, "reason_code": "narration_identity_invalid", "visual": visual, "narration": narration}
        try:
            if abs(float(narration.get("base_speed")) - float(requested.get("voice_speed"))) > 0.0005:
                return {"eligible": False, "mode": mode, "reason_code": "narration_identity_invalid", "visual": visual, "narration": narration}
        except (TypeError, ValueError):
            return {"eligible": False, "mode": mode, "reason_code": "narration_identity_invalid", "visual": visual, "narration": narration}
        mix_path = Path(str(narration.get("mix_path") or ""))
        try:
            in_run = run_dir.resolve() in mix_path.resolve().parents
        except (OSError, RuntimeError):
            in_run = False
        if not in_run or not mix_path.is_file() or str(narration.get("mix_sha256") or "").lower() != sha256_file(mix_path).lower():
            return {"eligible": False, "mode": mode, "reason_code": "narration_artifact_invalid", "visual": visual, "narration": narration}
        expected = int(narration.get("expected_retimed_cue_count") or 0)
        actual = int(narration.get("retimed_cue_count") or 0)
        if expected <= 0 or actual != expected:
            return {"eligible": False, "mode": mode, "reason_code": "narration_timing_incomplete", "visual": visual, "narration": narration}

    return {"eligible": True, "mode": mode, "visual": visual, "narration": narration}


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
        elif dialogue_count != int(content_validation.get("cue_count") or 0):
            reason_code = "subtitle_render_count_mismatch"

    v2_acceptance = None
    if reason_code is None:
        output_hash = sha256_file(output).lower() if output else ""
        v2_acceptance = _validate_v2_acceptance_evidence(row, requested, output_hash)
        if not v2_acceptance.get("eligible"):
            reason_code = str(v2_acceptance.get("reason_code") or "localization_v2_acceptance_failed")

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
        "localization_v2_acceptance": v2_acceptance,
        "caption_integrity": {
            "resolved_cue_count": int(content_validation.get("cue_count") or 0) if content_validation else None,
            "ass_dialogue_count": dialogue_count,
            "counts_match": (
                dialogue_count == int(content_validation.get("cue_count") or 0)
                if subtitle_required and content_validation is not None and dialogue_count is not None
                else None
            ),
        },
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
        "progress": _stage_progress(row.internal_state, row.current_phase, requested.get("caption_mode")),
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
        "provider_calls": _provider_calls_for_run(row.run_id, Path(row.run_directory)),
        "upload_publish": {"upload": "not_performed", "publish": "not_performed"},
        "subtitle_tracks": _subtitle_track_summary(row.run_id),
        "analysis_progress": load_caption_analysis_progress(Path(row.run_directory)),
        "monitor": _monitor_summary(row, validation),
        "render_progress": _load_render_progress(Path(row.run_directory)),
        "run_snapshot": _load_localization_run_snapshot(Path(row.run_directory)),
        "localization_quality": _load_localization_quality_evidence(Path(row.run_directory)),
    }
    payload.update(extra)
    return payload


def _parse_monitor_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _monitor_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _monitor_summary(row: SimpleWorkflowRun, validation: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(row.run_directory)
    requested = json.loads(row.requested_settings_json or "{}")
    analysis = load_caption_analysis_progress(run_dir) or {}
    render = _load_render_progress(run_dir)
    resolved = _monitor_json(run_dir / "subtitles" / "resolved_active_track.json")
    active_track = resolved.get("active_track") if isinstance(resolved.get("active_track"), dict) else {}
    metadata = active_track.get("metadata") if isinstance(active_track.get("metadata"), dict) else {}
    usage = metadata.get("provider_usage") if isinstance(metadata.get("provider_usage"), dict) else {}
    captions = list(analysis.get("preview_cues") or [])
    if not captions:
        captions = [
            {
                "cue_id": str(cue.get("cue_id") or ""),
                "start_ms": int(cue.get("start_ms") or 0),
                "end_ms": int(cue.get("end_ms") or 0),
                "source_text": str(cue.get("source_text") or "")[:240],
                "translated_text": str(cue.get("resolved_text") or cue.get("translation_text") or "")[:240],
                "ocr_confidence": cue.get("ocr_confidence"),
                "status": "resolved",
                "attention": False,
            }
            for cue in list(resolved.get("cues") or [])[-24:]
        ]
    now = datetime.now(timezone.utc)
    started = _parse_monitor_datetime(row.started_at)
    elapsed = max(0.0, (now - started).total_seconds()) if started else 0.0
    candidates = [
        _parse_monitor_datetime(analysis.get("last_progress_at")),
        _parse_monitor_datetime(analysis.get("last_heartbeat_at")),
        _parse_monitor_datetime(row.updated_at),
    ]
    latest_activity = max((item for item in candidates if item is not None), default=None)
    activity_age = max(0.0, (now - latest_activity).total_seconds()) if latest_activity else None
    worker_state = str(analysis.get("worker_state") or "")
    stalled = bool(
        row.internal_state == "processing"
        and row.current_phase != "Render video"
        and (
            worker_state == "failed"
            or analysis.get("error_code") == "EMBEDDED_CAPTION_ANALYSIS_STALLED"
            or (activity_age is not None and activity_age >= 45)
        )
    )
    translation_provider = str(metadata.get("translation_provider") or "").strip()
    provider = {
        "name": translation_provider or ("Gemini" if requested.get("caption_mode") == SOURCE_CAPTION_MODE else "Local source path"),
        "model": metadata.get("translation_model") or metadata.get("gemini_model"),
        "source_provider": metadata.get("asr_provider") or metadata.get("ocr_provider"),
        "translation_fallback": bool(metadata.get("translation_fallback")),
        "translation_fallback_reason": metadata.get("translation_fallback_reason"),
        "request_count": int(analysis.get("gemini_requests") or usage.get("request_count") or 0),
        "retry_count": int(analysis.get("gemini_retries") or usage.get("retry_count") or 0),
        "cache_hits": int(analysis.get("gemini_cache_hits") or usage.get("cache_hits") or 0),
        "cache_misses": int(analysis.get("gemini_cache_misses") or usage.get("cache_misses") or 0),
        "prevalidated_evidence_reused": bool(analysis.get("prevalidated_evidence_reused")),
    }
    resolved_count = len(list(resolved.get("cues") or []))
    quality_evidence = _load_localization_quality_evidence(run_dir) or {}
    subtitle_projection_repair = _monitor_json(run_dir / "subtitles" / "subtitle_projection_repair_evidence.json")
    asr_route = _monitor_json(run_dir / "logs" / "asr_compute_route.json")
    visual_ocr_route = _monitor_json(run_dir / "logs" / "visual_ocr_compute_route.json")
    visual_quality = _monitor_json(run_dir / "visual" / "visual_quality_evidence.json")
    narration = _monitor_json(run_dir / "audio" / "narration_manifest.json")
    narration_repair = _monitor_json(run_dir / "audio" / "narration_repair_evidence.json")
    run_snapshot = _load_localization_run_snapshot(run_dir) or {}
    narration_fit = list(narration.get("fit") or []) if isinstance(narration, dict) else []
    narration_overflow_count = sum(1 for item in narration_fit if isinstance(item, dict) and float(item.get("overflow_ms") or 0) > 30.0)
    ass_count = validation.get("ass_dialogue_count")
    content_validation = validation.get("subtitle_content_validation") if isinstance(validation.get("subtitle_content_validation"), dict) else {}
    v2_evidence = {
        "mode": str(requested.get("localization_mode") or SUBTITLE_LOCALIZATION),
        "settings_sha256": run_snapshot.get("settings_sha256"),
        "compute": {
            "asr": asr_route or None,
            "visual_ocr": visual_ocr_route or None,
        },
        "subtitle": quality_evidence or None,
        "subtitle_projection_repair": ({
            "status": subtitle_projection_repair.get("status"),
            "repair_kind": subtitle_projection_repair.get("repair_kind"),
            "repaired_cue_count": subtitle_projection_repair.get("repaired_cue_count"),
            "provider": subtitle_projection_repair.get("provider"),
            "model": subtitle_projection_repair.get("model"),
            "request_count": subtitle_projection_repair.get("request_count"),
            "retry_count": subtitle_projection_repair.get("retry_count"),
            "cache_hits": subtitle_projection_repair.get("cache_hits"),
            "cache_misses": subtitle_projection_repair.get("cache_misses"),
        } if subtitle_projection_repair else None),
        "visual": ({
            "status": visual_quality.get("status"),
            "eligible": visual_quality.get("eligible"),
            "expected_localized_track_count": visual_quality.get("expected_localized_track_count"),
            "planned_replacement_count": visual_quality.get("planned_replacement_count"),
            "residual_source_track_count": visual_quality.get("residual_source_track_count"),
            "unresolved_replacement_count": visual_quality.get("unresolved_replacement_count"),
            "failure_count": visual_quality.get("failure_count"),
        } if visual_quality else None),
        "narration": ({
            "status": narration.get("status"),
            "provider": narration.get("provider"),
            "model": narration.get("model"),
            "voice_id": narration.get("voice_id"),
            "base_speed": narration.get("base_speed"),
            "tts_unit_count": narration.get("tts_unit_count"),
            "retimed_cue_count": narration.get("retimed_cue_count"),
            "expected_retimed_cue_count": narration.get("expected_retimed_cue_count"),
            "overflow_count": narration_overflow_count,
            "repair": ({
                "status": narration_repair.get("status"),
                "repair_kind": narration_repair.get("repair_kind"),
                "repaired_unit_ids": narration_repair.get("repaired_unit_ids"),
                "repaired_cue_count": narration_repair.get("repaired_cue_count"),
                "provider": narration_repair.get("provider"),
                "model": narration_repair.get("model"),
                "request_count": narration_repair.get("request_count"),
                "retry_count": narration_repair.get("retry_count"),
                "cache_hits": narration_repair.get("cache_hits"),
                "cache_misses": narration_repair.get("cache_misses"),
            } if narration_repair else None),
        } if narration else None),
    }
    return {
        "elapsed_seconds": round(elapsed, 1),
        "last_progress_at": analysis.get("last_progress_at"),
        "last_heartbeat_at": analysis.get("last_heartbeat_at"),
        "activity_age_seconds": round(activity_age, 1) if activity_age is not None else None,
        "worker_state": worker_state or ("active" if row.internal_state == "processing" else row.internal_state),
        "stalled": stalled,
        "analysis": analysis,
        "provider": provider,
        "captions": captions[-24:],
        "render_progress": render,
        "v2": v2_evidence,
        "qc": {
            "detected_caption_count": int(analysis.get("caption_intervals_total") or 0),
            "resolved_caption_count": resolved_count,
            "rendered_dialogue_count": int(ass_count) if ass_count is not None else None,
            "caption_loss_detected": bool(ass_count is not None and resolved_count > 0 and int(ass_count) != resolved_count),
            "content_validation": content_validation.get("status") or validation.get("status"),
            "subtitle_quality": quality_evidence,
            "visual_quality": v2_evidence.get("visual"),
            "narration_quality": v2_evidence.get("narration"),
            "output_validation": validation.get("status"),
            "output_eligible": bool(validation.get("eligible")),
        },
    }


def _load_failure_detail(run_directory: Path) -> dict[str, str] | None:
    for name in ("processing_failure.json", "ocr_runtime_readiness.json", "gemini_readiness.json", "runtime_readiness.json", "subtitle_source_block.json"):
        try:
            payload = json.loads((run_directory / "logs" / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        code = payload.get("code")
        message = payload.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return {"code": code, "message": message}
    progress = load_caption_analysis_progress(run_directory)
    if isinstance(progress, dict):
        code = progress.get("error_code")
        message = progress.get("error_message")
        if isinstance(code, str) and code and isinstance(message, str) and message:
            return {"code": code, "message": message}
    return None


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
        provider_usage = active_metadata.get("provider_usage") if isinstance(active_metadata.get("provider_usage"), dict) else {}
        active_cues = active_metadata.get("cues") if isinstance(active_metadata.get("cues"), list) else []
        preview_cues = []
        for cue in active_cues[-24:]:
            if not isinstance(cue, dict):
                continue
            confidence = cue.get("ocr_confidence")
            try:
                attention = confidence is not None and float(confidence) < 0.75
            except (TypeError, ValueError):
                attention = False
            preview_cues.append(
                {
                    "cue_id": str(cue.get("cue_id") or ""),
                    "start_ms": int(cue.get("start_ms") or 0),
                    "end_ms": int(cue.get("end_ms") or 0),
                    "source_text": str(cue.get("source_text") or "")[:240],
                    "translated_text": str(cue.get("resolved_text") or cue.get("text") or "")[:240],
                    "ocr_confidence": confidence,
                    "status": "ready",
                    "attention": attention,
                }
            )
        return {
            "active_track_id": active_id,
            "operator_notice": active_metadata.get("operator_notice"),
            "automatic_caption_count": int(active_metadata.get("automatic_caption_count") or len(active_cues) or 0),
            "human_reviewed_caption_count": int(active_metadata.get("human_reviewed_caption_count") or 0),
            "subtitle_provenance": active_metadata.get("subtitle_provenance"),
            "translation_model": active_metadata.get("translation_model"),
            "provider_usage": {
                "request_count": int(provider_usage.get("request_count") or 0),
                "retry_count": int(provider_usage.get("retry_count") or 0),
                "cache_hits": int(provider_usage.get("cache_hits") or 0),
                "cache_misses": int(provider_usage.get("cache_misses") or 0),
            },
            "preview_cues": preview_cues,
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


def _provider_calls_for_run(run_id: str, run_directory: Path | str | None = None) -> dict[str, int]:
    gemini_calls = 0
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
        gemini_calls += int(usage.get("request_count") or 0)
    except Exception:
        pass

    if run_directory is not None:
        run_dir = Path(run_directory)
        visual = _monitor_json(run_dir / "visual" / "visual_text_localized.json")
        visual_usage = visual.get("provider_usage") if isinstance(visual.get("provider_usage"), dict) else {}
        if visual_usage:
            gemini_calls += int(visual_usage.get("request_count") or 0)
        subtitle_projection_repair = _monitor_json(run_dir / "subtitles" / "subtitle_projection_repair_evidence.json")
        if subtitle_projection_repair:
            gemini_calls += int(subtitle_projection_repair.get("request_count") or 0)
        narration_repair = _monitor_json(run_dir / "audio" / "narration_repair_evidence.json")
        if narration_repair:
            gemini_calls += int(narration_repair.get("request_count") or 0)
        if not visual_usage:
            failure = _monitor_json(run_dir / "logs" / "processing_failure.json")
            failure_usage = failure.get("provider_usage") if isinstance(failure.get("provider_usage"), dict) else {}
            if str(failure.get("provider") or "") == "gemini":
                gemini_calls += int(failure_usage.get("request_count") or 0)

    return {"gemini": gemini_calls, "elevenlabs": 0, "youtube": 0}


def _stage_progress(state: str, phase: str | None = None, caption_mode: str | None = None) -> dict[str, Any]:
    if state in {"completed", "approved"}:
        current = "verifying_result"
        completed = [stage_id for stage_id, _ in USER_STAGES]
        status_label = None
    elif state == "processing":
        runtime_stage_ids = [
            "checking_runtime",
            "downloading_autosubs",
            "preparing_autosubs_model",
            "preparing_translation",
            "runtime_ready",
        ]
        runtime_phase = str(phase or "").removeprefix("runtime:")
        runtime_messages = {
            "checking_runtime": "Đang kiểm tra khả năng cục bộ",
            "downloading_autosubs": "Đang tải AutoSubs cục bộ",
            "preparing_autosubs_model": "Đang chuẩn bị mô hình AutoSubs small",
            "preparing_translation": "Đang chuẩn bị dịch Trung → Anh ngoại tuyến",
            "runtime_ready": "Khả năng cục bộ đã sẵn sàng",
        }
        if runtime_phase in runtime_messages:
            runtime_stages = [stage_id for stage_id, _label in USER_STAGES]
            current = runtime_phase
            completed = runtime_stages[:runtime_stages.index(runtime_phase)]
            status_label = runtime_messages[runtime_phase]
            return {
                "mode": "stage",
                "current_stage": current,
                "completed_stages": completed,
                "percentage": None,
                "status_label": status_label,
            }
        if caption_mode == SOURCE_CAPTION_MODE:
            phase_map = {
                "Check OCR runtime": ("checking_runtime", [], "Đang kiểm tra bộ đọc phụ đề OCR"),
                "Check Gemini": ("checking_runtime", [], "Đang kiểm tra kết nối Gemini"),
                "Read and translate embedded captions": (
                    "analysing_dialogue",
                    ["checking_runtime"],
                    "Đang đọc phụ đề và dịch bằng Gemini",
                ),
                "Create subtitles": (
                    "preparing_english_subtitles",
                    ["checking_runtime", "analysing_dialogue"],
                    "Đang chuẩn bị phụ đề tiếng Anh",
                ),
                "Render video": (
                    "rendering_video",
                    ["checking_runtime", "analysing_dialogue", "preparing_english_subtitles", "cleaning_dialogue_subtitles"],
                    "Đang xuất video",
                ),
            }
        else:
            phase_map = {
                "Prepare audio": ("checking_video", runtime_stage_ids, "Đang chuẩn bị âm thanh"),
                "Recognize speech": ("analysing_dialogue", runtime_stage_ids + ["checking_video"], "Đang nhận dạng lời nói"),
                "Create subtitles": (
                    "preparing_english_subtitles",
                    runtime_stage_ids + ["checking_video", "analysing_dialogue"],
                    "Đang tạo phụ đề",
                ),
                "Render video": (
                    "rendering_video",
                    runtime_stage_ids + ["checking_video", "analysing_dialogue", "preparing_english_subtitles", "cleaning_dialogue_subtitles"],
                    "Đang xuất video",
                ),
            }
        current, completed, status_label = phase_map.get(
            phase,
            ("analysing_dialogue", ["checking_video"] if caption_mode != SOURCE_CAPTION_MODE else ["checking_runtime"], "Đang xử lý"),
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
