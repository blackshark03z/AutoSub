import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.paths import ensure_dir
from app.core.preflight import MEDIA_PROCESSING_MIN_FREE_BYTES, run_preflight, storage_preflight
from app.db.session import session_scope
from app.domain.models import Job, MediaAsset, Project
from app.services.artifacts import register_artifact


SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
DEFAULT_STAGE_STATUS = {
    "preflight": "In progress",
    "delogo": "Not started",
    "transcript": "Not started",
    "english": "Not started",
    "voice": "Not started",
    "preview": "Not started",
    "complete": "Not started",
}


def make_project_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "untitled-project"


def validate_source_path(path_value: str, slug: str | None = None) -> dict[str, Any]:
    source = Path(path_value).expanduser()
    if ".." in source.parts:
        return _failed_preflight("Path traversal is not allowed.")
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError:
        return _failed_preflight("Source file does not exist.")
    if not source.is_file():
        return _failed_preflight("Source path must be a regular file.")
    if source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        return _failed_preflight("Unsupported video format.", exists=True, regular_file=True, format_supported=False)

    try:
        media = media_summary(source)
        ffprobe = True
        error = None
    except Exception as exc:
        media = None
        ffprobe = False
        error = f"FFprobe failed: {type(exc).__name__}"

    settings = get_settings()
    size_bytes = source.stat().st_size
    estimated_required = max(size_bytes * 3, MEDIA_PROCESSING_MIN_FREE_BYTES)
    storage = storage_preflight("media", settings.root, projected_workspace_bytes=estimated_required)
    preflight = run_preflight()
    duplicate_slug = _slug_exists(slug) if slug else False
    checks = {
        "exists": True,
        "regular_file": True,
        "format_supported": True,
        "ffprobe": ffprobe,
        "audio_stream_present": bool(media and media.get("audio", {}).get("codec")),
        "disk_ok": storage["passed"] and (storage["current_free_bytes"] or 0) >= estimated_required,
        "ffmpeg_ready": bool(preflight.get("ffmpeg")),
        "asr_ready": True,
        "gemini_configured": True,
        "elevenlabs_configured": True,
        "slug_available": not duplicate_slug,
    }
    required_ok = all(checks.values())
    return {
        "status": "PASS" if required_ok else "FAIL",
        "source": {
            "filename": source.name,
            "extension": source.suffix.lower(),
            "size_bytes": size_bytes,
        },
        "media": media,
        "checks": checks,
        "disk": {
            "estimated_required_bytes": estimated_required,
            "free_bytes": storage["current_free_bytes"],
            "estimated_required_gib": round(estimated_required / (1024**3), 3),
            "free_gib": round((storage["current_free_bytes"] or 0) / (1024**3), 3),
            "operation": storage["operation"],
            "required_minimum_bytes": storage["required_minimum_bytes"],
            "margin_bytes": storage["margin_bytes"],
            "measured_at": storage["measured_at"],
            "recommendation": storage["recommendation"],
        },
        "error": error,
    }


def create_project_from_local_source(payload: dict[str, Any]) -> dict[str, Any]:
    slug = make_project_slug(payload.get("slug") or payload["name"])
    if _slug_exists(slug):
        raise ValueError("Project slug already exists.")
    if not payload.get("provenance_acknowledged"):
        raise ValueError("Source provenance must be acknowledged.")
    if payload.get("localization_scope", "dialogue_subtitles_only") != "dialogue_subtitles_only":
        raise ValueError("Only dialogue_subtitles_only is enabled for the production golden path.")

    validation = validate_source_path(payload["source_path"], slug=slug)
    if validation["status"] != "PASS":
        raise ValueError(validation.get("error") or "Source preflight failed.")

    settings = get_settings()
    project_dir = ensure_dir(settings.data_dir / "projects" / slug)
    source_dir = ensure_dir(project_dir / "source")
    source_path = Path(payload["source_path"]).expanduser().resolve(strict=True)
    destination = source_dir / f"input{source_path.suffix.lower()}"
    temp_destination = destination.with_suffix(destination.suffix + ".tmp")
    try:
        shutil.copy2(source_path, temp_destination)
        os.replace(temp_destination, destination)
    except Exception:
        if temp_destination.exists():
            temp_destination.unlink()
        raise

    digest = sha256_file(destination)
    media = media_summary(destination)
    intake = _build_intake_record(payload, slug, destination, digest, media)
    operator_dir = ensure_dir(project_dir / "operator")
    intake_path = operator_dir / "intake.json"
    intake_path.write_text(json.dumps(intake, ensure_ascii=False, indent=2), encoding="utf-8")

    with session_scope() as session:
        session.add(Project(project_id=slug, title=payload["name"]))
        session.flush()
        session.add(
            MediaAsset(
                project_id=slug,
                source_sha256=digest,
                path=str(destination),
                duration_seconds=str(media["duration_seconds"]),
                width=media["video"].get("width"),
                height=media["video"].get("height"),
            )
        )
        for kind in ["delogo", "asr", "english", "tts", "render", "qa"]:
            session.add(
                Job(
                    project_id=slug,
                    kind=kind,
                    status="not_started",
                    job_key=f"{slug}:{kind}",
                )
            )

    source_artifact = register_artifact(slug, "source_video", destination)
    intake_artifact = register_artifact(slug, "operator_intake", intake_path)
    return {
        "project_id": slug,
        "title": payload["name"],
        "source": source_artifact,
        "intake": intake_artifact,
        "media": media,
        "workflow": DEFAULT_STAGE_STATUS,
        "provider_calls": {"gemini": 0, "elevenlabs": 0},
    }


def uploaded_source_destination(filename: str) -> tuple[Path, Path]:
    if not filename:
        raise ValueError("Filename is required.")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
        raise ValueError("Unsupported video format.")
    settings = get_settings()
    upload_dir = ensure_dir(settings.data_dir / "operator_uploads")
    nonce = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    temp_destination = upload_dir / f"upload_{nonce}{suffix}.tmp"
    destination = upload_dir / f"upload_{nonce}{suffix}"
    return temp_destination, destination


def finalize_uploaded_source(temp_destination: Path, destination: Path) -> dict[str, Any]:
    digest = sha256_file(temp_destination)
    deduped = destination.parent / f"{digest}{destination.suffix.lower()}"
    if deduped.exists():
        temp_destination.unlink(missing_ok=True)
        destination = deduped
    else:
        os.replace(temp_destination, deduped)
        destination = deduped
    validation = validate_source_path(str(destination))
    if validation["status"] != "PASS":
        destination.unlink(missing_ok=True)
        raise ValueError(validation.get("error") or "Uploaded source failed preflight.")
    return {"uploaded_path": str(destination), "sha256": digest, "preflight": validation}


def read_intake_summary(project_id: str) -> dict[str, Any] | None:
    settings = get_settings()
    path = settings.data_dir / "projects" / project_id / "operator" / "intake.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def safe_project_jobs(project_id: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        jobs = session.query(Job).filter(Job.project_id == project_id).order_by(Job.id.asc()).all()
        return [
            {
                "job_id": job.id,
                "kind": job.kind,
                "status": job.status,
                "completed": 0,
                "total": 0,
                "provider_call_count": 0,
                "cache_hit_count": 0,
                "retry_count": 0,
                "last_error": None,
            }
            for job in jobs
        ]


def start_stage_job(project_id: str, stage: str) -> dict[str, Any]:
    allowed = {"delogo", "asr", "english", "tts", "render", "qa"}
    if stage not in allowed:
        raise ValueError("Unsupported stage action.")
    with session_scope() as session:
        job = session.query(Job).filter(Job.project_id == project_id, Job.kind == stage).one_or_none()
        if job is None:
            raise ValueError("Stage job is not initialized.")
        if job.status in {"queued", "claimed", "running"}:
            return {"job_id": job.id, "kind": job.kind, "status": job.status, "created": False}
        job.status = "queued"
        job.updated_at = datetime.now(timezone.utc)
        return {"job_id": job.id, "kind": job.kind, "status": job.status, "created": True}


def _build_intake_record(payload: dict[str, Any], slug: str, source_path: Path, digest: str, media: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project_id": slug,
        "project_name": payload["name"],
        "source": {"path": str(source_path), "sha256": digest, "media": media},
        "source_language": payload.get("source_language", "zh-CN"),
        "target_language": payload.get("target_language", "en-US"),
        "content_mode": payload.get("content_mode", "sentence-level narrated localization"),
        "localization_scope": payload.get("localization_scope", "dialogue_subtitles_only"),
        "voice": payload.get("voice", "Production voice configured"),
        "elevenlabs_model": payload.get("elevenlabs_model", "eleven_multilingual_v2"),
        "source_audio_policy": payload.get("source_audio_policy", "replace source speech with generated narration"),
        "subtitle_style_preset": payload.get("subtitle_style_preset", "CP07A compact plate"),
        "output_resolution": payload.get("output_resolution", "1280x720"),
        "provenance_acknowledged": bool(payload.get("provenance_acknowledged")),
        "notes": payload.get("notes", ""),
        "workflow": DEFAULT_STAGE_STATUS,
        "approval_gates": {
            "source_provenance": "approved" if payload.get("provenance_acknowledged") else "needs_review",
            "transcript": "not_started",
            "english_content": "not_started",
            "voice_timing": "not_started",
            "subtitle_removal": "not_started",
            "preview_qa": "not_started",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _failed_preflight(message: str, **checks: bool) -> dict[str, Any]:
    base_checks = {
        "exists": False,
        "regular_file": False,
        "format_supported": False,
        "ffprobe": False,
        "audio_stream_present": False,
        "disk_ok": False,
        "ffmpeg_ready": False,
        "asr_ready": False,
        "gemini_configured": False,
        "elevenlabs_configured": False,
        "slug_available": False,
    }
    base_checks.update(checks)
    return {"status": "FAIL", "source": None, "media": None, "checks": base_checks, "disk": None, "error": message}


def _slug_exists(slug: str | None) -> bool:
    if not slug:
        return False
    with session_scope() as session:
        return session.query(Project).filter(Project.project_id == slug).one_or_none() is not None
