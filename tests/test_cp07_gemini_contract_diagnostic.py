import json

import pytest

from app.providers.translation.gemini_contract import (
    GeminiContractError,
    enforce_same_hash_attempt_guard,
    extract_text_parts,
    parse_generated_json,
    validate_atomic_cache_roundtrip,
    validate_diagnostic_response,
)
from app.core.provider_cache import write_cached_response


EXPECTED_IDS = ["seg_0071", "seg_0072"]


def test_parse_json_code_fences():
    parsed = parse_generated_json('```json\n{"segments":[]}\n```')
    assert parsed.payload == {"segments": []}
    assert parsed.markdown_fences_detected is True


def test_parse_leading_trailing_prose():
    parsed = parse_generated_json('Here is the JSON: {"segments": []} done.')
    assert parsed.payload == {"segments": []}
    assert parsed.parser_stage == "embedded_json"


def test_parse_json_with_cannot_phrase_is_not_safety_refusal():
    parsed = parse_generated_json(
        '{"segments":[{"source_segment_id":"seg_0071","english_text":"I cannot let you pass.","spoken_status":"spoken"}]}'
    )
    assert parsed.payload["segments"][0]["english_text"] == "I cannot let you pass."


def test_parse_multipart_text_concatenation():
    wrapper = {"choices": [{"message": {"content": [{"text": '{"segments":'}, {"text": "[]}"}]}}]}
    parsed = parse_generated_json(extract_text_parts(wrapper))
    assert parsed.payload == {"segments": []}


def test_parse_truncated_json_rejected():
    with pytest.raises(GeminiContractError) as exc:
        parse_generated_json('{"segments": [')
    assert exc.value.classification == "CP07_BLOCKED_GEMINI_EMPTY_RESPONSE"


def test_validate_duplicate_ids_rejected():
    payload = {"segments": [{"source_segment_id": "seg_0071", "english_text": "One", "spoken_status": "spoken"}] * 2}
    with pytest.raises(GeminiContractError) as exc:
        validate_diagnostic_response(EXPECTED_IDS, payload)
    assert exc.value.classification == "CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA"


def test_validate_missing_ids_rejected():
    payload = {"segments": [{"source_segment_id": "seg_0071", "english_text": "One", "spoken_status": "spoken"}]}
    with pytest.raises(GeminiContractError) as exc:
        validate_diagnostic_response(EXPECTED_IDS, payload)
    assert exc.value.classification == "CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA"


def test_validate_unknown_ids_rejected():
    payload = {
        "segments": [
            {"source_segment_id": "seg_0071", "english_text": "One", "spoken_status": "spoken"},
            {"source_segment_id": "seg_9999", "english_text": "Two", "spoken_status": "spoken"},
        ]
    }
    with pytest.raises(GeminiContractError) as exc:
        validate_diagnostic_response(EXPECTED_IDS, payload)
    assert exc.value.classification == "CP07_BLOCKED_GEMINI_RESPONSE_SCHEMA"


def test_empty_response_rejected():
    with pytest.raises(GeminiContractError) as exc:
        parse_generated_json("")
    assert exc.value.classification == "CP07_BLOCKED_GEMINI_EMPTY_RESPONSE"


def test_safety_response_rejected():
    with pytest.raises(GeminiContractError) as exc:
        parse_generated_json("I cannot comply because of safety policy.")
    assert exc.value.classification == "CP07_BLOCKED_GEMINI_SAFETY_RESPONSE"


def test_same_hash_third_attempt_prevention(tmp_path):
    ledger = tmp_path / "ledger.json"
    enforce_same_hash_attempt_guard(ledger, "a" * 64, max_attempts=2)
    enforce_same_hash_attempt_guard(ledger, "a" * 64, max_attempts=2)
    with pytest.raises(GeminiContractError) as exc:
        enforce_same_hash_attempt_guard(ledger, "a" * 64, max_attempts=2)
    assert exc.value.classification == "CP07_BLOCKED_GEMINI_RETRY_INVARIANT"


def test_atomic_diagnostic_cache_write_and_reread():
    request_hash = "b" * 64
    payload = {
        "schema_version": 1,
        "segments": [
            {"source_segment_id": "seg_0071", "english_text": "One", "spoken_status": "spoken"},
            {"source_segment_id": "seg_0072", "english_text": "Two", "spoken_status": "spoken"},
        ],
    }
    write_cached_response("test_cp07_gemini_contract", request_hash, payload)
    validate_atomic_cache_roundtrip("test_cp07_gemini_contract", request_hash, payload)
