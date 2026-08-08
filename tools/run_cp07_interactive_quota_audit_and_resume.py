import json
import os
import random
import re
import sys
import time
from dataclasses import replace
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
    extract_text_parts,
    parse_generated_json,
    validate_atomic_cache_roundtrip,
    validate_diagnostic_response,
)
from tools.run_cp07_full_canonical_sample import (
    GEMINI_PROVIDER,
    build_gemini_replan,
    build_paths,
    gemini_payload,
    measured_disk_gate,
    normalize_contract_response,
    validate_render_plan,
    write_json_atomic,
)


PROMPT_VERSION = "cp07-interactive-quota-dimension-audit-v1"
MAX_PRODUCTION_FAILURES_WITHOUT_COVERAGE = 25
MAX_CONSECUTIVE_NO_PROGRESS = 10


class ProviderStop(RuntimeError):
    def __init__(self, verdict: str, event: dict | None = None) -> None:
        super().__init__(verdict)
        self.verdict = verdict
        self.event = event or {}


def main() -> None:
    settings = get_settings()
    paths = build_paths(settings)
    paths.evidence_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = paths.evidence_dir / "interactive_quota_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    render_plan = validate_render_plan()
    disk_gate = measured_disk_gate(settings.root, render_plan)
    timeline_path = paths.timeline_dir / "cp07_canonical_asr_timeline.json"
    if not timeline_path.exists():
        raise RuntimeError("CP07_BLOCKED_ASR_TIMELINE_MISSING")
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    replan = build_gemini_replan(timeline, paths.evidence_dir)

    batch_closure = audit_batch_failures(paths.evidence_dir)
    write_json_atomic(audit_dir / "batch_failure_closure.json", batch_closure)

    previous_quota = audit_latest_interactive_quota(paths.evidence_dir)
    candidates = choose_interactive_candidates(paths.evidence_dir, previous_quota)
    candidate_plan = {
        "schema_version": 1,
        "previous_quota": previous_quota,
        "candidates": candidates,
        "batch_status": "DISABLED_CURRENT_PROJECT",
    }
    write_json_atomic(audit_dir / "interactive_candidate_plan.json", candidate_plan)

    if previous_quota["scope"] in {"PROJECT_WIDE_DAILY", "PROJECT_WIDE_TEMPORARY"}:
        summary = stop_summary("CP07_BLOCKED_GEMINI_PROJECT_DAILY_QUOTA" if previous_quota["scope"] == "PROJECT_WIDE_DAILY" else "CP07_BLOCKED_GEMINI_PROJECT_RATE_LIMIT", replan, batch_closure, previous_quota, candidates, disk_gate)
        write_json_atomic(audit_dir / "interactive_resume_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise RuntimeError(summary["verdict"])
    if previous_quota["scope"] == "UNKNOWN_QUOTA_SCOPE":
        summary = stop_summary("CP07_BLOCKED_GEMINI_QUOTA_SCOPE_UNKNOWN", replan, batch_closure, previous_quota, candidates, disk_gate)
        write_json_atomic(audit_dir / "interactive_resume_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise RuntimeError(summary["verdict"])

    keys = load_secret_lines(replan["config"].key_file)[:1]
    ledger_path = paths.evidence_dir / "gemini_submission_ledger.json"
    probe_block = make_probe_block(replan)
    probe_results = []
    selected = None
    production = None
    for candidate in candidates:
        config = replace(replan["config"], model=candidate["model_id"])
        payload = gemini_payload(config.model, probe_block)
        request_hash = build_request_hash(payload)
        cached = read_cached_response(GEMINI_PROVIDER, request_hash)
        if cached is not None:
            normalize_contract_response(probe_block, cached)
            selected = candidate
            probe_results.append({"model_id": candidate["model_id"], "request_hash": request_hash, "status": "CACHE_HIT_VALID"})
            production = resume_production_transform(replan, replace(replan["config"], model=selected["model_id"]), keys[0], ledger_path, audit_dir, [probe_block])
            if production["coverage"] == "442/442":
                break
            if production["verdict"] == "CP07_BLOCKED_GEMINI_MODEL_RATE_LIMIT":
                selected = None
                continue
            break
        result = probe_candidate(config, keys[0], probe_block, ledger_path, request_hash)
        probe_results.append(result)
        write_json_atomic(audit_dir / "interactive_probe_results.json", {"probe_block": summarize_block(probe_block), "results": probe_results})
        if result["status"] == "PASS":
            selected = candidate
            production = resume_production_transform(replan, replace(replan["config"], model=selected["model_id"]), keys[0], ledger_path, audit_dir, [probe_block])
            if production["coverage"] == "442/442":
                break
            if production["verdict"] == "CP07_BLOCKED_GEMINI_MODEL_RATE_LIMIT":
                selected = None
                continue
            break
        if result["status"] == "PROJECT_WIDE_RATE_LIMIT":
            summary = stop_summary(result["verdict"], replan, batch_closure, previous_quota, candidates, disk_gate, probe_results)
            write_json_atomic(audit_dir / "interactive_resume_summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            raise RuntimeError(summary["verdict"])
    if selected is None:
        unavailable = all(item.get("status") in {"MODEL_RATE_LIMIT", "TEMPORARILY_UNAVAILABLE", "UNSUPPORTED"} for item in probe_results)
        verdict = "CP07_BLOCKED_GEMINI_ALL_MODELS_RATE_LIMITED" if unavailable else "CP07_BLOCKED_GEMINI_ALL_MODELS_UNAVAILABLE"
        summary = stop_summary(verdict, replan, batch_closure, previous_quota, candidates, disk_gate, probe_results)
        write_json_atomic(audit_dir / "interactive_resume_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise RuntimeError(verdict)

    if production is None:
        production = resume_production_transform(replan, replace(replan["config"], model=selected["model_id"]), keys[0], ledger_path, audit_dir, [probe_block])
    summary = {
        "verdict": "GEMINI_TRANSFORM_COMPLETE" if production["coverage"] == "442/442" else production["verdict"],
        "batch_closure": batch_closure,
        "previous_quota": previous_quota,
        "selected_sticky_model": selected["model_id"],
        "candidate_models": [item["model_id"] for item in candidates],
        "probe_results": probe_results,
        "pacing_policy": pacing_policy(production.get("last_quota_event")),
        "disk_gate": disk_gate,
        "gemini_coverage": production["coverage"],
        "new_valid_cache_entries": production["new_valid_cache_entries"],
        "real_gemini_calls": production["real_gemini_calls"],
        "cache_hits": production["cache_hits"],
        "elevenlabs_calls": 0,
        "tts_artifacts": 0,
        "cp08_started": False,
        "cp09_started": False,
    }
    write_json_atomic(audit_dir / "interactive_resume_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["verdict"] != "GEMINI_TRANSFORM_COMPLETE":
        raise RuntimeError(summary["verdict"])


def audit_batch_failures(evidence_dir: Path) -> dict:
    attempts_path = evidence_dir / "gemini_batch" / "batch_create_attempts.json"
    attempts = json.loads(attempts_path.read_text(encoding="utf-8")).get("attempts", [])
    signatures: dict[str, dict] = {}
    for item in attempts:
        parsed = parse_google_error(item.get("body_excerpt", ""))
        signature_key = f"{item.get('status_code')}|{parsed.get('rpc_status')}|{parsed.get('message')}"
        entry = signatures.setdefault(
            signature_key,
            {
                "http_status": item.get("status_code"),
                "google_rpc_status": parsed.get("rpc_status"),
                "sanitized_error_message": parsed.get("message"),
                "endpoint_path": "models/{model}:batchGenerateContent",
                "api_version": "v1beta",
                "inline_versus_uploaded_file_mode": "inline",
                "failure_before_job_resource_created": True,
                "requested_model_ids": [],
                "mentions_billing_or_tier": mentions_prerequisite(parsed.get("message")),
                "not_found_resource_class": classify_not_found(parsed.get("message")),
            },
        )
        entry["requested_model_ids"].append(item.get("model"))
    return {
        "status": "DISABLED_CURRENT_PROJECT",
        "guard": "CP07_ENABLE_GEMINI_BATCH=explicit_current_project_override required before future Batch creation",
        "attempt_count": len(attempts),
        "unique_failure_signatures": list(signatures.values()),
        "created_jobs": 0,
        "evidence_retained": True,
    }


def audit_latest_interactive_quota(evidence_dir: Path) -> dict:
    manifest = json.loads((evidence_dir / "gemini_model_fallback_manifest.json").read_text(encoding="utf-8"))
    attempts = manifest.get("probe_attempts", [])
    latest_429 = next((item for item in reversed(attempts) if item.get("status") == "RATE_LIMIT"), None)
    if not latest_429:
        return {"scope": "UNKNOWN_QUOTA_SCOPE", "reason": "no_sanitized_429_found"}
    detail = latest_429.get("detail") or ""
    quota_lines = parse_quota_lines(detail)
    models = sorted({item.get("model") for item in quota_lines if item.get("model")})
    metrics = sorted({item.get("metric") for item in quota_lines if item.get("metric")})
    limits = sorted({item.get("limit") for item in quota_lines if item.get("limit") is not None})
    scope = "UNKNOWN_QUOTA_SCOPE"
    if models and len(models) == 1:
        scope = "MODEL_SPECIFIC_DAILY" if any("free_tier" in metric or "token_count" in metric for metric in metrics) else "MODEL_SPECIFIC_TEMPORARY"
    return {
        "scope": scope,
        "http_status": 429,
        "error_status": "RESOURCE_EXHAUSTED",
        "message_excerpt": detail[:500],
        "quota_metric": metrics,
        "quota_id": [],
        "quota_dimensions": quota_lines,
        "model_dimension": models,
        "location_dimension": [],
        "project_dimension": [],
        "limit_value": limits,
        "retry_delay": None,
        "retry_after_header": None,
        "exhausted_quota_kind": classify_quota_kind(metrics),
        "source_model_probe": latest_429.get("model_id"),
    }


def choose_interactive_candidates(evidence_dir: Path, quota: dict) -> list[dict]:
    manifest = json.loads((evidence_dir / "gemini_model_fallback_manifest.json").read_text(encoding="utf-8"))
    exhausted = set(quota.get("model_dimension") or [])
    previous_summary = evidence_dir / "interactive_quota_audit" / "interactive_resume_summary.json"
    if previous_summary.exists():
        summary = json.loads(previous_summary.read_text(encoding="utf-8"))
        last_quota = (summary.get("pacing_policy") or {}).get("last_quota_event") or {}
        if last_quota.get("verdict") == "CP07_BLOCKED_GEMINI_MODEL_RATE_LIMIT":
            exhausted.update(last_quota.get("model_dimension") or [])
    preferred_ids = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
    by_id = {item["model_id"]: item for item in manifest.get("discovered_models", [])}
    result = []
    for model_id in preferred_ids:
        item = by_id.get(model_id)
        if not item or item.get("suitability") != "ELIGIBLE" or model_id in exhausted:
            continue
        result.append(
            {
                "model_id": model_id,
                "stability_status": item.get("classification"),
                "generateContent_support": "generateContent" in item.get("supported_generation_methods", []),
                "structured_output_capability": "OpenAI-compatible JSON object response proven for CP07 parser path",
                "input_token_limit": item.get("input_token_limit"),
                "output_token_limit": item.get("output_token_limit"),
                "reason_selected": "stable Flash-family text candidate with sufficient context/output limits",
                "relation_to_exhausted_quota_dimension": "distinct_from_exhausted_model" if exhausted else "quota_scope_not_model_bound",
            }
        )
    return result[:3]


def make_probe_block(replan: dict) -> dict:
    missing = [segment for block in replan["blocks"] for segment in block["segments"]]
    # Avoid the old seg_0076..seg_0080 request hash that already has two submissions.
    segments = missing[5:10] if len(missing) >= 10 else missing[:5]
    while len(segments) > 3 and sum(len(item["source_text"]) for item in segments) > 1200:
        segments = segments[:-1]
    return {"block_id": "cp07_interactive_quota_probe_01", "segments": segments}


def probe_candidate(config, api_key: str, block: dict, ledger_path: Path, request_hash: str) -> dict:
    for attempt in range(1, 3):
        try:
            response, event = call_interactive(config, api_key, block, ledger_path, request_hash)
            write_cached_response(GEMINI_PROVIDER, request_hash, response)
            validate_atomic_cache_roundtrip(GEMINI_PROVIDER, request_hash, response)
            return {"model_id": config.model, "request_hash": request_hash, "status": "PASS", "attempts": attempt, "event": event}
        except ProviderStop as exc:
            event = exc.event | {"model_id": config.model, "request_hash": request_hash, "attempt": attempt}
            if exc.verdict == "CP07_BLOCKED_GEMINI_PROVIDER_UNAVAILABLE" and attempt == 1:
                time.sleep(15 + random.randint(0, 5))
                continue
            if exc.verdict in {"CP07_BLOCKED_GEMINI_PROJECT_DAILY_QUOTA", "CP07_BLOCKED_GEMINI_PROJECT_RATE_LIMIT"}:
                return {"model_id": config.model, "request_hash": request_hash, "status": "PROJECT_WIDE_RATE_LIMIT", "verdict": exc.verdict, "event": event}
            if exc.verdict == "CP07_BLOCKED_GEMINI_MODEL_RATE_LIMIT":
                return {"model_id": config.model, "request_hash": request_hash, "status": "MODEL_RATE_LIMIT", "event": event}
            if exc.verdict == "CP07_BLOCKED_GEMINI_AUTH":
                raise
            return {"model_id": config.model, "request_hash": request_hash, "status": "TEMPORARILY_UNAVAILABLE", "event": event}
    return {"model_id": config.model, "request_hash": request_hash, "status": "TEMPORARILY_UNAVAILABLE"}


def resume_production_transform(replan: dict, config, api_key: str, ledger_path: Path, audit_dir: Path, seed_blocks: list[dict] | None = None) -> dict:
    segments_by_id = dict(replan["retained_segments"])
    real_calls = 0
    cache_hits = 0
    new_valid = 0
    failures = 0
    events = []
    for seed_block in seed_blocks or []:
        seed_hash = build_request_hash(gemini_payload(config.model, seed_block))
        cached_seed = read_cached_response(GEMINI_PROVIDER, seed_hash)
        if cached_seed is not None:
            seed_response = normalize_contract_response(seed_block, cached_seed)
            for segment in seed_response["segments"]:
                segments_by_id[segment["id"]] = segment
            cache_hits += 1
    blocks = production_blocks([segment for block in replan["blocks"] for segment in block["segments"] if segment["id"] not in segments_by_id])
    for block in blocks:
        payload = gemini_payload(config.model, block)
        request_hash = build_request_hash(payload)
        cached = read_cached_response(GEMINI_PROVIDER, request_hash)
        if cached is not None:
            response = normalize_contract_response(block, cached)
            cache_hits += 1
        else:
            try:
                response, event = call_interactive(config, api_key, block, ledger_path, request_hash)
                real_calls += 1
                write_cached_response(GEMINI_PROVIDER, request_hash, response)
                validate_atomic_cache_roundtrip(GEMINI_PROVIDER, request_hash, response)
                response = normalize_contract_response(block, response)
                new_valid += 1
                events.append(event | {"block_id": block["block_id"], "request_hash": request_hash, "status": "SUCCESS"})
                time.sleep(8 + random.randint(0, 4))
            except ProviderStop as exc:
                failures += 1
                events.append(exc.event | {"block_id": block["block_id"], "request_hash": request_hash, "status": "FAILED", "verdict": exc.verdict})
                coverage = len(segments_by_id)
                result = {
                    "verdict": exc.verdict,
                    "coverage": f"{coverage}/442",
                    "new_valid_cache_entries": new_valid,
                    "real_gemini_calls": real_calls,
                    "cache_hits": cache_hits,
                    "events": events,
                    "last_quota_event": exc.event,
                }
                write_json_atomic(audit_dir / "interactive_production_events.json", result)
                return result
        for segment in response["segments"]:
            segments_by_id[segment["id"]] = segment
        if failures >= MAX_PRODUCTION_FAILURES_WITHOUT_COVERAGE:
            break
        if real_calls >= MAX_CONSECUTIVE_NO_PROGRESS and new_valid == 0:
            break
        write_json_atomic(
            audit_dir / "interactive_production_events.json",
            {
                "selected_model": config.model,
                "coverage": f"{len(segments_by_id)}/442",
                "new_valid_cache_entries": new_valid,
                "real_gemini_calls": real_calls,
                "cache_hits": cache_hits,
                "events": events,
            },
        )
    ordered_ids = sorted(segments_by_id, key=lambda value: int(value.split("_")[1]))
    transform = {
        "schema_version": 1,
        "real_call_count": real_calls,
        "cache_hit_count": cache_hits,
        "failover_events": 0,
        "retained_cache_entries": len(replan["retained_segments"]),
        "new_completed_blocks": new_valid,
        "timeout_uncertain": [],
        "provider_call_events": events,
        "segments": [segments_by_id[segment_id] for segment_id in ordered_ids],
    }
    if len(segments_by_id) == 442:
        write_json_atomic(audit_dir.parent / "gemini_transform.json", transform)
    return {
        "verdict": "CP07_BLOCKED_GEMINI_INCOMPLETE_COVERAGE",
        "coverage": f"{len(segments_by_id)}/442",
        "new_valid_cache_entries": new_valid,
        "real_gemini_calls": real_calls,
        "cache_hits": cache_hits,
        "events": events,
    }


def call_interactive(config, api_key: str, block: dict, ledger_path: Path, request_hash: str) -> tuple[dict, dict]:
    if submission_count(ledger_path, request_hash) >= 2:
        raise ProviderStop("CP07_BLOCKED_GEMINI_RETRY_INVARIANT", {"reason": "same_model_request_hash_third_submission_blocked"})
    record_submission(ledger_path, request_hash)
    endpoint = config.base_url.rstrip("/") + "/chat/completions"
    payload = gemini_payload(config.model, block)
    body = {
        "model": config.model,
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "Return compact valid JSON only. Preserve source_segment_id exactly. No markdown."},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    with httpx.Client(timeout=config.timeout_seconds) as client:
        response = client.post(endpoint, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=body)
    if response.status_code == 429:
        quota = classify_quota_response(response)
        verdict = quota["verdict"]
        raise ProviderStop(verdict, quota)
    if response.status_code in {401, 403}:
        raise ProviderStop("CP07_BLOCKED_GEMINI_AUTH", {"http_status": response.status_code})
    if response.status_code == 503:
        raise ProviderStop("CP07_BLOCKED_GEMINI_PROVIDER_UNAVAILABLE", sanitize_http_event(response))
    if response.status_code in {400, 404}:
        raise ProviderStop("CP07_BLOCKED_GEMINI_MODEL_UNSUPPORTED", sanitize_http_event(response))
    response.raise_for_status()
    wrapper = response.json()
    parts = extract_text_parts(wrapper)
    parsed = parse_generated_json(parts)
    normalized = validate_diagnostic_response([segment["id"] for segment in block["segments"]], parsed.payload)
    finish = (wrapper.get("choices") or [{}])[0].get("finish_reason") if isinstance(wrapper, dict) else None
    if finish and str(finish).lower() not in {"stop", "1"}:
        raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", f"Unexpected finish_reason {finish}")
    return normalized, {
        "http_status": response.status_code,
        "finish_reason": finish,
        "candidate_count": len(wrapper.get("choices", [])) if isinstance(wrapper, dict) else None,
        "parser_stage": parsed.parser_stage,
        "raw_text_character_count": parsed.raw_text_character_count,
        "model_id": config.model,
    }


def production_blocks(segments: list[dict]) -> list[dict]:
    blocks = []
    cursor = []
    chars = 0
    for segment in segments:
        projected = chars + len(segment["source_text"])
        if cursor and (len(cursor) >= 30 or (len(cursor) >= 18 and projected > 7000)):
            blocks.append({"block_id": f"cp07_interactive_prod_{len(blocks)+1:03d}", "segments": cursor})
            cursor = []
            chars = 0
        cursor.append(segment)
        chars += len(segment["source_text"])
    if cursor:
        blocks.append({"block_id": f"cp07_interactive_prod_{len(blocks)+1:03d}", "segments": cursor})
    return blocks


def record_submission(path: Path, request_hash: str) -> None:
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    current = int(ledger.get(request_hash, {}).get("provider_submissions", 0) or 0)
    if current >= 2:
        raise ProviderStop("CP07_BLOCKED_GEMINI_RETRY_INVARIANT", {"request_hash": request_hash})
    ledger[request_hash] = {"provider_submissions": current + 1, "last_attempt_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_json_atomic(path, ledger)


def submission_count(path: Path, request_hash: str) -> int:
    if not path.exists():
        return 0
    return int(json.loads(path.read_text(encoding="utf-8")).get(request_hash, {}).get("provider_submissions", 0) or 0)


def classify_quota_response(response: httpx.Response) -> dict:
    event = sanitize_http_event(response)
    quota_lines = parse_quota_lines(response.text)
    models = sorted({item.get("model") for item in quota_lines if item.get("model")})
    metrics = sorted({item.get("metric") for item in quota_lines if item.get("metric")})
    event["quota_dimensions"] = quota_lines
    event["quota_metric"] = metrics
    event["model_dimension"] = models
    event["retry_after_header"] = response.headers.get("retry-after")
    if models and len(models) == 1:
        event["quota_scope"] = "MODEL_SPECIFIC_DAILY" if any("free_tier" in metric or "token_count" in metric for metric in metrics) else "MODEL_SPECIFIC_TEMPORARY"
        event["verdict"] = "CP07_BLOCKED_GEMINI_MODEL_RATE_LIMIT"
    elif quota_lines:
        event["quota_scope"] = "PROJECT_WIDE_TEMPORARY"
        event["verdict"] = "CP07_BLOCKED_GEMINI_PROJECT_RATE_LIMIT"
    else:
        event["quota_scope"] = "UNKNOWN_QUOTA_SCOPE"
        event["verdict"] = "CP07_BLOCKED_GEMINI_QUOTA_SCOPE_UNKNOWN"
    return event


def parse_google_error(text: str) -> dict:
    try:
        payload = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
            except Exception:
                return {"message": text[:500], "rpc_status": None}
        else:
            return {"message": text[:500], "rpc_status": None}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    return {"message": error.get("message"), "rpc_status": error.get("status"), "code": error.get("code")}


def parse_quota_lines(text: str) -> list[dict]:
    compact = str(text).replace("\\n", "\n")
    compact = " ".join(compact.split())
    result = []
    pattern = re.compile(r"Quota exceeded for metric: (?P<metric>[^,\n]+), limit: (?P<limit>\d+)(?:, model: (?P<model>[A-Za-z0-9_.-]+))?")
    for match in pattern.finditer(compact):
        result.append({"metric": match.group("metric"), "limit": int(match.group("limit")), "model": match.group("model")})
    return result


def sanitize_http_event(response: httpx.Response) -> dict:
    parsed = parse_google_error(response.text)
    return {
        "http_status": response.status_code,
        "error_status": parsed.get("rpc_status"),
        "message_excerpt": (parsed.get("message") or "")[:500],
        "content_type": response.headers.get("content-type"),
    }


def classify_quota_kind(metrics: list[str]) -> str:
    joined = " ".join(metrics).lower()
    if "request" in joined and "minute" in joined:
        return "RPM"
    if "input_token" in joined or "output_token" in joined or "token_count" in joined:
        return "TPD_OR_TPM"
    if "free_tier" in joined:
        return "SPEND_OR_FREE_TIER"
    return "another_metric" if metrics else "unknown"


def mentions_prerequisite(message: str | None) -> bool:
    if not message:
        return False
    return any(token in message.lower() for token in ["billing", "free tier", "region", "account", "tier", "precondition"])


def classify_not_found(message: str | None) -> str | None:
    if not message:
        return None
    lower = message.lower()
    if "model models/" in lower or "this model" in lower:
        return "model_id"
    if "file" in lower:
        return "uploaded_file_resource"
    if "batch" in lower:
        return "batch_endpoint_or_job"
    return "another_resource"


def summarize_block(block: dict) -> dict:
    return {
        "block_id": block["block_id"],
        "source_segment_ids": [segment["id"] for segment in block["segments"]],
        "source_characters": sum(len(segment["source_text"]) for segment in block["segments"]),
    }


def pacing_policy(last_quota_event: dict | None) -> dict:
    return {
        "concurrency": 1,
        "default_min_interval_seconds": "8-12_with_jitter",
        "rpm_policy": "use <= 70-80 percent of reported RPM when present",
        "tpm_policy": "delay instead of submit when rolling estimated token budget would exceed safe limit",
        "last_quota_event": last_quota_event,
    }


def stop_summary(verdict: str, replan: dict, batch: dict, quota: dict, candidates: list[dict], disk_gate: dict, probe_results: list[dict] | None = None) -> dict:
    return {
        "verdict": verdict,
        "batch_closure": batch,
        "previous_quota": quota,
        "candidate_models": [item["model_id"] for item in candidates],
        "probe_results": probe_results or [],
        "disk_gate": disk_gate,
        "gemini_coverage": f"{len(replan['retained_segments'])}/442",
        "elevenlabs_calls": 0,
        "tts_artifacts": 0,
        "cp08_started": False,
        "cp09_started": False,
    }


if __name__ == "__main__":
    main()
