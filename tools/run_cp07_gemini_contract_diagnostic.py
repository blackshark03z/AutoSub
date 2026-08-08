import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.provider_cache import build_request_hash, read_cached_response, write_cached_response
from app.core.secret_files import load_secret_lines
from app.providers.translation.gemini import load_gemini_translation_config
from app.providers.translation.gemini_contract import (
    GeminiContractError,
    enforce_same_hash_attempt_guard,
    extract_text_parts,
    parse_generated_json,
    validate_atomic_cache_roundtrip,
    validate_diagnostic_response,
)
from tools.run_cp07_full_canonical_sample import (
    GEMINI_PROVIDER,
    MEASURED_GATE_GIB,
    PROJECT_ID,
    build_paths,
    measured_disk_gate,
    validate_render_plan,
)


DIAGNOSTIC_PROMPT_VERSION = "cp07-gemini-contract-diagnostic-v1"
MAX_DIAGNOSTIC_CALLS = 3


def main() -> None:
    settings = get_settings()
    paths = build_paths(settings)
    evidence_dir = settings.root / "evidence" / "CP07" / "gemini_contract_diagnostic"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    render_plan = validate_render_plan()
    disk_gate = measured_disk_gate(settings.root, render_plan)
    timeline = json.loads((paths.timeline_dir / "cp07_canonical_asr_timeline.json").read_text(encoding="utf-8"))
    previous_audit = audit_previous_failure(settings.root / "evidence" / "CP07" / "gemini_replan_manifest.json")
    block = select_diagnostic_block(timeline, settings.root / "evidence" / "CP07" / "gemini_replan_manifest.json")
    config = load_gemini_translation_config()
    payload = diagnostic_payload(config.model, block)
    request_hash = build_request_hash(payload)
    manifest = {
        "schema_version": 1,
        "disk_gate": disk_gate,
        "render_path": render_plan,
        "elevenlabs_call_path_available": False,
        "full_video_processing_available": False,
        "previous_failure_audit": previous_audit,
        "diagnostic_block": summarize_block(block, request_hash),
        "prompt_version": DIAGNOSTIC_PROMPT_VERSION,
        "provider": GEMINI_PROVIDER,
        "model": config.model,
        "call_budget": MAX_DIAGNOSTIC_CALLS,
    }
    write_json_atomic(evidence_dir / "diagnostic_block_manifest.json", manifest)

    cached = read_cached_response(GEMINI_PROVIDER, request_hash)
    if cached is not None:
        validate_diagnostic_response([segment["id"] for segment in block["segments"]], cached)
        verdict = "CP07_GEMINI_RESPONSE_CONTRACT_PROVEN"
        real_calls = 0
        cache_status = "hit"
    else:
        verdict, real_calls, cache_status = execute_probe(config, payload, request_hash, block, evidence_dir)

    summary = {
        "verdict": verdict,
        "real_gemini_calls": real_calls,
        "cache_status": cache_status,
        "request_hash": request_hash,
        "source_segment_ids": [segment["id"] for segment in block["segments"]],
        "elevenlabs_calls": 0,
        "cp07_full_resume_started": False,
        "cp08_started": False,
        "cp09_started": False,
        "free_disk_after_gib": round(shutil.disk_usage(settings.root).free / (1024**3), 6),
    }
    if verdict == "CP07_GEMINI_RESPONSE_CONTRACT_PROVEN":
        summary["proposed_production_resume_plan"] = {
            "simplified_output_schema": ["source_segment_id", "english_text", "spoken_status"],
            "proposed_uncovered_block_size": "18-30 source segments, capped by 1200-2000 source characters after diagnostic proof",
            "estimated_production_calls": "16-24",
            "timeout_split_reserve": "8-12",
            "recommended_hard_call_cap": 36,
        }
    write_json_atomic(evidence_dir / "diagnostic_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if verdict != "CP07_GEMINI_RESPONSE_CONTRACT_PROVEN":
        raise RuntimeError(verdict)


def audit_previous_failure(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {"manifest_present": False}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    events = manifest.get("provider_call_events", [])
    first_hash = "f6b57a9a0633aa0627030cc1fac7c9d7d37545fb2e17372da690374aa0feced1"
    first_hash_events = [event for event in events if event.get("request_hash") == first_hash]
    return {
        "manifest_present": True,
        "available_event_count": len(events),
        "known_provider_attempts_from_persisted_events": sum(int(event.get("attempts", 0)) for event in events),
        "first_failure_hash": first_hash,
        "first_failure_events": first_hash_events,
        "exact_http_status_classes": "not_available_in_prior_manifest",
        "timeout_count": sum(1 for event in events if event.get("status") == "TIMEOUT_UNCERTAIN"),
        "response_present_count": "not_available_in_prior_manifest",
        "response_empty_count": "not_available_in_prior_manifest",
        "safety_block_count": "not_available_in_prior_manifest",
        "rate_limit_count": "not_available_in_prior_manifest",
        "authentication_failure_count": "not_available_in_prior_manifest",
        "malformed_json_count": sum(1 for event in events if event.get("reason") == "CP07_BLOCKED_GEMINI_PROVIDER_RESPONSE"),
        "schema_validation_failure_count": "not_available_in_prior_manifest",
        "truncated_response_count": "not_available_in_prior_manifest",
        "parser_exception_count": sum(1 for event in events if event.get("reason") == "CP07_BLOCKED_GEMINI_PROVIDER_RESPONSE"),
        "finish_reasons": "not_available_in_prior_manifest",
        "candidate_count": "not_available_in_prior_manifest",
        "text_part_count": "not_available_in_prior_manifest",
        "markdown_fences_present": "not_available_in_prior_manifest",
        "json_inside_surrounding_prose": "not_available_in_prior_manifest",
        "same_hash_retry_audit": {
            "classification": "multiple_provider_key_failover_attempts_aggregated_by_previous_helper",
            "recorded_attempts": sum(int(event.get("attempts", 0)) for event in first_hash_events),
            "invariant_status": "fixed_before_new_call",
        },
    }


def select_diagnostic_block(timeline: dict, replan_manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(replan_manifest_path.read_text(encoding="utf-8"))
    covered = set(manifest.get("covered_source_ids", []))
    missing = [segment for segment in timeline["segments"] if segment["id"] not in covered and segment.get("enabled", True)]
    selected = missing[:5]
    while len(selected) > 3 and sum(len(segment["source_text"]) for segment in selected) > 1200:
        selected.pop()
    if len(selected) < 3:
        raise RuntimeError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA")
    return {
        "block_id": "cp07_contract_diagnostic_block_01",
        "segments": [
            {
                "id": segment["id"],
                "ordinal": segment["ordinal"],
                "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"],
                "source_text": segment["source_text"],
                "duration_budget_ms": segment["end_ms"] - segment["start_ms"],
            }
            for segment in selected
        ],
    }


def diagnostic_payload(model: str, block: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": GEMINI_PROVIDER,
        "model": model,
        "prompt_version": DIAGNOSTIC_PROMPT_VERSION,
        "project_id": PROJECT_ID,
        "task": "Transform Chinese game narration/dialogue into concise natural American English.",
        "target_locale": "en-US",
        "requirements": [
            "Return JSON only.",
            "Preserve source_segment_id exactly and in order.",
            "Return one item for every input segment.",
            "Do not add unrelated narration.",
        ],
        "schema": {"segments": [{"source_segment_id": "seg_0001", "english_text": "natural en-US line", "spoken_status": "spoken"}]},
        "block": block,
    }


def execute_probe(config, payload: dict[str, Any], request_hash: str, block: dict[str, Any], evidence_dir: Path) -> tuple[str, int, str]:
    keys = load_secret_lines(config.key_file)
    if not keys:
        return "CP07_BLOCKED_GEMINI_AUTH", 0, "miss"
    endpoint = config.base_url.rstrip("/") + "/chat/completions"
    ledger_path = evidence_dir / "retry_ledger.json"
    expected_ids = [segment["id"] for segment in block["segments"]]
    real_calls = 0
    last_classification = "CP07_BLOCKED_GEMINI_EMPTY_RESPONSE"
    for call_index in range(1, MAX_DIAGNOSTIC_CALLS + 1):
        if call_index > 2:
            break
        try:
            enforce_same_hash_attempt_guard(ledger_path, request_hash, max_attempts=2)
        except GeminiContractError as exc:
            write_envelope(evidence_dir, call_index, request_hash, {"failure_classification": exc.classification, "parser_stage": "retry_guard"})
            return exc.classification, real_calls, "miss"
        started = time.perf_counter()
        envelope: dict[str, Any] = {
            "call_index": call_index,
            "request_hash": request_hash,
            "http_status": None,
            "elapsed_ms": None,
            "response_content_type": None,
            "provider_finish_reason": None,
            "candidate_count": None,
            "part_count": None,
            "raw_text_character_count": 0,
            "text_empty": True,
            "markdown_fences_detected": False,
            "json_substring_detected": False,
            "parser_stage": "not_started",
            "schema_validation_result": "not_started",
            "failure_classification": None,
        }
        try:
            real_calls += 1
            with httpx.Client(timeout=config.timeout_seconds) as client:
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
                    json={
                        "model": config.model,
                        "response_format": {"type": "json_object"},
                        "temperature": 0,
                        "messages": [
                            {
                                "role": "system",
                                "content": "Return compact valid JSON only. No markdown. Preserve IDs exactly.",
                            },
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                    },
                )
            envelope["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            envelope["http_status"] = response.status_code
            envelope["response_content_type"] = response.headers.get("content-type", "")
            if response.status_code == 401:
                envelope["failure_classification"] = "CP07_BLOCKED_GEMINI_AUTH"
                write_envelope(evidence_dir, call_index, request_hash, envelope)
                return envelope["failure_classification"], real_calls, "miss"
            if response.status_code == 429:
                envelope["failure_classification"] = "CP07_BLOCKED_GEMINI_RATE_LIMIT"
                write_envelope(evidence_dir, call_index, request_hash, envelope)
                return envelope["failure_classification"], real_calls, "miss"
            response.raise_for_status()
            body = response.json()
            choices = body.get("choices", []) if isinstance(body, dict) else []
            envelope["candidate_count"] = len(choices)
            if choices:
                envelope["provider_finish_reason"] = choices[0].get("finish_reason")
            parts = extract_text_parts(body)
            envelope["part_count"] = len(parts)
            joined = "".join(parts)
            envelope["raw_text_character_count"] = len(joined)
            envelope["text_empty"] = not bool(joined.strip())
            parsed = parse_generated_json(parts)
            envelope["markdown_fences_detected"] = parsed.markdown_fences_detected
            envelope["json_substring_detected"] = parsed.json_substring_detected
            envelope["parser_stage"] = parsed.parser_stage
            normalized = validate_diagnostic_response(expected_ids, parsed.payload)
            envelope["schema_validation_result"] = "PASS"
            write_cached_response(GEMINI_PROVIDER, request_hash, normalized)
            validate_atomic_cache_roundtrip(GEMINI_PROVIDER, request_hash, normalized)
            write_envelope(evidence_dir, call_index, request_hash, envelope)
            write_json_atomic(evidence_dir / "cache_validation.json", {"request_hash": request_hash, "status": "PASS"})
            return "CP07_GEMINI_RESPONSE_CONTRACT_PROVEN", real_calls, "miss"
        except httpx.TimeoutException:
            last_classification = "CP07_BLOCKED_GEMINI_TIMEOUT"
            envelope["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            envelope["failure_classification"] = last_classification
            write_envelope(evidence_dir, call_index, request_hash, envelope)
            if read_cached_response(GEMINI_PROVIDER, request_hash) is not None:
                return "CP07_GEMINI_RESPONSE_CONTRACT_PROVEN", real_calls, "hit_late"
            return last_classification, real_calls, "miss"
        except GeminiContractError as exc:
            last_classification = exc.classification
            envelope["failure_classification"] = exc.classification
            envelope["schema_validation_result"] = "FAIL" if "SCHEMA" in exc.classification else envelope["schema_validation_result"]
            write_envelope(evidence_dir, call_index, request_hash, envelope)
            if exc.classification in {"CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "CP07_BLOCKED_GEMINI_PARSER"} and call_index == 1:
                continue
            return exc.classification, real_calls, "miss"
        except (httpx.HTTPStatusError, httpx.TransportError, KeyError, json.JSONDecodeError, ValueError):
            last_classification = "CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA"
            envelope["failure_classification"] = last_classification
            write_envelope(evidence_dir, call_index, request_hash, envelope)
            return last_classification, real_calls, "miss"
    return last_classification, real_calls, "miss"


def summarize_block(block: dict[str, Any], request_hash: str) -> dict[str, Any]:
    segments = block["segments"]
    return {
        "block_id": block["block_id"],
        "source_segment_ids": [segment["id"] for segment in segments],
        "source_time_range_ms": [segments[0]["start_ms"], segments[-1]["end_ms"]],
        "source_character_count": sum(len(segment["source_text"]) for segment in segments),
        "request_hash": request_hash,
    }


def write_envelope(evidence_dir: Path, call_index: int, request_hash: str, envelope: dict[str, Any]) -> None:
    safe = dict(envelope)
    safe["request_hash"] = request_hash
    write_json_atomic(evidence_dir / f"call_{call_index:02d}_envelope.json", safe)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


if __name__ == "__main__":
    main()
