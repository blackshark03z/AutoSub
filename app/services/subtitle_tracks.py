from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.hashing import sha256_file
from app.core.paths import ensure_dir
from app.db.session import session_scope
from app.domain.models import SimpleWorkflowRun, SubtitleTrack, SubtitleTrackItem


TRACK_TYPES = {"translation", "creative", "imported"}
FALLBACK_POLICIES = {"fallback_to_translation", "block_render", "render_blank"}
VALID_SUBTITLE_PROVENANCE = {
    "local_transcription",
    "provider_transcription",
    "source_caption_ocr_translation",
    "source_caption_gemini_translation",
    "source_caption_gemini_translation_with_human_review",
    "user_import",
    "user_authored",
}
INVALID_SUBTITLE_PROVENANCE = {
    "test_fixture",
    "mock",
    "synthetic_placeholder",
    "unknown",
}
TEST_FIXTURE_ENV = "TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES"
USER_CONTENT_ERROR = (
    "Không thể tạo nội dung phụ đề thực từ video này. "
    "Video chưa được xuất để tránh tạo kết quả sai. "
    "Không tìm thấy nguồn phiên âm khả dụng."
)
PLACEHOLDER_BLOCKERS = [
    "TODO",
    "TBD",
    "placeholder",
    "pending operator review",
    "untranslated",
    "operator review required",
]
PLACEHOLDER_CONTENT_RE = re.compile(
    r"^\s*(?:translation|original|subtitle|source\s+dialogue)\s+line\s+\d+\s*[.!?…]*\s*$",
    re.IGNORECASE,
)
PLACEHOLDER_EXACT_RE = re.compile(
    r"^\s*(?:sample\s+subtitle|placeholder|mock(?:\s+subtitle)?|"
    r"test(?:\s+fixture|\s+subtitle)?|smoke(?:\s+test|\s+subtitle)?|"
    r"deterministic(?:\s+fallback|\s+subtitle)?)\s*[.!?…]*\s*$",
    re.IGNORECASE,
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
HTML_RE = re.compile(r"<\s*/?\s*(script|iframe|object|embed|html|body|style)\b", re.IGNORECASE)
CUE_RE = re.compile(
    r"^\[(?P<cue_id>CUE_\d{4,})\]\s*$"
    r"(?P<body>.*?)(?=^\[CUE_\d{4,}\]\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
FIELD_RE = re.compile(r"^(?P<key>TIME|SOURCE|TRANSLATION|SCENE_NOTE|TEXT):(?P<value>.*)$", re.MULTILINE)


@dataclass(frozen=True)
class CanonicalCue:
    cue_id: str
    start_ms: int
    end_ms: int
    source_text: str
    translation_text: str


class SubtitleContentUnavailableError(ValueError):
    pass


def test_fixture_context_enabled() -> bool:
    return os.environ.get(TEST_FIXTURE_ENV) == "1"


def is_placeholder_subtitle_text(text: str) -> bool:
    normalized = " ".join(str(text).split())
    return bool(PLACEHOLDER_CONTENT_RE.fullmatch(normalized) or PLACEHOLDER_EXACT_RE.fullmatch(normalized))


def validate_resolved_subtitle_content(
    payload: dict[str, Any],
    *,
    duration_ms: int,
    allow_test_fixture: bool | None = None,
) -> dict[str, Any]:
    allow_fixture = test_fixture_context_enabled() if allow_test_fixture is None else allow_test_fixture
    provenance = str(payload.get("subtitle_provenance") or "unknown").strip().lower()
    cues = payload.get("cues") if isinstance(payload.get("cues"), list) else []
    nonempty = [cue for cue in cues if str(cue.get("resolved_text") or cue.get("text") or "").strip()]
    reason_code = None

    if not nonempty:
        reason_code = "subtitle_content_empty"
    elif provenance == "test_fixture" and allow_fixture:
        reason_code = None
    elif provenance not in VALID_SUBTITLE_PROVENANCE:
        reason_code = "subtitle_provenance_invalid"
    elif any(is_placeholder_subtitle_text(cue.get("resolved_text") or cue.get("text") or "") for cue in nonempty):
        reason_code = "subtitle_content_placeholder"

    if reason_code is None and not (provenance == "test_fixture" and allow_fixture):
        for cue in nonempty:
            start_ms = _safe_int(cue.get("start_ms"))
            end_ms = _safe_int(cue.get("end_ms"))
            if start_ms is None or end_ms is None or start_ms < 0 or end_ms <= start_ms or end_ms > duration_ms:
                reason_code = "subtitle_timing_invalid"
                break
            if provenance not in {"user_import", "user_authored"} and end_ms - start_ms >= max(int(duration_ms * 0.9), 1):
                reason_code = "subtitle_timing_synthetic"
                break

    if reason_code is None and provenance not in {"user_import", "user_authored", "test_fixture"}:
        if _looks_like_mechanical_thirds(nonempty, duration_ms):
            reason_code = "subtitle_timing_synthetic"

    return {
        "status": "PASS" if reason_code is None else "FAIL",
        "eligible": reason_code is None,
        "reason_code": reason_code,
        "provenance": provenance,
        "cue_count": len(nonempty),
        "placeholder_match_count": sum(
            1 for cue in nonempty if is_placeholder_subtitle_text(cue.get("resolved_text") or cue.get("text") or "")
        ),
        "message": None if reason_code is None else USER_CONTENT_ERROR,
    }


def canonical_cues(run_id: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        row = _get_run(session, run_id)
        metadata = json.loads(row.source_metadata_json)
    duration_ms = max(int(float(metadata.get("duration_seconds") or 1) * 1000), 1000)
    cue_count = 3 if duration_ms >= 900 else 1
    span = max(duration_ms // cue_count, 300)
    cues = []
    for index in range(cue_count):
        start = min(index * span, duration_ms - 1)
        end = duration_ms if index == cue_count - 1 else min((index + 1) * span - 50, duration_ms)
        cue_id = f"CUE_{index + 1:04d}"
        cues.append(
            {
                "cue_id": cue_id,
                "start_ms": start,
                "end_ms": max(end, start + 250),
                "source_text": f"Source dialogue {index + 1}",
                "translation_text": f"Translation line {index + 1}.",
            }
        )
    return cues


def export_creative_template(run_id: str, fmt: str) -> dict[str, Any]:
    with session_scope() as session:
        row = _get_run(session, run_id)
        project_id = row.project_id
    cues = canonical_cues(run_id)
    if fmt == "txt":
        content = "\n".join(_format_txt_cue(cue) for cue in cues) + "\n"
        filename = f"{run_id}_creative_script_template.txt"
    elif fmt == "json":
        content = json.dumps(
            {
                "schema_version": "creative_script_v1",
                "project_id": project_id,
                "run_id": run_id,
                "source_track": "translation",
                "cues": [
                    {
                        "cue_id": cue["cue_id"],
                        "start_ms": cue["start_ms"],
                        "end_ms": cue["end_ms"],
                        "source_text": cue["source_text"],
                        "translation_text": cue["translation_text"],
                        "scene_note": "",
                        "creative_text": "",
                    }
                    for cue in cues
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        filename = f"{run_id}_creative_script_template.json"
    else:
        raise ValueError("Unsupported template format.")
    return {"filename": filename, "content": content, "sha256": _sha256_text(content), "cue_count": len(cues), "format": fmt}


def preview_import_candidate(
    run_id: str,
    *,
    content: str,
    fmt: str,
    filename: str = "creative_script.txt",
    mode: str = "cue_id",
) -> dict[str, Any]:
    _validate_filename(filename)
    raw_bytes = content.encode("utf-8")
    source_sha = _sha256_text(content)
    with session_scope() as session:
        row = _get_run(session, run_id)
        project_id = row.project_id
    cues = canonical_cues(run_id)
    cue_map = {cue["cue_id"]: cue for cue in cues}
    parsed = _parse_content(content, fmt=fmt, mode=mode, project_id=project_id, run_id=run_id, canonical_cues=cues)
    items = parsed["items"]
    warnings = list(parsed["warnings"])
    unknown = sorted(cue_id for cue_id in items if cue_id not in cue_map)
    missing = sorted(cue_id for cue_id in cue_map if cue_id not in items)
    duplicates = sorted(parsed["duplicates"])
    empty = sorted(cue_id for cue_id, text in items.items() if not text.strip())
    changed = sorted(cue_id for cue_id, text in items.items() if cue_id in cue_map and text.strip() and text.strip() != cue_map[cue_id]["translation_text"])
    unchanged = sorted(cue_id for cue_id, text in items.items() if cue_id in cue_map and text.strip() == cue_map[cue_id]["translation_text"])
    for cue_id, text in items.items():
        warnings.extend(_text_warnings(cue_id, text, cue_map.get(cue_id)))
    warnings.extend({"code": "unknown_cue", "cue_id": cue_id} for cue_id in unknown)
    warnings.extend({"code": "missing_cue", "cue_id": cue_id} for cue_id in missing)
    warnings.extend({"code": "duplicate_cue", "cue_id": cue_id} for cue_id in duplicates)
    warnings.extend({"code": "empty_cue", "cue_id": cue_id} for cue_id in empty)
    layout = [_estimate_layout(cue_map[cue_id], items.get(cue_id, "")) for cue_id in cue_map]
    status = "PASS" if not unknown and not duplicates and not parsed["malformed"] else "FAIL"
    return {
        "status": status,
        "project_id": project_id,
        "run_id": run_id,
        "format": fmt,
        "mode": mode,
        "filename": filename,
        "source_sha256": source_sha,
        "bytes": len(raw_bytes),
        "total_canonical_cues": len(cues),
        "matched_cues": len([cue_id for cue_id in items if cue_id in cue_map]),
        "missing_cues": missing,
        "unknown_cues": unknown,
        "duplicate_cues": duplicates,
        "empty_cues": empty,
        "changed_cues": changed,
        "unchanged_cues": unchanged,
        "malformed_blocks": parsed["malformed"],
        "warnings": warnings,
        "layout_estimates": layout,
        "items": [{"cue_id": cue_id, "text": text} for cue_id, text in sorted(items.items()) if cue_id in cue_map],
        "state_mutated": False,
    }


def apply_import_candidate(
    run_id: str,
    *,
    content: str,
    fmt: str,
    filename: str,
    track_type: str = "creative",
    display_name: str | None = None,
    fallback_policy: str = "fallback_to_translation",
    mode: str = "cue_id",
) -> dict[str, Any]:
    if track_type not in {"creative", "imported"}:
        raise ValueError("Imported content can only create creative or imported tracks.")
    if fallback_policy not in FALLBACK_POLICIES:
        raise ValueError("Unsupported fallback policy.")
    preview = preview_import_candidate(run_id, content=content, fmt=fmt, filename=filename, mode=mode)
    if preview["status"] != "PASS":
        raise ValueError("Import preview failed; fix validation errors before applying.")
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = _get_run(session, run_id)
        run_dir = Path(row.run_directory)
        import_dir = ensure_dir(run_dir / "imports")
        import_path = _collision_safe_path(import_dir / Path(filename).name)
        import_path.write_text(content, encoding="utf-8")
        track_id = f"track_{uuid4().hex[:12]}"
        metadata = {
            "schema_version": 1,
            "format": fmt,
            "mode": mode,
            "matched_cue_count": preview["matched_cues"],
            "validation_warnings": preview["warnings"],
            "operator_decision": f"apply_as_{track_type}",
            "import_path": str(import_path),
            "fallback_policy": fallback_policy,
            "subtitle_provenance": "user_import" if track_type == "imported" else "user_authored",
        }
        session.add(
            SubtitleTrack(
                track_id=track_id,
                project_id=row.project_id,
                run_id=row.run_id,
                track_type=track_type,
                display_name=display_name or ("Creative script" if track_type == "creative" else "Imported script"),
                source_type=fmt,
                source_filename=Path(filename).name,
                source_sha256=preview["source_sha256"],
                active=False,
                review_state="applied",
                fallback_policy=fallback_policy,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                created_at=now,
                updated_at=now,
            )
        )
        for item in preview["items"]:
            session.add(
                SubtitleTrackItem(
                    item_id=f"item_{uuid4().hex[:12]}",
                    track_id=track_id,
                    cue_id=item["cue_id"],
                    text=item["text"],
                    status="ready" if item["text"].strip() else "empty",
                    warning_codes=json.dumps([w["code"] for w in preview["warnings"] if w.get("cue_id") == item["cue_id"]]),
                    created_at=now,
                    updated_at=now,
                )
            )
        row.updated_at = now
        _write_track_manifest(run_dir, track_id, metadata)
    return {"status": "PASS", "track": get_track(run_id, track_id), "preview": preview}


def list_tracks(run_id: str) -> dict[str, Any]:
    _ensure_translation_track(run_id)
    with session_scope() as session:
        row = _get_run(session, run_id)
        tracks = session.query(SubtitleTrack).filter(SubtitleTrack.run_id == run_id).order_by(SubtitleTrack.created_at.asc(), SubtitleTrack.id.asc()).all()
        active = next((track.track_id for track in tracks if track.active and track.review_state != "disabled"), None)
    return {"run_id": run_id, "project_id": row.project_id, "active_track_id": active, "tracks": [_serialize_track(track) for track in tracks]}


def get_track(run_id: str, track_id: str) -> dict[str, Any]:
    with session_scope() as session:
        track = _get_track(session, run_id, track_id)
        items = session.query(SubtitleTrackItem).filter(SubtitleTrackItem.track_id == track_id).order_by(SubtitleTrackItem.cue_id.asc()).all()
        payload = _serialize_track(track)
        payload["items"] = [_serialize_item(item) for item in items]
        return payload


def set_active_track(run_id: str, track_id: str, fallback_policy: str = "fallback_to_translation") -> dict[str, Any]:
    if fallback_policy not in FALLBACK_POLICIES:
        raise ValueError("Unsupported fallback policy.")
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        target = _get_track(session, run_id, track_id)
        if target.review_state == "disabled":
            raise ValueError("Disabled tracks cannot be selected as active.")
        tracks = session.query(SubtitleTrack).filter(SubtitleTrack.run_id == run_id).all()
        for track in tracks:
            track.active = track.track_id == target.track_id
            if track.track_id == target.track_id:
                track.fallback_policy = fallback_policy
            track.updated_at = now
    return list_tracks(run_id)


def set_track_enabled(run_id: str, track_id: str, enabled: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        target = _get_track(session, run_id, track_id)
        if target.track_type == "translation" and not enabled:
            raise ValueError("The canonical Translation track cannot be disabled.")
        target.review_state = "applied" if enabled else "disabled"
        target.updated_at = now
        if not enabled and target.active:
            translation = session.query(SubtitleTrack).filter(SubtitleTrack.run_id == run_id, SubtitleTrack.track_type == "translation").one_or_none()
            if translation is not None:
                target.active = False
                translation.active = True
                translation.updated_at = now
    return list_tracks(run_id)


def update_track_item(run_id: str, track_id: str, cue_id: str, text: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        _get_track(session, run_id, track_id)
        item = session.query(SubtitleTrackItem).filter(SubtitleTrackItem.track_id == track_id, SubtitleTrackItem.cue_id == cue_id).one_or_none()
        if item is None:
            item = SubtitleTrackItem(item_id=f"item_{uuid4().hex[:12]}", track_id=track_id, cue_id=cue_id, text="", created_at=now, updated_at=now)
            session.add(item)
        item.text = text
        item.status = "ready" if text.strip() else "empty"
        item.warning_codes = json.dumps([w["code"] for w in _text_warnings(cue_id, text, None)])
        item.updated_at = now
    return get_track(run_id, track_id)


def undo_last_import(run_id: str) -> dict[str, Any]:
    with session_scope() as session:
        track = (
            session.query(SubtitleTrack)
            .filter(SubtitleTrack.run_id == run_id, SubtitleTrack.track_type.in_(["creative", "imported"]))
            .order_by(SubtitleTrack.created_at.desc(), SubtitleTrack.id.desc())
            .first()
        )
        if track is None:
            return {"status": "NOOP", "tracks": list_tracks(run_id)["tracks"]}
        session.query(SubtitleTrackItem).filter(SubtitleTrackItem.track_id == track.track_id).delete()
        session.delete(track)
    return {"status": "PASS", "tracks": list_tracks(run_id)["tracks"]}


def active_track_provenance(run_id: str) -> str | None:
    with session_scope() as session:
        active = (
            session.query(SubtitleTrack)
            .filter(SubtitleTrack.run_id == run_id, SubtitleTrack.active.is_(True), SubtitleTrack.review_state != "disabled")
            .order_by(SubtitleTrack.updated_at.desc(), SubtitleTrack.id.desc())
            .first()
        )
        return _track_provenance(active) if active is not None else None


def create_local_transcription_track(
    run_id: str,
    *,
    cues: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    usable = [
        {
            "cue_id": str(cue["cue_id"]),
            "start_ms": int(cue["start_ms"]),
            "end_ms": int(cue["end_ms"]),
            "source_text": str(cue["text"]).strip(),
            "text": str(cue["text"]).strip(),
        }
        for cue in cues
        if str(cue.get("text") or "").strip() and int(cue.get("end_ms") or 0) > int(cue.get("start_ms") or 0)
    ]
    if not usable:
        raise SubtitleContentUnavailableError(USER_CONTENT_ERROR)
    now = datetime.now(timezone.utc)
    track_id = f"track_local_asr_{uuid4().hex[:10]}"
    track_metadata = {
        "schema_version": 1,
        "subtitle_provenance": "local_transcription",
        "asr_provider": "faster_whisper",
        "cues": usable,
        **metadata,
    }
    with session_scope() as session:
        row = _get_run(session, run_id)
        for track in session.query(SubtitleTrack).filter(SubtitleTrack.run_id == run_id).all():
            track.active = False
            track.updated_at = now
        session.add(
            SubtitleTrack(
                track_id=track_id,
                project_id=row.project_id,
                run_id=run_id,
                track_type="source",
                display_name="Offline source transcription",
                source_type="local_transcription",
                source_filename=metadata.get("audio_filename"),
                source_sha256=metadata.get("audio_sha256"),
                active=True,
                review_state="canonical",
                fallback_policy="block_render",
                metadata_json=json.dumps(track_metadata, ensure_ascii=False),
                created_at=now,
                updated_at=now,
            )
        )
        for cue in usable:
            session.add(
                SubtitleTrackItem(
                    item_id=f"item_{uuid4().hex[:12]}",
                    track_id=track_id,
                    cue_id=cue["cue_id"],
                    text=cue["text"],
                    status="ready",
                    warning_codes="[]",
                    created_at=now,
                    updated_at=now,
                )
            )
    return get_track(run_id, track_id)


def create_source_caption_translation_track(
    run_id: str,
    *,
    cues: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Persist OCR-timed captions without routing them through ASR."""
    usable = []
    for cue in cues:
        text = str(cue.get("text") or "").strip()
        start_ms = int(cue.get("start_ms") or 0)
        end_ms = int(cue.get("end_ms") or 0)
        if text and end_ms > start_ms:
            usable.append({
                "cue_id": str(cue["cue_id"]),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_text": str(cue.get("source_text") or "").strip(),
                "text": text,
                "source_bbox": cue.get("source_bbox"),
                "source_interval": cue.get("source_interval"),
                "line_count": int(cue.get("line_count") or 1),
                "ocr_confidence": cue.get("ocr_confidence"),
            })
    if not usable:
        raise SubtitleContentUnavailableError(
            "KhÃ´ng thá»ƒ Ä‘á»c vÃ  dá»‹ch phá»¥ Ä‘á» cÃ³ sáºµn trong video nÃ y. "
            "Video chÆ°a Ä‘Æ°á»£c xuáº¥t Ä‘á»ƒ trÃ¡nh táº¡o káº¿t quáº£ sai."
        )
    now = datetime.now(timezone.utc)
    track_id = f"track_source_caption_{uuid4().hex[:10]}"
    track_metadata = {
        "schema_version": 1,
        "subtitle_provenance": metadata.get("subtitle_provenance", "source_caption_gemini_translation"),
        "ocr_provider": metadata.get("ocr_provider", "paddleocr"),
        "ocr_model": metadata.get("ocr_model"),
        "translation_provider": metadata.get("translation_provider", "gemini"),
        "translation_model": metadata.get("translation_model"),
        "source_language": "zh",
        "target_language": "en",
        "cues": usable,
        **metadata,
    }
    with session_scope() as session:
        row = _get_run(session, run_id)
        for track in session.query(SubtitleTrack).filter(SubtitleTrack.run_id == run_id).all():
            track.active = False
            track.updated_at = now
        session.add(SubtitleTrack(
            track_id=track_id,
            project_id=row.project_id,
            run_id=run_id,
            track_type="translation",
            display_name="Gemini OCR source caption translation",
            source_type=metadata.get("subtitle_provenance", "source_caption_gemini_translation"),
            source_filename=metadata.get("source_filename"),
            source_sha256=metadata.get("source_sha256"),
            active=True,
            review_state="canonical",
            fallback_policy="block_render",
            metadata_json=json.dumps(track_metadata, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        ))
        for cue in usable:
            session.add(SubtitleTrackItem(
                item_id=f"item_{uuid4().hex[:12]}",
                track_id=track_id,
                cue_id=cue["cue_id"],
                text=cue["text"],
                status="ready",
                warning_codes="[]",
                created_at=now,
                updated_at=now,
            ))
    return get_track(run_id, track_id)


def resolved_cues(run_id: str) -> dict[str, Any]:
    _ensure_translation_track(run_id)
    with session_scope() as session:
        row = _get_run(session, run_id)
        source_metadata = json.loads(row.source_metadata_json or "{}")
        active = (
            session.query(SubtitleTrack)
            .filter(SubtitleTrack.run_id == run_id, SubtitleTrack.active.is_(True), SubtitleTrack.review_state != "disabled")
            .order_by(SubtitleTrack.updated_at.desc(), SubtitleTrack.id.desc())
            .first()
        )
        if active is None:
            active = session.query(SubtitleTrack).filter(SubtitleTrack.run_id == run_id, SubtitleTrack.track_type == "translation").first()
        items = {}
        if active is not None:
            rows = session.query(SubtitleTrackItem).filter(SubtitleTrackItem.track_id == active.track_id).all()
            items = {item.cue_id: item.text for item in rows}
        fallback = active.fallback_policy if active else "fallback_to_translation"
        active_track = _serialize_track(active) if active else None
        provenance = _track_provenance(active)
    if active is None:
        raise SubtitleContentUnavailableError(USER_CONTENT_ERROR)
    source_caption_provenance = {
        "source_caption_ocr_translation",
        "source_caption_gemini_translation",
        "source_caption_gemini_translation_with_human_review",
    }
    if provenance == "local_transcription":
        cues = [
            {
                "cue_id": cue["cue_id"],
                "start_ms": int(cue["start_ms"]),
                "end_ms": int(cue["end_ms"]),
                "source_text": str(cue.get("source_text") or cue["text"]),
                "translation_text": "",
            }
            for cue in active_track.get("metadata", {}).get("cues", [])
        ]
    elif provenance in source_caption_provenance:
        cues = [
            {
                "cue_id": cue["cue_id"],
                "start_ms": int(cue["start_ms"]),
                "end_ms": int(cue["end_ms"]),
                "source_text": str(cue.get("source_text") or ""),
                "translation_text": str(cue["text"]),
                **({
                    "source_bbox": cue.get("source_bbox"),
                    "source_interval": cue.get("source_interval"),
                    "line_count": cue.get("line_count"),
                    "ocr_confidence": cue.get("ocr_confidence"),
                } if provenance in source_caption_provenance else {}),
            }
            for cue in active_track.get("metadata", {}).get("cues", [])
        ]
    else:
        cues = canonical_cues(run_id)
    duration_ms = max(int(float(source_metadata.get("duration_seconds") or 0) * 1000), 1)
    resolved = []
    for cue in cues:
        text = items.get(cue["cue_id"], "")
        source = active_track["track_type"] if text.strip() and active_track else "translation"
        if not text.strip():
            if fallback == "block_render":
                raise ValueError(f"Missing text for {cue['cue_id']}")
            if fallback == "render_blank":
                text = ""
                source = "blank"
            else:
                if not test_fixture_context_enabled():
                    raise SubtitleContentUnavailableError(USER_CONTENT_ERROR)
                text = cue["translation_text"]
                source = "translation_fallback"
        resolved.append({**cue, "resolved_text": text, "content_source": source})
    payload = {
        "run_id": run_id,
        "active_track": active_track,
        "fallback_policy": fallback,
        "subtitle_provenance": provenance,
        "cues": resolved,
    }
    validation = validate_resolved_subtitle_content(payload, duration_ms=duration_ms)
    payload["content_validation"] = validation
    if not validation["eligible"]:
        raise SubtitleContentUnavailableError(validation["message"])
    return payload


def _ensure_translation_track(run_id: str) -> None:
    if not test_fixture_context_enabled():
        return
    cues = canonical_cues(run_id)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = _get_run(session, run_id)
        existing = session.query(SubtitleTrack).filter(SubtitleTrack.run_id == run_id, SubtitleTrack.track_type == "translation").one_or_none()
        if existing is not None:
            return
        track_id = f"track_translation_{uuid4().hex[:8]}"
        session.add(
            SubtitleTrack(
                track_id=track_id,
                project_id=row.project_id,
                run_id=run_id,
                track_type="translation",
                display_name="Translation",
                source_type="test_fixture",
                source_filename=None,
                source_sha256=None,
                active=True,
                review_state="canonical",
                fallback_policy="fallback_to_translation",
                metadata_json=json.dumps(
                    {
                        "schema_version": 1,
                        "created_by": "cp12a_default_translation_track",
                        "subtitle_provenance": "test_fixture",
                    }
                ),
                created_at=now,
                updated_at=now,
            )
        )
        for cue in cues:
            session.add(
                SubtitleTrackItem(
                    item_id=f"item_{uuid4().hex[:12]}",
                    track_id=track_id,
                    cue_id=cue["cue_id"],
                    text=cue["translation_text"],
                    status="ready",
                    warning_codes="[]",
                    created_at=now,
                    updated_at=now,
                )
            )


def _track_provenance(track: SubtitleTrack | None) -> str:
    if track is None:
        return "unknown"
    metadata = json.loads(track.metadata_json or "{}")
    explicit = str(metadata.get("subtitle_provenance") or "").strip().lower()
    if explicit in VALID_SUBTITLE_PROVENANCE | INVALID_SUBTITLE_PROVENANCE:
        return explicit
    if track.track_type == "imported":
        return "user_import"
    if track.track_type == "creative":
        return "user_authored"
    if track.source_type == "test_fixture" or metadata.get("created_by") == "cp12a_default_translation_track":
        return "test_fixture"
    return "unknown"


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _looks_like_mechanical_thirds(cues: list[dict[str, Any]], duration_ms: int) -> bool:
    if len(cues) != 3 or duration_ms <= 0:
        return False
    spans = []
    for cue in cues:
        start_ms = _safe_int(cue.get("start_ms"))
        end_ms = _safe_int(cue.get("end_ms"))
        if start_ms is None or end_ms is None:
            return False
        spans.append(end_ms - start_ms)
    target = duration_ms / 3
    tolerance = max(duration_ms * 0.02, 100)
    return all(abs(span - target) <= tolerance for span in spans)


def _parse_content(content: str, *, fmt: str, mode: str, project_id: str, run_id: str, canonical_cues: list[dict[str, Any]]) -> dict[str, Any]:
    if not content:
        return {"items": {}, "duplicates": set(), "malformed": [{"code": "empty_file"}], "warnings": []}
    if CONTROL_RE.search(content):
        return {"items": {}, "duplicates": set(), "malformed": [{"code": "unsupported_control_character"}], "warnings": []}
    if HTML_RE.search(content):
        return {"items": {}, "duplicates": set(), "malformed": [{"code": "html_script_content"}], "warnings": []}
    if mode == "line_by_line":
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) != len(canonical_cues):
            return {"items": {cue["cue_id"]: lines[index] for index, cue in enumerate(canonical_cues[: len(lines)])}, "duplicates": set(), "malformed": [{"code": "cue_count_mismatch", "line_count": len(lines)}], "warnings": []}
        return {"items": {cue["cue_id"]: lines[index] for index, cue in enumerate(canonical_cues)}, "duplicates": set(), "malformed": [], "warnings": []}
    if fmt == "json":
        return _parse_json(content, project_id=project_id, run_id=run_id)
    return _parse_txt_template(content)


def _parse_json(content: str, *, project_id: str, run_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        return {"items": {}, "duplicates": set(), "malformed": [{"code": "malformed_json", "message": str(exc)}], "warnings": []}
    malformed = []
    warnings = []
    if payload.get("schema_version") != "creative_script_v1":
        malformed.append({"code": "unsupported_schema"})
    if payload.get("project_id") not in {None, project_id}:
        malformed.append({"code": "wrong_project_metadata"})
    if payload.get("run_id") not in {None, run_id}:
        malformed.append({"code": "wrong_run_metadata"})
    items: dict[str, str] = {}
    duplicates = set()
    for cue in payload.get("cues", []):
        cue_id = str(cue.get("cue_id", "")).strip()
        if not cue_id:
            malformed.append({"code": "missing_cue_id"})
            continue
        if cue_id in items:
            duplicates.add(cue_id)
        items[cue_id] = str(cue.get("creative_text") or cue.get("text") or "")
        if cue.get("start_ms") is not None or cue.get("end_ms") is not None:
            warnings.append({"code": "external_timing_ignored", "cue_id": cue_id})
    return {"items": items, "duplicates": duplicates, "malformed": malformed, "warnings": warnings}


def _parse_txt_template(content: str) -> dict[str, Any]:
    items: dict[str, str] = {}
    duplicates = set()
    malformed = []
    for match in CUE_RE.finditer(content):
        cue_id = match.group("cue_id")
        fields = {field.group("key"): field.group("value").strip() for field in FIELD_RE.finditer(match.group("body"))}
        if "TEXT" not in fields:
            malformed.append({"code": "missing_text_field", "cue_id": cue_id})
            continue
        if cue_id in items:
            duplicates.add(cue_id)
        items[cue_id] = fields.get("TEXT", "")
    if not items and content.strip():
        malformed.append({"code": "malformed_blocks"})
    return {"items": items, "duplicates": duplicates, "malformed": malformed, "warnings": []}


def _text_warnings(cue_id: str, text: str, cue: dict[str, Any] | None) -> list[dict[str, Any]]:
    warnings = []
    lower = text.lower()
    for token in PLACEHOLDER_BLOCKERS:
        if token.lower() in lower:
            warnings.append({"code": "placeholder_blocker", "cue_id": cue_id, "token": token})
    if len(text) > 90:
        warnings.append({"code": "long_text", "cue_id": cue_id})
    if cue and len(text) / max((cue["end_ms"] - cue["start_ms"]) / 1000, 0.25) > 28:
        warnings.append({"code": "reading_speed", "cue_id": cue_id})
    if len(text) > 74:
        warnings.append({"code": "more_than_two_lines_estimated", "cue_id": cue_id})
    return warnings


def _estimate_layout(cue: dict[str, Any], text: str) -> dict[str, Any]:
    rendered_width = min(max(len(text) * 22, 0), 1280)
    line_count = 1 if len(text) <= 38 else 2 if len(text) <= 74 else 3
    duration_ms = cue["end_ms"] - cue["start_ms"]
    return {
        "cue_id": cue["cue_id"],
        "rendered_width": rendered_width,
        "line_count": line_count,
        "overflow": line_count > 2 or rendered_width > 1024,
        "minimum_display_duration_ms": max(900, len(text) * 45),
        "reading_speed_warning": len(text) / max(duration_ms / 1000, 0.25) > 28,
    }


def _format_txt_cue(cue: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"[{cue['cue_id']}]",
            f"TIME: {_fmt_time(cue['start_ms'])} --> {_fmt_time(cue['end_ms'])}",
            f"SOURCE: {cue['source_text']}",
            f"TRANSLATION: {cue['translation_text']}",
            "SCENE_NOTE:",
            "TEXT:",
            "",
        ]
    )


def _fmt_time(ms: int) -> str:
    seconds, milli = divmod(ms, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d}.{milli:03d}"


def _get_run(session, run_id: str) -> SimpleWorkflowRun:
    row = session.query(SimpleWorkflowRun).filter(SimpleWorkflowRun.run_id == run_id, SimpleWorkflowRun.is_test_fixture.is_(False)).one_or_none()
    if row is None:
        raise FileNotFoundError("Run was not found.")
    return row


def _get_track(session, run_id: str, track_id: str) -> SubtitleTrack:
    track = session.query(SubtitleTrack).filter(SubtitleTrack.run_id == run_id, SubtitleTrack.track_id == track_id).one_or_none()
    if track is None:
        raise FileNotFoundError("Subtitle track was not found.")
    return track


def _serialize_track(track: SubtitleTrack | None) -> dict[str, Any]:
    if track is None:
        return {}
    return {
        "track_id": track.track_id,
        "project_id": track.project_id,
        "run_id": track.run_id,
        "track_type": track.track_type,
        "display_name": track.display_name,
        "source_type": track.source_type,
        "source_filename": track.source_filename,
        "source_sha256": track.source_sha256,
        "active": track.active,
        "review_state": track.review_state,
        "enabled": track.review_state != "disabled",
        "fallback_policy": track.fallback_policy,
        "metadata": json.loads(track.metadata_json or "{}"),
        "created_at": track.created_at.isoformat() if track.created_at else None,
        "updated_at": track.updated_at.isoformat() if track.updated_at else None,
    }


def _serialize_item(item: SubtitleTrackItem) -> dict[str, Any]:
    return {"cue_id": item.cue_id, "text": item.text, "status": item.status, "warning_codes": json.loads(item.warning_codes or "[]")}


def _validate_filename(filename: str) -> None:
    name = Path(filename).name
    if not name or name != filename or ".." in Path(filename).parts:
        raise ValueError("Unsafe import filename.")


def _collision_safe_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("Could not create collision-safe import path.")


def _write_track_manifest(run_dir: Path, track_id: str, metadata: dict[str, Any]) -> None:
    manifest_dir = ensure_dir(run_dir / "track_manifests")
    (manifest_dir / f"{track_id}.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def _sha256_text(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
