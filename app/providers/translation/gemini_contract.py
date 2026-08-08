import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.provider_cache import provider_cache_path


class GeminiContractError(RuntimeError):
    def __init__(self, classification: str, message: str) -> None:
        super().__init__(message)
        self.classification = classification


@dataclass(frozen=True)
class ParsedGeminiPayload:
    payload: Any
    parser_stage: str
    markdown_fences_detected: bool
    json_substring_detected: bool
    raw_text_character_count: int


def extract_text_parts(provider_response: Any) -> list[str]:
    if isinstance(provider_response, str):
        return [provider_response]
    if not isinstance(provider_response, dict):
        return []
    if "choices" in provider_response:
        parts: list[str] = []
        for choice in provider_response.get("choices") or []:
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif isinstance(item, str):
                        parts.append(item)
        return parts
    if "candidates" in provider_response:
        parts = []
        for candidate in provider_response.get("candidates") or []:
            content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
            for part in content.get("parts", []) if isinstance(content, dict) else []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        return parts
    return []


def parse_generated_json(text_parts: list[str] | str) -> ParsedGeminiPayload:
    if isinstance(text_parts, str):
        text = text_parts
    else:
        text = "".join(text_parts)
    raw = text.strip()
    if not raw:
        raise GeminiContractError("CP07_BLOCKED_GEMINI_EMPTY_RESPONSE", "Provider returned no generated text")

    markdown = "```" in raw
    values = _json_values_in_text(raw)
    if not values:
        lower = raw.lower()
        if any(marker in lower for marker in ["i can't", "i cannot", "cannot comply", "safety policy", "blocked for safety"]):
            raise GeminiContractError("CP07_BLOCKED_GEMINI_SAFETY_RESPONSE", "Provider returned a safety refusal")
        raise GeminiContractError("CP07_BLOCKED_GEMINI_EMPTY_RESPONSE", "No JSON payload was found in provider text")
    if len(values) > 1:
        raise GeminiContractError("CP07_BLOCKED_GEMINI_PARSER", "Multiple JSON payloads were found")
    payload, start, end = values[0]
    stage = "direct_json" if start == 0 and not raw[end:].strip() else "embedded_json"
    return ParsedGeminiPayload(
        payload=payload,
        parser_stage=stage,
        markdown_fences_detected=markdown,
        json_substring_detected=True,
        raw_text_character_count=len(raw),
    )


def _json_values_in_text(text: str) -> list[tuple[Any, int, int]]:
    decoder = json.JSONDecoder()
    values: list[tuple[Any, int, int]] = []
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        values.append((value, index, index + end))
    return _drop_nested_values(values)


def _drop_nested_values(values: list[tuple[Any, int, int]]) -> list[tuple[Any, int, int]]:
    result = []
    for value in values:
        _, start, end = value
        if any(other_start < start and end <= other_end for _, other_start, other_end in values):
            continue
        result.append(value)
    return result


def validate_diagnostic_response(expected_ids: list[str], payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("segments"), list):
        items = payload["segments"]
    else:
        raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Payload must be an array or object with segments")
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Segment item is not an object")
        source_id = item.get("source_segment_id") or item.get("id")
        english_text = item.get("english_text") or item.get("spoken_text")
        spoken_status = item.get("spoken_status") or item.get("status") or "spoken"
        if not isinstance(source_id, str) or not source_id:
            raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Segment missing source_segment_id")
        if not isinstance(english_text, str) or not english_text.strip():
            raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Segment missing english_text")
        if not isinstance(spoken_status, str) or not spoken_status.strip():
            raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Segment missing spoken_status")
        normalized.append(
            {
                "source_segment_id": source_id,
                "english_text": " ".join(english_text.split()),
                "spoken_status": " ".join(spoken_status.split()),
            }
        )
    actual_ids = [item["source_segment_id"] for item in normalized]
    if len(actual_ids) != len(set(actual_ids)):
        raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Duplicate source IDs")
    unknown = [item for item in actual_ids if item not in expected_ids]
    if unknown:
        raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Unknown source IDs")
    missing = [item for item in expected_ids if item not in actual_ids]
    if missing:
        raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Missing source IDs")
    if actual_ids != expected_ids:
        raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Source IDs reordered")
    return {"schema_version": 1, "segments": normalized}


def enforce_same_hash_attempt_guard(ledger_path: Path, request_hash: str, max_attempts: int = 2) -> dict[str, Any]:
    ledger = _read_ledger(ledger_path)
    attempts = int(ledger.get(request_hash, {}).get("provider_submissions", 0))
    if attempts >= max_attempts:
        raise GeminiContractError("CP07_BLOCKED_GEMINI_RETRY_INVARIANT", "Third provider submission blocked")
    ledger[request_hash] = {
        "provider_submissions": attempts + 1,
        "last_attempt_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write_json_atomic(ledger_path, ledger)
    return ledger[request_hash]


def validate_atomic_cache_roundtrip(provider: str, request_hash: str, expected_payload: dict[str, Any]) -> None:
    path = provider_cache_path(provider, request_hash)
    if not path.exists():
        raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Cache file missing after write")
    cached = json.loads(path.read_text(encoding="utf-8"))
    if cached != expected_payload:
        raise GeminiContractError("CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA", "Cache roundtrip mismatch")


def _read_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)
