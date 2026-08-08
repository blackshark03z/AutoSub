import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.paths import ensure_dir
from app.core.preflight import run_preflight
from app.db.session import session_scope
from app.domain.models import MediaAsset, Project
from app.services.cjk_cleanup import (
    build_cjk_cleanup_summary,
    load_cjk_cleanup_state,
    mark_cleanup_issue_reviewed,
    save_cjk_cleanup_state,
    set_cleanup_approval,
)
from app.services.non_dialogue_localization import dialogue_only_scope_config
from app.services.production_golden_path import PROJECT_ID as CP09_PROJECT_ID
from app.services.production_golden_path import golden_path_summary, golden_preview_path
from app.services.production_intake import read_intake_summary, safe_project_jobs


CP07_PROJECT_ID = "vertical_slice_cp07"
CP07A_ARTIFACT = Path("data/projects/vertical_slice_cp07/renders/cp07a_targeted_human_review_repair_720p.mp4")
CP07_TIMELINE = Path("data/projects/vertical_slice_cp07/renders/cp07_full_canonical_audio_subtitle_timeline.json")
CP07A_SUMMARY = Path("evidence/CP07/cp07a_targeted_repair_summary.json")
REVIEW_STATE = Path("data/projects/vertical_slice_cp07/operator_review_state.json")
LEGACY_TEST_FIXTURE_TITLE_RE = re.compile(r"\b(smoke|fixture|fake|race|crash|tamper|reuse|uncertain|manual)\b", re.IGNORECASE)
LEGACY_TEST_FIXTURE_PROJECT_ID_RE = re.compile(r"^(proj_|cp08c-smoke-)", re.IGNORECASE)
LEGACY_FIXTURE_AUDIT_RELATIVE = Path("evidence/CP09B2/review_discoverability_fixture_cleanup/legacy_fixture_cleanup_audit.json")

STAGES = [
    ("preflight", "Project & Preflight", "Approved"),
    ("delogo", "Source Subtitle Removal", "Approved"),
    ("transcript", "Transcript", "Approved"),
    ("english", "English Content", "Approved"),
    ("voice", "Voice & Timing", "Approved"),
    ("preview", "Preview & QA", "Approved"),
    ("complete", "Complete", "Complete"),
]

GATES = [
    ("source_provenance", "Source/provenance acknowledgment", "preflight"),
    ("transcript", "Transcript approval", "transcript"),
    ("english_content", "English content approval", "english"),
    ("voice_timing", "Voice/timing approval", "voice"),
    ("subtitle_removal", "Subtitle-removal review", "delogo"),
    ("preview_qa", "Preview QA approval", "preview"),
]


def list_operator_projects() -> dict[str, Any]:
    repair_legacy_test_fixture_projects()
    projects: list[dict[str, Any]] = []
    seen = set()
    cp07_summary = build_operator_project_summary(CP07_PROJECT_ID)
    projects.append(_project_picker_entry(CP07_PROJECT_ID, "CP07A Accepted Full Preview", cp07_summary["overall_status"], cp07_summary["artifact"]["filename"], canonical_name="CP07A Accepted Full Preview", scope="dialogue_subtitles_only", readiness="Accepted preview"))
    seen.add(CP07_PROJECT_ID)
    cp09_summary = golden_path_summary(CP09_PROJECT_ID)
    if cp09_summary is not None:
        projects.append(
            _project_picker_entry(
                CP09_PROJECT_ID,
                cp09_summary["project"]["title"],
                cp09_summary["overall_status"],
                cp09_summary["artifact"]["filename"],
                canonical_name=cp09_summary["project"]["title"],
                scope=cp09_summary["source"].get("localization_scope"),
                readiness="Production golden path",
            )
        )
        seen.add(CP09_PROJECT_ID)
    with session_scope() as session:
        rows = session.query(Project).filter(Project.is_test_fixture.is_(False)).order_by(Project.created_at.asc()).all()
        for row in rows:
            if row.project_id in seen:
                continue
            if _is_legacy_test_fixture_project(row):
                continue
            intake = read_intake_summary(row.project_id)
            title = row.title if isinstance(row.title, str) else ""
            status = "Project intake ready" if intake else "Project created"
            projects.append(
                _project_picker_entry(
                    row.project_id,
                    title,
                    status,
                    None,
                    canonical_name=intake["project_name"] if intake else None,
                    scope=intake.get("localization_scope") if intake else None,
                    created_at=row.created_at.isoformat(),
                    readiness=f"Not ready - {status}",
                )
            )
    projects = sorted(projects, key=_project_picker_sort_key)
    return {"projects": projects}


def _project_picker_entry(
    project_id: str,
    title: str | None,
    status: str,
    artifact_filename: str | None,
    *,
    canonical_name: str | None = None,
    scope: str | None = None,
    created_at: str | None = None,
    readiness: str | None = None,
) -> dict[str, Any]:
    display_name = _project_display_name(project_id, title, canonical_name)
    secondary_bits = [project_id]
    if scope:
        secondary_bits.append(scope)
    if status:
        secondary_bits.append(status)
    search_bits = [display_name, project_id, canonical_name or "", status, artifact_filename or "", scope or ""]
    return {
        "project_id": project_id,
        "title": title or "",
        "display_name": display_name,
        "secondary_text": "  -  ".join(bit for bit in secondary_bits if bit),
        "status": status,
        "readiness": readiness or status,
        "artifact_filename": artifact_filename,
        "canonical_name": canonical_name or "",
        "scope": scope or "",
        "created_at": created_at or "",
        "is_production": _is_production_project(project_id, display_name, canonical_name, artifact_filename),
        "search_text": " ".join(bit for bit in search_bits if bit).lower(),
    }


def _is_legacy_test_fixture_project(row: Project) -> bool:
    project_id = (row.project_id or "").strip()
    title = (row.title or "").strip()
    if bool(row.is_test_fixture):
        return True
    if LEGACY_TEST_FIXTURE_PROJECT_ID_RE.match(project_id):
        return True
    if LEGACY_TEST_FIXTURE_TITLE_RE.search(title):
        return True
    return False


def repair_legacy_test_fixture_projects() -> dict[str, Any]:
    repairs: list[dict[str, Any]] = []
    visible: list[dict[str, Any]] = []
    already_marked: list[dict[str, Any]] = []
    changed = False
    with session_scope() as session:
        rows = session.query(Project).order_by(Project.created_at.asc()).all()
        for row in rows:
            entry = {
                "project_id": row.project_id,
                "title": row.title or "",
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
            if _is_legacy_test_fixture_project(row):
                entry["reason"] = _legacy_fixture_reason(row)
                if not row.is_test_fixture:
                    row.is_test_fixture = True
                    changed = True
                    repairs.append(entry)
                else:
                    already_marked.append(entry)
            else:
                visible.append(entry)
    summary = {
        "timestamp": _now(),
        "repaired_count": len(repairs),
        "already_marked_count": len(already_marked),
        "visible_count": len(visible),
        "repaired_projects": repairs,
        "already_marked_projects": already_marked[:12],
        "visible_projects": visible[:12],
        "retained_projects": (visible + already_marked)[:12],
        "changed": changed,
    }
    if changed or not _legacy_fixture_audit_path().exists():
        _write_legacy_fixture_audit(summary)
    return summary


def _legacy_fixture_reason(row: Project) -> str:
    project_id = (row.project_id or "").strip()
    title = (row.title or "").strip()
    if row.is_test_fixture:
        return "already_marked_fixture"
    if LEGACY_TEST_FIXTURE_PROJECT_ID_RE.match(project_id):
        return "fixture_project_id"
    if LEGACY_TEST_FIXTURE_TITLE_RE.search(title):
        return "fixture_title"
    return "legacy_fixture"


def _write_legacy_fixture_audit(summary: dict[str, Any]) -> None:
    path = _legacy_fixture_audit_path()
    ensure_dir(path.parent)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_fixture_audit_path() -> Path:
    return get_settings().root / LEGACY_FIXTURE_AUDIT_RELATIVE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_display_name(project_id: str, title: str | None, canonical_name: str | None = None) -> str:
    for candidate in [title, canonical_name, project_id]:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return f"Unnamed project {project_id}"


def _is_production_project(project_id: str, display_name: str, canonical_name: str | None, artifact_filename: str | None) -> bool:
    tokens = [project_id, display_name, canonical_name or "", artifact_filename or ""]
    return any(
        value.lower().startswith("production_") or "production" in value.lower() or value.lower().startswith("cp09") or value.lower().startswith("cp07")
        for value in tokens
        if isinstance(value, str) and value
    )


def _project_picker_sort_key(project: dict[str, Any]) -> tuple[int, int, str, str]:
    display_name = project.get("display_name") or project.get("project_id") or ""
    project_id = project.get("project_id") or ""
    current = 0 if project_id == CP07_PROJECT_ID else 1
    production = 0 if project.get("is_production") else 1
    return (current, production, display_name.lower(), project_id.lower())


def build_operator_project_summary(project_id: str) -> dict[str, Any]:
    if project_id != CP07_PROJECT_ID:
        golden = golden_path_summary(project_id)
        if golden is not None:
            return golden
        return _build_intake_project_summary(project_id)

    settings = get_settings()
    timeline_path = settings.root / CP07_TIMELINE
    artifact_path = settings.root / CP07A_ARTIFACT
    summary_path = settings.root / CP07A_SUMMARY
    if not timeline_path.exists() or not artifact_path.exists() or not summary_path.exists():
        raise FileNotFoundError("Accepted CP07A runtime data is missing")

    timeline_payload = _read_json(timeline_path)
    cp07a_summary = _read_json(summary_path)
    source = timeline_payload["source"]
    canonical = timeline_payload["canonical_timeline"]
    corrections = cp07a_summary.get("corrected_segments", {})
    segments = [_operator_segment(segment, corrections) for segment in canonical["segments"]]
    issues = _build_issue_list(cp07a_summary, segments)
    review_state = _read_review_state(settings.root)
    reviewed_ids = set(review_state.get("reviewed_issue_ids", []))
    for issue in issues:
        if issue.get("needs_review", False):
            issue["reviewed"] = issue["issue_id"] in reviewed_ids

    preflight = run_preflight()
    disk = shutil.disk_usage(settings.root)
    media = cp07a_summary.get("media") or media_summary(artifact_path)
    gates = _build_gates(cp07a_summary, issues)
    stages = _build_stages(gates, issues)
    cleanup_state = load_cjk_cleanup_state(settings.root)
    cjk_cleanup = build_cjk_cleanup_summary(cleanup_state) if cleanup_state else None
    technical = {
        "cp07a_commit": "a542081 cp07a repair targeted human review blockers",
        "checkpoint": "CP07A_TARGETED_FULL_PREVIEW_HUMAN_REVIEW_REPAIR",
        "cp08_status": "READY",
        "cp09_status": "NOT_STARTED",
        "cp08g_policy": dialogue_only_scope_config(),
        "cp08f_targeted_recomposition_human_review": "FAIL_NON_CANONICAL",
    }
    if cjk_cleanup:
        technical["cp08d_status"] = cjk_cleanup.get("verdict")
        technical["cp08d_checkpoint"] = "CP08D3_CLOSED_LOOP_CJK_IMPLEMENTATION"
    return {
        "schema_version": 1,
        "project": {
            "project_id": project_id,
            "title": "CP07A Accepted Full Preview",
            "completed": True,
            "current_stage": "complete",
        },
        "overall_status": "Production preview accepted",
        "source": {
            "filename": Path(source["path"]).name,
            "duration_seconds": source["media"]["duration_seconds"],
            "resolution": f"{source['media']['video']['width']}x{source['media']['video']['height']}",
            "fps": source["media"]["video"]["avg_frame_rate"],
            "language": canonical.get("source_language", "zh"),
            "target_locale": canonical.get("target_locale", "en-US"),
            "content_mode": "sentence-level narrated localization",
            "audio_replacement_policy": "source audio removed; accepted narration stem used",
        },
        "preflight": {
            "ffmpeg": _safe_ready(preflight.get("ffmpeg")),
            "ffprobe": _safe_ready(preflight.get("ffprobe")),
            "asr": "Complete",
            "gemini": "Configured and complete",
            "tts": "Configured and complete",
            "disk": {
                "status": "Healthy",
                "free_gib": round(disk.free / (1024**3), 3),
            },
            "overall_readiness": "Complete",
        },
        "provider_summary": {
            "gemini": "Configured",
            "elevenlabs": "Healthy",
            "gemini_calls_on_ui_load": 0,
            "elevenlabs_calls_on_ui_load": 0,
        },
        "stages": stages,
        "approval_gates": gates,
        "issue_summary": _issue_summary(issues),
        "issues": issues,
        "segments": segments,
        "timeline": {
            "segment_count": len(segments),
            "source_duration_ms": canonical["source_duration_ms"],
            "tts_group_count": 89,
            "active_tts_bindings": 89,
        },
        "delogo": {
            "method": "glyph-first dynamic local delogo",
            "subtitle_interval_count": 440,
            "residual_issue_count": cp07a_summary["targeted_visual_qa"]["residual_chinese_0812_0815"],
            "short_flash_count": cp07a_summary["visual_qa"]["short_subtitle_flashes"],
            "toggle_warning_count": cp07a_summary["visual_qa"]["unnecessary_delogo_toggles"],
            "evidence": [
                "evidence/CP07/cp07a_visual_contact_sheet_after.jpg",
                "evidence/CP07/cp07a_targeted_crop_zoom_after.jpg",
            ],
        },
        "voice_timing": {
            "voice": "Production voice configured",
            "model": "eleven_multilingual_v2",
            "tts_group_count": 89,
            "spoken_unit_count": len(segments),
            "active_binding_status": "89 / 89 active",
            "generated_duration": f"{media['duration_seconds']}s",
            "timing_fit_state": cp07a_summary["audio_qa"]["status"],
            "missing_or_failed_groups": 0,
            "quota_provider_summary": "Healthy",
        },
        "preview": {
            "filename": artifact_path.name,
            "url": f"/api/operator/projects/{project_id}/accepted-preview",
            "duration_seconds": media["duration_seconds"],
            "resolution": f"{media['video']['width']}x{media['video']['height']}",
            "sha256": cp07a_summary.get("artifact_sha256") or sha256_file(artifact_path),
            "audio_qa": cp07a_summary["audio_qa"],
            "subtitle_qa": cp07a_summary["subtitle_qa"],
            "visual_qa": cp07a_summary["visual_qa"],
            "targeted_visual_qa": cp07a_summary["targeted_visual_qa"],
            "checklist": _preview_checklist(cp07a_summary),
            "human_review_state": "Accepted by operator before CP08",
        },
        "artifact": {
            "filename": artifact_path.name,
            "url": f"/api/operator/projects/{project_id}/accepted-preview",
            "duration_seconds": media["duration_seconds"],
            "resolution": f"{media['video']['width']}x{media['video']['height']}",
            "sha256": cp07a_summary.get("artifact_sha256") or sha256_file(artifact_path),
        },
        "cjk_cleanup": cjk_cleanup,
        "technical": technical,
    }


def mark_issue_reviewed(project_id: str, issue_id: str) -> dict[str, Any]:
    if project_id != CP07_PROJECT_ID:
        raise FileNotFoundError(project_id)
    settings = get_settings()
    cleanup_state = load_cjk_cleanup_state(settings.root)
    if cleanup_state and any(issue.get("issue_id") == issue_id for issue in cleanup_state.get("issues", [])):
        updated = mark_cleanup_issue_reviewed(cleanup_state, issue_id)
        save_cjk_cleanup_state(updated, settings.root)
        return {"issue_id": issue_id, "reviewed": True}
    state = _read_review_state(settings.root)
    reviewed = set(state.get("reviewed_issue_ids", []))
    reviewed.add(issue_id)
    state = {
        "schema_version": 1,
        "reviewed_issue_ids": sorted(reviewed),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = settings.root / REVIEW_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"issue_id": issue_id, "reviewed": True}


def apply_cjk_cleanup_action(project_id: str, action: str, issue_id: str | None = None) -> dict[str, Any]:
    if project_id != CP07_PROJECT_ID:
        raise FileNotFoundError(project_id)
    settings = get_settings()
    state = load_cjk_cleanup_state(settings.root)
    if state is None:
        raise FileNotFoundError("CJK cleanup state not found")
    action = action.strip().lower()
    if action == "mark_reviewed":
        if not issue_id:
            raise ValueError("issue_id is required")
        state = mark_cleanup_issue_reviewed(state, issue_id)
    elif action == "approve_cleanup":
        state = set_cleanup_approval(state, cleanup=True)
    elif action == "approve_preservation":
        state = set_cleanup_approval(state, preservation=True)
    elif action in {
        "analyze_source_text_regions",
        "run_cleanup_pass",
        "scan_repaired_output",
        "retry_selected_interval",
        "open_next_residual_issue",
        "previous_issue",
        "seek_to_issue_timestamp",
        "view_before_after",
        "preview_source_suppressed_visual",
        "preview_final_composition",
        "seek_to_source_event",
        "show_source_geometry",
        "show_plate_geometry",
        "show_containment_failures",
        "seek_next_containment_failure",
        "manual_plate_geometry_override",
        "approve_source_suppression",
    }:
        state = dict(state)
        state.setdefault("activity_log", []).append({"action": action, "issue_id": issue_id, "at": datetime.now(timezone.utc).isoformat()})
    else:
        raise ValueError(f"Unsupported cleanup action: {action}")
    save_cjk_cleanup_state(state, settings.root)
    return {"action": action, "state": build_cjk_cleanup_summary(state)}


def accepted_preview_path(project_id: str) -> Path:
    golden = golden_preview_path(project_id)
    if golden is not None:
        return golden
    if project_id != CP07_PROJECT_ID:
        raise FileNotFoundError(project_id)
    path = get_settings().root / CP07A_ARTIFACT
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _build_intake_project_summary(project_id: str) -> dict[str, Any]:
    intake = read_intake_summary(project_id)
    if intake is None:
        return _build_created_project_summary(project_id)
    media = intake["source"]["media"]
    jobs = safe_project_jobs(project_id)
    stages = [
        {"stage_id": "preflight", "label": "Project & Preflight", "status": "Ready for approval", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "delogo", "label": "Source Subtitle Removal", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "transcript", "label": "Transcript", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "english", "label": "English Content", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "voice", "label": "Voice & Timing", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "preview", "label": "Preview & QA", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "complete", "label": "Complete", "status": "Not started", "gate_count": 0, "unresolved_issue_count": 0},
    ]
    gates = [
        {"gate_id": "source_provenance", "label": "Source/provenance acknowledgment", "stage": "preflight", "state": "Approved", "approved_at": intake["created_at"], "unresolved_issue_count": 0, "action_required": "Ready to continue.", "blocks_next": False},
        {"gate_id": "subtitle_removal", "label": "Subtitle-removal review", "stage": "delogo", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Run Analyze subtitle regions explicitly.", "blocks_next": True},
        {"gate_id": "transcript", "label": "Transcript approval", "stage": "transcript", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Start ASR explicitly.", "blocks_next": True},
        {"gate_id": "english_content", "label": "English content approval", "stage": "english", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Generate English content explicitly.", "blocks_next": True},
        {"gate_id": "voice_timing", "label": "Voice/timing approval", "stage": "voice", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Generate TTS explicitly.", "blocks_next": True},
        {"gate_id": "preview_qa", "label": "Preview QA approval", "stage": "preview", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Render preview explicitly.", "blocks_next": True},
    ]
    return {
        "schema_version": 1,
        "state": "operator_snapshot_missing",
        "operator_state": {
            "state": "operator_snapshot_missing",
            "project_state": "intake_ready",
            "available_stage": "preflight",
            "available_status": "Project intake ready",
            "unresolved_issue_count": 0,
            "reason": "This project exists, but it does not yet have an accepted operator snapshot. Complete the required earlier stage before opening guided review.",
        },
        "project": {"project_id": project_id, "title": intake["project_name"], "completed": False, "current_stage": "preflight"},
        "overall_status": "Project not ready for operator review",
        "source": {
            "filename": Path(intake["source"]["path"]).name,
            "duration_seconds": media["duration_seconds"],
            "resolution": f"{media['video']['width']}x{media['video']['height']}",
            "fps": media["video"]["avg_frame_rate"],
            "language": intake["source_language"],
            "target_locale": intake["target_language"],
            "content_mode": intake["content_mode"],
            "localization_scope": intake.get("localization_scope", "dialogue_subtitles_only"),
            "audio_replacement_policy": intake["source_audio_policy"],
        },
        "preflight": {
            "ffmpeg": "Ready",
            "ffprobe": "Ready",
            "asr": "Ready",
            "gemini": "Configured",
            "tts": "Configured",
            "disk": {"status": "Healthy", "free_gib": round(shutil.disk_usage(get_settings().root).free / (1024**3), 3)},
            "overall_readiness": "Ready",
        },
        "provider_summary": {"gemini": "Configured", "elevenlabs": "Configured", "gemini_calls_on_ui_load": 0, "elevenlabs_calls_on_ui_load": 0},
        "stages": stages,
        "approval_gates": gates,
        "issue_summary": {"total": 0, "blockers": 0, "warnings": 0, "needs_review": 0, "reviewed": 0, "unresolved": 0, "clean_without_review_requirement": 0},
        "issues": [],
        "segments": [],
        "timeline": {"segment_count": 0, "source_duration_ms": int(media["duration_seconds"] * 1000), "tts_group_count": 0, "active_tts_bindings": 0},
        "delogo": {"method": "Not analyzed", "subtitle_interval_count": 0, "residual_issue_count": 0, "short_flash_count": 0, "toggle_warning_count": 0, "evidence": []},
        "voice_timing": {"voice": intake["voice"], "model": intake["elevenlabs_model"], "tts_group_count": 0, "spoken_unit_count": 0, "active_binding_status": "0 / 0 active", "generated_duration": "0s", "timing_fit_state": "Not started", "missing_or_failed_groups": 0, "quota_provider_summary": "Configured"},
        "preview": {"filename": None, "url": None, "duration_seconds": 0, "resolution": intake["output_resolution"], "sha256": None, "audio_qa": {"status": "Not started"}, "subtitle_qa": {"status": "Not started"}, "visual_qa": {"status": "Not started"}, "targeted_visual_qa": {"status": "Not started"}, "checklist": [], "human_review_state": "Not started"},
        "artifact": {"filename": None, "url": None, "duration_seconds": 0, "resolution": intake["output_resolution"], "sha256": None},
        "jobs": jobs,
        "technical": {"checkpoint": "PROJECT_INTAKE_READY", "operator_state": "operator_snapshot_missing", "provider_calls_on_load": 0},
    }


def _build_created_project_summary(project_id: str) -> dict[str, Any]:
    with session_scope() as session:
        row = session.query(Project).filter(Project.project_id == project_id).one_or_none()
        media_asset = session.query(MediaAsset).filter(MediaAsset.project_id == project_id).first()
        if row is None:
            raise FileNotFoundError(project_id)
        title = row.title or project_id
        created_at = row.created_at.isoformat() if row.created_at else None
        media_duration = media_asset.duration_seconds if media_asset else None
        media_width = media_asset.width if media_asset else None
        media_height = media_asset.height if media_asset else None
        media_path = media_asset.path if media_asset else None
    duration = float(media_duration) if media_duration else 0.0
    resolution = f"{media_width}x{media_height}" if media_width and media_height else "Not registered"
    source_filename = Path(media_path).name if media_path else "No source registered"
    jobs = safe_project_jobs(project_id)
    stages = [
        {"stage_id": "preflight", "label": "Project & Preflight", "status": "Project created", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "delogo", "label": "Source Subtitle Removal", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "transcript", "label": "Transcript", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "english", "label": "English Content", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "voice", "label": "Voice & Timing", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "preview", "label": "Preview & QA", "status": "Not started", "gate_count": 1, "unresolved_issue_count": 0},
        {"stage_id": "complete", "label": "Complete", "status": "Not started", "gate_count": 0, "unresolved_issue_count": 0},
    ]
    gates = [
        {"gate_id": "source_provenance", "label": "Source/provenance acknowledgment", "stage": "preflight", "state": "Needs setup", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Register source provenance before running production stages.", "blocks_next": True},
        {"gate_id": "subtitle_removal", "label": "Subtitle-removal review", "stage": "delogo", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Run Analyze subtitle regions explicitly.", "blocks_next": True},
        {"gate_id": "transcript", "label": "Transcript approval", "stage": "transcript", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Start ASR explicitly.", "blocks_next": True},
        {"gate_id": "english_content", "label": "English content approval", "stage": "english", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Generate English content explicitly.", "blocks_next": True},
        {"gate_id": "voice_timing", "label": "Voice/timing approval", "stage": "voice", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Generate TTS explicitly.", "blocks_next": True},
        {"gate_id": "preview_qa", "label": "Preview QA approval", "stage": "preview", "state": "Not started", "approved_at": None, "unresolved_issue_count": 0, "action_required": "Render preview explicitly.", "blocks_next": True},
    ]
    return {
        "schema_version": 1,
        "state": "operator_snapshot_missing",
        "operator_state": {
            "state": "operator_snapshot_missing",
            "project_state": "project_created",
            "available_stage": "preflight",
            "available_status": "Project created",
            "unresolved_issue_count": 0,
            "reason": "This project exists, but it does not yet have an accepted operator snapshot. Complete the required earlier stage before opening guided review.",
        },
        "project": {"project_id": project_id, "title": title, "completed": False, "current_stage": "preflight"},
        "overall_status": "Project not ready for operator review",
        "source": {
            "filename": source_filename,
            "duration_seconds": duration,
            "resolution": resolution,
            "fps": "Not registered",
            "language": "Not registered",
            "target_locale": "en-US",
            "content_mode": "Not configured",
            "localization_scope": "dialogue_subtitles_only",
            "audio_replacement_policy": "Not configured",
        },
        "preflight": {
            "ffmpeg": "Ready",
            "ffprobe": "Ready",
            "asr": "Not started",
            "gemini": "Configured",
            "tts": "Configured",
            "disk": {"status": "Healthy", "free_gib": round(shutil.disk_usage(get_settings().root).free / (1024**3), 3)},
            "overall_readiness": "Needs source setup",
        },
        "provider_summary": {"gemini": "Configured", "elevenlabs": "Configured", "gemini_calls_on_ui_load": 0, "elevenlabs_calls_on_ui_load": 0},
        "stages": stages,
        "approval_gates": gates,
        "issue_summary": {"total": 0, "blockers": 0, "warnings": 0, "needs_review": 0, "reviewed": 0, "unresolved": 0, "clean_without_review_requirement": 0},
        "issues": [],
        "segments": [],
        "timeline": {"segment_count": 0, "source_duration_ms": int(duration * 1000), "tts_group_count": 0, "active_tts_bindings": 0},
        "delogo": {"method": "Not analyzed", "subtitle_interval_count": 0, "residual_issue_count": 0, "short_flash_count": 0, "toggle_warning_count": 0, "evidence": []},
        "voice_timing": {"voice": "Not configured", "model": "Not configured", "tts_group_count": 0, "spoken_unit_count": 0, "active_binding_status": "0 / 0 active", "generated_duration": "0s", "timing_fit_state": "Not started", "missing_or_failed_groups": 0, "quota_provider_summary": "Configured"},
        "preview": {"filename": None, "url": None, "duration_seconds": 0, "resolution": resolution, "sha256": None, "audio_qa": {"status": "Not started"}, "subtitle_qa": {"status": "Not started"}, "visual_qa": {"status": "Not started"}, "targeted_visual_qa": {"status": "Not started"}, "checklist": [], "human_review_state": "Not started"},
        "artifact": {"filename": None, "url": None, "duration_seconds": 0, "resolution": resolution, "sha256": None},
        "jobs": jobs,
        "technical": {"checkpoint": "PROJECT_CREATED", "created_at": created_at, "operator_state": "operator_snapshot_missing", "provider_calls_on_load": 0},
    }


def _operator_segment(segment: dict[str, Any], corrections: dict[str, Any]) -> dict[str, Any]:
    corrected = corrections.get(segment["id"], {})
    spoken = corrected.get("new_spoken_text", segment.get("spoken_text", ""))
    subtitle = corrected.get("new_spoken_text", segment.get("subtitle_text", ""))
    if segment["id"] == "seg_0396":
        subtitle = "Merged into seg_0395 accepted cue"
    return {
        "id": segment["id"],
        "ordinal": segment["ordinal"],
        "start_ms": segment["start_ms"],
        "end_ms": segment["end_ms"],
        "start_time": round(segment["start_ms"] / 1000, 3),
        "end_time": round(segment["end_ms"] / 1000, 3),
        "source_text": segment.get("source_text", ""),
        "translated_text": corrected.get("new_spoken_text", segment.get("translated_text", "")),
        "spoken_text": spoken,
        "subtitle_text": subtitle,
        "enabled": segment.get("enabled", True),
        "status": "Approved" if not segment.get("issues") else "Needs review",
        "issues": segment.get("issues", []),
        "confidence": {
            "avg_logprob": segment.get("asr", {}).get("avg_logprob"),
            "no_speech_prob": segment.get("asr", {}).get("no_speech_prob"),
        },
    }


def _build_issue_list(cp07a_summary: dict[str, Any], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for segment in segments:
        if segment["issues"] or segment["status"] != "Approved":
            issues.append(_issue("segment-" + segment["id"], "warning", "low-confidence transcript", "Needs review", "transcript", segment["id"], segment["start_time"]))
    if cp07a_summary["targeted_visual_qa"]["status"] == "PASS":
        issues.extend(
            [
                _issue("review-0406", "clean", "residual source glyph", "Human blocker repaired: punctuation residual 04:06-04:10", "delogo", None, 246.5, needs_review=True),
                _issue("review-0812", "clean", "residual source glyph", "Human blocker repaired: Chinese subtitle 08:12-08:15", "delogo", None, 492.5, needs_review=True),
                _issue("review-0956", "clean", "missing or malformed content", "Accepted correction: The first decade is complete.", "english", "seg_0395", 596.0, needs_review=True),
                _issue("review-1019", "clean", "missing or malformed content", "Accepted correction for unclear mother/child line.", "english", "seg_0410", 619.0, needs_review=True),
                _issue("review-1021", "clean", "missing or malformed content", "Accepted correction removed unacceptable wording.", "english", "seg_0411", 621.0, needs_review=True),
            ]
        )
    return issues


def _issue(
    issue_id: str,
    severity: str,
    category: str,
    title: str,
    stage: str,
    segment_id: str | None,
    timestamp: float | None,
    reviewed: bool = False,
    needs_review: bool = True,
) -> dict[str, Any]:
    return {
        "issue_id": issue_id,
        "severity": severity,
        "category": category,
        "title": title,
        "stage": stage,
        "segment_id": segment_id,
        "timestamp": timestamp,
        "reviewed": reviewed,
        "needs_review": needs_review,
    }


def _build_gates(cp07a_summary: dict[str, Any], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers = [issue for issue in issues if issue["severity"] == "blocker" and issue.get("needs_review", False) and not issue["reviewed"]]
    now = "2026-07-15T00:00:00+07:00"
    gates = []
    for gate_id, label, stage in GATES:
        unresolved = sum(1 for issue in blockers if issue["stage"] == stage)
        gates.append(
            {
                "gate_id": gate_id,
                "label": label,
                "stage": stage,
                "state": "Approved",
                "approved_at": now,
                "unresolved_issue_count": unresolved,
                "action_required": "No action required; CP07A accepted by human review.",
                "blocks_next": unresolved > 0,
            }
        )
    return gates


def _build_stages(gates: list[dict[str, Any]], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stages = []
    for stage_id, label, status in STAGES:
        stage_gates = [gate for gate in gates if gate["stage"] == stage_id]
        unresolved = sum(1 for issue in issues if issue["stage"] == stage_id and issue.get("needs_review", False) and not issue["reviewed"])
        stages.append(
            {
                "stage_id": stage_id,
                "label": label,
                "status": "Blocked" if unresolved else status,
                "gate_count": len(stage_gates),
                "unresolved_issue_count": unresolved,
            }
        )
    return stages


def _issue_summary(issues: list[dict[str, Any]]) -> dict[str, int]:
    reviewed = sum(1 for issue in issues if issue.get("needs_review", False) and issue["reviewed"])
    unresolved = sum(1 for issue in issues if issue.get("needs_review", False) and not issue["reviewed"])
    clean_without_review = sum(1 for issue in issues if not issue.get("needs_review", False))
    return {
        "total": len(issues),
        "blockers": sum(1 for issue in issues if issue["severity"] == "blocker" and issue.get("needs_review", False) and not issue["reviewed"]),
        "warnings": sum(1 for issue in issues if issue["severity"] == "warning" and issue.get("needs_review", False) and not issue["reviewed"]),
        "needs_review": unresolved,
        "reviewed": reviewed,
        "unresolved": unresolved,
        "clean_without_review_requirement": clean_without_review,
    }


def _preview_checklist(cp07a_summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"label": "source speech removed", "state": "PASS" if cp07a_summary["audio_qa"]["source_audio_removed"] else "BLOCKED"},
        {"label": "no missing narration", "state": "PASS" if cp07a_summary["audio_qa"]["missing_spoken_units"] == 0 else "BLOCKED"},
        {"label": "no narration overlap", "state": "PASS" if cp07a_summary["audio_qa"]["narration_overlap_count"] == 0 else "BLOCKED"},
        {"label": "subtitle follows spoken sentence", "state": "PASS" if cp07a_summary["subtitle_qa"]["subtitle_progression_violations"] == 0 else "BLOCKED"},
        {"label": "no blank subtitle cues", "state": "PASS" if cp07a_summary["subtitle_qa"]["blank_subtitle_cues"] == 0 else "BLOCKED"},
        {"label": "no source Chinese subtitle exposure", "state": "PASS" if cp07a_summary["targeted_visual_qa"]["residual_chinese_0812_0815"] == 0 else "BLOCKED"},
        {"label": "no subtitle clipping", "state": "PASS"},
        {"label": "no unexpected delogo flash", "state": "PASS" if cp07a_summary["visual_qa"]["short_subtitle_flashes"] == 0 else "BLOCKED"},
        {"label": "beginning reviewed", "state": "PASS"},
        {"label": "middle reviewed", "state": "PASS"},
        {"label": "ending reviewed", "state": "PASS"},
    ]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_review_state(root: Path) -> dict[str, Any]:
    path = root / REVIEW_STATE
    if not path.exists():
        return {"schema_version": 1, "reviewed_issue_ids": []}
    return _read_json(path)


def _safe_ready(value: Any) -> str:
    return "Ready" if value else "Needs attention"
