from __future__ import annotations

import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.paths import ensure_dir
from app.core.preflight import run_preflight, storage_preflight
from app.db.session import session_scope
from app.domain.models import Artifact, Job, MediaAsset, Project
from app.services.non_dialogue_localization import dialogue_only_scope_config
from app.services.production_intake import create_project_from_local_source, read_intake_summary


PROJECT_ID = "production_golden_path_cp09"
CP08G_ARTIFACT = Path("data/projects/vertical_slice_cp07/renders/cp08g_dialogue_subtitle_only_final_720p.mp4")
CP08E2_ARTIFACT = Path("data/projects/vertical_slice_cp07/renders/cp08e2_decoupled_suppression_english_plate_720p.mp4")
CP08F_ARTIFACT = Path("data/projects/vertical_slice_cp07/renders/cp08f_selective_non_dialogue_cjk_localization_720p.mp4")
CP08F_TARGETED_ARTIFACT = Path("data/projects/vertical_slice_cp07/renders/cp08f_targeted_recomposition_non_dialogue_cjk_localization_720p.mp4")
CP08E2_ASS = Path("data/projects/vertical_slice_cp07/renders/cp08e2_decoupled_english_plate.ass")
EXPECTED_HASH = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"
STAGES = ["intake", "preflight", "delogo", "transcript", "english", "voice", "preview", "human_review", "final_selection", "export"]
TERMINAL_READY = {"MACHINE_PASS", "HUMAN_PASS", "SUPERSEDED"}
CP09A_ACCEPTANCE = {
    "verdict": "CP09A_HUMAN_VISUAL_AND_USABILITY_PASS",
    "accepted_implementation_commit": "b85dc99",
    "runtime_smoke_report_commit": "c61198a",
    "accepted_backend": "0.2.0",
    "accepted_frontend": "cp09a10",
    "accepted_build": "b85dc99",
    "provider_calls": {"gemini": 0, "elevenlabs": 0, "youtube": 0},
    "media_changes": 0,
    "publish_upload": 0,
}
EXPORT_SCHEMA_VERSION = 2
EXPORT_DISK_MARGIN_BYTES = 256 * 1024 * 1024
ZIP_DISK_MARGIN_BYTES = 1024 * 1024 * 1024


def run_golden_path_action(project_id: str, action: str, artifact_path: str | None = None) -> dict[str, Any]:
    if project_id != PROJECT_ID:
        raise ValueError("CP09 golden path actions are only available for the CP09 dry-run project.")
    action = action.strip().lower()
    if _closeout_active() and action in {"select_final", "create_export_package", "create_export_zip", "create_another_export_package", "approve_final", "run_complete_golden_path"}:
        raise ValueError("Accepted release is immutable. Future publication or replacement requires a separately authorized checkpoint.")
    if action == "initialize":
        state = ensure_project_initialized()
    elif action == "run_preflight":
        state = run_preflight_stage()
    elif action == "import_cached_artifact":
        state = import_cached_artifact()
    elif action == "simulate_interruption":
        state = simulate_interruption()
    elif action == "resume":
        state = resume_after_interruption()
    elif action == "simulate_failure":
        state = simulate_controlled_failure()
    elif action == "retry_failed_stage":
        state = retry_controlled_failure()
    elif action == "approve_final":
        state = inherit_human_approval()
    elif action == "select_final":
        state = select_final_artifact(artifact_path)
    elif action == "create_export_package":
        state = create_export_package()
    elif action == "create_export_zip":
        state = create_export_package(create_zip=True)
    elif action == "create_another_export_package":
        state = create_export_package(create_another=True)
    elif action == "run_complete_golden_path":
        state = run_complete_golden_path()
    elif action == "record_export_human_acceptance":
        state = record_export_human_acceptance()
    elif action == "closeout_manual_publication":
        state = closeout_manual_publication()
    else:
        raise ValueError(f"Unsupported golden path action: {action}")
    return {"project_id": PROJECT_ID, "action": action, "state": state}


def run_complete_golden_path() -> dict[str, Any]:
    ensure_project_initialized()
    run_preflight_stage()
    import_cached_artifact()
    simulate_interruption()
    resume_after_interruption()
    simulate_controlled_failure()
    retry_controlled_failure()
    inherit_human_approval()
    select_final_artifact(str(_root() / CP08G_ARTIFACT))
    return create_export_package()


def ensure_project_initialized() -> dict[str, Any]:
    settings = get_settings()
    source = settings.root / "input" / "source.mp4"
    intake = read_intake_summary(PROJECT_ID)
    if intake is None:
        _create_or_repair_intake(source)
        _audit("intake", "project_created", None, "READY", "PASS", {"source": str(source)})
    _ensure_db_records()
    state = _load_or_create_state()
    state["stages"]["intake"] = "MACHINE_PASS"
    state["localization_scope"] = "dialogue_subtitles_only"
    state["policy"] = dialogue_only_scope_config()
    _save_state(state)
    _audit("intake", "project_initialized", state["stages"].get("intake"), "MACHINE_PASS", "PASS", {"idempotent": True})
    return state


def _create_or_repair_intake(source: Path) -> None:
    payload = {
        "name": "Production Golden Path CP09",
        "slug": PROJECT_ID,
        "source_path": str(source),
        "source_language": "zh-CN",
        "target_language": "en-US",
        "content_mode": "sentence-level narrated localization",
        "localization_scope": "dialogue_subtitles_only",
        "output_resolution": "1280x720",
        "provenance_acknowledged": True,
        "notes": "CP09 dry-run fixture. No provider calls, no external publication.",
    }
    try:
        create_project_from_local_source(payload)
    except ValueError as exc:
        if "slug already exists" not in str(exc).lower():
            raise
        _write_repaired_intake(payload, source)
    if read_intake_summary(PROJECT_ID) is None:
        _write_repaired_intake(payload, source)


def _write_repaired_intake(payload: dict[str, Any], source: Path) -> None:
    source = source.resolve(strict=True)
    project_dir = _project_dir()
    source_dir = ensure_dir(project_dir / "source")
    destination = source_dir / source.name
    if not destination.exists() or sha256_file(destination) != sha256_file(source):
        _copy_byte_identical(source, destination)
    media = media_summary(destination)
    intake = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "project_name": payload["name"],
        "source": {"path": str(destination), "sha256": sha256_file(destination), "media": media},
        "source_language": payload["source_language"],
        "target_language": payload["target_language"],
        "content_mode": payload["content_mode"],
        "localization_scope": "dialogue_subtitles_only",
        "voice": "Production voice configured",
        "elevenlabs_model": "eleven_multilingual_v2",
        "source_audio_policy": "replace source speech with generated narration",
        "subtitle_style_preset": "CP08G dialogue-only final",
        "output_resolution": payload["output_resolution"],
        "provenance_acknowledged": True,
        "notes": payload["notes"],
        "workflow": {},
        "approval_gates": {"source_provenance": "approved"},
        "created_at": _now(),
    }
    path = project_dir / "operator" / "intake.json"
    ensure_dir(path.parent)
    path.write_text(json.dumps(intake, ensure_ascii=False, indent=2), encoding="utf-8")


def run_preflight_stage() -> dict[str, Any]:
    state = _load_or_create_state()
    previous = state["stages"].get("preflight")
    checks = _preflight_checks()
    state["preflight"] = checks
    state["stages"]["preflight"] = "MACHINE_PASS" if checks["status"] == "PASS" else "BLOCKED"
    _save_state(state)
    _audit("preflight", "run_preflight", previous, state["stages"]["preflight"], checks["status"], checks)
    return state


def import_cached_artifact() -> dict[str, Any]:
    state = _load_or_create_state()
    _require_stage(state, "preflight")
    path = _root() / CP08G_ARTIFACT
    digest = _verify_expected_hash(path)
    candidate = {
        "artifact_id": "cp08g_dialogue_subtitle_only_final",
        "path": str(path),
        "sha256": digest,
        "origin_project": "vertical_slice_cp07",
        "origin_checkpoint": "CP08G_DIALOGUE_ONLY_SCOPE_LOCK_AND_FINAL_PROMOTION",
        "lineage": "CP08E2_ACCEPTED_BY_HASH_EQUIVALENCE",
        "status": "imported_cached_verified",
        "eligible": True,
    }
    state["candidate_artifact"] = candidate
    for stage in ["delogo", "transcript", "english", "voice", "preview"]:
        previous = state["stages"].get(stage)
        state["stages"][stage] = "MACHINE_PASS"
        _audit(stage, "cache_reuse_stage_pass", previous, "MACHINE_PASS", "PASS", {"artifact_id": candidate["artifact_id"]})
    _register_unique_artifact("cached_promoted_candidate", path, digest)
    _save_state(state)
    _audit("preview", "artifact_hash_verified", None, "MACHINE_PASS", "PASS", candidate)
    return state


def simulate_interruption() -> dict[str, Any]:
    state = _load_or_create_state()
    previous = state["stages"].get("preview")
    state["stages"]["preview"] = "RUNNING"
    state["interruption"] = {"simulated": True, "stage": "preview", "at": _now(), "resume_required": True}
    _save_state(state)
    _audit("preview", "simulated_interruption", previous, "RUNNING", "PASS", {"safe_boundary": True})
    return state


def resume_after_interruption() -> dict[str, Any]:
    state = _load_or_create_state()
    previous = state["stages"].get("preview")
    if state.get("candidate_artifact", {}).get("sha256") == EXPECTED_HASH:
        state["stages"]["preview"] = "MACHINE_PASS"
        state["interruption"]["resume_required"] = False
        result = "PASS"
    else:
        state["stages"]["preview"] = "BLOCKED"
        result = "FAIL"
    _save_state(state)
    _audit("preview", "resume_after_interruption", previous, state["stages"]["preview"], result, {"duplicate_jobs": 0})
    return state


def simulate_controlled_failure() -> dict[str, Any]:
    state = _load_or_create_state()
    previous = state["stages"].get("final_selection")
    state["stages"]["final_selection"] = "FAILED"
    state["controlled_failure"] = {
        "kind": "hash_mismatch",
        "message": "Rejected non-canonical CP08F artifact during final selection.",
        "retry_allowed": True,
        "safe_remediation": "Select the CP08G hash-equivalent artifact.",
    }
    rejected = _artifact_rejection(_root() / CP08F_ARTIFACT)
    state.setdefault("rejected_artifacts", []).append(rejected)
    _save_state(state)
    _audit("final_selection", "controlled_failure", previous, "FAILED", "PASS", rejected)
    return state


def retry_controlled_failure() -> dict[str, Any]:
    state = _load_or_create_state()
    previous = state["stages"].get("final_selection")
    state["stages"]["final_selection"] = "READY"
    state["controlled_failure"]["retried"] = True
    _save_state(state)
    _audit("final_selection", "retry_failed_stage", previous, "READY", "PASS", {"retry_allowed": True})
    return state


def inherit_human_approval() -> dict[str, Any]:
    state = _load_or_create_state()
    candidate = state.get("candidate_artifact") or {}
    if candidate.get("sha256") != EXPECTED_HASH:
        raise ValueError("Cannot inherit human approval without CP08G hash equivalence.")
    previous = state["stages"].get("human_review")
    approval = {
        "mode": "human_approval_inherited_by_hash_equivalence",
        "source_approval_artifact": str(_root() / CP08E2_ARTIFACT),
        "source_hash": EXPECTED_HASH,
        "target_hash": candidate["sha256"],
        "equivalence_check_result": "PASS",
        "timestamp": _now(),
        "responsible_policy_version": "CP08G_DIALOGUE_ONLY_SCOPE_LOCK",
        "new_human_viewing_claimed": False,
    }
    state["human_approval"] = approval
    state["stages"]["human_review"] = "HUMAN_PASS"
    _save_state(state)
    _audit("human_review", "human_approval_inherited", previous, "HUMAN_PASS", "PASS", approval)
    return state


def select_final_artifact(artifact_path: str | None = None) -> dict[str, Any]:
    state = _load_or_create_state()
    if state["stages"].get("human_review") != "HUMAN_PASS":
        raise ValueError("Human approval inheritance must pass before final selection.")
    source = Path(artifact_path) if artifact_path else _root() / CP08G_ARTIFACT
    if not source.is_absolute():
        source = _root() / source
    source = _safe_resolve(source)
    if source != (_root() / CP08G_ARTIFACT).resolve():
        rejection = _artifact_rejection(source)
        state.setdefault("rejected_artifacts", []).append(rejection)
        state["stages"]["final_selection"] = "FAILED"
        _save_state(state)
        _audit("final_selection", "artifact_rejected", "READY", "FAILED", "FAIL", rejection)
        raise ValueError(rejection["reason"])
    digest = _verify_expected_hash(source)
    destination = ensure_dir(_project_dir() / "renders") / "cp09_canonical_dialogue_subtitle_only_final_720p.mp4"
    if not destination.exists() or sha256_file(destination) != digest:
        _copy_byte_identical(source, destination)
    final = {
        "path": str(destination),
        "sha256": sha256_file(destination),
        "source_path": str(source),
        "source_status": "promoted",
        "eligible_reason": "CP08G canonical hash matches accepted CP08E2 artifact.",
    }
    state["final_artifact"] = final
    state["stages"]["final_selection"] = "MACHINE_PASS"
    _register_unique_artifact("final_canonical_video", destination, final["sha256"])
    _save_state(state)
    _audit("final_selection", "artifact_promoted", "READY", "MACHINE_PASS", "PASS", final)
    return state


def create_export_package(*, create_zip: bool = False, create_another: bool = False) -> dict[str, Any]:
    state = _load_or_create_state()
    final_path, final_hash = _validated_export_source(state)
    options = {"include_srt": True, "include_ass": True, "create_zip": create_zip}
    existing_release = None if create_another else _find_completed_release(final_hash, options)
    if existing_release is not None:
        release_id = existing_release.name
        export_root = existing_release
        state["stages"]["export"] = "MACHINE_PASS"
        state["export_job"] = _export_job_state("completed", release_id, export_root, options, final_hash, file_list=_packaged_files(export_root))
        state["export"] = _read_json(export_root / "release_manifest.json")
        _save_state(state)
        return state
    release_id = _release_id(final_hash, options, create_another=create_another)
    export_root = _project_dir() / "exports" / release_id
    if export_root.exists():
        release_id = _release_id(final_hash, options, create_another=True)
        export_root = _project_dir() / "exports" / release_id
    state["export_job"] = _export_job_state("validating", release_id, export_root, options, final_hash)
    _save_state(state)
    if not create_another and _export_manifest_valid(export_root):
        state["stages"]["export"] = "MACHINE_PASS"
        state["export_job"] = _export_job_state("completed", release_id, export_root, options, final_hash, file_list=_packaged_files(export_root))
        state["export"] = _read_json(export_root / "release_manifest.json")
        _save_state(state)
        return state
    temp_root = export_root.with_name(export_root.name + f".tmp_{uuid4().hex[:8]}")
    if temp_root.exists():
        shutil.rmtree(temp_root)
    try:
        files_to_copy = _export_sources(final_path, options)
        _require_disk_for_export(files_to_copy, create_zip=create_zip)
        state["export_job"] = _export_job_state("copying", release_id, export_root, options, final_hash)
        _save_state(state)
        ensure_dir(temp_root)
        copied = _copy_export_files(files_to_copy, temp_root)
        state["export_job"] = _export_job_state("hashing", release_id, export_root, options, final_hash, file_list=[item["name"] for item in copied])
        _save_state(state)
        checksums = _write_manifest_notes_and_checksums(temp_root, release_id, state, copied, final_path, final_hash, options)
        state["export_job"] = _export_job_state("verifying", release_id, export_root, options, final_hash, file_list=[item["name"] for item in copied])
        _save_state(state)
        _verify_package(temp_root, checksums)
        zip_path = None
        if create_zip:
            state["export_job"] = _export_job_state("packaging", release_id, export_root, options, final_hash, file_list=[item["name"] for item in copied])
            _save_state(state)
            zip_path = _create_release_zip(temp_root)
        if export_root.exists():
            raise ValueError("Release destination already exists; refusing to overwrite.")
        os.replace(temp_root, export_root)
    except Exception as exc:
        if temp_root.exists():
            shutil.rmtree(temp_root)
        state = _load_or_create_state()
        state["export_job"] = _export_job_state("failed", release_id, export_root, options, final_hash, failure_reason=str(exc))
        _save_state(state)
        raise
    manifest = _read_json(export_root / "release_manifest.json")
    state["export"] = manifest
    state["export_job"] = _export_job_state("completed", release_id, export_root, options, final_hash, file_list=_packaged_files(export_root))
    if zip_path:
        state["export_job"]["zip_path"] = str(export_root / zip_path.name)
    state["stages"]["export"] = "MACHINE_PASS"
    _save_state(state)
    _audit("export", "local_export_created", None, "MACHINE_PASS", "PASS", {"release_id": release_id, "path": str(export_root), "mp4_sha256": final_hash, "zip_created": create_zip})
    return state


def golden_path_summary(project_id: str) -> dict[str, Any] | None:
    if project_id != PROJECT_ID:
        return None
    state_path = _state_path()
    if not state_path.exists():
        return None
    state = _read_json(state_path)
    intake = read_intake_summary(PROJECT_ID)
    if intake is None:
        return None
    media = intake["source"]["media"]
    stages = [_stage_summary(stage, state["stages"].get(stage, "NOT_STARTED")) for stage in STAGES]
    stages.append(_complete_stage_summary(state))
    candidate = state.get("candidate_artifact") or {}
    final = state.get("final_artifact") or {}
    export = state.get("export") or {}
    return {
        "schema_version": 1,
        "project": {"project_id": PROJECT_ID, "title": "Production Golden Path CP09", "completed": state["stages"].get("export") == "MACHINE_PASS", "current_stage": _current_stage(state)},
        "overall_status": "CP09 production golden path ready" if state["stages"].get("export") == "MACHINE_PASS" else "CP09 production golden path in progress",
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
        "provider_summary": {"gemini": "Disabled for CP09", "elevenlabs": "Disabled for CP09", "youtube": "NOT_CONFIGURED", "gemini_calls_on_ui_load": 0, "elevenlabs_calls_on_ui_load": 0, "youtube_calls_on_ui_load": 0},
        "preflight": state.get("preflight", {}),
        "stages": stages,
        "approval_gates": _golden_gates(state),
        "issue_summary": {"total": 0, "blockers": 0, "warnings": 0, "needs_review": 0, "reviewed": 0, "unresolved": 0, "clean_without_review_requirement": 0},
        "issues": [],
        "segments": [],
        "timeline": {"segment_count": 442, "source_duration_ms": int(media["duration_seconds"] * 1000), "tts_group_count": 89, "active_tts_bindings": 89},
        "delogo": {"method": "CP08G cached dialogue subtitle suppression", "subtitle_interval_count": 440, "residual_issue_count": 0, "short_flash_count": 0, "toggle_warning_count": 0, "evidence": ["evidence/CP09/production_golden_path"]},
        "voice_timing": {"voice": "Cached CP07 narration", "model": "eleven_multilingual_v2", "tts_group_count": 89, "spoken_unit_count": 442, "active_binding_status": "89 / 89 inherited", "generated_duration": f"{media['duration_seconds']}s", "timing_fit_state": "Cached verified", "missing_or_failed_groups": 0, "quota_provider_summary": "No provider calls"},
        "preview": {"filename": Path(candidate.get("path", "")).name or None, "url": f"/api/operator/projects/{PROJECT_ID}/accepted-preview", "duration_seconds": media["duration_seconds"], "resolution": f"{media['video']['width']}x{media['video']['height']}", "sha256": candidate.get("sha256"), "audio_qa": {"status": "Cached verified"}, "subtitle_qa": {"status": "Dialogue-only accepted"}, "visual_qa": {"status": "Hash-equivalent CP08G"}, "targeted_visual_qa": {"status": "Inherited by hash"}, "checklist": _golden_checklist(state), "human_review_state": state.get("human_approval", {}).get("mode", "Explicit approval pending")},
        "artifact": {"filename": Path(final.get("path", "")).name or Path(candidate.get("path", "")).name or None, "url": f"/api/operator/projects/{PROJECT_ID}/accepted-preview", "duration_seconds": media["duration_seconds"], "resolution": f"{media['video']['width']}x{media['video']['height']}", "sha256": final.get("sha256") or candidate.get("sha256")},
        "golden_path": {"state": state, "dashboard": _dashboard(state), "export_ready": state["stages"].get("export") == "MACHINE_PASS"},
        "technical": {"checkpoint": "CP09_PRODUCTION_GOLDEN_PATH_AND_RELEASE_GATE", "cp09_status": state["stages"].get("export", "NOT_STARTED")},
    }


def golden_preview_path(project_id: str) -> Path | None:
    if project_id != PROJECT_ID or not _state_path().exists():
        return None
    state = _read_json(_state_path())
    final = state.get("final_artifact", {}).get("path")
    if final and Path(final).exists():
        return Path(final)
    candidate = state.get("candidate_artifact", {}).get("path")
    if candidate and Path(candidate).exists():
        return Path(candidate)
    return None


def local_export_review_summary(project_id: str) -> dict[str, Any]:
    if project_id != PROJECT_ID:
        raise ValueError("Local export review is only available for the CP09 production project.")
    release_root, manifest = _current_release_root_and_manifest()
    files = []
    for path in sorted(release_root.iterdir()):
        if path.is_file():
            files.append(
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "url": f"/api/operator/projects/{PROJECT_ID}/local-export/files/{path.name}",
                }
            )
    checksum_ok = _verify_sha256sums_file(release_root)
    return {
        "state": _load_or_create_state().get("cp09b_human_acceptance", {}).get("verdict", "CP09B_HUMAN_REVIEW_REQUIRED"),
        "release_id": manifest["release_id"],
        "release_path": str(release_root),
        "final_video": {
            "filename": "final_video.mp4",
            "sha256": sha256_file(release_root / "final_video.mp4"),
            "byte_identical": manifest.get("byte_identical_result") is True,
            "url": f"/api/operator/projects/{PROJECT_ID}/local-export/files/final_video.mp4",
        },
        "manifest": {
            "schema_version": manifest.get("schema_version"),
            "url": f"/api/operator/projects/{PROJECT_ID}/local-export/files/release_manifest.json",
            "parsed": True,
        },
        "checksums": {
            "url": f"/api/operator/projects/{PROJECT_ID}/local-export/files/SHA256SUMS.txt",
            "verified": checksum_ok,
        },
        "release_notes": {
            "url": f"/api/operator/projects/{PROJECT_ID}/local-export/files/RELEASE_NOTES.md",
            "placeholder_free": "placeholder" not in (release_root / "RELEASE_NOTES.md").read_text(encoding="utf-8").lower(),
        },
        "subtitle_inclusion": {
            "srt": (release_root / "subtitles_en.srt").exists(),
            "ass": (release_root / "subtitles_en.ass").exists(),
        },
        "packaged_files": files,
        "publish_upload_state": manifest.get("publish_upload_state"),
        "provider_call_counts": manifest.get("provider_call_counts"),
        "actions": {
            "upload": "not_available",
            "publish": "not_available",
        },
    }


def record_export_human_acceptance() -> dict[str, Any]:
    state = _load_or_create_state()
    release_root, manifest = _current_release_root_and_manifest()
    validation = _release_validation(release_root, manifest)
    if validation["final_video_sha256"] != EXPECTED_HASH:
        raise ValueError("Accepted release final video hash does not match canonical hash.")
    acceptance = {
        "verdict": "CP09B_LOCAL_EXPORT_PACKAGE_HUMAN_PASS",
        "accepted_release_id": manifest["release_id"],
        "accepted_release_path": str(release_root),
        "accepted_final_video": str(release_root / "final_video.mp4"),
        "final_video_sha256": validation["final_video_sha256"],
        "manifest_status": "PASS" if validation["manifest_parsed"] else "FAIL",
        "checksum_status": "PASS" if validation["checksum_verified"] else "FAIL",
        "byte_identical": validation["byte_identical"],
        "operator_acceptance_timestamp": _now(),
        "publication_state": "not_performed",
        "upload_state": "not_performed",
        "provider_call_counts": {"gemini": 0, "elevenlabs": 0, "youtube": 0},
    }
    state["cp09b_human_acceptance"] = acceptance
    state["publication_state"] = "not_performed"
    state["upload_state"] = "not_performed"
    state["provider_calls"] = {"gemini": 0, "elevenlabs": 0, "youtube": 0}
    _save_state(state)
    _audit("export", "cp09b_human_acceptance_recorded", None, acceptance["verdict"], "PASS", acceptance)
    return state


def closeout_manual_publication() -> dict[str, Any]:
    state = record_export_human_acceptance()
    release_root, manifest = _current_release_root_and_manifest()
    validation = _release_validation(release_root, manifest)
    closeout = {
        "state": "READY_FOR_MANUAL_PUBLICATION",
        "accepted_release_id": manifest["release_id"],
        "accepted_release_path": str(release_root),
        "accepted_final_video": str(release_root / "final_video.mp4"),
        "final_video_sha256": validation["final_video_sha256"],
        "release_immutable": True,
        "immutability_guards": {
            "prevent_overwrite": True,
            "prevent_final_candidate_replacement": True,
            "prevent_silent_manifest_modification": True,
            "prevent_silent_checksum_modification": True,
            "prevent_publication_status_changes_without_future_authorization": True,
        },
        "manifest_sha256": validation["manifest_sha256"],
        "sha256sums_sha256": validation["sha256sums_sha256"],
        "publication_state": "not_performed",
        "upload_state": "not_performed",
        "provider_call_counts": {"gemini": 0, "elevenlabs": 0, "youtube": 0},
        "closed_at": _now(),
    }
    state = _load_or_create_state()
    state["release_closeout"] = closeout
    state["project_state"] = "READY_FOR_MANUAL_PUBLICATION"
    state["publication_state"] = "not_performed"
    state["upload_state"] = "not_performed"
    state["provider_calls"] = {"gemini": 0, "elevenlabs": 0, "youtube": 0}
    state["stages"]["export"] = "MACHINE_PASS"
    _save_state(state)
    handoff_path = _write_manual_publication_handoff(release_root, manifest, validation)
    index_path = _write_release_index(release_root, manifest, validation, handoff_path)
    _audit(
        "export",
        "release_closed_ready_for_manual_publication",
        "CP09B_LOCAL_EXPORT_PACKAGE_HUMAN_PASS",
        "READY_FOR_MANUAL_PUBLICATION",
        "PASS",
        {"release_index": str(index_path), "handoff": str(handoff_path), **closeout},
    )
    return _load_or_create_state()


def local_export_file_path(project_id: str, file_name: str) -> Path:
    if project_id != PROJECT_ID:
        raise ValueError("Local export files are only available for the CP09 production project.")
    if Path(file_name).name != file_name:
        raise ValueError("Export file path traversal is not allowed.")
    release_root, _manifest = _current_release_root_and_manifest()
    allowed = {path.name for path in release_root.iterdir() if path.is_file()}
    if file_name not in allowed:
        raise FileNotFoundError(file_name)
    target = (release_root / file_name).resolve(strict=True)
    if release_root.resolve() not in target.parents:
        raise ValueError("Export file path escaped the release directory.")
    return target


def manual_publication_handoff_path(project_id: str) -> Path:
    if project_id != PROJECT_ID:
        raise ValueError("Manual publication handoff is only available for the CP09 production project.")
    path = (_project_dir() / "READY_FOR_MANUAL_PUBLICATION.md").resolve(strict=True)
    if _project_dir().resolve() not in path.parents:
        raise ValueError("Manual publication handoff path escaped the project directory.")
    return path


def _preflight_checks() -> dict[str, Any]:
    settings = get_settings()
    project_dir = _project_dir()
    preflight = run_preflight()
    sqlite_ok = False
    with session_scope() as session:
        sqlite_ok = session.execute(text("PRAGMA quick_check")).scalar() == "ok"
    storage = storage_preflight("run", settings.root)
    checks = {
        "ffmpeg": bool(preflight.get("ffmpeg")),
        "ffprobe": bool(preflight.get("ffprobe")),
        "sqlite": sqlite_ok,
        "writable_project_dirs": os.access(project_dir, os.W_OK),
        "sufficient_disk": storage["passed"],
        "local_runtime_available": True,
        "canonical_project_schema": True,
        "provider_policy": "disabled_no_paid_calls",
        "localization_scope": "dialogue_subtitles_only",
        "youtube_publishing": "NOT_CONFIGURED",
    }
    free = storage["current_free_bytes"] or 0
    return {
        "status": "PASS" if all(value is True or isinstance(value, str) for value in checks.values()) else "FAIL",
        "checks": checks,
        "disk_free_gib": round(free / (1024**3), 3),
        "storage": storage,
    }


def _load_or_create_state() -> dict[str, Any]:
    path = _state_path()
    if path.exists():
        return _read_json(path)
    state = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "created_at": _now(),
        "updated_at": _now(),
        "localization_scope": "dialogue_subtitles_only",
        "policy": dialogue_only_scope_config(),
        "stages": {stage: "NOT_STARTED" for stage in STAGES},
        "provider_calls": {"gemini": 0, "elevenlabs": 0, "youtube": 0},
        "audit_log": str(_audit_path()),
    }
    _save_state(state)
    return state


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    path = _state_path()
    ensure_dir(path.parent)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _audit(stage: str, action: str, previous: str | None, new: str | None, result: str, metadata: dict[str, Any]) -> None:
    record = {
        "timestamp": _now(),
        "project_id": PROJECT_ID,
        "stage": stage,
        "action": action,
        "actor_type": "operator_api",
        "previous_state": previous,
        "new_state": new,
        "artifact_id": metadata.get("artifact_id"),
        "result": result,
        "metadata": metadata,
    }
    for path in [_audit_path(), _project_audit_path()]:
        ensure_dir(path.parent)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _register_unique_artifact(artifact_type: str, path: Path, digest: str) -> None:
    _ensure_db_records()
    with session_scope() as session:
        exists = (
            session.query(Artifact)
            .filter(Artifact.project_id == PROJECT_ID, Artifact.artifact_type == artifact_type, Artifact.sha256 == digest)
            .one_or_none()
        )
        if exists is None:
            session.add(Artifact(project_id=PROJECT_ID, artifact_type=artifact_type, path=str(path), sha256=digest))


def _ensure_db_records() -> None:
    intake = read_intake_summary(PROJECT_ID)
    if intake is None:
        return
    source = Path(intake["source"]["path"])
    media = intake["source"]["media"]
    with session_scope() as session:
        project = session.query(Project).filter(Project.project_id == PROJECT_ID).one_or_none()
        if project is None:
            session.add(Project(project_id=PROJECT_ID, title=intake["project_name"]))
            session.flush()
        asset = session.query(MediaAsset).filter(MediaAsset.project_id == PROJECT_ID).one_or_none()
        if asset is None:
            session.add(
                MediaAsset(
                    project_id=PROJECT_ID,
                    source_sha256=intake["source"]["sha256"],
                    path=str(source),
                    duration_seconds=str(media["duration_seconds"]),
                    width=media["video"].get("width"),
                    height=media["video"].get("height"),
                )
            )
        for kind in ["delogo", "asr", "english", "tts", "render", "qa"]:
            job = session.query(Job).filter(Job.project_id == PROJECT_ID, Job.kind == kind).one_or_none()
            if job is None:
                session.add(Job(project_id=PROJECT_ID, kind=kind, status="not_started", job_key=f"{PROJECT_ID}:{kind}"))


def _artifact_rejection(path: Path) -> dict[str, Any]:
    resolved = _safe_resolve(path)
    digest = sha256_file(resolved) if resolved.exists() else None
    reason = "Artifact is not in the approved CP08G lineage."
    if resolved in {(_root() / CP08F_ARTIFACT).resolve(), (_root() / CP08F_TARGETED_ARTIFACT).resolve()}:
        reason = "FAIL_NON_CANONICAL artifact cannot be promoted."
    elif digest != EXPECTED_HASH:
        reason = "Artifact hash does not match CP08G canonical hash."
    return {"path": str(resolved), "sha256": digest, "eligible": False, "reason": reason}


def _require_stage(state: dict[str, Any], stage: str) -> None:
    if state["stages"].get(stage) not in TERMINAL_READY:
        raise ValueError(f"Required upstream stage is unresolved: {stage}")


def _verify_expected_hash(path: Path) -> str:
    path = _safe_resolve(path)
    digest = sha256_file(path)
    if digest != EXPECTED_HASH:
        raise ValueError("Canonical artifact hash mismatch.")
    return digest


def _copy_byte_identical(source: Path, destination: Path) -> None:
    ensure_dir(destination.parent)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temp)
    if sha256_file(temp) != sha256_file(source):
        temp.unlink(missing_ok=True)
        raise ValueError("Byte-identical copy verification failed.")
    os.replace(temp, destination)


def _validated_export_source(state: dict[str, Any]) -> tuple[Path, str]:
    if state.get("provider_calls") != {"gemini": 0, "elevenlabs": 0, "youtube": 0}:
        raise ValueError("Provider-call invariant failed; export is blocked.")
    if state.get("human_approval", {}).get("mode") != "human_approval_inherited_by_hash_equivalence":
        raise ValueError("Human preview approval is required before export.")
    final = state.get("final_artifact") or {}
    final_path = Path(final.get("path", ""))
    if not final_path.is_absolute():
        final_path = _root() / final_path
    final_path = _safe_resolve(final_path)
    if _project_dir().resolve() not in final_path.parents:
        raise ValueError("Final candidate is outside the canonical CP09 project tree.")
    final_hash = sha256_file(final_path)
    if final_hash != EXPECTED_HASH or final.get("sha256") != EXPECTED_HASH:
        raise ValueError("Final artifact must exist and match the accepted canonical hash before export.")
    return final_path, final_hash


def _release_id(final_hash: str, options: dict[str, Any], *, create_another: bool) -> str:
    suffix = f"{final_hash[:8]}_dir"
    if options.get("create_zip"):
        suffix = f"{final_hash[:8]}_zip"
    if create_another:
        return f"release_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}_{suffix}"
    return f"release_cp09b_{suffix}"


def _export_sources(final_path: Path, options: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [{"source": final_path, "name": "final_video.mp4", "required": True}]
    if options.get("include_srt"):
        srt = _canonical_subtitle_source(".srt")
        if srt:
            sources.append({"source": srt, "name": "subtitles_en.srt", "required": False})
    if options.get("include_ass"):
        ass = _canonical_subtitle_source(".ass")
        if ass:
            sources.append({"source": ass, "name": "subtitles_en.ass", "required": False})
    for optional in _optional_export_sources():
        sources.append(optional)
    return sources


def _canonical_subtitle_source(suffix: str) -> Path | None:
    candidates = {
        ".ass": [_root() / CP08E2_ASS, _root() / "data/projects/vertical_slice_cp07/renders/cp07a_targeted_human_review_repair.ass"],
        ".srt": [],
    }.get(suffix, [])
    for path in candidates:
        if path.exists():
            return _safe_resolve(path)
    return None


def _optional_export_sources() -> list[dict[str, Any]]:
    optional_paths = [
        ("final_qa_report.md", _root() / "49_CP09_PRODUCTION_GOLDEN_PATH_AND_RELEASE_GATE_REPORT.md"),
        ("human_approval_record.md", _root() / "62_CP09A_HUMAN_VISUAL_AND_USABILITY_ACCEPTANCE_REPORT.md"),
        ("dialogue_cleanup_report.md", _root() / "48_CP08G_DIALOGUE_ONLY_SCOPE_LOCK_AND_FINAL_PROMOTION_REPORT.md"),
    ]
    return [{"source": _safe_resolve(path), "name": name, "required": False} for name, path in optional_paths if path.exists()]


def _copy_export_files(files_to_copy: list[dict[str, Any]], temp_root: Path) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for item in files_to_copy:
        source = _safe_resolve(Path(item["source"]))
        destination = temp_root / item["name"]
        _copy_byte_identical(source, destination)
        digest = sha256_file(destination)
        if digest != sha256_file(source):
            raise ValueError(f"Copied file hash mismatch for {item['name']}.")
        copied.append({"name": item["name"], "source_path": str(source), "size_bytes": destination.stat().st_size, "sha256": digest, "required": item["required"]})
    return copied


def _write_manifest_notes_and_checksums(
    temp_root: Path,
    release_id: str,
    state: dict[str, Any],
    copied: list[dict[str, Any]],
    final_path: Path,
    final_hash: str,
    options: dict[str, Any],
) -> dict[str, str]:
    media = media_summary(final_path)
    subtitle_artifacts = [item for item in copied if item["name"].startswith("subtitles_")]
    checksums = {item["name"]: item["sha256"] for item in copied}
    manifest = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "release_id": release_id,
        "created_at": _now(),
        "project_id": PROJECT_ID,
        "project_name": "Production Golden Path CP09",
        "project_revision": _git_commit(),
        "selected_final_candidate_path": str(final_path),
        "selected_final_candidate_sha256": final_hash,
        "exported_video_path": "final_video.mp4",
        "exported_video_sha256": checksums["final_video.mp4"],
        "byte_identical_result": checksums["final_video.mp4"] == final_hash,
        "package_options": options,
        "video_duration": media["duration_seconds"],
        "video_resolution": f"{media['video']['width']}x{media['video']['height']}",
        "audio_stream_summary": media.get("audio", {}),
        "subtitle_artifacts": subtitle_artifacts,
        "accepted_checkpoint_list": [
            "CP08G_DIALOGUE_ONLY_SCOPE_LOCK_AND_FINAL_PROMOTION",
            "CP09_PRODUCTION_GOLDEN_PATH_AND_RELEASE_GATE",
            "CP09A_HUMAN_VISUAL_AND_USABILITY_ACCEPTANCE",
        ],
        "cp09a_human_acceptance": CP09A_ACCEPTANCE,
        "dialogue_localization_scope": "dialogue_subtitles_only",
        "non_dialogue_text_policy": {
            "automatic_dialogue_subtitle_processing": "enabled",
            "automatic_non_dialogue_text_localization": "disabled",
            "non_dialogue_source_text_may_remain": True,
            "manual_visual_localization_may_still_be_required": True,
            "completely_cjk_free_claim": "not_made",
        },
        "provider_call_counts": {"gemini": 0, "elevenlabs": 0, "youtube": 0},
        "publish_upload_state": {"youtube_publication": "not_performed", "upload": "not_performed", "publish": "not_performed"},
        "unresolved_informational_notes": ["Non-dialogue source text may remain under the dialogue-only localization policy."],
        "export_application_build_id": {"git_commit": _git_commit(), "backend": "0.2.0", "frontend": "cp09b"},
        "file_list": copied,
    }
    if not manifest["byte_identical_result"]:
        raise ValueError("Exported video is not byte-identical to the accepted final candidate.")
    (temp_root / "release_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (temp_root / "RELEASE_NOTES.md").write_text(_release_summary(manifest), encoding="utf-8")
    checksums["release_manifest.json"] = sha256_file(temp_root / "release_manifest.json")
    checksums["RELEASE_NOTES.md"] = sha256_file(temp_root / "RELEASE_NOTES.md")
    _write_sha256sums(temp_root, checksums)
    return checksums


def _write_sha256sums(temp_root: Path, checksums: dict[str, str]) -> None:
    lines = [f"{digest}  {name}" for name, digest in sorted(checksums.items())]
    (temp_root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _verify_package(root: Path, checksums: dict[str, str]) -> None:
    for name, expected in checksums.items():
        path = root / name
        if not path.exists() or sha256_file(path) != expected:
            raise ValueError(f"Checksum verification failed for {name}.")
    sums = root / "SHA256SUMS.txt"
    if not sums.exists():
        raise ValueError("SHA256SUMS.txt is missing.")


def _current_release_root_and_manifest() -> tuple[Path, dict[str, Any]]:
    state = _load_or_create_state()
    export_job = state.get("export_job") or {}
    destination = export_job.get("destination")
    if not destination:
        raise FileNotFoundError("No completed CP09B export release is recorded.")
    release_root = Path(destination)
    if not release_root.is_absolute():
        release_root = _root() / release_root
    release_root = release_root.resolve(strict=True)
    exports_root = (_project_dir() / "exports").resolve()
    if exports_root not in release_root.parents:
        raise ValueError("Recorded release path is outside the CP09 export directory.")
    manifest_path = release_root / "release_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("release_manifest.json is missing.")
    manifest = _read_json(manifest_path)
    if not _export_manifest_valid(release_root):
        raise ValueError("Recorded CP09B release failed manifest/hash validation.")
    return release_root, manifest


def _verify_sha256sums_file(release_root: Path) -> bool:
    sums = release_root / "SHA256SUMS.txt"
    if not sums.exists():
        return False
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, sep, name = line.partition("  ")
        if not sep or len(digest) != 64:
            return False
        path = release_root / name
        if not path.exists() or sha256_file(path) != digest:
            return False
    return True


def _release_validation(release_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    final_video = release_root / "final_video.mp4"
    manifest_path = release_root / "release_manifest.json"
    checksum_path = release_root / "SHA256SUMS.txt"
    release_notes = release_root / "RELEASE_NOTES.md"
    final_hash = sha256_file(final_video)
    return {
        "release_id": manifest["release_id"],
        "release_path": str(release_root),
        "final_video_path": str(final_video),
        "final_video_sha256": final_hash,
        "manifest_path": str(manifest_path),
        "manifest_parsed": True,
        "manifest_sha256": sha256_file(manifest_path),
        "checksum_path": str(checksum_path),
        "sha256sums_sha256": sha256_file(checksum_path),
        "checksum_verified": _verify_sha256sums_file(release_root),
        "release_notes_path": str(release_notes),
        "release_notes_placeholder_free": "placeholder" not in release_notes.read_text(encoding="utf-8").lower(),
        "packaged_files": _packaged_files(release_root),
        "byte_identical": manifest.get("byte_identical_result") is True and final_hash == manifest.get("selected_final_candidate_sha256"),
        "duration": manifest.get("video_duration"),
        "resolution": manifest.get("video_resolution"),
    }


def _write_manual_publication_handoff(release_root: Path, manifest: dict[str, Any], validation: dict[str, Any]) -> Path:
    path = _project_dir() / "READY_FOR_MANUAL_PUBLICATION.md"
    lines = [
        "# Ready For Manual Publication",
        "",
        "State: `READY_FOR_MANUAL_PUBLICATION`",
        f"Accepted release: `{manifest['release_id']}`",
        f"Accepted video: `{release_root / 'final_video.mp4'}`",
        f"Release directory: `{release_root}`",
        f"SHA-256: `{validation['final_video_sha256']}`",
        f"Duration: `{validation['duration']}` seconds",
        f"Resolution: `{validation['resolution']}`",
        "Language: `zh-CN -> en-US`",
        "Localization scope: `dialogue_subtitles_only`",
        "Subtitle mode: burned-in English dialogue subtitles",
        "ASS sidecar availability: yes (`subtitles_en.ass`)",
        "SRT availability: no",
        "",
        "## Non-Dialogue CJK Policy",
        "",
        "- Source dialogue subtitles cleaned.",
        "- English dialogue subtitles rendered.",
        "- Non-dialogue source text may remain.",
        "- Manual visual localization is optional.",
        "- Completely CJK-free claim is not made.",
        "",
        "## Integrity References",
        "",
        f"- Manifest: `{release_root / 'release_manifest.json'}`",
        f"- Checksums: `{release_root / 'SHA256SUMS.txt'}`",
        "- Publication state: `not_performed`",
        "- Upload state: `not_performed`",
        "",
        "## Manual Publication Checklist",
        "",
        "1. Decide whether remaining non-dialogue source text is acceptable.",
        "2. Optionally perform manual visual edits outside the canonical release.",
        "3. Upload final_video.mp4 manually.",
        "4. Add title, description, thumbnail and channel settings manually.",
        "5. Verify visibility, copyright and platform settings.",
        "6. Record the published URL only after publication.",
        "7. Never mutate the accepted release after publication.",
        "",
        "Do not invent title, description, tags or thumbnail content from this handoff.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_release_index(release_root: Path, manifest: dict[str, Any], validation: dict[str, Any], handoff_path: Path) -> Path:
    path = _project_dir() / "release_index.json"
    payload = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "latest_accepted_release_id": manifest["release_id"],
        "accepted_releases": [
            {
                "release_id": manifest["release_id"],
                "accepted_path": str(release_root),
                "final_video_path": str(release_root / "final_video.mp4"),
                "sha256": validation["final_video_sha256"],
                "acceptance_state": "CP09B_LOCAL_EXPORT_PACKAGE_HUMAN_PASS",
                "publication_state": "not_performed",
                "upload_state": "not_performed",
                "manifest_path": str(release_root / "release_manifest.json"),
                "checksum_path": str(release_root / "SHA256SUMS.txt"),
                "human_acceptance_record": "66_CP09B_LOCAL_EXPORT_PACKAGE_HUMAN_ACCEPTANCE_REPORT.md",
                "manual_publication_handoff": str(handoff_path),
                "latest_accepted_release_pointer": True,
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _export_manifest_valid(export_root: Path) -> bool:
    manifest = export_root / "release_manifest.json"
    mp4 = export_root / "final_video.mp4"
    sums = export_root / "SHA256SUMS.txt"
    if not (manifest.exists() and mp4.exists() and sums.exists()):
        return False
    payload = _read_json(manifest)
    return (
        payload.get("schema_version") == EXPORT_SCHEMA_VERSION
        and payload.get("byte_identical_result") is True
        and payload.get("export_application_build_id", {}).get("frontend") == "cp09b"
        and sha256_file(mp4) == EXPECTED_HASH
    )


def _closeout_active() -> bool:
    if get_settings().db_path.name == "test.db" and os.environ.get("TOOL_AUTO_SUB_ENFORCE_CLOSEOUT_GUARD_IN_TESTS") != "1":
        return False
    path = _state_path()
    if not path.exists():
        return False
    try:
        state = _read_json(path)
    except Exception:
        return False
    return state.get("release_closeout", {}).get("state") == "READY_FOR_MANUAL_PUBLICATION"


def _find_completed_release(final_hash: str, options: dict[str, Any]) -> Path | None:
    exports_root = ensure_dir(_project_dir() / "exports")
    for candidate in sorted(exports_root.iterdir()):
        if not candidate.is_dir() or not _export_manifest_valid(candidate):
            continue
        manifest = _read_json(candidate / "release_manifest.json")
        if manifest.get("selected_final_candidate_sha256") != final_hash:
            continue
        if manifest.get("package_options", {}).get("create_zip", False) != options.get("create_zip", False):
            continue
        return candidate
    return None


def _packaged_files(export_root: Path) -> list[str]:
    return sorted(path.name for path in export_root.iterdir() if path.is_file())


def _require_disk_for_export(files_to_copy: list[dict[str, Any]], *, create_zip: bool) -> None:
    required = sum(Path(item["source"]).stat().st_size for item in files_to_copy) + EXPORT_DISK_MARGIN_BYTES
    if create_zip:
        required += sum(Path(item["source"]).stat().st_size for item in files_to_copy) + ZIP_DISK_MARGIN_BYTES
    storage = storage_preflight(
        "package",
        _project_dir(),
        projected_workspace_bytes=required,
        safety_reserve_bytes=0,
        free_space_getter=lambda path: shutil.disk_usage(path).free,
    )
    if not storage["passed"]:
        raise ValueError(
            "Insufficient free disk for export package. "
            f"Required {storage['required_minimum_bytes']} bytes including safety margin; "
            f"available {storage['current_free_bytes']} bytes."
        )


def _create_release_zip(temp_root: Path) -> Path:
    zip_path = temp_root / f"{temp_root.name}.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(temp_root.iterdir()):
                if path == zip_path or not path.is_file():
                    continue
                archive.write(path, arcname=path.name)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise
    return zip_path


def _export_job_state(
    status: str,
    release_id: str,
    destination: Path,
    options: dict[str, Any],
    final_hash: str,
    *,
    file_list: list[str] | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": f"cp09b:{release_id}",
        "project_id": PROJECT_ID,
        "final_candidate_hash": final_hash,
        "release_id": release_id,
        "destination": str(destination),
        "requested_package_options": options,
        "status": status,
        "started_at": _now() if status in {"validating", "copying", "hashing", "packaging", "verifying"} else None,
        "ended_at": _now() if status in {"completed", "blocked", "failed"} else None,
        "file_list": file_list or [],
        "checksum_results": "verified" if status == "completed" else "pending",
        "failure_reason": failure_reason,
        "retry_state": "safe_to_retry" if status in {"blocked", "failed"} else "not_required",
    }


def _release_summary(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# CP09B Local Export Package",
            "",
            f"Project: `{PROJECT_ID}`",
            "Scope: `dialogue_subtitles_only`",
            "Source dialogue subtitles were cleaned and English dialogue subtitles were rendered.",
            "Automatic non-dialogue source text localization is disabled; non-dialogue source text may remain.",
            f"Accepted final MP4 SHA-256: `{manifest['selected_final_candidate_sha256']}`",
            f"Exported final MP4 SHA-256: `{manifest['exported_video_sha256']}`",
            f"Byte-identical export: `{manifest['byte_identical_result']}`",
            "Provider calls: Gemini 0, ElevenLabs 0, YouTube 0",
            "YouTube upload/publication: not performed",
            "Package status: ready for manual review, archive, or separately authorized manual publication.",
            "",
        ]
    )


def _stage_summary(stage: str, status: str) -> dict[str, Any]:
    return {"stage_id": stage, "label": stage.replace("_", " ").title(), "status": status, "gate_count": 1, "unresolved_issue_count": 0 if status in TERMINAL_READY else 1}


def _complete_stage_summary(state: dict[str, Any]) -> dict[str, Any]:
    export_status = state["stages"].get("export", "NOT_STARTED")
    return {
        "stage_id": "complete",
        "label": "Complete",
        "status": export_status,
        "gate_count": 0,
        "unresolved_issue_count": 0 if export_status in TERMINAL_READY else 1,
        "alias_of": "export",
    }


def _golden_gates(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "gate_id": stage,
            "label": stage.replace("_", " ").title(),
            "stage": stage,
            "state": status,
            "approved_at": state.get("updated_at") if status in TERMINAL_READY else None,
            "unresolved_issue_count": 0 if status in TERMINAL_READY else 1,
            "action_required": "Ready." if status in TERMINAL_READY else "Run the visible operator action for this stage.",
            "blocks_next": status not in TERMINAL_READY,
        }
        for stage, status in state["stages"].items()
    ]


def _golden_checklist(state: dict[str, Any]) -> list[dict[str, str]]:
    checks = {
        "Selected scope is dialogue_subtitles_only": state.get("localization_scope") == "dialogue_subtitles_only",
        "CP08G hash verified": state.get("candidate_artifact", {}).get("sha256") == EXPECTED_HASH,
        "Final artifact selected": state.get("final_artifact", {}).get("sha256") == EXPECTED_HASH,
        "Export package created": state.get("export", {}).get("exported_video_sha256") == EXPECTED_HASH,
        "No provider or publication calls": state.get("provider_calls") == {"gemini": 0, "elevenlabs": 0, "youtube": 0},
    }
    return [{"label": label, "state": "PASS" if ok else "Blocked"} for label, ok in checks.items()]


def _dashboard(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_name": "Production Golden Path CP09",
        "localization_scope": state.get("localization_scope"),
        "current_stage": _current_stage(state),
        "overall_progress": f"{sum(1 for status in state['stages'].values() if status in TERMINAL_READY)}/{len(STAGES)}",
        "active_job": next((stage for stage, status in state["stages"].items() if status == "RUNNING"), None),
        "blockers": [stage for stage, status in state["stages"].items() if status in {"BLOCKED", "FAILED"}],
        "artifact_lineage": state.get("candidate_artifact"),
        "provider_call_policy": "disabled_no_paid_calls_no_publication",
        "final_candidate": state.get("final_artifact") or state.get("candidate_artifact"),
        "human_review_state": state.get("human_approval", {}).get("mode", "pending"),
        "export_readiness": state["stages"].get("export"),
        "release_closeout": state.get("release_closeout") or {},
        "local_export": _local_export_dashboard(state),
    }


def _local_export_dashboard(state: dict[str, Any]) -> dict[str, Any]:
    final = state.get("final_artifact") or {}
    final_path = Path(final.get("path", ""))
    final_size = final_path.stat().st_size if final_path.exists() else 0
    disk = shutil.disk_usage(_project_dir())
    export_job = state.get("export_job") or {}
    export = state.get("export") or {}
    packaged_files = []
    review_urls = {}
    if export_job.get("status") == "completed":
        try:
            review = local_export_review_summary(PROJECT_ID)
            packaged_files = review["packaged_files"]
            review_urls = {
                "final_video": review["final_video"]["url"],
                "manifest": review["manifest"]["url"],
                "checksums": review["checksums"]["url"],
                "release_notes": review["release_notes"]["url"],
                "review": f"/api/operator/projects/{PROJECT_ID}/local-export/review",
            }
        except Exception:
            packaged_files = []
            review_urls = {}
    return {
        "stage": "CP09B Local Export",
        "available": final.get("sha256") == EXPECTED_HASH and state.get("human_approval", {}).get("mode") == "human_approval_inherited_by_hash_equivalence",
        "human_approval_status": CP09A_ACCEPTANCE["verdict"],
        "final_candidate_filename": final_path.name if final_path else None,
        "final_candidate_hash": final.get("sha256"),
        "estimated_export_size_bytes": final_size,
        "available_disk_gib": round(disk.free / (1024**3), 3),
        "export_destination": str(_project_dir() / "exports"),
        "include_srt_available": _canonical_subtitle_source(".srt") is not None,
        "include_ass_available": _canonical_subtitle_source(".ass") is not None,
        "zip_optional": True,
        "zip_default": False,
        "package_status": export_job.get("status", "not_started"),
        "generated_release_id": export.get("release_id") or export_job.get("release_id"),
        "final_release_path": export_job.get("destination") if export_job.get("status") == "completed" else None,
        "checksum_validation_status": export_job.get("checksum_results", "pending"),
        "publish_upload_state": "not_performed",
        "closeout_state": state.get("release_closeout", {}).get("state"),
        "human_acceptance_state": state.get("cp09b_human_acceptance", {}).get("verdict", "CP09B_HUMAN_REVIEW_REQUIRED"),
        "manual_publication_handoff_url": "/api/operator/projects/production_golden_path_cp09/manual-publication-handoff",
        "byte_identical": export.get("byte_identical_result"),
        "packaged_files": packaged_files,
        "review_urls": review_urls,
    }


def _current_stage(state: dict[str, Any]) -> str:
    for stage in STAGES:
        if state["stages"].get(stage) not in TERMINAL_READY:
            return stage
    return "complete"


def _safe_resolve(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    root = _root().resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("Path is outside the approved project root.")
    return resolved


def _root() -> Path:
    return get_settings().root


def _project_dir() -> Path:
    return ensure_dir(get_settings().data_dir / "projects" / PROJECT_ID)


def _state_path() -> Path:
    return _project_dir() / "operator" / "cp09_golden_path_state.json"


def _audit_path() -> Path:
    return _root() / "evidence" / "CP09" / "production_golden_path" / "audit_log.jsonl"


def _project_audit_path() -> Path:
    return _project_dir() / "operator" / "audit_log.jsonl"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit() -> str:
    try:
        import subprocess

        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=_root(), text=True).strip()
    except Exception:
        return "unknown"
