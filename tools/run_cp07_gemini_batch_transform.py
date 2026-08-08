import json
import os
import random
import re
import sys
import time
from pathlib import Path
from statistics import median

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.canonical import canonical_hash, canonical_json
from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.provider_cache import build_request_hash, read_cached_response, write_cached_response
from app.core.secret_files import load_secret_lines
from app.providers.translation.gemini import load_gemini_translation_config
from app.providers.translation.gemini_contract import (
    extract_text_parts,
    parse_generated_json,
    validate_atomic_cache_roundtrip,
    validate_diagnostic_response,
)
from tools.run_cp07_full_canonical_sample import (
    GEMINI_PROVIDER,
    MEASURED_GATE_GIB,
    build_gemini_replan,
    build_paths,
    measured_disk_gate,
    normalize_contract_response,
    validate_render_plan,
)


BATCH_PROMPT_VERSION = "cp07-full-canonical-batch-simplified-v1"
OBSERVATION_SECONDS = 180


class BatchCreateError(RuntimeError):
    def __init__(self, verdict: str, payload: dict) -> None:
        super().__init__(verdict)
        self.verdict = verdict
        self.payload = payload


def main() -> None:
    if os.environ.get("CP07_ENABLE_GEMINI_BATCH") != "explicit_current_project_override":
        raise RuntimeError("CP07_BLOCKED_GEMINI_BATCH_DISABLED_CURRENT_PROJECT")
    settings = get_settings()
    paths = build_paths(settings)
    evidence_dir = settings.root / "evidence" / "CP07" / "gemini_batch"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    render_plan = validate_render_plan()
    disk_gate = measured_disk_gate(settings.root, render_plan)
    timeline = json.loads((paths.timeline_dir / "cp07_canonical_asr_timeline.json").read_text(encoding="utf-8"))
    replan = build_gemini_replan(timeline, paths.evidence_dir)
    model_candidates = select_batch_models(paths.evidence_dir)
    batch_blocks = build_batch_blocks(replan["blocks"])
    job_state_path = evidence_dir / "batch_job_state.json"
    existing_state = load_json(job_state_path)
    if existing_state.get("batch_name") and existing_state.get("selected_model"):
        model_candidates = [existing_state["model_summary"]]

    attempt_errors = []
    for model_summary in model_candidates:
        selected_model = model_summary["model_id"]
        manifest = build_batch_manifest(selected_model, batch_blocks, disk_gate, render_plan, model_summary)
        validate_batch_manifest(manifest, replan)
        manifest_hash = canonical_hash(manifest)
        input_body = build_batch_body(selected_model, manifest)
        if secret_like(json.dumps(input_body, ensure_ascii=False)):
            raise RuntimeError("CP07_BLOCKED_GEMINI_BATCH_INPUT")

        job_state = load_json(job_state_path)
        if job_state.get("manifest_hash") == manifest_hash and job_state.get("batch_name"):
            batch_name = job_state["batch_name"]
            input_sha = job_state["input_sha256"]
            created = False
            break
        try:
            batch_name = create_batch_job(selected_model, input_body)
            created = True
            manifest_path = evidence_dir / "batch_input_manifest.json"
            input_path = evidence_dir / "batch_input_requests.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            input_path.write_text(json.dumps(input_body, ensure_ascii=False, indent=2), encoding="utf-8")
            input_sha = sha256_file(input_path)
            job_state = {
                "schema_version": 1,
                "manifest_hash": manifest_hash,
                "batch_name": batch_name,
                "selected_model": selected_model,
                "model_summary": model_summary,
                "input_sha256": input_sha,
                "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "submission_status": "CREATED",
                "state_history": [],
            }
            write_json(job_state_path, job_state)
            break
        except BatchCreateError as exc:
            attempt_errors.append(exc.payload)
            write_json(evidence_dir / "batch_create_attempts.json", {"attempts": attempt_errors})
            if exc.verdict == "CP07_BLOCKED_GEMINI_BATCH_RATE_LIMIT":
                raise RuntimeError(exc.verdict) from exc
    else:
        summary = {
            "verdict": "CP07_BLOCKED_GEMINI_BATCH_UNSUPPORTED",
            "attempted_model_count": len(attempt_errors),
            "coverage": "75/442",
            "elevenlabs_calls": 0,
            "cp08_started": False,
            "cp09_started": False,
        }
        write_json(evidence_dir / "batch_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise RuntimeError(summary["verdict"])

    manifest_path = evidence_dir / "batch_input_manifest.json"
    input_path = evidence_dir / "batch_input_requests.json"
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if not input_path.exists():
        input_path.write_text(json.dumps(input_body, ensure_ascii=False, indent=2), encoding="utf-8")

    result = poll_batch_job(batch_name, job_state_path)
    if result["state"] in {"BATCH_STATE_PENDING", "BATCH_STATE_RUNNING", "BATCH_STATE_UNSPECIFIED"}:
        summary = {
            "verdict": "CP07_GEMINI_BATCH_PENDING",
            "batch_name": batch_name,
            "selected_model": selected_model,
            "manifest_hash": manifest_hash,
            "input_sha256": input_sha,
            "created_this_run": created,
            "latest_state": result["state"],
            "poll_count": result["poll_count"],
            "resume_command": "python tools\\run_cp07_gemini_batch_transform.py",
            "gemini_coverage": "75/442",
            "elevenlabs_calls": 0,
            "cp08_started": False,
            "cp09_started": False,
        }
        write_json(evidence_dir / "batch_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise RuntimeError("CP07_GEMINI_BATCH_PENDING")
    if result["state"] != "BATCH_STATE_SUCCEEDED":
        summary = {"verdict": "CP07_BLOCKED_GEMINI_BATCH_PROVIDER", "batch_name": batch_name, "latest_state": result["state"]}
        write_json(evidence_dir / "batch_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise RuntimeError(summary["verdict"])

    imported = import_batch_results(result["batch"], manifest)
    write_json(evidence_dir / "batch_import_summary.json", imported)
    print(json.dumps(imported, ensure_ascii=False, indent=2))


def select_batch_models(evidence_root: Path) -> list[dict]:
    fallback = load_json(evidence_root / "gemini_model_fallback_manifest.json")
    models = fallback.get("discovered_models", [])
    eligible = [
        model
        for model in models
        if model.get("suitability") == "ELIGIBLE" and "batchGenerateContent" in model.get("supported_generation_methods", [])
    ]
    def score(model: dict) -> tuple[int, str]:
        model_id = model["model_id"]
        lowered = model_id.lower()
        if "2.0" in lowered:
            version_rank = 5
        elif "2.5" in lowered:
            version_rank = 1
        elif "3.5" in lowered or "3.1" in lowered:
            version_rank = 0
        elif "latest" in lowered:
            version_rank = 2
        else:
            version_rank = 3
        if "flash-lite" in lowered:
            rank = 0
        elif "flash" in lowered and "image" not in lowered and "tts" not in lowered:
            rank = 1
        elif "pro" in lowered and "preview" not in lowered:
            rank = 2
        else:
            rank = 3
        return (rank, version_rank, model_id)
    if not eligible:
        raise RuntimeError("CP07_BLOCKED_GEMINI_BATCH_UNSUPPORTED")
    return sorted(eligible, key=score)


def build_batch_blocks(replan_blocks: list[dict]) -> list[dict]:
    logical = []
    cursor = []
    chars = 0
    for block in replan_blocks:
        for segment in block["segments"]:
            if cursor and (len(cursor) >= 24 or (len(cursor) >= 18 and chars + len(segment["source_text"]) > 7000)):
                logical.append(make_batch_block(len(logical) + 1, cursor))
                cursor = []
                chars = 0
            cursor.append(segment)
            chars += len(segment["source_text"])
    if cursor:
        logical.append(make_batch_block(len(logical) + 1, cursor))
    return logical


def make_batch_block(index: int, segments: list[dict]) -> dict:
    return {"request_key": f"cp07_batch_req_{index:03d}", "segments": segments}


def build_batch_manifest(model: str, blocks: list[dict], disk_gate: dict, render_plan: dict, model_summary: dict) -> dict:
    requests = []
    for block in blocks:
        payload = gemini_payload(model, block)
        request_hash = build_request_hash(payload)
        segments = block["segments"]
        requests.append(
            {
                "request_key": block["request_key"],
                "source_segment_ids": [segment["id"] for segment in segments],
                "first_source_id": segments[0]["id"],
                "last_source_id": segments[-1]["id"],
                "source_time_range_ms": [segments[0]["start_ms"], segments[-1]["end_ms"]],
                "source_character_count": sum(len(segment["source_text"]) for segment in segments),
                "model_id": model,
                "request_hash": request_hash,
                "prompt_schema_version": BATCH_PROMPT_VERSION,
            }
        )
    sizes = [len(item["source_segment_ids"]) for item in requests]
    chars = [item["source_character_count"] for item in requests]
    return {
        "schema_version": 1,
        "selected_model": model,
        "model_summary": model_summary,
        "disk_gate": disk_gate,
        "render_plan": render_plan,
        "prompt_schema_version": BATCH_PROMPT_VERSION,
        "target_locale": "en-US",
        "transformation_policy": "concise_natural_american_english_dubbing",
        "logical_request_count": len(requests),
        "block_stats": {
            "min_segments": min(sizes) if sizes else 0,
            "median_segments": median(sizes) if sizes else 0,
            "max_segments": max(sizes) if sizes else 0,
            "min_source_characters": min(chars) if chars else 0,
            "median_source_characters": median(chars) if chars else 0,
            "max_source_characters": max(chars) if chars else 0,
        },
        "requests": requests,
    }


def validate_batch_manifest(manifest: dict, replan: dict) -> None:
    assigned = [sid for item in manifest["requests"] for sid in item["source_segment_ids"]]
    covered = set(replan["retained_segments"].keys())
    if len(assigned) != 367 or len(assigned) != len(set(assigned)) or set(assigned) & covered:
        raise RuntimeError("CP07_BLOCKED_GEMINI_BATCH_INPUT")


def build_batch_body(model: str, manifest: dict) -> dict:
    requests = []
    for item in manifest["requests"]:
        block = {"request_key": item["request_key"], "segments": []}
        # The model prompt stores canonical text in the user payload; metadata maps output back.
        block["segments"] = source_segments_for_ids(item["source_segment_ids"])
        prompt = json.dumps(gemini_payload(model, block), ensure_ascii=False)
        requests.append(
            {
                "metadata": {"key": item["request_key"], "request_hash": item["request_hash"]},
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "system_instruction": {
                        "parts": [{"text": "Return compact valid JSON only. Preserve source_segment_id exactly. No markdown."}]
                    },
                    "generation_config": {"temperature": 0, "response_mime_type": "application/json"},
                },
            }
        )
    return {"batch": {"display_name": "cp07-full-canonical-transform", "input_config": {"requests": {"requests": requests}}}}


def source_segments_for_ids(ids: list[str]) -> list[dict]:
    settings = get_settings()
    timeline = json.loads((settings.data_dir / "projects" / "vertical_slice_cp07" / "timeline" / "cp07_canonical_asr_timeline.json").read_text(encoding="utf-8"))
    by_id = {segment["id"]: segment for segment in timeline["segments"]}
    return [
        {
            "id": sid,
            "ordinal": by_id[sid]["ordinal"],
            "start_ms": by_id[sid]["start_ms"],
            "end_ms": by_id[sid]["end_ms"],
            "source_text": by_id[sid]["source_text"],
            "duration_budget_ms": by_id[sid]["end_ms"] - by_id[sid]["start_ms"],
        }
        for sid in ids
    ]


def gemini_payload(model: str, block: dict) -> dict:
    return {
        "provider": GEMINI_PROVIDER,
        "model": model,
        "prompt_version": BATCH_PROMPT_VERSION,
        "task": "Transform Chinese game narration/dialogue into concise natural American English for dubbing.",
        "target_locale": "en-US",
        "requirements": [
            "Return JSON only.",
            "Return exactly one item per input source segment.",
            "Preserve source_segment_id exactly and in the same order.",
            "Use concise natural American English suitable for the scene.",
            "Use spoken_status='non_spoken' only for genuinely non-spoken source segments.",
        ],
        "schema": {"segments": [{"source_segment_id": "seg_0001", "english_text": "natural en-US line", "spoken_status": "spoken"}]},
        "block": block,
    }


def create_batch_job(model: str, body: dict) -> str:
    config = load_gemini_translation_config()
    key = load_secret_lines(config.key_file)[0]
    url = config.base_url.split("/openai/")[0].rstrip("/") + f"/models/{model}:batchGenerateContent"
    with httpx.Client(timeout=60.0) as client:
        response = client.post(url, headers={"x-goog-api-key": key}, json=body)
    if response.status_code == 429:
        payload = sanitize_response(response, model)
        write_json(get_settings().root / "evidence" / "CP07" / "gemini_batch" / "batch_create_error.json", payload)
        raise BatchCreateError("CP07_BLOCKED_GEMINI_BATCH_RATE_LIMIT", payload)
    if response.status_code in {400, 404}:
        payload = sanitize_response(response, model)
        write_json(get_settings().root / "evidence" / "CP07" / "gemini_batch" / "batch_create_error.json", payload)
        raise BatchCreateError("CP07_BLOCKED_GEMINI_BATCH_UNSUPPORTED", payload)
    response.raise_for_status()
    payload = response.json()
    batch_name = payload.get("name") or payload.get("batch", {}).get("name")
    if not batch_name:
        raise RuntimeError("CP07_BLOCKED_GEMINI_BATCH_PROVIDER")
    return batch_name


def poll_batch_job(batch_name: str, job_state_path: Path) -> dict:
    config = load_gemini_translation_config()
    key = load_secret_lines(config.key_file)[0]
    url = config.base_url.split("/openai/")[0].rstrip("/") + f"/{batch_name}"
    deadline = time.time() + OBSERVATION_SECONDS
    interval = 30
    polls = 0
    latest = {}
    while time.time() < deadline:
        polls += 1
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers={"x-goog-api-key": key})
        if response.status_code == 429:
            raise RuntimeError("CP07_BLOCKED_GEMINI_BATCH_RATE_LIMIT")
        response.raise_for_status()
        latest = response.json()
        state = latest.get("state", "BATCH_STATE_UNSPECIFIED")
        job_state = load_json(job_state_path)
        history = job_state.get("state_history", [])
        history.append({"poll": polls, "state": state, "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        job_state["state_history"] = history
        job_state["latest_state"] = state
        write_json(job_state_path, job_state)
        if state in {"BATCH_STATE_SUCCEEDED", "BATCH_STATE_FAILED", "BATCH_STATE_CANCELLED", "BATCH_STATE_EXPIRED"}:
            return {"state": state, "poll_count": polls, "batch": latest}
        time.sleep(min(60, interval) + random.randint(0, 5))
        interval = min(120, interval + 30)
    return {"state": latest.get("state", "BATCH_STATE_PENDING") if latest else "BATCH_STATE_PENDING", "poll_count": polls, "batch": latest}


def import_batch_results(batch: dict, manifest: dict) -> dict:
    output = batch.get("output", {})
    inline = output.get("inlinedResponses", {}).get("inlinedResponses", [])
    if not inline:
        raise RuntimeError("CP07_GEMINI_BATCH_PENDING")
    by_key = {item["request_key"]: item for item in manifest["requests"]}
    imported = []
    for response_item in inline:
        metadata = response_item.get("metadata", {})
        key = metadata.get("request_key") or metadata.get("key")
        if key not in by_key or response_item.get("error"):
            raise RuntimeError("CP07_BLOCKED_GEMINI_BATCH_PARTIAL_FAILURE")
        request = by_key[key]
        parsed = parse_generated_json(extract_text_parts(response_item.get("response", {})))
        validated = validate_diagnostic_response(request["source_segment_ids"], parsed.payload)
        write_cached_response(GEMINI_PROVIDER, request["request_hash"], validated)
        validate_atomic_cache_roundtrip(GEMINI_PROVIDER, request["request_hash"], validated)
        imported.append(key)
    return {"verdict": "BATCH_IMPORT_PASS", "imported_request_count": len(imported), "coverage": "442/442" if len(imported) == len(manifest["requests"]) else "PARTIAL"}


def sanitize_response(response: httpx.Response, model: str | None = None) -> dict:
    text = " ".join(response.text.split())[:800]
    return {"model": model, "status_code": response.status_code, "content_type": response.headers.get("content-type"), "body_excerpt": text}


def secret_like(text: str) -> bool:
    return bool(re.search(r"AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9_-]{20,}", text))


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
