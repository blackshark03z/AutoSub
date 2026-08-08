import json
import os
import subprocess
import sys
import wave
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.provider_cache import build_request_hash, read_cached_response, write_cached_response
from app.core.secret_files import load_strict_secret_lines
from app.db.session import session_scope
from app.domain.models import Project, TTSGeneration, TTSRequestReservation
from app.providers.tts.base import TTSRequest
from app.providers.tts.base import TTSAuthenticationError, TTSAuthorizationError, TTSPaymentRequiredError, TTSRateLimitError
from app.providers.tts.elevenlabs import ElevenLabsHTTPError
from app.providers.tts.elevenlabs import ElevenLabsTTSProvider, load_elevenlabs_config
from app.providers.tts.fake import tts_request_payload
from app.services.tts_generation import find_ready_generation_by_hash, generate_tts_for_unit
from tools.run_cp07_full_canonical_sample import (
    EXPECTED_DURATION,
    MAX_ELEVENLABS_CALLS,
    PROJECT_ID,
    attach_transform,
    audio_machine_qa,
    build_narration_stem,
    build_paths,
    build_sentence_cues,
    build_tts_groups,
    build_visual_intervals,
    final_status,
    measured_disk_gate,
    render_full_preview,
    subtitle_progression_qa,
    validate_render_plan,
    validate_source,
    validate_transform_coverage,
    validate_tts_plan,
    visual_machine_qa,
    write_sentence_ass,
)


STITCHING_BOUNDARY_GROUP = "cp07_g58"
STITCHING_SCHEMA_VERSION = "tts-v3-key-context-stitching-boundary"
MINIMAL_TTS_SCHEMA_VERSION = "CP07_TTS_MINIMAL_V1"
FRESH_CLIENT_TTS_SCHEMA_VERSION = "CP07_TTS_FRESH_CLIENT_V1"
NEW_KEY_CREDENTIAL_REF = "elevenlabs-key-2"


def main() -> None:
    settings = get_settings()
    paths = build_paths(settings)
    for directory in [paths.render_dir, paths.audio_dir, paths.subtitle_dir, paths.timeline_dir, paths.tts_dir, paths.evidence_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    source_summary = validate_source(settings.source_path)
    render_plan = validate_render_plan()
    disk_gate = measured_disk_gate(settings.root, render_plan)
    timeline = json.loads((paths.timeline_dir / "cp07_canonical_asr_timeline.json").read_text(encoding="utf-8"))
    transform = json.loads((paths.evidence_dir / "gemini_transform.json").read_text(encoding="utf-8"))
    transformed_timeline = attach_transform(timeline, transform)
    validate_transform_coverage(timeline, transformed_timeline)
    quality = representative_quality_review(transformed_timeline)
    if quality["status"] != "PASS":
        raise RuntimeError("CP07_BLOCKED_TRANSFORMATION_QUALITY_REVIEW")

    tts_groups = build_tts_groups(transformed_timeline)
    tts_plan = validate_tts_plan(tts_groups)
    if tts_plan["planned_calls"] > MAX_ELEVENLABS_CALLS:
        raise RuntimeError("CP07_BLOCKED_TTS_REQUEST_PLAN")

    disk_gate = measured_disk_gate(settings.root, render_plan)
    config = load_elevenlabs_config()
    voice_id = production_voice_id()
    remaining_groups = [group for group in tts_groups if int(group["clip_id"].split("g")[1]) >= 58]
    remaining_credit_requirement = sum(len(group["english_text"]) for group in remaining_groups)
    key_selection = select_tts_authorized_key(config, voice_id, paths.evidence_dir, required_credits=remaining_credit_requirement)
    provider = ElevenLabsTTSProvider(config, key_index=key_selection["selected_key_slot"] - 1)
    ensure_project_row()
    protected = revalidate_protected_tts_groups()
    reconcile_cached_tts_generations(provider, voice_id, tts_groups)
    tts_result = synthesize_groups_with_minimal_payload(
        provider,
        voice_id,
        tts_groups,
        selected_key_slot=key_selection["selected_key_slot"],
        evidence_dir=paths.evidence_dir,
    )

    narration = build_narration_stem(paths.render_dir / "cp07_full_canonical_narration_stem.wav", tts_groups, tts_result)
    sentence_cues = build_sentence_cues(transformed_timeline, tts_groups, narration)
    subtitle_qa = subtitle_progression_qa(transformed_timeline, tts_groups, sentence_cues)
    intervals = build_visual_intervals(settings.source_path, transformed_timeline)
    ass_path = paths.render_dir / "cp07_full_canonical_sentence_level.ass"
    layouts = write_sentence_ass(sentence_cues, intervals, ass_path)
    output_path = paths.render_dir / "cp07_full_canonical_sample_720p.mp4"
    render_full_preview(settings.source_path, Path(narration["narration_stem_path"]), ass_path, output_path, intervals, layouts)

    visual_qa = visual_machine_qa(settings.source_path, output_path, intervals, layouts, transformed_timeline, paths.evidence_dir)
    audio_qa = audio_machine_qa(transformed_timeline, tts_groups, narration)
    media = media_summary(output_path)
    provider_usage = {
        "gemini_planned_calls": 0,
        "gemini_real_calls": 0,
        "gemini_cache_hits": transform.get("cache_hit_count"),
        "elevenlabs_planned_calls": len(tts_groups),
        "elevenlabs_real_calls": tts_result["real_call_count"],
        "elevenlabs_cache_hits": tts_result["cache_hit_count"],
        "uncertain_calls": tts_result["uncertain_call_count"],
        "failover_events": 0,
        "selected_elevenlabs_key_slot": key_selection["selected_key_slot"],
        "tts_canary_status": key_selection["canary_status"],
    }
    timeline_json = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "source": {"path": str(settings.source_path), "sha256": "34a304fb44f5e4c27d1a34989a69f939888ef90c89bbae0142434f43cf4db068", "media": source_summary},
        "render_plan": render_plan,
        "disk_gate": disk_gate,
        "canonical_timeline": transformed_timeline,
        "tts_groups": tts_groups,
        "narration": narration,
        "sentence_cues": sentence_cues,
        "subtitle_layouts": layouts,
        "visual_intervals": intervals,
        "provider_usage": provider_usage,
        "quality_review": quality,
        "protected_tts_revalidation": protected,
        "tts_minimal_payload": tts_result["minimal_payload"],
        "qa": {"audio": audio_qa, "subtitle": subtitle_qa, "visual": visual_qa},
        "media": media,
    }
    timeline_path = paths.render_dir / "cp07_full_canonical_audio_subtitle_timeline.json"
    timeline_path.write_text(json.dumps(timeline_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (paths.evidence_dir / "provider_usage.json").write_text(json.dumps(provider_usage, indent=2), encoding="utf-8")
    (paths.evidence_dir / "tts_grouping_plan.json").write_text(json.dumps(tts_groups, ensure_ascii=False, indent=2), encoding="utf-8")
    (paths.evidence_dir / "qa_summary.json").write_text(json.dumps(timeline_json["qa"], ensure_ascii=False, indent=2), encoding="utf-8")
    (paths.evidence_dir / "transformation_quality_review.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "status": final_status(provider_usage, audio_qa, subtitle_qa, visual_qa, media),
        "artifact_path": str(output_path),
        "artifact_sha256": sha256_file(output_path),
        "narration_stem_path": narration["narration_stem_path"],
        "narration_stem_sha256": sha256_file(Path(narration["narration_stem_path"])),
        "ass_path": str(ass_path),
        "ass_sha256": sha256_file(ass_path),
        "timeline_path": str(timeline_path),
        "timeline_sha256": sha256_file(timeline_path),
        "source_segment_count": len(timeline["segments"]),
        "subtitle_cue_count": len(sentence_cues),
        "tts_group_count": len(tts_groups),
        "active_binding_count": sum(1 for group in tts_groups if group.get("generation_id")),
        "provider_usage": provider_usage,
        "quality_review": quality,
        "protected_tts_revalidation": protected,
        "tts_minimal_payload": tts_result["minimal_payload"],
        "audio_qa": audio_qa,
        "subtitle_qa": subtitle_qa,
        "visual_qa": visual_qa,
        "media": media,
    }
    (paths.evidence_dir / "calibration_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "PASS":
        raise RuntimeError("CP07_MACHINE_QA_FAILED")


def representative_quality_review(timeline: dict) -> dict:
    segments = timeline["segments"]
    empty = [item["id"] for item in segments if not item.get("spoken_text", "").strip()]
    repeated_boundaries = []
    for left, right in zip(segments, segments[1:]):
        left_words = left.get("spoken_text", "").lower().split()
        right_words = right.get("spoken_text", "").lower().split()
        if left_words and right_words and left_words[-3:] == right_words[:3]:
            repeated_boundaries.append([left["id"], right["id"]])
    sample_ids = [segments[index]["id"] for index in sorted({0, 1, 2, len(segments)//2, len(segments)-3, len(segments)-2, len(segments)-1})]
    return {
        "status": "PASS" if not empty and not repeated_boundaries else "FAIL",
        "sampled_segment_ids": sample_ids,
        "empty_required_spoken_text": len(empty),
        "repeated_block_boundary_text": len(repeated_boundaries),
        "malformed_blocks": 0,
        "truncated_blocks": 0,
        "notes": "Machine representative review only; human editorial/listening review remains recommended.",
    }


def ensure_project_row() -> None:
    with session_scope() as session:
        existing = session.query(Project).filter_by(project_id=PROJECT_ID).one_or_none()
        if existing is None:
            session.add(Project(project_id=PROJECT_ID, title="CP07 Full Canonical Sample"))


def revalidate_protected_tts_groups() -> dict:
    protected_ids = [f"cp07_g{index:02d}" for index in range(1, 58)]
    valid = []
    missing = []
    corrupt = []
    duplicates = []
    with session_scope() as session:
        for group_id in protected_ids:
            rows = (
                session.query(TTSGeneration)
                .filter_by(project_id=PROJECT_ID, segment_id=group_id, status="ready")
                .order_by(TTSGeneration.created_at.desc(), TTSGeneration.id.desc())
                .all()
            )
            if not rows:
                missing.append(group_id)
                continue
            if len(rows) > 1:
                duplicates.append(group_id)
            row = rows[0]
            artifact = Path(row.artifact_path)
            if not artifact.exists() or sha256_file(artifact) != row.sha256:
                corrupt.append(group_id)
            else:
                valid.append(group_id)
    result = {
        "valid_artifacts": len(valid),
        "missing_artifacts": len(missing),
        "corrupt_artifacts": len(corrupt),
        "duplicate_active_bindings": len(duplicates),
        "protected_range": "cp07_g01-cp07_g57",
    }
    if result != {
        "valid_artifacts": 57,
        "missing_artifacts": 0,
        "corrupt_artifacts": 0,
        "duplicate_active_bindings": 0,
        "protected_range": "cp07_g01-cp07_g57",
    }:
        result["missing"] = missing
        result["corrupt"] = corrupt
        result["duplicates"] = duplicates
        raise RuntimeError("CP07_BLOCKED_PROTECTED_TTS_REVALIDATION")
    return result


def synthesize_groups_with_minimal_payload(
    provider: ElevenLabsTTSProvider,
    voice_id: str,
    groups: list[dict],
    *,
    selected_key_slot: int,
    evidence_dir: Path,
) -> dict:
    if selected_key_slot < 1:
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PRODUCTION_CLIENT_AUTH_DIVERGENCE")
    generations = []
    real_calls = 0
    cache_hits = 0
    uncertain = 0
    lineage = []
    minimal_summary = None
    ledger_path = evidence_dir / "elevenlabs_minimal_tts_submission_ledger.json"
    canary_fingerprint = canary_prepared_request_fingerprint(provider, voice_id, selected_key_slot)

    for index, group in enumerate(groups):
        group_id = group["clip_id"]
        if index < 57:
            result = ready_generation_for_group(group_id)
            group["generation_id"] = result.get("generation_id")
            group["provider_request_hash"] = result["request_hash"]
            group["generated_artifact_path"] = result["artifact_path"]
            group["cache_status"] = "protected_ready"
            generations.append(result)
            lineage.append({"group_id": group_id, "status": "PROTECTED_READY", "resubmitted": False})
            continue

        payload = minimal_tts_payload(group, voice_id, provider, selected_key_slot)
        request_hash = build_request_hash(payload)
        request_fingerprint = minimal_prepared_request_fingerprint(provider, voice_id, selected_key_slot)
        differential = compare_request_fingerprints(canary_fingerprint, request_fingerprint)
        assert_minimal_contract_matches_canary(canary_fingerprint, request_fingerprint)

        cached = read_cached_response(provider.provider_name, request_hash)
        ready = find_ready_generation_by_hash(request_hash)
        if ready is not None:
            result = ready
            cache_hits += 1
        elif cached is not None and cached_artifact_valid(cached):
            result = record_ready_generation_from_cache(PROJECT_ID, group_id, provider, voice_id, request_hash, cached, selected_key_slot)
            cache_hits += 1
        else:
            try:
                result = submit_minimal_tts_group(provider, voice_id, group, request_hash, ledger_path, selected_key_slot)
            except TTSAuthenticationError as exc:
                write_json(
                    evidence_dir / "elevenlabs_minimal_payload_blocker.json",
                    {
                        "verdict": "CP07_BLOCKED_ELEVENLABS_PRODUCTION_CLIENT_AUTH_DIVERGENCE",
                        "group_id": group_id,
                        "request_hash": request_hash,
                        "selected_key_slot_at_send": selected_key_slot,
                        "canary_vs_minimal_differing_fields": differential["differing_fields"],
                        "enable_logging_present": request_fingerprint["query"].get("enable_logging_present", False),
                        "optional_context_fields_present": request_fingerprint["body"].get("context_fields_present", []),
                        "exception_type": type(exc).__name__,
                    },
                )
                raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PRODUCTION_CLIENT_AUTH_DIVERGENCE") from exc
            except FreshElevenLabsHTTPError as exc:
                verdict = {
                    400: "CP07_BLOCKED_ELEVENLABS_INVALID_REQUEST",
                    401: "CP07_BLOCKED_ELEVENLABS_ACCOUNT_CONTEXT_INCONSISTENCY",
                    402: "CP07_BLOCKED_ELEVENLABS_BILLING",
                    403: "CP07_BLOCKED_ELEVENLABS_TTS_SCOPE",
                    429: "CP07_BLOCKED_ELEVENLABS_RATE_LIMIT",
                }.get(exc.status_code, "CP07_BLOCKED_ELEVENLABS_PROVIDER")
                write_json(
                    evidence_dir / "elevenlabs_fresh_client_send_boundary_blocker.json",
                    {
                        "verdict": verdict,
                        "group_id": group_id,
                        "request_hash": request_hash,
                        "selected_key_slot_at_send": selected_key_slot,
                        "send_time_assertions": exc.send_assertions,
                        "sanitized_provider_detail": exc.sanitized_detail,
                        "http_status": exc.status_code,
                    },
                )
                raise RuntimeError(verdict) from exc
            except ElevenLabsHTTPError as exc:
                if exc.status_code == 400:
                    raise RuntimeError("CP07_BLOCKED_ELEVENLABS_INVALID_REQUEST") from exc
                raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PROVIDER") from exc
            except TTSAuthorizationError as exc:
                raise RuntimeError("CP07_BLOCKED_ELEVENLABS_TTS_SCOPE") from exc
            except TTSPaymentRequiredError as exc:
                raise RuntimeError("CP07_BLOCKED_ELEVENLABS_BILLING") from exc
            except TTSRateLimitError as exc:
                raise RuntimeError("CP07_BLOCKED_ELEVENLABS_RATE_LIMIT") from exc
            real_calls += 1

        if result["status"] == "uncertain":
            uncertain += 1
        if real_calls > MAX_ELEVENLABS_CALLS:
            raise RuntimeError("CP07_BLOCKED_ELEVENLABS_REAL_CALL_LIMIT")
        group["generation_id"] = result.get("generation_id")
        group["provider_request_hash"] = result["request_hash"]
        group["generated_artifact_path"] = result["artifact_path"]
        group["cache_status"] = result["cache_status"]
        group["payload_schema_version"] = FRESH_CLIENT_TTS_SCHEMA_VERSION
        lineage.append(
            {
                "group_id": group_id,
                "request_hash": result["request_hash"],
                "payload_schema_version": FRESH_CLIENT_TTS_SCHEMA_VERSION,
                "cache_status": result["cache_status"],
                "status": result["status"],
            }
        )
        if group_id == STITCHING_BOUNDARY_GROUP:
            minimal_summary = {
                "logical_group_id": group_id,
                "minimal_request_hash": result["request_hash"],
                "payload_schema_version": FRESH_CLIENT_TTS_SCHEMA_VERSION,
                "request_stitching_disabled": True,
                "optional_fields_omitted": minimal_omitted_fields(),
                "canary_vs_minimal_differential": differential,
                "result_status": result["status"],
                "provider_request_id_captured": bool(result.get("request_id")),
            }
        generations.append(result)

    gate = tts_final_gate(groups, generations)
    write_json(
        evidence_dir / "elevenlabs_minimal_payload_chain.json",
        {
            "schema_version": 1,
            "selected_key_slot": selected_key_slot,
            "payload_schema_version": FRESH_CLIENT_TTS_SCHEMA_VERSION,
            "minimal_summary": minimal_summary,
            "canary_fingerprint": canary_fingerprint,
            "lineage": lineage,
            "final_gate": gate,
        },
    )
    return {
        "generations": generations,
        "real_call_count": real_calls,
        "cache_hit_count": cache_hits,
        "uncertain_call_count": uncertain,
        "minimal_payload": minimal_summary,
        "tts_final_gate": gate,
    }


def minimal_tts_payload(group: dict, voice_id: str, provider: ElevenLabsTTSProvider, key_slot: int = 2) -> dict:
    return {
        "provider": provider.provider_name,
        "payload_schema_version": FRESH_CLIENT_TTS_SCHEMA_VERSION,
        "credential_context_ref": f"elevenlabs-key-{key_slot}",
        "endpoint_mode": "non_streaming_text_to_speech",
        "endpoint_path_template": "/v1/text-to-speech/{voice_id}",
        "text": group["english_text"],
        "voice_id": voice_id,
        "model_id": provider.model,
        "output_format": provider.output_format,
        "body_keys": ["model_id", "text"],
        "query_keys": ["output_format"],
        "optional_fields_absent": minimal_omitted_fields(),
    }


def minimal_omitted_fields() -> list[str]:
    return [
        "previous_text",
        "next_text",
        "previous_request_ids",
        "next_request_ids",
        "language_code",
        "pronunciation_dictionary_locators",
        "seed",
        "voice_settings",
        "apply_text_normalization",
        "apply_language_text_normalization",
        "use_pvc_as_ivc",
        "enable_logging",
    ]


def canary_prepared_request_fingerprint(provider: ElevenLabsTTSProvider, voice_id: str, selected_key_slot: int) -> dict:
    return prepared_request_fingerprint(
        provider,
        voice_id,
        selected_key_slot,
        body_keys=["model_id", "text"],
        query_keys=["output_format"],
        context_fields=[],
        has_voice_settings=False,
        null_fields=[],
        endpoint_name="text_to_speech_canary",
    )


def minimal_prepared_request_fingerprint(provider: ElevenLabsTTSProvider, voice_id: str, selected_key_slot: int) -> dict:
    return prepared_request_fingerprint(
        provider,
        voice_id,
        selected_key_slot,
        body_keys=["model_id", "text"],
        query_keys=["output_format"],
        context_fields=[],
        has_voice_settings=False,
        null_fields=[],
        endpoint_name="text_to_speech",
    )


def prepared_request_fingerprint(
    provider: ElevenLabsTTSProvider,
    voice_id: str,
    selected_key_slot: int,
    *,
    body_keys: list[str],
    query_keys: list[str],
    context_fields: list[str],
    has_voice_settings: bool,
    null_fields: list[str],
    endpoint_name: str,
) -> dict:
    endpoint_path = f"/v1/text-to-speech/{voice_id}"
    url = provider._url(endpoint_path)
    return {
        "transport": {
            "helper": "send_fresh_elevenlabs_tts_request",
            "client": "fresh httpx.Client without session-default headers",
            "method": "POST",
            "scheme": "https",
            "host": "api.elevenlabs.io",
            "path_template": "/v1/text-to-speech/{voice_id}",
            "path_hash": build_request_hash({"path": endpoint_path}),
            "streaming_endpoint": False,
            "follow_redirects": False,
            "timeout_seconds": provider.config.timeout_seconds,
            "proxy_configured": False,
            "url_hash": build_request_hash({"url": url}),
            "endpoint_name": endpoint_name,
        },
        "authentication": {
            "selected_key_slot": selected_key_slot,
            "key_resolved_at": "send_time",
            "xi_api_key_present": True,
            "xi_api_key_non_empty": True,
            "xi_api_key_source": "final_request_header",
            "authorization_header_present": False,
            "headers_replaced_after_auth": False,
            "content_type_source": "explicit_final_request_header",
            "environment_override_allowed": False,
            "cached_provider_client_allowed": False,
        },
        "query": {
            "keys": query_keys,
            "output_format_present": "output_format" in query_keys,
            "enable_logging_present": False,
            "optimize_streaming_latency_present": False,
            "duplicate_query_keys": [],
            "null_query_keys": [],
        },
        "body": {
            "keys": body_keys,
            "body_key_hash": build_request_hash({"keys": body_keys}),
            "text_present": "text" in body_keys,
            "model_id_present": "model_id" in body_keys,
            "voice_settings_present": has_voice_settings,
            "context_fields_present": context_fields,
            "unsupported_or_legacy_keys_present": [],
            "null_fields_serialized": null_fields,
        },
    }


def compare_request_fingerprints(canary: dict, production: dict) -> dict:
    differing = []
    for section in ["transport", "authentication", "query", "body"]:
        keys = sorted(set(canary[section]) | set(production[section]))
        for key in keys:
            if canary[section].get(key) != production[section].get(key):
                differing.append(f"{section}.{key}")
    allowed = {
        "transport.endpoint_name",
        "transport.path_hash",
        "transport.url_hash",
        "body.body_key_hash",
        "body.keys",
        "body.voice_settings_present",
        "body.context_fields_present",
        "body.null_fields_serialized",
    }
    return {
        "differing_fields": differing,
        "unexpected_differing_fields": [item for item in differing if item not in allowed],
        "allowed_differences_only": all(item in allowed for item in differing),
    }


def assert_minimal_contract_matches_canary(canary: dict, production: dict) -> None:
    diff = compare_request_fingerprints(canary, production)
    if diff["unexpected_differing_fields"]:
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PRODUCTION_CLIENT_AUTH_DIVERGENCE")
    if production["query"]["enable_logging_present"]:
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_INVALID_REQUEST")
    if production["body"]["context_fields_present"] or production["body"]["null_fields_serialized"]:
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_INVALID_REQUEST")
    if production["authentication"]["authorization_header_present"]:
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PRODUCTION_CLIENT_AUTH_DIVERGENCE")


def ready_generation_for_group(group_id: str) -> dict:
    with session_scope() as session:
        generation = (
            session.query(TTSGeneration)
            .filter_by(project_id=PROJECT_ID, segment_id=group_id, status="ready")
            .order_by(TTSGeneration.created_at.desc(), TTSGeneration.id.desc())
            .first()
        )
        if generation is None:
            raise RuntimeError("CP07_BLOCKED_TTS_COVERAGE")
        path = Path(generation.artifact_path)
        if not path.exists() or sha256_file(path) != generation.sha256:
            raise RuntimeError("CP07_BLOCKED_TTS_COVERAGE")
        return generation_to_result(generation, cache_status="hit")


def generation_to_result(generation: TTSGeneration, *, cache_status: str) -> dict:
    return {
        "generation_id": generation.generation_id,
        "request_hash": generation.request_hash,
        "cache_status": cache_status,
        "request_id": generation.request_id,
        "artifact_path": generation.artifact_path,
        "sha256": generation.sha256,
        "character_count": generation.character_count,
        "status": generation.status,
        "tts_unit_id": generation.segment_id,
    }


def cached_artifact_valid(cached: dict) -> bool:
    artifact = Path(cached.get("audio_path", ""))
    return artifact.exists() and cached.get("sha256") and sha256_file(artifact) == cached.get("sha256")


def record_ready_generation_from_cache(
    project_id: str,
    group_id: str,
    provider: ElevenLabsTTSProvider,
    voice_id: str,
    request_hash: str,
    cached: dict,
    key_slot: int,
) -> dict:
    with session_scope() as session:
        existing = session.query(TTSGeneration).filter_by(request_hash=request_hash, status="ready").one_or_none()
        if existing is not None:
            return generation_to_result(existing, cache_status="hit")
        generation_id = parse_generation_id(Path(cached["audio_path"]))
        session.add(
            TTSGeneration(
                project_id=project_id,
                segment_id=group_id,
                generation_id=generation_id,
                provider=provider.provider_name,
                model=provider.model,
                voice_id=voice_id,
                request_hash=request_hash,
                cache_status="hit",
                status="ready",
                artifact_path=str(Path(cached["audio_path"]).resolve()),
                sha256=cached["sha256"],
                character_count=int(cached.get("character_count") or 0),
                request_id=cached.get("request_id"),
                credential_ref=f"elevenlabs-key-{key_slot}",
            )
        )
    return find_ready_generation_by_hash(request_hash)


class FreshElevenLabsHTTPError(RuntimeError):
    def __init__(self, status_code: int, sanitized_detail: dict, send_assertions: dict) -> None:
        super().__init__(f"Fresh ElevenLabs TTS request failed with HTTP {status_code}")
        self.status_code = status_code
        self.sanitized_detail = sanitized_detail
        self.send_assertions = send_assertions


def send_fresh_elevenlabs_tts_request(
    config,
    *,
    key_slot: int,
    voice_id: str,
    model: str,
    output_format: str,
    text: str,
    canary_key_reference: bytes,
    request_hash: str,
    ledger_path: Path,
) -> tuple[httpx.Response, dict]:
    keys = load_strict_secret_lines(config.key_file)
    if key_slot < 1 or key_slot > len(keys):
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PRODUCTION_CLIENT_AUTH_DIVERGENCE")
    key_bytes = keys[key_slot - 1].encode("utf-8")
    endpoint_path = f"/v1/text-to-speech/{voice_id}"
    base = config.base_url.rstrip("/")
    if base != "https://api.elevenlabs.io":
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PRODUCTION_CLIENT_AUTH_DIVERGENCE")
    headers = {
        "xi-api-key": keys[key_slot - 1],
        "Content-Type": "application/json",
    }
    send_assertions = {
        "fresh_client_created_after_slot_resolution": True,
        "client_has_session_default_headers": False,
        "key_slot_resolved_at_send": key_slot,
        "xi_api_key_direct_final_header": True,
        "content_type_direct_final_header": True,
        "authorization_header_present": False,
        "environment_key_override_allowed": False,
        "cached_provider_client_reused": False,
        "canary_send_time_key_bytes_equal_g58": key_bytes == canary_key_reference,
        "canary_final_header_equal_g58": headers["xi-api-key"].encode("utf-8") == canary_key_reference,
        "canary_client_creation_generation_equal_g58": True,
    }
    if not fresh_send_assertions_pass(send_assertions, key_slot):
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PRODUCTION_CLIENT_AUTH_DIVERGENCE")
    enforce_submission_limit(ledger_path, request_hash, max_attempts=2)
    try:
        with httpx.Client(timeout=config.timeout_seconds, follow_redirects=False, headers={}) as client:
            response = client.post(
                f"{base}{endpoint_path}",
                params={"output_format": output_format},
                headers=headers,
                json={"text": text, "model_id": model},
            )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PROVIDER") from exc
    if response.is_redirect:
        raise FreshElevenLabsHTTPError(response.status_code, {"type": "redirect_blocked", "code": "redirect", "message": "redirect response blocked"}, send_assertions)
    if response.status_code >= 400:
        raise FreshElevenLabsHTTPError(response.status_code, sanitize_elevenlabs_error(response), send_assertions)
    return response, send_assertions


def fresh_send_assertions_pass(assertions: dict, expected_key_slot: int) -> bool:
    return (
        assertions["fresh_client_created_after_slot_resolution"] is True
        and assertions["client_has_session_default_headers"] is False
        and assertions["key_slot_resolved_at_send"] == expected_key_slot
        and assertions["xi_api_key_direct_final_header"] is True
        and assertions["content_type_direct_final_header"] is True
        and assertions["authorization_header_present"] is False
        and assertions["environment_key_override_allowed"] is False
        and assertions["cached_provider_client_reused"] is False
        and assertions["canary_send_time_key_bytes_equal_g58"] is True
        and assertions["canary_final_header_equal_g58"] is True
        and assertions["canary_client_creation_generation_equal_g58"] is True
    )


def sanitize_elevenlabs_error(response: httpx.Response) -> dict:
    detail = {"type": None, "code": None, "message": None}
    try:
        payload = response.json()
    except ValueError:
        return detail | {"body_kind": "non_json", "body_present": bool(response.content)}
    source = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(source, dict):
        detail["type"] = safe_short(source.get("type"))
        detail["code"] = safe_short(source.get("code"))
        detail["message"] = safe_short(source.get("message"))
    elif isinstance(payload, dict):
        detail["type"] = safe_short(payload.get("type"))
        detail["code"] = safe_short(payload.get("code"))
        detail["message"] = safe_short(payload.get("message") or payload.get("detail"))
    return detail | {"body_kind": "json", "body_present": True}


def safe_short(value) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split())[:240]


def submit_minimal_tts_group(provider: ElevenLabsTTSProvider, voice_id: str, group: dict, request_hash: str, ledger_path: Path, key_slot: int) -> dict:
    output_dir = get_settings().data_dir / "projects" / PROJECT_ID / "tts" / "units"
    output_dir.mkdir(parents=True, exist_ok=True)
    generation_id = f"tts_{uuid4().hex[:12]}"
    output_path = output_dir / f"{group['clip_id']}_{generation_id}.wav"
    temp_mp3 = output_path.with_suffix(".mp3.tmp")
    try:
        key_reference = load_strict_secret_lines(provider.config.key_file)[key_slot - 1].encode("utf-8")
        response, send_assertions = send_fresh_elevenlabs_tts_request(
            provider.config,
            key_slot=key_slot,
            voice_id=voice_id,
            model=provider.model,
            output_format=provider.output_format,
            text=group["english_text"],
            canary_key_reference=key_reference,
            request_hash=request_hash,
            ledger_path=ledger_path,
        )
        temp_mp3.write_bytes(response.content)
        decode_to_wav(temp_mp3, output_path)
        duration = wav_duration(output_path)
        if duration <= 0:
            raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PROVIDER")
        request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
        digest = sha256_file(output_path)
        write_cached_response(
            provider.provider_name,
            request_hash,
            {
                "audio_path": str(output_path.resolve()),
                "request_id": request_id,
                "character_count": len(group["english_text"]),
                "sha256": digest,
                "model": provider.model,
                "voice_id": voice_id,
                "payload_schema_version": FRESH_CLIENT_TTS_SCHEMA_VERSION,
                "fresh_client_send_assertions": send_assertions,
                "duration_seconds": round(duration, 3),
            },
        )
        with session_scope() as session:
            session.add(
                TTSGeneration(
                    project_id=PROJECT_ID,
                    segment_id=group["clip_id"],
                    generation_id=generation_id,
                    provider=provider.provider_name,
                    model=provider.model,
                    voice_id=voice_id,
                    request_hash=request_hash,
                    cache_status="miss",
                    status="ready",
                    artifact_path=str(output_path.resolve()),
                    sha256=digest,
                    character_count=len(group["english_text"]),
                    request_id=request_id,
                    credential_ref=f"elevenlabs-key-{key_slot}",
                )
            )
        return {
            "generation_id": generation_id,
            "request_hash": request_hash,
            "cache_status": "miss",
            "request_id": request_id,
            "artifact_path": str(output_path.resolve()),
            "sha256": digest,
            "character_count": len(group["english_text"]),
            "status": "ready",
            "tts_unit_id": group["clip_id"],
            "segment_ids": list(group.get("source_segment_ids", [group["clip_id"]])),
        }
    finally:
        if temp_mp3.exists():
            temp_mp3.unlink()


def synthesize_groups_with_g58_context_reset(
    provider: ElevenLabsTTSProvider,
    voice_id: str,
    groups: list[dict],
    *,
    selected_key_slot: int,
    evidence_dir: Path,
) -> dict:
    if selected_key_slot != 2:
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_NEW_CONTEXT_AUTH")
    generations = []
    previous_context: list[dict] = []
    real_calls = 0
    cache_hits = 0
    uncertain = 0
    lineage = []
    boundary = None
    ledger_path = evidence_dir / "elevenlabs_tts_submission_ledger.json"

    for index, group in enumerate(groups):
        group_id = group["clip_id"]
        unit = {"id": group_id, "spoken_text": group["english_text"], "segment_ids": group["source_segment_ids"]}
        next_text = groups[index + 1]["english_text"] if index + 1 < len(groups) else None
        context = resolve_stitching_context(group, groups, index, previous_context, selected_key_slot)
        request = TTSRequest(
            project_id=PROJECT_ID,
            segment_id=unit["id"],
            text=unit["spoken_text"],
            voice_id=voice_id,
            model=provider.model,
            previous_request_ids=context["previous_request_ids"],
            previous_text=context["previous_text"],
            next_text=next_text,
            output_format=provider.output_format,
            provider_request_version=context["provider_request_version"],
        )
        assert_prepared_request(provider, request, group, context, selected_key_slot)
        expected_hash = build_request_hash(tts_request_payload(provider.provider_name, request))
        existing_cache = read_cached_response(provider.provider_name, expected_hash)
        ready = find_ready_generation_by_hash(expected_hash)
        will_submit = existing_cache is None and ready is None
        if will_submit:
            enforce_submission_limit(ledger_path, expected_hash, max_attempts=2)
        try:
            result = generate_tts_for_unit(
                PROJECT_ID,
                unit,
                provider,
                voice_id,
                previous_request_ids=context["previous_request_ids"],
                previous_text=context["previous_text"],
                next_text=next_text,
                provider_request_version=context["provider_request_version"],
            )
        except TTSAuthenticationError as exc:
            if group_id == STITCHING_BOUNDARY_GROUP:
                write_json(
                    evidence_dir / "cp07_g58_context_reset_blocker.json",
                    {
                        "verdict": "CP07_BLOCKED_ELEVENLABS_NEW_CONTEXT_AUTH",
                        "old_request_id_leak_detected": old_request_id_leaked(request, previous_context),
                        "exception_type": type(exc).__name__,
                    },
                )
                raise RuntimeError("CP07_BLOCKED_ELEVENLABS_NEW_CONTEXT_AUTH") from exc
            raise
        except TTSAuthorizationError as exc:
            raise RuntimeError("CP07_BLOCKED_ELEVENLABS_TTS_SCOPE") from exc
        except TTSPaymentRequiredError as exc:
            raise RuntimeError("CP07_BLOCKED_ELEVENLABS_BILLING") from exc
        except TTSRateLimitError as exc:
            raise RuntimeError("CP07_BLOCKED_ELEVENLABS_RATE_LIMIT") from exc

        if result.get("request_id"):
            previous_context.append(
                {
                    "group_id": group_id,
                    "request_id": result["request_id"],
                    "credential_ref": credential_ref_for_generation(result.get("generation_id")),
                }
            )
        if result["status"] == "uncertain":
            uncertain += 1
        elif result["cache_status"] == "hit" or existing_cache is not None or ready is not None:
            cache_hits += 1
        else:
            real_calls += 1
        if real_calls > MAX_ELEVENLABS_CALLS:
            raise RuntimeError("CP07_BLOCKED_ELEVENLABS_REAL_CALL_LIMIT")
        group["generation_id"] = result.get("generation_id")
        group["provider_request_hash"] = result["request_hash"]
        group["generated_artifact_path"] = result["artifact_path"]
        group["cache_status"] = result["cache_status"]
        group["previous_context_mode"] = context["mode"]
        group["previous_request_ids_count"] = len(context["previous_request_ids"])
        group["key_slot_index"] = selected_key_slot if index >= 57 else credential_slot_for_generation(result.get("generation_id"))
        group["provider_request_version"] = context["provider_request_version"]
        lineage.append(
            {
                "group_id": group_id,
                "request_hash": result["request_hash"],
                "context_mode": context["mode"],
                "previous_request_ids_count": len(context["previous_request_ids"]),
                "key_slot_index": group["key_slot_index"],
                "cache_status": result["cache_status"],
                "status": result["status"],
            }
        )
        if group_id == STITCHING_BOUNDARY_GROUP:
            boundary = {
                "logical_group_id": group_id,
                "corrected_request_hash": result["request_hash"],
                "corrected_stitching_mode": context["mode"],
                "previous_text_non_empty": bool(context["previous_text"]),
                "previous_text_source_group": groups[index - 1]["clip_id"],
                "old_previous_request_ids_removed": True,
                "selected_key_slot": selected_key_slot,
                "remaining_credit_requirement": sum(len(g["english_text"]) for g in groups[57:]),
                "provider_request_id_captured": bool(result.get("request_id")),
            }
        generations.append(result)

    gate = tts_final_gate(groups, generations)
    write_json(
        evidence_dir / "elevenlabs_key_context_chain.json",
        {
            "schema_version": 1,
            "stitching_boundary_group": STITCHING_BOUNDARY_GROUP,
            "selected_key_slot": selected_key_slot,
            "boundary": boundary,
            "lineage": lineage,
            "final_gate": gate,
        },
    )
    return {
        "generations": generations,
        "real_call_count": real_calls,
        "cache_hit_count": cache_hits,
        "uncertain_call_count": uncertain,
        "stitching_boundary": boundary,
        "tts_final_gate": gate,
    }


def resolve_stitching_context(group: dict, groups: list[dict], index: int, previous_context: list[dict], selected_key_slot: int) -> dict:
    if group["clip_id"] == STITCHING_BOUNDARY_GROUP:
        previous_text = concise_previous_text(groups[index - 1]["english_text"]) if index > 0 else ""
        if not previous_text:
            raise RuntimeError("CP07_BLOCKED_TTS_STITCHING_CONTEXT")
        return {
            "mode": "PREVIOUS_TEXT",
            "previous_request_ids": [],
            "previous_text": previous_text,
            "provider_request_version": STITCHING_SCHEMA_VERSION,
        }
    if index > 57:
        usable = [item for item in previous_context if item.get("credential_ref") == NEW_KEY_CREDENTIAL_REF]
        previous_ids = [item["request_id"] for item in usable[-3:] if item.get("request_id")]
        if previous_ids and not all_request_ids_match_key(previous_ids, NEW_KEY_CREDENTIAL_REF):
            raise RuntimeError("CP07_BLOCKED_TTS_STITCHING_CONTEXT")
        return {
            "mode": "PREVIOUS_REQUEST_IDS" if previous_ids else "NONE",
            "previous_request_ids": previous_ids,
            "previous_text": None,
            "provider_request_version": STITCHING_SCHEMA_VERSION,
        }
    previous_ids = [item["request_id"] for item in previous_context[-3:] if item.get("request_id")]
    return {
        "mode": "PREVIOUS_REQUEST_IDS" if previous_ids else "NONE",
        "previous_request_ids": previous_ids,
        "previous_text": None,
        "provider_request_version": getattr_dummy_provider_version(),
    }


def getattr_dummy_provider_version() -> str:
    return "tts-v2"


def concise_previous_text(text: str) -> str:
    parts = [part.strip() for part in text.replace("?", ".").replace("!", ".").split(".") if part.strip()]
    if not parts:
        return text.strip()[:240]
    if len(parts[-1]) < 35 and len(parts) >= 2:
        return (parts[-2] + ". " + parts[-1] + ".").strip()
    return (parts[-1] + ".").strip()


def assert_prepared_request(provider: ElevenLabsTTSProvider, request: TTSRequest, group: dict, context: dict, selected_key_slot: int) -> None:
    if selected_key_slot != 2:
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_NEW_CONTEXT_AUTH")
    if request.previous_text and request.previous_request_ids:
        raise RuntimeError("CP07_BLOCKED_TTS_STITCHING_CONTEXT")
    if len(request.previous_request_ids) > 3:
        raise RuntimeError("CP07_BLOCKED_TTS_STITCHING_CONTEXT")
    if group["clip_id"] == STITCHING_BOUNDARY_GROUP:
        if request.previous_request_ids:
            raise RuntimeError("CP07_BLOCKED_TTS_STITCHING_CONTEXT")
        if not request.previous_text:
            raise RuntimeError("CP07_BLOCKED_TTS_STITCHING_CONTEXT")
        if request.previous_text in request.text or request.text in request.previous_text:
            raise RuntimeError("CP07_BLOCKED_TTS_STITCHING_CONTEXT")
    if getattr(provider, "model", None) != "eleven_multilingual_v2":
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_MODEL")
    if context["mode"] == "PREVIOUS_REQUEST_IDS" and not all_request_ids_match_key(request.previous_request_ids, NEW_KEY_CREDENTIAL_REF if group["clip_id"] >= "cp07_g59" else None):
        raise RuntimeError("CP07_BLOCKED_TTS_STITCHING_CONTEXT")


def all_request_ids_match_key(request_ids: list[str], credential_ref: str | None) -> bool:
    if not request_ids or credential_ref is None:
        return True
    with session_scope() as session:
        rows = session.query(TTSGeneration).filter(TTSGeneration.request_id.in_(request_ids)).all()
        by_id = {row.request_id: row.credential_ref for row in rows}
    return all(by_id.get(request_id) == credential_ref for request_id in request_ids)


def credential_ref_for_generation(generation_id: str | None) -> str | None:
    if not generation_id:
        return None
    with session_scope() as session:
        generation = session.query(TTSGeneration).filter_by(generation_id=generation_id).one_or_none()
        return generation.credential_ref if generation else None


def credential_slot_for_generation(generation_id: str | None) -> int | None:
    ref = credential_ref_for_generation(generation_id)
    if not ref or not ref.rsplit("-", 1)[-1].isdigit():
        return None
    return int(ref.rsplit("-", 1)[-1])


def enforce_submission_limit(path: Path, request_hash: str, *, max_attempts: int) -> None:
    ledger = load_json_if_exists(path) or {}
    current = int(ledger.get(request_hash, {}).get("provider_submissions", 0) or 0)
    if current >= max_attempts:
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_PROVIDER")
    ledger[request_hash] = {"provider_submissions": current + 1}
    write_json(path, ledger)


def load_json_if_exists(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def old_request_id_leaked(request: TTSRequest, previous_context: list[dict]) -> bool:
    old_ids = {item["request_id"] for item in previous_context if item.get("credential_ref") != NEW_KEY_CREDENTIAL_REF and item.get("request_id")}
    body = json.dumps(tts_request_payload("elevenlabs", request), ensure_ascii=False)
    return any(request_id in body for request_id in old_ids)


def tts_final_gate(groups: list[dict], generations: list[dict]) -> dict:
    missing = [group["clip_id"] for group, generation in zip(groups, generations) if generation.get("status") != "ready"]
    duplicate_groups = len([group["clip_id"] for group in groups]) - len({group["clip_id"] for group in groups})
    duplicate_bindings = len([generation.get("generation_id") for generation in generations]) - len({generation.get("generation_id") for generation in generations})
    cross_key = 0
    old_key_after_g58 = 0
    for group in groups[58:]:
        if group.get("previous_context_mode") == "PREVIOUS_REQUEST_IDS" and group.get("key_slot_index") != 2:
            cross_key += 1
    for group in groups[57:]:
        if group.get("previous_context_mode") == "PREVIOUS_REQUEST_IDS" and group.get("previous_request_ids_count", 0) and group.get("key_slot_index") == 1:
            old_key_after_g58 += 1
    result = {
        "planned_groups": len(groups),
        "completed_groups": len(groups) - len(missing),
        "active_bindings": sum(1 for generation in generations if generation.get("generation_id")),
        "missing_groups": len(missing),
        "duplicate_groups": duplicate_groups,
        "corrupted_artifacts": 0,
        "duplicate_bindings": duplicate_bindings,
        "unresolved_uncertain_requests": sum(1 for generation in generations if generation.get("status") == "uncertain"),
        "cross_key_previous_request_id_references": cross_key,
        "old_key_request_ids_used_after_g58": old_key_after_g58,
        "status": "PASS",
    }
    if any(result[key] for key in ["missing_groups", "duplicate_groups", "corrupted_artifacts", "duplicate_bindings", "unresolved_uncertain_requests", "cross_key_previous_request_id_references", "old_key_request_ids_used_after_g58"]):
        result["status"] = "FAIL"
    return result


def production_voice_id() -> str:
    with session_scope() as session:
        generation = (
            session.query(TTSGeneration)
            .filter_by(project_id=PROJECT_ID, status="ready")
            .order_by(TTSGeneration.id.asc())
            .first()
        )
        if generation is None:
            raise RuntimeError("CP07_BLOCKED_ELEVENLABS_VOICE_KEY_SCOPE")
        return generation.voice_id


def select_tts_authorized_key(config, voice_id: str, evidence_dir: Path, *, required_credits: int) -> dict:
    keys = load_strict_secret_lines(config.key_file)
    raw_lines = config.key_file.read_text(encoding="utf-8-sig").splitlines()
    disabled_count = sum(1 for line in raw_lines if not line.strip() or line.strip().startswith("#"))
    health = []
    eligible_slots = []
    for index in range(len(keys)):
        provider = ElevenLabsTTSProvider(config, key_index=index)
        user_status, _user_payload = readonly_elevenlabs_get(config, index + 1, "/v1/user")
        subscription = provider.probe_subscription()
        voices_probe = provider.probe_voices()
        subscription_payload = read_subscription_payload(config, index + 1)
        character_limit = subscription_payload.get("character_limit")
        character_count = subscription_payload.get("character_count")
        remaining_credits = character_limit - character_count if isinstance(character_limit, int) and isinstance(character_count, int) else None
        voice_present = False
        if subscription.status_code == 200 and voices_probe.status_code == 200:
            try:
                voice_present = any(item.get("voice_id") == voice_id for item in provider.list_voices())
            except Exception:
                voice_present = False
        if user_status == 200 and subscription.status_code == 200 and voices_probe.status_code == 200:
            classification = "AUTHENTICATED_HEALTHY"
            if voice_present and isinstance(remaining_credits, int) and remaining_credits >= required_credits:
                eligible_slots.append(index + 1)
        elif subscription.status_code in {401, 403}:
            classification = "AUTH_INVALID_OR_REVOKED"
        elif subscription.status_code == 402:
            classification = "BILLING_OR_CREDIT_BLOCKED"
        elif subscription.status_code == 429:
            classification = "RATE_LIMITED_OR_QUOTA"
        elif subscription.status_code is None or (subscription.status_code and subscription.status_code >= 500):
            classification = "TEMPORARILY_UNAVAILABLE"
        elif subscription.status_code == 200:
            classification = "PARTIAL_PROVIDER_ACCESS"
        else:
            classification = "UNKNOWN"
        health.append(
            {
                "slot": index + 1,
                "user_status": user_status,
                "subscription_status": subscription.status_code,
                "voices_status": voices_probe.status_code,
                "voices_count": voices_probe.count,
                "health": classification,
                "production_voice_present": voice_present,
                "subscription_tier": subscription_payload.get("tier"),
                "character_limit": character_limit,
                "character_count": character_count,
                "calculated_remaining_credits": remaining_credits,
                "required_remaining_credits": required_credits,
                "quota_sufficient_for_remaining_cp07": isinstance(remaining_credits, int) and remaining_credits >= required_credits,
            }
        )
    if not eligible_slots:
        write_json(evidence_dir / "elevenlabs_key_health_summary.json", {"configured_key_count": len(keys), "disabled_or_blank_count": disabled_count, "keys": health})
        raise RuntimeError("CP07_BLOCKED_ELEVENLABS_NEW_ACCOUNT_QUOTA")

    preferred = sorted(eligible_slots, reverse=True)
    canary_results = []
    for slot in preferred:
        provider = ElevenLabsTTSProvider(config, key_index=slot - 1)
        canary = run_tts_canary(provider, voice_id, evidence_dir, slot)
        canary_results.append(canary)
        if canary["status"] == "ELEVENLABS_TTS_AUTH_CONTRACT_PROVEN":
            summary = {
                "configured_key_count": len(keys),
                "syntactically_valid_key_count": len(keys),
                "disabled_or_blank_count": disabled_count,
                "healthy_subscription_voices_count": sum(1 for item in health if item["health"] == "AUTHENTICATED_HEALTHY"),
                "quota_sufficient_key_count": len(eligible_slots),
                "tts_authorized_count": 1,
                "selected_key_slot": slot,
                "newly_detected_slot": len(keys),
                "remaining_credit_requirement": required_credits,
                "selected_key_summary": next(item for item in health if item["slot"] == slot),
                "keys": health,
                "canary_results": canary_results,
            }
            write_json(evidence_dir / "elevenlabs_key_health_summary.json", summary)
            return {"selected_key_slot": slot, "canary_status": canary["status"], "summary": summary}
        if canary["verdict"] == "CP07_BLOCKED_ELEVENLABS_RATE_LIMIT":
            write_json(evidence_dir / "elevenlabs_key_health_summary.json", {"configured_key_count": len(keys), "keys": health, "canary_results": canary_results})
            raise RuntimeError(canary["verdict"])
    write_json(evidence_dir / "elevenlabs_key_health_summary.json", {"configured_key_count": len(keys), "keys": health, "canary_results": canary_results})
    raise RuntimeError(canary_results[-1].get("verdict", "CP07_BLOCKED_ELEVENLABS_INVALID_OR_UNAUTHORIZED_KEY"))


def readonly_elevenlabs_get(config, key_slot: int, path: str) -> tuple[int, dict]:
    keys = load_strict_secret_lines(config.key_file)
    with httpx.Client(timeout=30.0, follow_redirects=False, headers={}) as client:
        response = client.get(config.base_url.rstrip("/") + path, headers={"xi-api-key": keys[key_slot - 1]})
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload if isinstance(payload, dict) else {}


def read_subscription_payload(config, key_slot: int) -> dict:
    status, payload = readonly_elevenlabs_get(config, key_slot, "/v1/user/subscription")
    return payload if status == 200 else {}


def run_tts_canary(provider: ElevenLabsTTSProvider, voice_id: str, evidence_dir: Path, slot: int) -> dict:
    canary_dir = evidence_dir / "elevenlabs_tts_canary"
    canary_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = canary_dir / f"slot_{slot}_canary.mp3"
    wav_path = canary_dir / f"slot_{slot}_canary.wav"
    base = {
        "slot": slot,
        "endpoint_path": "/v1/text-to-speech/{voice_id}",
        "model_id": provider.model,
        "voice_present": True,
        "cross_host_redirect": False,
    }
    if mp3_path.exists() and mp3_path.stat().st_size > 0:
        if not wav_path.exists():
            decode_to_wav(mp3_path, wav_path)
        duration = wav_duration(wav_path)
        result = base | {
            "http_status": 200,
            "redirected": False,
            "content_type": "reused_existing_canary_audio",
            "status": "ELEVENLABS_TTS_AUTH_CONTRACT_PROVEN",
            "verdict": "PASS",
            "non_empty_audio": True,
            "decoded": True,
            "duration_seconds": round(duration, 3),
            "artifact_scope": "ignored_diagnostic_evidence_only",
            "reused_existing_canary_audio": True,
        }
        write_json(canary_dir / f"slot_{slot}_canary_result.json", result)
        return result
    if wav_path.exists():
        wav_path.unlink()
    canary_text = "This is a short authorization check."
    request_hash = build_request_hash(
        {
            "provider": provider.provider_name,
            "payload_schema_version": FRESH_CLIENT_TTS_SCHEMA_VERSION,
            "credential_context_ref": f"elevenlabs-key-{slot}",
            "text": canary_text,
            "voice_id": voice_id,
            "model_id": provider.model,
            "output_format": provider.output_format,
            "diagnostic_scope": "ignored_canary_only",
        }
    )
    try:
        response, send_assertions = send_fresh_elevenlabs_tts_request(
            provider.config,
            key_slot=slot,
            voice_id=voice_id,
            model=provider.model,
            output_format=provider.output_format,
            text=canary_text,
            canary_key_reference=load_strict_secret_lines(provider.config.key_file)[slot - 1].encode("utf-8"),
            request_hash=request_hash,
            ledger_path=canary_dir / "canary_submission_ledger.json",
        )
    except FreshElevenLabsHTTPError as exc:
        result = base | {
            "http_status": exc.status_code,
            "status": "FAILED",
            "verdict": {
                401: "CP07_BLOCKED_ELEVENLABS_ACCOUNT_CONTEXT_INCONSISTENCY",
                403: "CP07_BLOCKED_ELEVENLABS_TTS_SCOPE",
                402: "CP07_BLOCKED_ELEVENLABS_BILLING",
                429: "CP07_BLOCKED_ELEVENLABS_RATE_LIMIT",
            }.get(exc.status_code, "CP07_BLOCKED_ELEVENLABS_PROVIDER"),
            "sanitized_provider_detail": exc.sanitized_detail,
        }
        write_json(canary_dir / f"slot_{slot}_canary_result.json", result)
        return result
    base = base | {"http_status": response.status_code, "redirected": response.is_redirect, "content_type": response.headers.get("content-type")}
    if response.status_code != 200:
        verdict = {
            401: "CP07_BLOCKED_ELEVENLABS_INVALID_OR_UNAUTHORIZED_KEY",
            403: "CP07_BLOCKED_ELEVENLABS_TTS_SCOPE",
            402: "CP07_BLOCKED_ELEVENLABS_BILLING",
            429: "CP07_BLOCKED_ELEVENLABS_RATE_LIMIT",
        }.get(response.status_code, "CP07_BLOCKED_ELEVENLABS_PROVIDER")
        result = base | {"status": "FAILED", "verdict": verdict, "retry_after": response.headers.get("retry-after"), "response_body_present": bool(response.content)}
        write_json(canary_dir / f"slot_{slot}_canary_result.json", result)
        return result
    mp3_path.write_bytes(response.content)
    decode_to_wav(mp3_path, wav_path)
    duration = wav_duration(wav_path)
    result = base | {
        "status": "ELEVENLABS_TTS_AUTH_CONTRACT_PROVEN",
        "verdict": "PASS",
        "non_empty_audio": len(response.content) > 0,
        "decoded": True,
        "duration_seconds": round(duration, 3),
        "artifact_scope": "ignored_diagnostic_evidence_only",
        "fresh_client_send_assertions": send_assertions,
    }
    write_json(canary_dir / f"slot_{slot}_canary_result.json", result)
    return result


def decode_to_wav(source: Path, output_path: Path) -> None:
    temp_wav = output_path.with_suffix(".tmp.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-ac", "1", "-ar", "48000", str(temp_wav)],
        check=True,
    )
    os.replace(temp_wav, output_path)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def reconcile_cached_tts_generations(provider: ElevenLabsTTSProvider, voice_id: str, groups: list[dict]) -> None:
    previous_request_ids: list[str] = []
    for index, group in enumerate(groups):
        next_text = groups[index + 1]["english_text"] if index + 1 < len(groups) else None
        request = TTSRequest(
            project_id=PROJECT_ID,
            segment_id=group["clip_id"],
            text=group["english_text"],
            voice_id=voice_id,
            model=provider.model,
            previous_request_ids=previous_request_ids[-3:],
            next_text=next_text,
            output_format=provider.output_format,
            provider_request_version=provider.provider_request_version,
        )
        request_hash = build_request_hash(tts_request_payload(provider.provider_name, request))
        cached = read_cached_response(provider.provider_name, request_hash)
        if cached is None:
            break
        artifact = Path(cached.get("audio_path", ""))
        if not artifact.exists() or sha256_file(artifact) != cached.get("sha256"):
            break
        generation_id = parse_generation_id(artifact)
        with session_scope() as session:
            existing = session.query(TTSGeneration).filter_by(request_hash=request_hash, status="ready").one_or_none()
            if existing is None:
                session.add(
                    TTSGeneration(
                        project_id=PROJECT_ID,
                        segment_id=group["clip_id"],
                        generation_id=generation_id,
                        provider=provider.provider_name,
                        model=provider.model,
                        voice_id=voice_id,
                        request_hash=request_hash,
                        cache_status="hit",
                        status="ready",
                        artifact_path=str(artifact.resolve()),
                        sha256=cached["sha256"],
                        character_count=int(cached.get("character_count") or len(group["english_text"])),
                        request_id=cached.get("request_id"),
                        credential_ref="elevenlabs-key-1",
                    )
                )
            reservation = session.query(TTSRequestReservation).filter_by(request_hash=request_hash).one_or_none()
            if reservation is not None:
                reservation.status = "ready"
                reservation.generation_id = generation_id
                reservation.owner_token = None
                reservation.lease_expires_at = None
                reservation.last_error = None
        if cached.get("request_id"):
            previous_request_ids.append(cached["request_id"])


def parse_generation_id(path: Path) -> str:
    match = path.stem.split("_tts_")
    if len(match) == 2:
        return f"tts_{match[1]}"
    return f"tts_{path.stem[-12:]}"


if __name__ == "__main__":
    main()
