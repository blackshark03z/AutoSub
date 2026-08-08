import json
from pathlib import Path
from uuid import uuid4

from app.core.hashing import sha256_file
from app.core.paths import ensure_dir
from app.db.session import session_scope
from app.domain.models import ContentRevision, ProviderRequest
from app.providers.translation.base import TranslationBlockRequest, TranslationProvider
from app.services.timeline import load_latest_timeline, save_timeline_revision, validate_timeline


def collect_translation_segments(timeline: dict) -> list[dict]:
    request_segments = []
    for segment in timeline["segments"]:
        if not segment.get("enabled", True):
            continue
        if segment.get("status") == "review_needed" or segment.get("issues"):
            continue
        duration_budget_ms = max(1, segment["end_ms"] - segment["start_ms"])
        request_segments.append(
            {
                "id": segment["id"],
                "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"],
                "duration_budget_ms": duration_budget_ms,
                "source_text": segment["source_text"],
            }
        )
    return request_segments


def build_translation_request(timeline: dict, model: str) -> TranslationBlockRequest:
    request_segments = collect_translation_segments(timeline)
    return build_translation_request_for_segments(timeline, model, request_segments)


def build_translation_request_for_segments(
    timeline: dict, model: str, request_segments: list[dict]
) -> TranslationBlockRequest:
    return TranslationBlockRequest(
        project_id=timeline["project_id"],
        market_profile_id=timeline["market_profile_id"],
        transformation_mode=timeline["transformation_mode"],
        target_locale=timeline["target_locale"],
        duration_budget_ms=sum(segment["duration_budget_ms"] for segment in request_segments),
        segments=request_segments,
        model=model,
    )


def transform_latest_timeline(project_id: str, provider: TranslationProvider) -> dict:
    return transform_timeline(project_id, load_latest_timeline(project_id), provider)


def transform_timeline(project_id: str, timeline: dict, provider: TranslationProvider, batch_size: int = 24) -> dict:
    if timeline.get("project_id") != project_id:
        raise ValueError("Timeline project does not match transform project")
    request_segments = collect_translation_segments(timeline)
    results = []
    combined_response = {
        "schema_version": 1,
        "market_profile_id": timeline["market_profile_id"],
        "segments": [],
        "transformation_log": [],
    }
    for chunk in partition_translation_segments(request_segments, max_segments=batch_size):
        request = build_translation_request_for_segments(timeline, provider.model, chunk)
        result = provider.transform_block(request)
        validate_translation_response(result.response, expected_ids=[segment["id"] for segment in chunk])
        results.append(result)
        combined_response["segments"].extend(result.response.get("segments", []))
        combined_response["transformation_log"].extend(result.response.get("transformation_log", []))
    request_hashes = [result.request_hash for result in results]
    existing_revision = find_existing_content_revision(project_id, request_hashes)
    if existing_revision is not None and _combined_cache_status(results) == "hit":
        for result in results:
            record_provider_request(result)
        return {
            "project_id": project_id,
            "provider": provider.provider_name,
            "model": provider.model,
            "request_hashes": request_hashes,
            "cache_status": "hit",
            "timeline": existing_revision["timeline"],
            "content_revision": existing_revision["content_revision"],
            "segments_transformed": existing_revision["segments_transformed"],
            "idempotent_reuse": True,
        }
    transformed = apply_translation_result(timeline, combined_response, expected_ids=[s["id"] for s in request_segments])
    timeline_revision = save_timeline_revision(project_id, transformed)
    content_revision = save_content_revision(project_id, timeline_revision, results, combined_response)
    for result in results:
        record_provider_request(result)
    return {
        "project_id": project_id,
        "provider": provider.provider_name,
        "model": provider.model,
        "request_hashes": request_hashes,
        "cache_status": _combined_cache_status(results),
        "timeline": timeline_revision,
        "content_revision": content_revision,
        "segments_transformed": len(combined_response["segments"]),
    }


def partition_translation_segments(
    segments: list[dict],
    *,
    max_segments: int = 24,
    max_duration_ms: int = 45_000,
    max_source_characters: int = 6_000,
) -> list[list[dict]]:
    blocks: list[list[dict]] = []
    current: list[dict] = []
    duration = 0
    characters = 0
    for segment in segments:
        segment_duration = segment["duration_budget_ms"]
        segment_characters = len(segment["source_text"])
        if current and (
            len(current) >= max_segments
            or duration + segment_duration > max_duration_ms
            or characters + segment_characters > max_source_characters
        ):
            blocks.append(current)
            current = []
            duration = 0
            characters = 0
        current.append(segment)
        duration += segment_duration
        characters += segment_characters
    if current:
        blocks.append(current)
    return blocks


def apply_translation_result(timeline: dict, response: dict, expected_ids: list[str] | None = None) -> dict:
    validate_translation_response(response, expected_ids=expected_ids)
    by_id = {segment["id"]: segment for segment in timeline["segments"]}
    for transformed in response["segments"]:
        segment = by_id.get(transformed["id"])
        if segment is None:
            raise ValueError(f"Unknown segment in transform response: {transformed['id']}")
        segment["translated_text"] = transformed["translated_text"]
        segment["spoken_text"] = transformed["spoken_text"]
        segment["subtitle_text"] = transformed["subtitle_text"]
        segment["status"] = transformed.get("status", "draft")
        segment["issues"] = transformed.get("issues", [])
        segment["qa"]["content_approved"] = False
    validate_timeline(timeline)
    return timeline


def validate_translation_response(response: dict, expected_ids: list[str] | None = None) -> None:
    if response.get("schema_version") != 1:
        raise ValueError("Transform response schema_version must be 1")
    if not isinstance(response.get("segments"), list):
        raise ValueError("Transform response requires segments")
    required = {"id", "translated_text", "spoken_text", "subtitle_text", "duration_budget_ms"}
    seen_ids: list[str] = []
    for segment in response["segments"]:
        missing = required.difference(segment)
        if missing:
            raise ValueError(f"Transform segment missing fields: {sorted(missing)}")
        if not isinstance(segment["id"], str) or not segment["id"]:
            raise ValueError("Transform segment id must be a non-empty string")
        if segment["id"] in seen_ids:
            raise ValueError(f"Duplicate transform segment id: {segment['id']}")
        seen_ids.append(segment["id"])
        if not isinstance(segment["duration_budget_ms"], int) or segment["duration_budget_ms"] < 1:
            raise ValueError("duration_budget_ms must be a positive integer")
        if "issues" in segment and not isinstance(segment["issues"], list):
            raise ValueError("issues must be a list")
        if "status" in segment and segment["status"] not in {"draft", "review_needed", "approved", "rejected"}:
            raise ValueError("Invalid transform segment status")
        for text_field in ("translated_text", "spoken_text", "subtitle_text"):
            if not isinstance(segment[text_field], str):
                raise ValueError(f"Transform field must be string: {text_field}")
    if expected_ids is not None and seen_ids != expected_ids:
        raise ValueError("Transform response segment IDs must exactly match request order")


def save_content_revision(project_id: str, timeline_revision: dict, results: list, response: dict) -> dict:
    revision_id = f"crev_{uuid4().hex[:12]}"
    content_dir = ensure_dir(Path(timeline_revision["path"]).parents[1] / "content")
    path = content_dir / f"{revision_id}.json"
    payload = {
        "revision_id": revision_id,
        "project_id": project_id,
        "timeline_revision_id": timeline_revision["revision_id"],
        "provider": results[0].provider if results else "none",
        "model": results[0].model if results else "none",
        "request_hashes": [result.request_hash for result in results],
        "cache_status": _combined_cache_status(results),
        "approval": {"content_approved": False},
        "response": response,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    digest = sha256_file(path)
    with session_scope() as session:
        session.add(
            ContentRevision(
                project_id=project_id,
                revision_id=revision_id,
                timeline_revision_id=timeline_revision["revision_id"],
                provider_request_hash=",".join(result.request_hash for result in results),
                path=str(path),
                sha256=digest,
                approved=False,
            )
        )
    return {"revision_id": revision_id, "path": str(path), "sha256": digest}


def _combined_cache_status(results: list) -> str:
    if not results:
        return "empty"
    statuses = {result.cache_status for result in results}
    if statuses == {"hit"}:
        return "hit"
    if statuses == {"miss"}:
        return "miss"
    return "mixed"


def find_existing_content_revision(project_id: str, request_hashes: list[str]) -> dict | None:
    joined_hashes = ",".join(request_hashes)
    with session_scope() as session:
        revision = (
            session.query(ContentRevision)
            .filter(ContentRevision.project_id == project_id)
            .filter(ContentRevision.provider_request_hash == joined_hashes)
            .order_by(ContentRevision.created_at.desc(), ContentRevision.id.desc())
            .first()
        )
        if revision is None:
            return None
        content_path = Path(revision.path)
        content_payload = json.loads(content_path.read_text(encoding="utf-8"))
        timeline_path = content_path.parents[1] / "revisions" / f"{revision.timeline_revision_id}.json"
    timeline_payload = json.loads(timeline_path.read_text(encoding="utf-8"))
    return {
        "timeline": {
            "revision_id": timeline_payload["active_revision_id"],
            "path": str(timeline_path),
            "sha256": sha256_file(timeline_path),
            "timeline": timeline_payload,
        },
        "content_revision": {
            "revision_id": content_payload["revision_id"],
            "path": str(content_path),
            "sha256": sha256_file(content_path),
        },
        "segments_transformed": len(content_payload.get("response", {}).get("segments", [])),
    }


def record_provider_request(result) -> None:
    with session_scope() as session:
        existing = (
            session.query(ProviderRequest).filter(ProviderRequest.request_hash == result.request_hash).one_or_none()
        )
        if existing is not None:
            existing.cache_status = result.cache_status
            return
        session.add(
            ProviderRequest(
                provider=result.provider,
                request_hash=result.request_hash,
                model=result.model,
                cache_status=result.cache_status,
                credential_ref=result.credential_ref,
                status="succeeded",
            )
        )
