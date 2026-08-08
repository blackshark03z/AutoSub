from pathlib import Path

import httpx
import pytest

from app.core.secret_files import load_strict_secret_lines
from app.providers.tts.base import (
    TTSAuthenticationError,
    TTSAuthorizationError,
    TTSPaymentRequiredError,
    TTSRateLimitError,
)
from app.providers.tts.elevenlabs import (
    ElevenLabsConfig,
    ElevenLabsHTTPError,
    ElevenLabsTTSProvider,
    classify_elevenlabs_status,
)


def _provider(tmp_path: Path, handler) -> tuple[ElevenLabsTTSProvider, list[httpx.Request]]:
    key_file = tmp_path / "elevenlabs_api.txt"
    key_file.write_text("\ufeff  test-key-without-spaces  \r\n", encoding="utf-8")
    requests: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(capture)
    config = ElevenLabsConfig(key_file=key_file, timeout_seconds=3, transport=transport)
    return ElevenLabsTTSProvider(config), requests


def test_probe_matrix_uses_same_xi_api_key_header_without_bearer(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": "redacted"}, request=request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=[{"model_id": "eleven_multilingual_v2"}], request=request)
        if request.url.path == "/v2/voices":
            return httpx.Response(200, json={"voices": [{"voice_id": "voice"}], "total_count": 1}, request=request)
        return httpx.Response(404, request=request)

    provider, requests = _provider(tmp_path, handler)
    matrix = provider.run_auth_probe_matrix()
    assert matrix["subscription"].classification == "ok"
    assert matrix["models"].configured_model_present is True
    assert matrix["voices"].count == 1
    assert [request.url.path for request in requests] == [
        "/v1/user/subscription",
        "/v1/models",
        "/v2/voices",
    ]
    headers = [request.headers for request in requests]
    assert {header.get("xi-api-key") for header in headers} == {"test-key-without-spaces"}
    assert all("authorization" not in header for header in headers)


def test_secret_parser_rejects_quoted_entries(tmp_path):
    key_file = tmp_path / "elevenlabs_api.txt"
    key_file.write_text('"quoted-key"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="quoted"):
        load_strict_secret_lines(key_file)


def test_secret_parser_rejects_embedded_whitespace(tmp_path):
    key_file = tmp_path / "elevenlabs_api.txt"
    key_file.write_text("bad key\n", encoding="utf-8")
    with pytest.raises(ValueError, match="whitespace"):
        load_strict_secret_lines(key_file)


@pytest.mark.parametrize(
    ("status_code", "classification"),
    [
        (401, "authentication"),
        (402, "payment_or_credit"),
        (403, "authorization_or_ip_restriction"),
        (429, "rate_or_concurrency"),
    ],
)
def test_status_classifier_maps_elevenlabs_blockers(status_code, classification):
    assert classify_elevenlabs_status(status_code) == classification


def test_subscription_ok_models_401_is_inconclusive_not_invalid_key(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": "redacted"}, request=request)
        if request.url.path == "/v1/models":
            return httpx.Response(401, json={"detail": "redacted"}, request=request)
        if request.url.path == "/v2/voices":
            return httpx.Response(200, json={"voices": []}, request=request)
        return httpx.Response(404, request=request)

    provider, _ = _provider(tmp_path, handler)
    matrix = provider.run_auth_probe_matrix()
    assert matrix["subscription"].classification == "ok"
    assert matrix["models"].classification == "authentication"
    verdict = _auth_matrix_verdict(matrix)
    assert verdict == "MODELS_INCONCLUSIVE_WITH_AUTHENTICATED_SUBSCRIPTION"


def test_models_403_does_not_block_explicit_tts_model_by_itself(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(200, json={"tier": "redacted"}, request=request)
        if request.url.path == "/v1/models":
            return httpx.Response(403, json={"detail": "redacted"}, request=request)
        if request.url.path == "/v2/voices":
            return httpx.Response(200, json={"voices": []}, request=request)
        return httpx.Response(404, request=request)

    provider, _ = _provider(tmp_path, handler)
    matrix = provider.run_auth_probe_matrix()
    assert matrix["models"].classification == "authorization_or_ip_restriction"
    verdict = _auth_matrix_verdict(matrix)
    assert verdict == "MODELS_SCOPE_RESTRICTED_TTS_CAN_CHECK_CAPABILITY"


def test_redirect_to_another_host_is_blocked_without_forwarding_secret(tmp_path):
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        return httpx.Response(307, headers={"location": "https://example.com/v1/models"}, request=request)

    provider, requests = _provider(tmp_path, handler)
    with pytest.raises(ElevenLabsHTTPError, match="redirect"):
        provider.list_models()
    assert seen_hosts == ["api.elevenlabs.io"]
    assert len(requests) == 1
    assert requests[0].headers.get("xi-api-key") == "test-key-without-spaces"


def test_http_error_exceptions_are_sanitized(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"secret_account_detail": "do-not-log"}, request=request)

    provider, _ = _provider(tmp_path, handler)
    with pytest.raises(TTSAuthenticationError) as exc:
        provider.list_models()
    message = str(exc.value)
    assert "test-key" not in message
    assert "secret_account_detail" not in message
    assert "xi-api-key" not in message


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, TTSAuthenticationError),
        (402, TTSPaymentRequiredError),
        (403, TTSAuthorizationError),
        (429, TTSRateLimitError),
    ],
)
def test_tts_blocker_statuses_stop_with_classified_errors(tmp_path, status_code, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "redacted"}, request=request)

    provider, _ = _provider(tmp_path, handler)
    with pytest.raises(expected):
        provider.list_models()


def _auth_matrix_verdict(matrix: dict) -> str:
    subscription = matrix["subscription"]
    models = matrix["models"]
    if subscription.status_code == 401:
        return "INVALID_OR_DISABLED_CREDENTIAL"
    if subscription.status_code == 200 and models.status_code == 401:
        return "MODELS_INCONCLUSIVE_WITH_AUTHENTICATED_SUBSCRIPTION"
    if subscription.status_code == 200 and models.status_code == 403:
        return "MODELS_SCOPE_RESTRICTED_TTS_CAN_CHECK_CAPABILITY"
    if subscription.status_code == 200 and models.status_code == 200:
        return "PREFLIGHT_PASS"
    return "BLOCKED"
