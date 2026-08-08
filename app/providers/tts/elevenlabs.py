import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.provider_cache import build_request_hash, read_cached_response, write_cached_response
from app.core.secret_files import load_strict_secret_lines
from app.providers.tts.base import (
    TTSAuthenticationError,
    TTSAuthorizationError,
    TTSPaymentRequiredError,
    TTSRateLimitError,
    TTSRequest,
    TTSResult,
    TTSUncertainError,
)
from app.providers.tts.fake import tts_request_payload


@dataclass(frozen=True)
class ElevenLabsConfig:
    key_file: Path
    model: str = "eleven_multilingual_v2"
    base_url: str = "https://api.elevenlabs.io"
    output_format: str = "mp3_44100_128"
    timeout_seconds: float = 180.0
    transport: httpx.BaseTransport | None = None


@dataclass(frozen=True)
class ElevenLabsProbeResult:
    endpoint: str
    status_code: int | None
    classification: str
    count: int | None = None
    configured_model_present: bool | None = None
    used_xi_api_key_header: bool = True
    used_authorization_header: bool = False
    redirected: bool = False
    redirected_cross_host: bool = False


class ElevenLabsHTTPError(RuntimeError):
    def __init__(self, endpoint: str, status_code: int, classification: str) -> None:
        super().__init__(f"ElevenLabs {endpoint} failed with HTTP {status_code} ({classification})")
        self.endpoint = endpoint
        self.status_code = status_code
        self.classification = classification


class ElevenLabsTTSProvider:
    provider_name = "elevenlabs"

    def __init__(self, config: ElevenLabsConfig, key_index: int = 0) -> None:
        self.config = config
        self.model = config.model
        self.output_format = config.output_format
        self.provider_request_version = "tts-v2"
        self._keys = load_strict_secret_lines(config.key_file)
        if key_index >= len(self._keys):
            raise IndexError("ElevenLabs key index out of range")
        self._key_index = key_index
        self._credential_ref = f"elevenlabs-key-{key_index + 1}"

    def validate_credentials(self) -> bool:
        result = self.probe_subscription()
        return result.status_code == 200

    def list_voices(self) -> list[dict]:
        response = self._request("GET", "/v2/voices", endpoint_name="voices", params={"page_size": 100})
        payload = response.json()
        if isinstance(payload, dict):
            return payload.get("voices", [])
        return []

    def list_models(self) -> list[dict]:
        response = self._request("GET", "/v1/models", endpoint_name="models")
        return response.json()

    def probe_subscription(self) -> ElevenLabsProbeResult:
        response = self._request("GET", "/v1/user/subscription", endpoint_name="subscription", raise_for_status=False)
        return _probe_from_response("subscription", response)

    def probe_models(self) -> ElevenLabsProbeResult:
        response = self._request("GET", "/v1/models", endpoint_name="models", raise_for_status=False)
        count = None
        configured_model_present = None
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, list):
                count = len(payload)
                configured_model_present = any(item.get("model_id") == self.model for item in payload if isinstance(item, dict))
        return _probe_from_response(
            "models",
            response,
            count=count,
            configured_model_present=configured_model_present,
        )

    def probe_voices(self) -> ElevenLabsProbeResult:
        response = self._request(
            "GET",
            "/v2/voices",
            endpoint_name="voices",
            params={"page_size": 1},
            raise_for_status=False,
        )
        count = None
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict):
                count = payload.get("total_count")
                if count is None:
                    count = len(payload.get("voices", []))
        return _probe_from_response("voices", response, count=count)

    def run_auth_probe_matrix(self) -> dict[str, ElevenLabsProbeResult]:
        return {
            "subscription": self.probe_subscription(),
            "models": self.probe_models(),
            "voices": self.probe_voices(),
        }

    def estimate_usage(self, request: TTSRequest) -> int:
        return len(request.text)

    def synthesize(self, request: TTSRequest, output_path: Path) -> TTSResult:
        if request.previous_text and request.previous_request_ids:
            raise ValueError("TTS request cannot combine previous_text with previous_request_ids")
        payload = tts_request_payload(self.provider_name, request)
        request_hash = build_request_hash(payload)
        cached = read_cached_response(self.provider_name, request_hash)
        if cached is not None and _cache_artifact_valid(cached):
            return TTSResult(
                self.provider_name,
                request.model,
                request.voice_id,
                request_hash,
                "hit",
                Path(cached["audio_path"]),
                cached.get("request_id"),
                self._credential_ref,
                len(request.text),
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_mp3 = output_path.with_suffix(".mp3.tmp")
        try:
            provider_payload = {
                "text": request.text,
                "model_id": request.model,
                "voice_settings": request.voice_settings,
                "next_text": request.next_text,
            }
            if request.previous_text:
                provider_payload["previous_text"] = request.previous_text
                provider_payload["previous_request_ids"] = []
            else:
                provider_payload["previous_request_ids"] = request.previous_request_ids[-3:]
            response = self._request(
                "POST",
                f"/v1/text-to-speech/{request.voice_id}",
                endpoint_name="text_to_speech",
                params={"output_format": self.config.output_format},
                json=provider_payload,
            )
            temp_mp3.write_bytes(response.content)
            _decode_to_wav(temp_mp3, output_path)
            request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
            digest = sha256_file(output_path)
            write_cached_response(
                self.provider_name,
                request_hash,
                {
                    "audio_path": str(output_path),
                    "request_id": request_id,
                    "character_count": len(request.text),
                    "sha256": digest,
                    "model": request.model,
                    "voice_id": request.voice_id,
                },
            )
            return TTSResult(
                self.provider_name,
                request.model,
                request.voice_id,
                request_hash,
                "miss",
                output_path,
                request_id,
                self._credential_ref,
                len(request.text),
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TTSUncertainError("ElevenLabs request outcome is uncertain") from exc
        except Exception:
            if output_path.exists():
                output_path.unlink()
            raise
        finally:
            if temp_mp3.exists():
                temp_mp3.unlink()

    def _request(
        self,
        method: str,
        path: str,
        *,
        endpoint_name: str,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        url = self._url(path)
        try:
            with self._client() as client:
                response = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TTSUncertainError(f"ElevenLabs {endpoint_name} request outcome is uncertain") from exc
        if response.is_redirect:
            location = response.headers.get("location", "")
            raise ElevenLabsHTTPError(
                endpoint_name,
                response.status_code,
                "redirect_blocked_cross_host" if _is_cross_host_redirect(url, location) else "redirect_blocked",
            )
        if raise_for_status and response.status_code >= 400:
            _raise_classified_http_error(endpoint_name, response.status_code)
        return response

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("ElevenLabs endpoint path must start with /")
        base = self.config.base_url.rstrip("/")
        if base != "https://api.elevenlabs.io":
            raise ValueError("ElevenLabs base URL must be https://api.elevenlabs.io")
        return f"{base}{path}"

    def _client(self) -> httpx.Client:
        headers = {"xi-api-key": self._keys[self._key_index]}
        return httpx.Client(
            timeout=self.config.timeout_seconds,
            headers=headers,
            follow_redirects=False,
            transport=self.config.transport,
        )


def load_elevenlabs_config() -> ElevenLabsConfig:
    settings = get_settings()
    return ElevenLabsConfig(key_file=settings.root / "secrets" / "elevenlabs_api.txt")


def classify_elevenlabs_status(status_code: int | None) -> str:
    if status_code is None:
        return "transport_or_timeout"
    if status_code == 200:
        return "ok"
    if status_code == 401:
        return "authentication"
    if status_code == 402:
        return "payment_or_credit"
    if status_code == 403:
        return "authorization_or_ip_restriction"
    if status_code == 429:
        return "rate_or_concurrency"
    if status_code >= 500:
        return "server_error"
    return "http_error"


def _decode_to_wav(source: Path, output_path: Path) -> None:
    temp_wav = output_path.with_suffix(output_path.suffix + ".tmp.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "48000",
            str(temp_wav),
        ],
        check=True,
    )
    os.replace(temp_wav, output_path)


def _cache_artifact_valid(cached: dict) -> bool:
    path = Path(cached.get("audio_path", "")).resolve()
    if not path.is_relative_to(get_settings().data_dir.resolve()):
        return False
    expected = cached.get("sha256")
    return path.exists() and expected is not None and sha256_file(path) == expected


def _probe_from_response(
    endpoint: str,
    response: httpx.Response,
    *,
    count: int | None = None,
    configured_model_present: bool | None = None,
) -> ElevenLabsProbeResult:
    return ElevenLabsProbeResult(
        endpoint=endpoint,
        status_code=response.status_code,
        classification=classify_elevenlabs_status(response.status_code),
        count=count,
        configured_model_present=configured_model_present,
        redirected=response.is_redirect,
        redirected_cross_host=_is_cross_host_redirect(str(response.request.url), response.headers.get("location", ""))
        if response.is_redirect
        else False,
    )


def _raise_classified_http_error(endpoint: str, status_code: int) -> None:
    classification = classify_elevenlabs_status(status_code)
    if status_code == 401:
        raise TTSAuthenticationError(f"ElevenLabs {endpoint} authentication failed")
    if status_code == 402:
        raise TTSPaymentRequiredError(f"ElevenLabs {endpoint} payment or credit required")
    if status_code == 403:
        raise TTSAuthorizationError(f"ElevenLabs {endpoint} authorization or IP restriction failed")
    if status_code == 429:
        raise TTSRateLimitError(f"ElevenLabs {endpoint} rate or concurrency limited")
    raise ElevenLabsHTTPError(endpoint, status_code, classification)


def _is_cross_host_redirect(url: str, location: str) -> bool:
    if not location:
        return False
    source = httpx.URL(url)
    target = httpx.URL(urljoin(url, location))
    return (source.scheme, source.host, source.port) != (target.scheme, target.host, target.port)
