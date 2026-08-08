import pytest

from app.core.provider_cache import build_request_hash
from app.providers.tts.base import TTSRequest
from app.providers.tts.fake import tts_request_payload
from tools import run_cp07_downstream_from_gemini_transform as cp07


class _Provider:
    provider_name = "elevenlabs"
    model = "eleven_multilingual_v2"
    output_format = "mp3_44100_128"
    provider_request_version = "tts-v2"
    config = type("Config", (), {"timeout_seconds": 180.0})()

    def _url(self, path):
        return "https://api.elevenlabs.io" + path


def _request(**overrides):
    base = {
        "project_id": "vertical_slice_cp07",
        "segment_id": "cp07_g58",
        "text": "Current sentence.",
        "voice_id": "voice",
        "model": "eleven_multilingual_v2",
        "previous_request_ids": [],
        "next_text": None,
    }
    base.update(overrides)
    return TTSRequest(**base)


def test_rejects_previous_text_plus_previous_request_ids():
    request = _request(previous_text="Previous sentence.", previous_request_ids=["old_req"])
    with pytest.raises(RuntimeError, match="CP07_BLOCKED_TTS_STITCHING_CONTEXT"):
        cp07.assert_prepared_request(_Provider(), request, {"clip_id": "cp07_g58"}, {"mode": "PREVIOUS_TEXT"}, 2)


def test_stitching_mode_changes_request_hash_and_preserves_group_id():
    old = _request(previous_request_ids=["req_a", "req_b", "req_c"], provider_request_version="tts-v2")
    corrected = _request(
        previous_text="Prior canonical sentence.",
        previous_request_ids=[],
        provider_request_version=cp07.STITCHING_SCHEMA_VERSION,
    )
    assert old.segment_id == corrected.segment_id == "cp07_g58"
    assert build_request_hash(tts_request_payload("elevenlabs", old)) != build_request_hash(tts_request_payload("elevenlabs", corrected))


def test_g58_context_reset_uses_previous_text_not_request_ids():
    groups = [
        {"clip_id": "cp07_g57", "english_text": "This is the final prior sentence."},
        {"clip_id": "cp07_g58", "english_text": "This is the corrected boundary sentence."},
        {"clip_id": "cp07_g59", "english_text": "This is the next sentence."},
    ]
    context = cp07.resolve_stitching_context(groups[1], groups, 1, [{"request_id": "old", "credential_ref": "elevenlabs-key-1"}], 2)
    assert context["mode"] == "PREVIOUS_TEXT"
    assert context["previous_request_ids"] == []
    assert context["previous_text"]
    assert context["provider_request_version"] == cp07.STITCHING_SCHEMA_VERSION


def test_g59_chain_begins_from_g58_only(monkeypatch):
    monkeypatch.setattr(cp07, "all_request_ids_match_key", lambda request_ids, credential_ref: True)
    groups = [
        {"clip_id": "cp07_g58", "english_text": "Boundary."},
        {"clip_id": "cp07_g59", "english_text": "Next."},
    ]
    context = cp07.resolve_stitching_context(
        groups[1],
        groups,
        58,
        [
            {"group_id": "cp07_g57", "request_id": "old_req", "credential_ref": "elevenlabs-key-1"},
            {"group_id": "cp07_g58", "request_id": "new_req", "credential_ref": "elevenlabs-key-2"},
        ],
        2,
    )
    assert context["mode"] == "PREVIOUS_REQUEST_IDS"
    assert context["previous_request_ids"] == ["new_req"]


def test_previous_request_ids_are_limited_to_three(monkeypatch):
    monkeypatch.setattr(cp07, "all_request_ids_match_key", lambda request_ids, credential_ref: True)
    context = cp07.resolve_stitching_context(
        {"clip_id": "cp07_g62", "english_text": "Later."},
        [],
        61,
        [{"request_id": f"req_{index}", "credential_ref": "elevenlabs-key-2"} for index in range(5)],
        2,
    )
    assert context["previous_request_ids"] == ["req_2", "req_3", "req_4"]


def test_minimal_payload_exact_field_set_and_no_stitching_fields():
    group = {"clip_id": "cp07_g58", "english_text": "Minimal production text."}
    payload = cp07.minimal_tts_payload(group, "voice", _Provider())
    assert payload["payload_schema_version"] == cp07.FRESH_CLIENT_TTS_SCHEMA_VERSION
    assert payload["body_keys"] == ["model_id", "text"]
    assert payload["query_keys"] == ["output_format"]
    assert "enable_logging" in payload["optional_fields_absent"]
    assert "previous_request_ids" in payload["optional_fields_absent"]
    assert "previous_text" in payload["optional_fields_absent"]
    assert "next_text" in payload["optional_fields_absent"]


def test_canary_and_minimal_use_same_authenticated_client_contract():
    provider = _Provider()
    canary = cp07.canary_prepared_request_fingerprint(provider, "voice", 2)
    minimal = cp07.minimal_prepared_request_fingerprint(provider, "voice", 2)
    diff = cp07.compare_request_fingerprints(canary, minimal)
    assert diff["unexpected_differing_fields"] == []
    assert minimal["authentication"]["selected_key_slot"] == 2
    assert minimal["authentication"]["xi_api_key_present"] is True
    assert minimal["authentication"]["authorization_header_present"] is False
    assert minimal["authentication"]["key_resolved_at"] == "send_time"
    assert minimal["authentication"]["xi_api_key_source"] == "final_request_header"
    assert minimal["authentication"]["content_type_source"] == "explicit_final_request_header"
    assert minimal["authentication"]["environment_override_allowed"] is False
    assert minimal["authentication"]["cached_provider_client_allowed"] is False
    assert minimal["query"]["enable_logging_present"] is False
    assert minimal["body"]["context_fields_present"] == []
    assert minimal["body"]["null_fields_serialized"] == []


def test_minimal_hash_changes_with_payload_schema_version():
    group = {"clip_id": "cp07_g58", "english_text": "Minimal production text."}
    payload = cp07.minimal_tts_payload(group, "voice", _Provider())
    changed = dict(payload)
    changed["payload_schema_version"] = "CP07_TTS_MINIMAL_V2"
    assert build_request_hash(payload) != build_request_hash(changed)
