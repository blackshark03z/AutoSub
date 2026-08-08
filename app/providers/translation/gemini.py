import base64
import ctypes
import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.provider_cache import build_request_hash, read_cached_response, write_cached_response
from app.core.secret_files import load_strict_secret_lines, load_strict_secret_text
from app.providers.translation.base import TranslationBlockRequest, TranslationBlockResult

try:
    import win32cred  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - platform dependent
    win32cred = None


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


@dataclass(frozen=True)
class GeminiTranslationConfig:
    base_url: str
    model: str
    timeout_seconds: float
    key_file: Path
    credential_name: str | None = None
    free_tier_evidence_path: Path | None = None


class GeminiCaptionTranslationError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(frozen=True)
class GeminiCaptionTranslationResult:
    translations: list[dict[str, str]]
    model: str
    request_count: int
    retry_count: int
    cache_hits: int
    cache_misses: int
    input_tokens: int
    output_tokens: int
    request_ids: list[str]


@dataclass(frozen=True)
class GeminiMultimodalResolutionResult:
    intervals: list[dict[str, Any]]
    model: str
    request_count: int
    retry_count: int
    cache_hits: int
    cache_misses: int
    input_tokens: int
    output_tokens: int
    request_ids: list[str]


@dataclass(frozen=True)
class GeminiModelDiscoveryResult:
    status: str
    sanitized_models: list[dict[str, Any]]
    raw_count: int
    selected_model: str | None
    selected_reason: str | None
    free_tier_verified: bool


FREE_TIER_MODEL_CANDIDATES = (
    "gemini-2.5-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)
DEFAULT_GEMINI_CREDENTIAL_NAME = "tool_auto_sub_worker_handoff_v0_2_gemini_api"
GEMINI_DISCOVERY_PROVIDER = "gemini_model_discovery"
GEMINI_DISCOVERY_PROMPT_VERSION = "model-discovery-v2"
SUPPORTED_GENERATION_METHODS = {"generateContent", "generateContentStream", "interactions"}
DEFAULT_FREE_TIER_EVIDENCE_PATH = (
    Path(tempfile.gettempdir()) / "task41h_free_tier_verification.json"
)


def _native_api_root(config: GeminiTranslationConfig) -> str:
    base = config.base_url.rstrip("/")
    if "/openai" in base:
        return base.split("/openai", 1)[0].rstrip("/")
    if base.endswith("/models"):
        return base.rsplit("/models", 1)[0].rstrip("/")
    return base


def _selected_credential_name(config: GeminiTranslationConfig) -> str | None:
    value = (config.credential_name or "").strip()
    return value or None


def _credential_source_status(config: GeminiTranslationConfig) -> tuple[str, list[str]]:
    credential_name = _selected_credential_name(config)
    if credential_name:
        secure_lines = _read_secure_gemini_secret_lines(credential_name)
        if secure_lines:
            return "secure_credential_manager", secure_lines
    if config.key_file.exists():
        return "plaintext_file", load_strict_secret_lines(config.key_file)
    return "missing", []


def _read_secure_gemini_secret_lines(credential_name: str) -> list[str]:
    if win32cred is not None:
        try:
            record = win32cred.CredRead(credential_name, win32cred.CRED_TYPE_GENERIC, 0)
        except Exception:
            record = None
        blob = record.get("CredentialBlob") if isinstance(record, dict) else None
        if blob:
            if isinstance(blob, bytes):
                # pywin32 returns string credential blobs as UTF-16LE bytes.
                text = blob.decode("utf-16-le") if b"\x00" in blob else blob.decode("utf-8")
            else:
                text = str(blob)
            return load_strict_secret_text(text, source_name=f"credential:{credential_name}")
    return _read_native_gemini_secret_lines(credential_name)


def _write_secure_gemini_secret_lines(credential_name: str, lines: list[str]) -> None:
    if win32cred is not None:
        credential_blob = "\n".join(lines)
        credential = {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": credential_name,
            "CredentialBlob": credential_blob,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            "UserName": credential_name,
        }
        try:
            win32cred.CredWrite(credential, 0)
            return
        except Exception:
            pass
    _write_native_gemini_secret_lines(credential_name, lines)


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_PCredentialW = ctypes.POINTER(_CredentialW)


def _advapi32() -> Any | None:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return None
    advapi = ctypes.windll.advapi32
    advapi.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_PCredentialW),
    ]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_CredentialW), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    return advapi


def _read_native_gemini_secret_lines(credential_name: str) -> list[str]:
    advapi = _advapi32()
    if advapi is None:
        return []
    credential = _PCredentialW()
    if not advapi.CredReadW(credential_name, CRED_TYPE_GENERIC, 0, ctypes.byref(credential)):
        return []
    try:
        blob_size = int(credential.contents.CredentialBlobSize or 0)
        if blob_size <= 0:
            return []
        blob = ctypes.string_at(credential.contents.CredentialBlob, blob_size)
        text = blob.decode("utf-16-le") if b"\x00" in blob else blob.decode("utf-8")
        return load_strict_secret_text(text, source_name=f"credential:{credential_name}")
    finally:
        advapi.CredFree(credential)


def _write_native_gemini_secret_lines(credential_name: str, lines: list[str]) -> None:
    advapi = _advapi32()
    if advapi is None:
        raise RuntimeError("Windows Credential Manager is unavailable.")
    blob = "\n".join(lines).encode("utf-16-le")
    blob_buffer = ctypes.create_string_buffer(blob)
    credential = _CredentialW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = credential_name
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_byte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = credential_name
    if not advapi.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError()


def load_gemini_secret_lines(config: GeminiTranslationConfig) -> list[str]:
    credential_name = _selected_credential_name(config)
    if credential_name:
        stored = _read_secure_gemini_secret_lines(credential_name)
        if stored:
            if config.key_file.exists():
                plaintext = load_strict_secret_lines(config.key_file)
                if plaintext != stored:
                    raise RuntimeError(
                        "Gemini secure credential does not match the pending plaintext migration."
                    )
                config.key_file.unlink()
            return stored
    if not config.key_file.exists():
        raise FileNotFoundError(f"Secret file not found: {config.key_file.name}")
    lines = load_strict_secret_lines(config.key_file)
    if credential_name:
        _write_secure_gemini_secret_lines(credential_name, lines)
        verified = _read_secure_gemini_secret_lines(credential_name)
        if verified != lines:
            raise RuntimeError("Gemini credential migration verification failed.")
        try:
            config.key_file.unlink()
        except FileNotFoundError:
            pass
    return lines


def _select_active_secret_line(lines: list[str], *, key_index: int = 0) -> str:
    if not lines:
        raise ValueError("Gemini secret file has no configured entries.")
    if key_index < 0 or key_index >= len(lines):
        raise IndexError("Gemini key index out of range")
    return lines[key_index]


def _active_key_fingerprint(active_key: str) -> str:
    return "sha256:" + hashlib.sha256(active_key.encode("utf-8")).hexdigest()[:16]


def _free_tier_project_verified(
    config: GeminiTranslationConfig,
    active_key: str,
) -> bool:
    evidence_path = config.free_tier_evidence_path or DEFAULT_FREE_TIER_EVIDENCE_PATH
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(evidence, dict):
        return False
    key_matches = (
        evidence.get("active_key_fingerprint") == _active_key_fingerprint(active_key)
    )
    ui_verified = bool(
        evidence.get("status") == "verified"
        and evidence.get("plan") == "Free Tier"
        and evidence.get("billing_linked") is False
        and evidence.get("verification_method") == "ai_studio_authenticated_ui"
        and str(evidence.get("project_fingerprint") or "").strip()
        and key_matches
    )
    owner_verified = bool(
        evidence.get("status") == "verified"
        and evidence.get("plan") == "Free Tier"
        and evidence.get("verification_method") == "owner_attestation"
        and evidence.get("free_tier_confirmation") == "OWNER_CONFIRMED"
        and evidence.get("billing_activation_requested") == "NO"
        and evidence.get("cost_ceiling") == 0
        and evidence.get("paid_tier_authorization") == "NOT_GRANTED"
        and str(evidence.get("project_fingerprint") or "").strip()
        and key_matches
    )
    return ui_verified or owner_verified


def _normalize_model_name(value: str) -> str:
    value = value.strip()
    if value.startswith("models/"):
        value = value.split("/", 1)[1]
    return value.casefold()


def _canonical_model_id(value: str) -> str:
    value = value.strip()
    if value.startswith("models/"):
        return value.split("/", 1)[1]
    return value


def _sanitized_model_entry(item: dict[str, Any]) -> dict[str, Any]:
    supported = item.get("supportedGenerationMethods")
    supported_methods = [str(value) for value in supported if str(value).strip()] if isinstance(supported, list) else []
    return {
        "name": str(item.get("name") or ""),
        "display_name": str(item.get("displayName") or item.get("display_name") or ""),
        "version": str(item.get("version") or ""),
        "supported_generation_methods": supported_methods,
        "input_token_limit": int(item.get("inputTokenLimit") or 0),
        "output_token_limit": int(item.get("outputTokenLimit") or 0),
    }


def _model_supports_generation(model: dict[str, Any]) -> bool:
    methods = model.get("supported_generation_methods")
    if not isinstance(methods, list):
        return False
    return any(str(method).strip() in SUPPORTED_GENERATION_METHODS for method in methods)


def _select_free_tier_model(models: list[dict[str, Any]], configured_model: str) -> tuple[str | None, str | None]:
    by_normalized_name = { _normalize_model_name(item["name"]): item for item in models if item.get("name") }
    by_display_name = { _normalize_model_name(item["display_name"]): item for item in models if item.get("display_name") }
    for candidate in FREE_TIER_MODEL_CANDIDATES:
        item = by_normalized_name.get(candidate.casefold()) or by_display_name.get(candidate.casefold())
        if item and _model_supports_generation(item):
            return _canonical_model_id(str(item["name"] or candidate)), f"matched free-tier candidate {candidate}"
    configured_norm = _normalize_model_name(configured_model)
    configured_item = by_normalized_name.get(configured_norm) or by_display_name.get(configured_norm)
    if configured_item and _model_supports_generation(configured_item):
        return _canonical_model_id(str(configured_item["name"] or configured_model)), "configured model is supported and usable"
    return None, "no verified free-tier Gemini model was returned by the model listing"


class GeminiOpenAICompatibleProvider:
    provider_name = "gemini_openai_compatible"
    prompt_version = "transform-v2-grounded-duration-aware"

    def __init__(self, config: GeminiTranslationConfig, key_index: int = 0) -> None:
        self._keys = load_gemini_secret_lines(config)
        self._active_key = _select_active_secret_line(self._keys, key_index=key_index)
        self.config = config
        self.model = config.model
        self._credential_ref = None

    def transform_block(self, request: TranslationBlockRequest) -> TranslationBlockResult:
        payload = _request_payload(self.provider_name, self.model, request, self.prompt_version)
        request_hash = build_request_hash(payload)
        cached = read_cached_response(self.provider_name, request_hash)
        if cached is not None:
            return TranslationBlockResult(
                self.provider_name, self.model, request_hash, "hit", cached, self._credential_ref
            )

        response = self._call_with_failover(payload)
        write_cached_response(self.provider_name, request_hash, response)
        return TranslationBlockResult(self.provider_name, self.model, request_hash, "miss", response, self._credential_ref)

    def _call_with_failover(self, payload: dict) -> dict:
        self._credential_ref = "gemini-key-1"
        try:
            return self._call(payload, self._active_key)
        except (httpx.HTTPStatusError, httpx.TransportError, KeyError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Gemini provider failed for the active credential.") from exc

    def _call(self, payload: dict, api_key: str) -> dict:
        endpoint = self.config.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return compact valid JSON only. No markdown. Schema: "
                        "{\"schema_version\":1,\"segments\":[{\"id\":\"\",\"translated_text\":\"\","
                        "\"spoken_text\":\"\",\"subtitle_text\":\"\",\"duration_budget_ms\":1,"
                        "\"status\":\"draft\",\"issues\":[],\"change_summary\":\"\"}],"
                        "\"transformation_log\":[]}. Preserve IDs and duration budgets. "
                        "Keep source, translation, spoken narration, and subtitle wording distinct. "
                        "Fit spoken text to each duration budget and do not invent unsupported facts."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            result = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
            result.raise_for_status()
        content = result.json()["choices"][0]["message"]["content"]
        return _parse_json_content(content)


class GeminiCaptionTranslator:
    provider_name = "gemini_caption_translation"
    prompt_version = "caption-translation-v1"
    schema_version = 1

    def __init__(
        self,
        config: GeminiTranslationConfig,
        *,
        key_index: int = 0,
        batch_size: int = 10,
        max_transient_retries: int = 1,
        max_schema_retries: int = 1,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._keys = load_gemini_secret_lines(config)
        self._active_key = _select_active_secret_line(self._keys, key_index=key_index)
        self.config = config
        self.model = config.model
        self.batch_size = max(1, min(batch_size, 20))
        self.max_transient_retries = max(0, min(max_transient_retries, 2))
        self.max_schema_retries = max(0, min(max_schema_retries, 1))
        self._transport = transport

    def translate(self, captions: list[dict[str, str]]) -> GeminiCaptionTranslationResult:
        _validate_caption_inputs(captions)
        translated: list[dict[str, str]] = []
        request_count = retry_count = cache_hits = cache_misses = 0
        input_tokens = output_tokens = 0
        request_ids: list[str] = []
        for offset in range(0, len(captions), self.batch_size):
            batch = captions[offset : offset + self.batch_size]
            request_payload = {
                "provider": self.provider_name,
                "model": self.model,
                "prompt_version": self.prompt_version,
                "schema_version": self.schema_version,
                "source_language": "zh",
                "target_language": "en-US",
                "captions": batch,
            }
            request_hash = build_request_hash(request_payload)
            cached = read_cached_response(self.provider_name, request_hash)
            if cached is not None:
                parsed = _validate_caption_response(cached, batch)
                cache_hits += 1
            else:
                parsed, metrics = self._request_batch(batch)
                write_cached_response(self.provider_name, request_hash, parsed)
                cache_misses += 1
                request_count += metrics["request_count"]
                retry_count += metrics["retry_count"]
                input_tokens += metrics["input_tokens"]
                output_tokens += metrics["output_tokens"]
                if metrics["request_id"]:
                    request_ids.append(metrics["request_id"])
            translated.extend(parsed["translations"])
        return GeminiCaptionTranslationResult(
            translations=translated,
            model=self.model,
            request_count=request_count,
            retry_count=retry_count,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_ids=request_ids,
        )

    def _request_batch(self, batch: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
        body = _caption_request_body(self.model, batch)
        last_error: Exception | None = None
        request_count = retry_count = 0
        transient_attempt = 0
        schema_attempt = 0
        while True:
            try:
                response = self._post(body, self._active_key)
                request_count += 1
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                if not isinstance(content, str) or content.strip().startswith("```"):
                    raise GeminiCaptionTranslationError(
                        "GEMINI_CAPTION_MARKDOWN_RESPONSE",
                        "Gemini returned markdown instead of structured JSON.",
                    )
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as exc:
                    if schema_attempt < self.max_schema_retries:
                        schema_attempt += 1
                        retry_count += 1
                        continue
                    raise GeminiCaptionTranslationError(
                        "GEMINI_CAPTION_INVALID_JSON",
                        "Gemini returned invalid JSON twice.",
                    ) from exc
                try:
                    validated = _validate_caption_response(parsed, batch)
                except GeminiCaptionTranslationError as exc:
                    if exc.retryable and schema_attempt < self.max_schema_retries:
                        schema_attempt += 1
                        retry_count += 1
                        continue
                    raise
                usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                return validated, {
                    "request_count": request_count,
                    "retry_count": retry_count,
                    "input_tokens": int(usage.get("prompt_tokens") or 0),
                    "output_tokens": int(usage.get("completion_tokens") or 0),
                    "request_id": str(response.headers.get("x-request-id") or ""),
                }
            except httpx.HTTPStatusError as exc:
                request_count += 1
                last_error = exc
                status = exc.response.status_code
                if status in {401, 403}:
                    raise GeminiCaptionTranslationError(
                        "GEMINI_CAPTION_AUTH_UNAVAILABLE",
                        "No configured Gemini credential authenticated successfully.",
                    ) from exc
                if status == 429 or status >= 500:
                    if transient_attempt < self.max_transient_retries:
                        transient_attempt += 1
                        retry_count += 1
                        time.sleep(min(0.5 * transient_attempt, 1.0))
                        continue
                    raise GeminiCaptionTranslationError(
                        "GEMINI_CAPTION_PROVIDER_UNAVAILABLE",
                        f"Gemini request failed with HTTP {status}.",
                    ) from exc
                raise GeminiCaptionTranslationError(
                    "GEMINI_CAPTION_HTTP_ERROR",
                    f"Gemini request failed with HTTP {status}.",
                ) from exc
            except httpx.TransportError as exc:
                request_count += 1
                last_error = exc
                if transient_attempt < self.max_transient_retries:
                    transient_attempt += 1
                    retry_count += 1
                    continue
                raise GeminiCaptionTranslationError(
                    "GEMINI_CAPTION_NETWORK_ERROR",
                    "Gemini request failed after a bounded network retry.",
                ) from exc
            except (KeyError, TypeError, ValueError) as exc:
                raise GeminiCaptionTranslationError(
                    "GEMINI_CAPTION_RESPONSE_CONTRACT_ERROR",
                    "Gemini response did not match the expected contract.",
                ) from exc

    def _post(self, body: dict[str, Any], api_key: str) -> httpx.Response:
        endpoint = self.config.base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=self.config.timeout_seconds, transport=self._transport) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            return response


class GeminiMultimodalCaptionResolver:
    provider_name = "gemini_multimodal_caption_resolution"
    prompt_version = "caption-multimodal-v1"
    schema_version = 1

    def __init__(
        self,
        config: GeminiTranslationConfig,
        *,
        key_index: int = 0,
        max_transient_retries: int = 1,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._keys = load_gemini_secret_lines(config)
        self._active_key = _select_active_secret_line(self._keys, key_index=key_index)
        self.config = config
        self.model = config.model
        self.max_transient_retries = max(0, min(max_transient_retries, 2))
        self._transport = transport

    def resolve(self, interval_request: dict[str, Any]) -> GeminiMultimodalResolutionResult:
        _validate_multimodal_input(interval_request)
        request_payload = _multimodal_request_body(self.model, interval_request)
        request_hash = build_request_hash(request_payload)
        cached = read_cached_response(self.provider_name, request_hash)
        if cached is not None:
            parsed = _validate_multimodal_response(cached, interval_request)
            return GeminiMultimodalResolutionResult(
                intervals=parsed["intervals"],
                model=self.model,
                request_count=0,
                retry_count=0,
                cache_hits=1,
                cache_misses=0,
                input_tokens=int(cached.get("usage", {}).get("prompt_tokens") or 0) if isinstance(cached.get("usage"), dict) else 0,
                output_tokens=int(cached.get("usage", {}).get("completion_tokens") or 0) if isinstance(cached.get("usage"), dict) else 0,
                request_ids=[str(cached.get("request_id") or "")] if cached.get("request_id") else [],
            )

        response_payload, metrics = self._request_interval(request_payload, interval_request)
        write_cached_response(self.provider_name, request_hash, response_payload)
        parsed = _validate_multimodal_response(response_payload, interval_request)
        return GeminiMultimodalResolutionResult(
            intervals=parsed["intervals"],
            model=self.model,
            request_count=metrics["request_count"],
            retry_count=metrics["retry_count"],
            cache_hits=0,
            cache_misses=1,
            input_tokens=metrics["input_tokens"],
            output_tokens=metrics["output_tokens"],
            request_ids=[metrics["request_id"]] if metrics["request_id"] else [],
        )

    def _request_interval(self, body: dict[str, Any], interval_request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        endpoint = _native_interactions_endpoint(self.config, self.model)
        request_count = retry_count = 0
        transient_attempt = 0
        while True:
            try:
                with httpx.Client(timeout=self.config.timeout_seconds, transport=self._transport) as client:
                    response = client.post(
                        endpoint,
                        headers={"x-goog-api-key": self._active_key, "Content-Type": "application/json"},
                        json=body,
                    )
                    response.raise_for_status()
                request_count += 1
                payload = response.json()
                text = _extract_interaction_text(payload)
                if not isinstance(text, str) or not text.strip():
                    raise GeminiCaptionTranslationError(
                        "GEMINI_MULTIMODAL_EMPTY_RESPONSE",
                        "Gemini returned an empty multimodal response.",
                    )
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise GeminiCaptionTranslationError(
                        "GEMINI_MULTIMODAL_INVALID_JSON",
                        "Gemini returned invalid multimodal JSON.",
                    ) from exc
                validated = _validate_multimodal_response(parsed, interval_request)
                usage = (
                    payload.get("usageMetadata")
                    if isinstance(payload.get("usageMetadata"), dict)
                    else {}
                )
                request_id = str(
                    response.headers.get("x-request-id")
                    or payload.get("request_id")
                    or payload.get("id")
                    or ""
                )
                return validated, {
                    "request_count": request_count,
                    "retry_count": retry_count,
                    "input_tokens": int(
                        usage.get("promptTokenCount")
                        or usage.get("prompt_tokens")
                        or 0
                    ),
                    "output_tokens": int(
                        usage.get("candidatesTokenCount")
                        or usage.get("completion_tokens")
                        or 0
                    ),
                    "request_id": request_id,
                }
            except httpx.HTTPStatusError as exc:
                request_count += 1
                status = exc.response.status_code
                if status in {401, 403}:
                    raise GeminiCaptionTranslationError(
                        "GEMINI_MULTIMODAL_AUTH_UNAVAILABLE",
                        "No configured Gemini credential authenticated successfully.",
                    ) from exc
                if status == 429 or status >= 500:
                    if transient_attempt < self.max_transient_retries:
                        transient_attempt += 1
                        retry_count += 1
                        time.sleep(min(0.5 * transient_attempt, 1.0))
                        continue
                    raise GeminiCaptionTranslationError(
                        "GEMINI_MULTIMODAL_PROVIDER_UNAVAILABLE",
                        f"Gemini request failed with HTTP {status}.",
                    ) from exc
                raise GeminiCaptionTranslationError(
                    "GEMINI_MULTIMODAL_HTTP_ERROR",
                    f"Gemini request failed with HTTP {status}.",
                ) from exc
            except httpx.TransportError as exc:
                request_count += 1
                if transient_attempt < self.max_transient_retries:
                    transient_attempt += 1
                    retry_count += 1
                    continue
                raise GeminiCaptionTranslationError(
                    "GEMINI_MULTIMODAL_NETWORK_ERROR",
                    "Gemini request failed after a bounded network retry.",
                ) from exc


def _native_interactions_endpoint(config: GeminiTranslationConfig, model: str) -> str:
    root = _native_api_root(config)
    return f"{root}/models/{model}:generateContent"


def _multimodal_request_body(model: str, interval_request: dict[str, Any]) -> dict[str, Any]:
    source_schema: dict[str, Any] = {"type": "string"}
    source_candidates = [
        str(value).strip()
        for value in interval_request.get("source_candidates", [])
        if str(value).strip()
    ]
    if source_candidates:
        source_schema["enum"] = source_candidates
    schema = {
        "type": "object",
        "properties": {
            "intervals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "source_chinese": source_schema,
                        "english": {"type": "string"},
                        "confidence": {"type": "number"},
                        "evidence": {
                            "type": "object",
                            "properties": {
                                "visual": {"type": "boolean"},
                                "audio": {"type": "boolean"},
                                "context": {"type": "boolean"},
                            },
                            "required": ["visual", "audio", "context"],
                            "additionalProperties": False,
                        },
                        "uncertain_characters": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "id",
                        "source_chinese",
                        "english",
                        "confidence",
                        "evidence",
                        "uncertain_characters",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["intervals"],
        "additionalProperties": False,
    }
    input_parts = [{"text": interval_request["prompt"]}]
    for crop in interval_request["visual_crops"]:
        input_parts.append(
            {
                "inlineData": {
                    "data": crop["data"],
                    "mimeType": crop["mime_type"],
                }
            }
        )
    audio = interval_request["audio"]
    input_parts.append(
        {
            "inlineData": {
                "data": audio["data"],
                "mimeType": audio["mime_type"],
            }
        }
    )
    return {
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        "Return compact valid JSON only. No markdown. "
                        "Resolve the single caption interval using the provided crops, "
                        "audio and context. Keep the requested interval ID exactly once. "
                        "The evidence booleans mean that each supplied source was inspected; "
                        "audio may be silent or non-diagnostic and must still be considered. "
                        "Do not add commentary."
                    )
                }
            ]
        },
        "contents": [{"role": "user", "parts": input_parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }


def _extract_interaction_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return str(payload["output_text"])
    if isinstance(payload.get("text"), str):
        return str(payload["text"])
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        parts = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            for part in content.get("parts", []):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        if parts:
            return "".join(parts)
    raise GeminiCaptionTranslationError(
        "GEMINI_MULTIMODAL_RESPONSE_CONTRACT_ERROR",
        "Gemini response did not expose a usable text payload.",
    )


def _validate_multimodal_input(interval_request: dict[str, Any]) -> None:
    if not isinstance(interval_request, dict):
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_INVALID_INPUT",
            "Multimodal interval request must be a dictionary.",
        )
    interval_id = str(interval_request.get("id") or "")
    prompt = str(interval_request.get("prompt") or "").strip()
    visual_crops = interval_request.get("visual_crops")
    audio = interval_request.get("audio")
    if not interval_id or not prompt or not isinstance(visual_crops, list) or len(visual_crops) != 3 or not isinstance(audio, dict):
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_INVALID_INPUT",
            "Multimodal interval request is missing required media or context.",
        )


def _validate_multimodal_response(payload: dict[str, Any], interval_request: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("intervals"), list) or len(payload["intervals"]) != 1:
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_SCHEMA_ERROR",
            "Gemini response is missing the intervals array.",
            retryable=True,
        )
    requested_id = interval_request if isinstance(interval_request, str) else str(interval_request.get("id") or "")
    item = payload["intervals"][0]
    if not isinstance(item, dict):
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_SCHEMA_ERROR",
            "Gemini response contains a non-object interval.",
            retryable=True,
        )
    if str(item.get("id") or "") != requested_id:
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_ID_MAPPING_ERROR",
            "Gemini response ID does not match the requested interval.",
            retryable=True,
        )
    source_chinese = str(item.get("source_chinese") or "").strip()
    english = str(item.get("english") or "").strip()
    if not source_chinese or not english:
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_EMPTY_TEXT",
            "Gemini returned an empty source or English caption.",
        )
    source_candidates = {
        str(value).strip()
        for value in (
            interval_request.get("source_candidates", [])
            if isinstance(interval_request, dict)
            else []
        )
        if str(value).strip()
    }
    if source_candidates and source_chinese not in source_candidates:
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_SOURCE_CANDIDATE_MISMATCH",
            "Gemini returned source text outside the constrained candidates.",
        )
    confidence = float(item.get("confidence") or 0.0)
    if confidence < 0.0 or confidence > 1.0:
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_CONFIDENCE_RANGE",
            "Gemini returned an invalid confidence value.",
        )
    if confidence < 0.8:
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_LOW_CONFIDENCE",
            "Gemini did not resolve the caption with sufficient confidence.",
        )
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    if not all(bool(evidence.get(key)) for key in ("visual", "audio", "context")):
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_EVIDENCE_INCOMPLETE",
            "Gemini did not confirm all required evidence sources.",
        )
    uncertain = item.get("uncertain_characters") if isinstance(item.get("uncertain_characters"), list) else None
    if uncertain is None:
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_SCHEMA_ERROR",
            "Gemini response did not include an uncertain_characters array.",
            retryable=True,
        )
    unresolved = [str(value).strip() for value in uncertain if str(value).strip()]
    if unresolved:
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_UNRESOLVED_GLYPHS",
            "Gemini left unresolved caption characters.",
        )
    if (
        not re.search(r"[\u3400-\u9fff]", source_chinese)
        or any(marker in source_chinese for marker in {"\ufffd", "\u25a1", "\u2588"})
        or not re.search(r"[A-Za-z]", english)
        or re.search(r"[\u3400-\u9fff]", english)
    ):
        raise GeminiCaptionTranslationError(
            "GEMINI_MULTIMODAL_SEMANTIC_GUARD",
            "Gemini returned unresolved or non-subtitle text.",
        )
    return {
        "intervals": [
            {
                "id": requested_id,
                "source_chinese": source_chinese,
                "english": english,
                "confidence": confidence,
                "evidence": {
                    "visual": bool(evidence.get("visual")),
                    "audio": bool(evidence.get("audio")),
                    "context": bool(evidence.get("context")),
                },
                "uncertain_characters": unresolved,
            }
        ]
    }


def _caption_request_body(model: str, captions: list[dict[str, str]]) -> dict[str, Any]:
    schema = {
        "name": "caption_translations",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "translations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "english": {"type": "string"},
                        },
                        "required": ["id", "english"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["translations"],
            "additionalProperties": False,
        },
    }
    user_payload = {
        "captions": [
            {
                "id": item["id"],
                "chinese": item["source_text"],
                "previous_chinese": item.get("previous_source_text") or "",
                "next_chinese": item.get("next_source_text") or "",
            }
            for item in captions
        ]
    }
    return {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": schema},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Translate each Chinese dialogue caption into concise, natural en-US subtitle text. "
                    "Preserve meaning and one-to-one IDs. Previous and next captions are context only. "
                    "Do not summarize, explain, add facts, merge captions, or return markdown."
                ),
            },
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }


def _validate_caption_inputs(captions: list[dict[str, str]]) -> None:
    if not captions:
        raise GeminiCaptionTranslationError("GEMINI_CAPTION_EMPTY_INPUT", "No captions were provided.")
    ids: set[str] = set()
    for item in captions:
        caption_id = str(item.get("id") or "")
        source = str(item.get("source_text") or "")
        if not caption_id or caption_id in ids or not source:
            raise GeminiCaptionTranslationError(
                "GEMINI_CAPTION_INVALID_INPUT",
                "Caption IDs and source text must be present and unique.",
            )
        if any(marker in source for marker in {"\ufffd", "\u25a1", "\u2588"}):
            raise GeminiCaptionTranslationError(
                "GEMINI_CAPTION_OCR_UNRESOLVED",
                "Unresolved OCR glyphs must not be sent to Gemini.",
            )
        ids.add(caption_id)


def _validate_caption_response(payload: dict[str, Any], batch: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("translations"), list):
        raise GeminiCaptionTranslationError(
            "GEMINI_CAPTION_SCHEMA_ERROR",
            "Gemini response is missing the translations array.",
            retryable=True,
        )
    expected = [item["id"] for item in batch]
    items = payload["translations"]
    returned = [str(item.get("id") or "") for item in items if isinstance(item, dict)]
    if len(items) != len(expected) or returned != expected or len(set(returned)) != len(returned):
        raise GeminiCaptionTranslationError(
            "GEMINI_CAPTION_ID_MAPPING_ERROR",
            "Gemini response IDs do not exactly match the requested order.",
            retryable=True,
        )
    outputs: list[str] = []
    validated: list[dict[str, str]] = []
    for source_item, item in zip(batch, items):
        if set(item) != {"id", "english"}:
            raise GeminiCaptionTranslationError(
                "GEMINI_CAPTION_SCHEMA_ERROR",
                "Gemini response contains unexpected fields.",
                retryable=True,
            )
        english = str(item.get("english") or "").strip()
        source = source_item["source_text"]
        if (
            not english
            or not re.search(r"[A-Za-z]", english)
            or re.search(r"[\u3400-\u9fff]", english)
            or re.search(
                r"(?i)\b(?:unintelligible|unreadable|unknown|inaudible|cannot determine)\b",
                english,
            )
            or "```" in english
            or len(english) > max(100, len(source) * 14)
            or re.search(r"(?i)(?:[a-z]:\\|https?://|\.mp4\b|system instruction)", english)
        ):
            raise GeminiCaptionTranslationError(
                "GEMINI_CAPTION_SEMANTIC_GUARD",
                "Gemini returned unsafe or non-subtitle content.",
            )
        outputs.append(english.casefold())
        validated.append({"id": item["id"], "english": english})
    if len(outputs) >= 4 and Counter(outputs).most_common(1)[0][1] > max(2, len(outputs) // 3):
        raise GeminiCaptionTranslationError(
            "GEMINI_CAPTION_REPEATED_GENERIC_OUTPUT",
            "Gemini repeated the same translation across unrelated captions.",
        )
    return {"translations": validated}


def load_gemini_translation_config(config_path: Path | None = None) -> GeminiTranslationConfig:
    settings = get_settings()
    env_path = Path(config_path) if config_path is not None else settings.root / "operator" / "translation_config.env"
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    try:
        run_config = json.loads(settings.run_config_path.read_text(encoding="utf-8"))
    except Exception:
        run_config = {}
    translation_config = run_config.get("translation") if isinstance(run_config, dict) else {}
    credential_name = values.get("TRANSLATION_CREDENTIAL_NAME") or (
        str(translation_config.get("credential_name") or "").strip() if isinstance(translation_config, dict) else ""
    )
    if not credential_name:
        credential_name = DEFAULT_GEMINI_CREDENTIAL_NAME
    return GeminiTranslationConfig(
        base_url=values["TRANSLATION_BASE_URL"],
        model=values["TRANSLATION_MODEL"],
        timeout_seconds=float(values.get("TRANSLATION_TIMEOUT_SECONDS", "180")),
        key_file=settings.root / values["TRANSLATION_KEY_FILE"],
        credential_name=credential_name,
        free_tier_evidence_path=Path(
            os.environ.get(
                "TASK41H_FREE_TIER_EVIDENCE_PATH",
                str(DEFAULT_FREE_TIER_EVIDENCE_PATH),
            )
        ),
    )


def discover_gemini_models(
    config: GeminiTranslationConfig | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> GeminiModelDiscoveryResult:
    config = config or load_gemini_translation_config()
    keys = load_gemini_secret_lines(config)
    active_key = _select_active_secret_line(keys)
    project_free_tier_verified = _free_tier_project_verified(config, active_key)
    request_payload = {
        "provider": GEMINI_DISCOVERY_PROVIDER,
        "prompt_version": GEMINI_DISCOVERY_PROMPT_VERSION,
        "base_url": _native_api_root(config),
        "configured_model": config.model,
        "candidate_models": list(FREE_TIER_MODEL_CANDIDATES),
        "credential_name": _selected_credential_name(config),
        "project_free_tier_verified": project_free_tier_verified,
    }
    request_hash = build_request_hash(request_payload)
    cached = read_cached_response(GEMINI_DISCOVERY_PROVIDER, request_hash)
    if cached is not None:
        return GeminiModelDiscoveryResult(
            status=str(cached.get("status") or "ok"),
            sanitized_models=list(cached.get("sanitized_models") or []),
            raw_count=int(cached.get("raw_count") or 0),
            selected_model=cached.get("selected_model"),
            selected_reason=cached.get("selected_reason"),
            free_tier_verified=bool(cached.get("free_tier_verified")),
        )

    endpoint = _native_api_root(config).rstrip("/") + "/models"
    try:
        with httpx.Client(timeout=config.timeout_seconds, transport=transport) as client:
            response = client.get(
                endpoint,
                headers={"x-goog-api-key": active_key, "Content-Type": "application/json"},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise GeminiCaptionTranslationError(
            "GEMINI_MODEL_DISCOVERY_HTTP_ERROR",
            f"Gemini model discovery failed with HTTP {exc.response.status_code}.",
        ) from exc
    except httpx.TransportError as exc:
        raise GeminiCaptionTranslationError(
            "GEMINI_MODEL_DISCOVERY_NETWORK_ERROR",
            "Gemini model discovery failed after a transport error.",
        ) from exc

    payload = response.json()
    raw_models = payload.get("models") if isinstance(payload, dict) and isinstance(payload.get("models"), list) else []
    sanitized_models = [
        _sanitized_model_entry(item)
        for item in raw_models
        if isinstance(item, dict)
    ]
    selected_model, selected_reason = _select_free_tier_model(sanitized_models, config.model)
    model_is_free_candidate = bool(
        selected_model
        and _normalize_model_name(str(selected_model))
        in {candidate.casefold() for candidate in FREE_TIER_MODEL_CANDIDATES}
    )
    free_tier_verified = bool(
        model_is_free_candidate and project_free_tier_verified
    )
    result = GeminiModelDiscoveryResult(
        status="ok" if free_tier_verified else "needs_review",
        sanitized_models=sanitized_models,
        raw_count=len(raw_models),
        selected_model=selected_model,
        selected_reason=selected_reason,
        free_tier_verified=free_tier_verified,
    )
    write_cached_response(
        GEMINI_DISCOVERY_PROVIDER,
        request_hash,
        {
            "status": result.status,
            "sanitized_models": result.sanitized_models,
            "raw_count": result.raw_count,
            "selected_model": result.selected_model,
            "selected_reason": result.selected_reason,
            "free_tier_verified": result.free_tier_verified,
        },
    )
    return result


def gemini_credential_status() -> dict[str, Any]:
    try:
        config = load_gemini_translation_config()
        source, keys = _credential_source_status(config)
        return {
            "configured": bool(keys),
            "count": len(keys),
            "model": config.model,
            "credential_name": _selected_credential_name(config),
            "credential_source": source,
        }
    except (FileNotFoundError, KeyError, ValueError):
        return {
            "configured": False,
            "count": 0,
            "model": None,
            "credential_name": None,
            "credential_source": "missing",
        }


def _request_payload(provider: str, model: str, request: TranslationBlockRequest, prompt_version: str) -> dict:
    return {
        "provider": provider,
        "model": model,
        "provider_request_version": "translation-v2",
        "prompt_version": prompt_version,
        "project_id": request.project_id,
        "market_profile_id": request.market_profile_id,
        "transformation_mode": request.transformation_mode,
        "target_locale": request.target_locale,
        "duration_budget_ms": request.duration_budget_ms,
        "segments": request.segments,
    }


def _parse_json_content(content: str) -> dict:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise
