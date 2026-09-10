import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
import pytest
from fastapi import BackgroundTasks
from PIL import Image

import app.api.routes as routes
import app.providers.translation.gemini as gemini_module
from app.providers.translation.gemini import (
    GeminiCaptionTranslationError,
    GeminiCaptionTranslator,
    GeminiMultimodalCaptionResolver,
    GeminiTranslationConfig,
    discover_gemini_models,
    load_gemini_secret_lines,
)
import app.services.source_caption_translation as source_caption_module
from app.services.source_caption_translation import _has_solid_caption_glyph


def _config(tmp_path: Path, model: str = "gemini-test") -> GeminiTranslationConfig:
    key_file = tmp_path / "gemini_api.txt"
    key_file.write_text("fake-test-key\n", encoding="utf-8")
    return GeminiTranslationConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model=model,
        timeout_seconds=2,
        key_file=key_file,
    )


def _captions() -> list[dict[str, str]]:
    return [
        {
            "id": "OCR_0001",
            "source_text": "我来了",
            "previous_source_text": "",
            "next_source_text": "我需要赶快回到我的房间",
        },
        {
            "id": "OCR_0002",
            "source_text": "我需要赶快回到我的房间",
            "previous_source_text": "我来了",
            "next_source_text": "",
        },
    ]


def _response(translations: list[dict[str, str]], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"x-request-id": "request-safe-id"},
        json={
            "choices": [{"message": {"content": json.dumps({"translations": translations})}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8},
        },
    )


def _disable_real_cache(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    cache: dict[str, dict] = {}
    monkeypatch.setattr(gemini_module, "read_cached_response", lambda provider, request_hash: cache.get(request_hash))
    monkeypatch.setattr(
        gemini_module,
        "write_cached_response",
        lambda provider, request_hash, payload: cache.setdefault(request_hash, payload),
    )
    return cache


def test_structured_text_only_request_context_usage_and_secret_redaction(tmp_path, monkeypatch):
    cache = _disable_real_cache(monkeypatch)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return _response(
            [
                {"id": "OCR_0001", "english": "I'm here."},
                {"id": "OCR_0002", "english": "I need to hurry back to my room."},
            ]
        )

    translator = GeminiCaptionTranslator(_config(tmp_path), transport=httpx.MockTransport(handler))
    result = translator.translate(_captions())

    assert [item["english"] for item in result.translations] == [
        "I'm here.",
        "I need to hurry back to my room.",
    ]
    assert result.request_count == 1
    assert result.input_tokens == 20
    assert result.output_tokens == 8
    assert result.request_ids == ["request-safe-id"]
    serialized_request = json.dumps(seen[0], ensure_ascii=False)
    assert "previous_chinese" in serialized_request
    assert "next_chinese" in serialized_request
    assert ".mp4" not in serialized_request
    assert "source_path" not in serialized_request
    assert "fake-test-key" not in json.dumps(cache)


@pytest.mark.parametrize(
    ("translations", "reason"),
    [
        ([{"id": "OCR_0001", "english": "I'm here."}], "GEMINI_CAPTION_ID_MAPPING_ERROR"),
        (
            [
                {"id": "OCR_0001", "english": "I'm here."},
                {"id": "OCR_0001", "english": "Duplicate."},
            ],
            "GEMINI_CAPTION_ID_MAPPING_ERROR",
        ),
        (
            [
                {"id": "OCR_0001", "english": "I'm here."},
                {"id": "UNKNOWN", "english": "Unknown."},
            ],
            "GEMINI_CAPTION_ID_MAPPING_ERROR",
        ),
    ],
)
def test_invalid_id_mapping_retries_once_then_fails(tmp_path, monkeypatch, translations, reason):
    _disable_real_cache(monkeypatch)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(translations)

    translator = GeminiCaptionTranslator(_config(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(GeminiCaptionTranslationError, match="requested order") as exc_info:
        translator.translate(_captions())
    assert exc_info.value.reason_code == reason
    assert calls == 2


@pytest.mark.parametrize(
    "english",
    ["", "```json\n{}\n```", "我来了", "C:\\private\\video.mp4"],
)
def test_semantic_invalid_output_is_not_retried_or_cached(tmp_path, monkeypatch, english):
    cache = _disable_real_cache(monkeypatch)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(
            [
                {"id": "OCR_0001", "english": english},
                {"id": "OCR_0002", "english": "I need to hurry back to my room."},
            ]
        )

    translator = GeminiCaptionTranslator(_config(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(GeminiCaptionTranslationError):
        translator.translate(_captions())
    assert calls == 1
    assert cache == {}


@pytest.mark.parametrize("first_status", [429, 500, 503])
def test_transient_http_failure_has_one_bounded_retry(tmp_path, monkeypatch, first_status):
    _disable_real_cache(monkeypatch)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(first_status, json={"error": {"message": "temporary"}})
        return _response(
            [
                {"id": "OCR_0001", "english": "I'm here."},
                {"id": "OCR_0002", "english": "I need to hurry back to my room."},
            ]
        )

    translator = GeminiCaptionTranslator(_config(tmp_path), transport=httpx.MockTransport(handler))
    result = translator.translate(_captions())
    assert calls == 2
    assert result.retry_count == 1


def test_bad_request_batch_is_bisected_and_preserves_order(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)
    calls = 0
    captions = [
        {
            "id": f"OCR_{index:04d}",
            "source_text": text,
            "previous_source_text": "",
            "next_source_text": "",
        }
        for index, text in enumerate(["第一", "第二", "第三", "第四"], start=1)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        items = payload["captions"]
        if len(items) > 1:
            return httpx.Response(400, json={"error": {"message": "invalid argument"}})
        caption_id = items[0]["id"]
        return _response([{"id": caption_id, "english": f"Translated {caption_id}."}])

    translator = GeminiCaptionTranslator(
        _config(tmp_path),
        batch_size=4,
        max_transient_retries=0,
        max_schema_retries=0,
        transport=httpx.MockTransport(handler),
    )
    result = translator.translate(captions)

    assert [item["id"] for item in result.translations] == [item["id"] for item in captions]
    assert calls == 7
    assert result.request_count == 7
    assert result.retry_count == 3


def test_single_bad_request_falls_back_to_json_object(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)
    response_modes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        response_modes.append(body["response_format"]["type"])
        payload = json.loads(body["messages"][1]["content"])
        caption_id = payload["captions"][0]["id"]
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "invalid argument"}})
        return _response([{"id": caption_id, "english": "Hello there."}])

    translator = GeminiCaptionTranslator(
        _config(tmp_path),
        batch_size=1,
        max_transient_retries=0,
        max_schema_retries=0,
        transport=httpx.MockTransport(handler),
    )
    result = translator.translate([_captions()[0]])

    assert response_modes == ["json_schema", "json_object"]
    assert result.translations[0]["english"] == "Hello there."
    assert result.request_count == 2
    assert result.retry_count == 1


def test_single_bad_request_failure_keeps_metrics_and_caption_id(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "invalid argument"}})

    translator = GeminiCaptionTranslator(
        _config(tmp_path),
        batch_size=1,
        max_transient_retries=0,
        max_schema_retries=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GeminiCaptionTranslationError) as exc_info:
        translator.translate([_captions()[0]])

    assert exc_info.value.reason_code == "GEMINI_CAPTION_NATIVE_BAD_REQUEST"
    assert exc_info.value.request_count == 3
    assert exc_info.value.retry_count == 2
    assert exc_info.value.failed_caption_ids == ["OCR_0001"]


def test_caption_translator_uses_next_key_after_quota_failure(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)
    config = _config(tmp_path)
    config.key_file.write_text(
        "first-key-that-is-long-enough-0001\nsecond-key-that-is-long-enough-0002\n",
        encoding="utf-8",
    )
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen_auth.append(auth)
        if "first-key-that-is-long-enough-0001" in auth:
            return httpx.Response(429, json={"error": {"message": "quota"}})
        return _response(
            [
                {"id": "OCR_0001", "english": "I'm here."},
                {"id": "OCR_0002", "english": "I need to hurry back to my room."},
            ]
        )

    translator = GeminiCaptionTranslator(config, transport=httpx.MockTransport(handler))
    result = translator.translate(_captions())

    assert len(seen_auth) == 2
    assert "first-key-that-is-long-enough-0001" in seen_auth[0]
    assert "second-key-that-is-long-enough-0002" in seen_auth[1]
    assert result.retry_count == 1
    assert translator._active_key_index == 1


def test_caption_translator_skips_invalid_api_key_reported_as_http_400(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)
    config = _config(tmp_path)
    config.key_file.write_text(
        "quota-key-that-is-long-enough-0001\ninvalid-key-that-is-long-enough-0002\nworking-key-that-is-long-enough-0003\n",
        encoding="utf-8",
    )
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen_auth.append(auth)
        if "quota-key-that-is-long-enough-0001" in auth:
            return httpx.Response(429, json={"error": {"message": "quota"}})
        if "invalid-key-that-is-long-enough-0002" in auth:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": 400,
                        "message": "API key not valid. Please pass a valid API key.",
                        "status": "INVALID_ARGUMENT",
                    }
                },
            )
        return _response(
            [
                {"id": "OCR_0001", "english": "I'm here."},
                {"id": "OCR_0002", "english": "I need to hurry back to my room."},
            ]
        )

    translator = GeminiCaptionTranslator(config, transport=httpx.MockTransport(handler))
    result = translator.translate(_captions())

    assert len(seen_auth) == 3
    assert result.retry_count == 2
    assert translator._active_key_index == 2
    assert [item["english"] for item in result.translations] == [
        "I'm here.",
        "I need to hurry back to my room.",
    ]


def test_caption_translator_uses_native_fallback_after_compatible_http_400(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)
    config = _config(tmp_path)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url).endswith("/chat/completions"):
            return httpx.Response(
                400,
                json={"error": {"code": 400, "message": "Malformed request payload", "status": "INVALID_ARGUMENT"}},
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps({"translations": [{"id": "OCR_0001", "english": "I'm here."}]})}]}}
                ],
                "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4},
            },
        )

    translator = GeminiCaptionTranslator(config, transport=httpx.MockTransport(handler))
    result = translator.translate(_captions()[:1])

    assert result.translations == [{"id": "OCR_0001", "english": "I'm here."}]
    assert result.request_count == 3
    assert result.retry_count == 2
    assert calls[0].endswith("/chat/completions")
    assert calls[1].endswith("/chat/completions")
    assert calls[2].endswith("/models/gemini-test:generateContent")


def test_caption_translator_does_not_treat_generic_http_400_as_bad_key(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)
    config = _config(tmp_path)
    config.key_file.write_text(
        "first-key-that-is-long-enough-0001\nsecond-key-that-is-long-enough-0002\n",
        encoding="utf-8",
    )
    seen_headers: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append((request.headers.get("authorization", ""), request.headers.get("x-goog-api-key", "")))
        return httpx.Response(
            400,
            json={"error": {"code": 400, "message": "Malformed request payload", "status": "INVALID_ARGUMENT"}},
        )

    translator = GeminiCaptionTranslator(config, transport=httpx.MockTransport(handler))
    with pytest.raises(GeminiCaptionTranslationError) as exc_info:
        translator.translate(_captions()[:1])

    assert exc_info.value.reason_code == "GEMINI_CAPTION_NATIVE_BAD_REQUEST"
    assert translator._active_key_index == 0
    assert len(seen_headers) == 3  # json_schema + json_object + native, same key only
    assert all("second-key" not in auth and "second-key" not in native for auth, native in seen_headers)


def test_multimodal_resolver_key_selection_advances_once_and_sticks(tmp_path):
    config = _config(tmp_path)
    config.key_file.write_text(
        "first-key-that-is-long-enough-0001\nsecond-key-that-is-long-enough-0002\n",
        encoding="utf-8",
    )
    resolver = GeminiMultimodalCaptionResolver(config)

    assert resolver._active_key_index == 0
    assert resolver._advance_key() is True
    assert resolver._active_key_index == 1
    assert resolver._active_key == "second-key-that-is-long-enough-0002"
    assert resolver._advance_key() is False
    assert resolver._active_key_index == 1


def test_invalid_json_retries_once_then_fails(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})

    translator = GeminiCaptionTranslator(_config(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(GeminiCaptionTranslationError) as exc_info:
        translator.translate(_captions())
    assert exc_info.value.reason_code == "GEMINI_CAPTION_INVALID_JSON"
    assert calls == 2


def test_network_timeout_has_one_bounded_retry(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    translator = GeminiCaptionTranslator(_config(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(GeminiCaptionTranslationError) as exc_info:
        translator.translate(_captions())
    assert exc_info.value.reason_code == "GEMINI_CAPTION_NETWORK_ERROR"
    assert calls == 2


def test_cache_hit_skips_provider_and_context_or_model_change_misses(tmp_path, monkeypatch):
    cache = _disable_real_cache(monkeypatch)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(
            [
                {"id": "OCR_0001", "english": "I'm here."},
                {"id": "OCR_0002", "english": "I need to hurry back to my room."},
            ]
        )

    transport = httpx.MockTransport(handler)
    translator = GeminiCaptionTranslator(_config(tmp_path), transport=transport)
    assert translator.translate(_captions()).cache_misses == 1
    assert translator.translate(_captions()).cache_hits == 1
    changed = _captions()
    changed[0]["next_source_text"] = "不同上下文"
    translator.translate(changed)
    GeminiCaptionTranslator(_config(tmp_path, model="gemini-other"), transport=transport).translate(_captions())
    assert calls == 3
    assert cache


def test_unresolved_ocr_glyph_is_blocked_before_transport(tmp_path, monkeypatch):
    _disable_real_cache(monkeypatch)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response([])

    captions = _captions()
    captions[1]["source_text"] = "我需要赶快□到我的房间"
    translator = GeminiCaptionTranslator(_config(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(GeminiCaptionTranslationError) as exc_info:
        translator.translate(captions)
    assert exc_info.value.reason_code == "GEMINI_CAPTION_OCR_UNRESOLVED"
    assert calls == 0


def test_solid_missing_glyph_detection_is_geometry_based(tmp_path):
    image = np.zeros((100, 500), dtype=np.uint8)
    cv2.rectangle(image, (210, 20), (260, 75), 255, thickness=-1)
    path = tmp_path / "caption.png"
    assert cv2.imwrite(str(path), image)
    assert _has_solid_caption_glyph(
        path,
        {"left_x": 50, "top_y": 10, "right_x": 450, "bottom_y": 85},
    )


def test_start_route_accepts_once_and_queues_background(monkeypatch):
    monkeypatch.setattr(
        routes,
        "accept_processing",
        lambda run_id, key: {
            "run_id": run_id,
            "internal_state": "processing",
            "start_accepted": True,
            "idempotency_reused": False,
        },
    )
    monkeypatch.setattr(routes, "start_processing", lambda run_id, accepted=False: None)
    tasks = BackgroundTasks()
    payload = routes.simple_start_run("run-1", tasks, "same-key")
    assert payload["run"]["start_accepted"] is True
    assert len(tasks.tasks) == 1


def test_duplicate_start_does_not_queue_second_worker(monkeypatch):
    monkeypatch.setattr(
        routes,
        "accept_processing",
        lambda run_id, key: {
            "run_id": run_id,
            "internal_state": "processing",
            "start_accepted": False,
            "duplicate_prevented": True,
            "idempotency_reused": True,
        },
    )
    tasks = BackgroundTasks()
    payload = routes.simple_start_run("run-1", tasks, "same-key")
    assert payload["run"]["duplicate_prevented"] is True
    assert tasks.tasks == []


def test_gemini_secret_file_is_canonical_and_not_migrated(tmp_path, monkeypatch):
    secret_file = tmp_path / "gemini_api.txt"
    secret_file.write_text("fake-one\nfake-two\n", encoding="utf-8")
    monkeypatch.setattr(gemini_module, "_read_secure_gemini_secret_lines", lambda _name: ["legacy-secure-key"])
    config = GeminiTranslationConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-test",
        timeout_seconds=2,
        key_file=secret_file,
        credential_name="tool_auto_sub_worker_handoff_v0_2_gemini_api",
    )

    lines = load_gemini_secret_lines(config)

    assert lines == ["fake-one", "fake-two"]
    assert secret_file.exists()
    source, status_lines = gemini_module._credential_source_status(config)
    assert source == "secret_file"
    assert status_lines == ["fake-one", "fake-two"]


def test_gemini_secure_store_reads_native_credential_manager_without_pywin32(tmp_path, monkeypatch):
    secret_file = tmp_path / "missing_gemini_api.txt"
    monkeypatch.setattr(gemini_module, "win32cred", None)
    monkeypatch.setattr(
        gemini_module,
        "_read_native_gemini_secret_lines",
        lambda credential_name: ["fake-native-one", "fake-native-two"]
        if credential_name == "tool_auto_sub_worker_handoff_v0_2_gemini_api"
        else [],
    )
    config = GeminiTranslationConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-test",
        timeout_seconds=2,
        key_file=secret_file,
        credential_name="tool_auto_sub_worker_handoff_v0_2_gemini_api",
    )

    source, lines = gemini_module._credential_source_status(config)

    assert source == "legacy_secure_credential"
    assert lines == ["fake-native-one", "fake-native-two"]


def test_gemini_secret_file_precedes_native_credential_manager(tmp_path, monkeypatch):
    secret_file = tmp_path / "gemini_api.txt"
    secret_file.write_text("file-one\nfile-two\n", encoding="utf-8")
    monkeypatch.setattr(gemini_module, "win32cred", None)
    monkeypatch.setattr(gemini_module, "_read_native_gemini_secret_lines", lambda _name: ["legacy-one", "legacy-two"])
    config = GeminiTranslationConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-test",
        timeout_seconds=2,
        key_file=secret_file,
        credential_name="tool_auto_sub_worker_handoff_v0_2_gemini_api",
    )

    lines = load_gemini_secret_lines(config)

    assert lines == ["file-one", "file-two"]
    assert secret_file.exists()


def test_sample_frames_falls_back_to_ffmpeg_when_cv2_cannot_open_source(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake")
    frame_dir = tmp_path / "frames"

    class ClosedCapture:
        def isOpened(self):
            return False

        def release(self):
            return None

    class FakePopen:
        def __init__(self, command, **_kwargs):
            self.returncode = 0
            self.stderr = type("Stream", (), {"read": lambda self: ""})()
            output_pattern = Path(command[-1])
            output_pattern.parent.mkdir(parents=True, exist_ok=True)
            for index in range(3):
                image = Image.new("RGB", (32, 18), color=(index * 20, 0, 0))
                image.save(output_pattern.parent / f"frame_{index + 1:05d}.jpg", format="JPEG")

        def poll(self):
            return self.returncode

    monkeypatch.setattr(source_caption_module.cv2, "VideoCapture", lambda _: ClosedCapture())
    monkeypatch.setattr(source_caption_module.shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(source_caption_module.subprocess, "Popen", FakePopen)

    frames = source_caption_module._sample_frames(source, frame_dir, duration=1.2, step=0.5)

    assert [frame["time"] for frame in frames] == [0.0, 0.5, 1.0]
    assert all(frame["path"].exists() for frame in frames)
    assert len(frames) == 3


def test_sample_frames_falls_back_to_ffmpeg_when_cv2_jpeg_write_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake")
    frame_dir = tmp_path / "frames"

    class ReadableCapture:
        def isOpened(self):
            return True

        def set(self, *_args):
            return True

        def read(self):
            return True, np.zeros((18, 32, 3), dtype=np.uint8)

        def release(self):
            return None

    class FakePopen:
        def __init__(self, command, **_kwargs):
            self.returncode = 0
            self.stderr = type("Stream", (), {"read": lambda self: ""})()
            output_pattern = Path(command[-1])
            output_pattern.parent.mkdir(parents=True, exist_ok=True)
            for index in range(2):
                Image.new("RGB", (32, 18), color=(0, index * 20, 0)).save(
                    output_pattern.parent / f"frame_{index + 1:05d}.jpg",
                    format="JPEG",
                )

        def poll(self):
            return self.returncode

    monkeypatch.setattr(source_caption_module.cv2, "VideoCapture", lambda _: ReadableCapture())
    monkeypatch.setattr(source_caption_module.cv2, "imwrite", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(source_caption_module.shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(source_caption_module.subprocess, "Popen", FakePopen)

    frames = source_caption_module._sample_frames(source, frame_dir, duration=1.0, step=0.5)

    assert [frame["time"] for frame in frames] == [0.0, 0.5]
    assert all(frame["path"].exists() for frame in frames)


def test_multimodal_interval_request_falls_back_to_ffmpeg_when_cv2_cannot_open_source(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake")

    class ClosedCapture:
        def isOpened(self):
            return False

        def release(self):
            return None

    def fake_run(command, check, capture_output=False, text=False):
        frame_path = Path(command[-1])
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (128, 72), color=(12, 24, 36)).save(frame_path, format="JPEG")
        return None

    def fake_extract_audio(_source, output, **_kwargs):
        output.write_bytes(b"RIFFfake-wave")

    monkeypatch.setattr(source_caption_module.cv2, "VideoCapture", lambda _: ClosedCapture())
    monkeypatch.setattr(source_caption_module.shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(source_caption_module.subprocess, "run", fake_run)
    monkeypatch.setattr(source_caption_module, "extract_asr_audio", fake_extract_audio)

    request = source_caption_module._build_multimodal_interval_request(
        source,
        {
            "id": "OCR_0003",
            "start_time": 10.0,
            "end_time": 11.0,
            "source_text": "测试字幕",
            "previous_source_text": "上一句",
            "next_source_text": "下一句",
            "source_bbox": {"left_x": 30, "top_y": 42, "right_x": 92, "bottom_y": 64},
        },
        width=128,
        height=72,
    )

    assert request["id"] == "OCR_0003"
    assert len(request["visual_crops"]) == 3
    assert request["audio"]["data"]
    assert "测试字幕" in request["prompt"]


def test_free_tier_model_discovery_selects_supported_candidate_and_caches(tmp_path, monkeypatch):
    cache: dict[str, dict] = {}
    monkeypatch.setattr(gemini_module, "read_cached_response", lambda provider, request_hash: cache.get(request_hash))
    monkeypatch.setattr(
        gemini_module,
        "write_cached_response",
        lambda provider, request_hash, payload: cache.setdefault(request_hash, payload),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "fake-test-key"
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-3.5-flash",
                        "displayName": "Gemini 3.5 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                        "inputTokenLimit": 8192,
                        "outputTokenLimit": 8192,
                    },
                    {
                        "name": "models/gemini-2.0-pro",
                        "displayName": "Gemini 2.0 Pro",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                ]
            },
        )

    evidence_path = tmp_path / "free_tier_evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "status": "verified",
                "plan": "Free Tier",
                "billing_linked": False,
                "verification_method": "ai_studio_authenticated_ui",
                "project_fingerprint": "project:test",
                "active_key_fingerprint": gemini_module._active_key_fingerprint(
                    "fake-test-key"
                ),
            }
        ),
        encoding="utf-8",
    )
    config = replace(_config(tmp_path), free_tier_evidence_path=evidence_path)
    discovery = discover_gemini_models(config, transport=httpx.MockTransport(handler))

    assert discovery.free_tier_verified is True
    assert discovery.selected_model == "gemini-3.5-flash"
    assert discovery.raw_count == 2
    assert cache


def test_model_listing_does_not_claim_free_tier_without_project_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(gemini_module, "read_cached_response", lambda provider, request_hash: None)
    monkeypatch.setattr(gemini_module, "write_cached_response", lambda *args: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-3.6-flash",
                        "displayName": "Gemini 3.6 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        )

    config = replace(
        _config(tmp_path),
        free_tier_evidence_path=tmp_path / "missing-free-tier-evidence.json",
    )
    discovery = discover_gemini_models(config, transport=httpx.MockTransport(handler))

    assert discovery.selected_model == "gemini-3.6-flash"
    assert discovery.free_tier_verified is False
    assert discovery.status == "needs_review"


def test_model_discovery_uses_next_key_after_auth_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(gemini_module, "read_cached_response", lambda *_args: None)
    monkeypatch.setattr(gemini_module, "write_cached_response", lambda *_args: None)
    config = _config(tmp_path)
    config.key_file.write_text(
        "first-key-that-is-long-enough-0001\nsecond-key-that-is-long-enough-0002\n",
        encoding="utf-8",
    )
    seen_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("x-goog-api-key", "")
        seen_keys.append(key)
        if key == "first-key-that-is-long-enough-0001":
            return httpx.Response(403, json={"error": {"message": "denied"}})
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-flash",
                        "displayName": "Gemini 2.5 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        )

    discovery = discover_gemini_models(
        config,
        transport=httpx.MockTransport(handler),
        use_cache=False,
    )

    assert seen_keys == [
        "first-key-that-is-long-enough-0001",
        "second-key-that-is-long-enough-0002",
    ]
    assert discovery.selected_model == "gemini-2.5-flash"


def test_multimodal_resolver_uses_three_visual_crops_audio_and_json_schema(tmp_path, monkeypatch):
    cache: dict[str, dict] = {}
    monkeypatch.setattr(gemini_module, "read_cached_response", lambda provider, request_hash: cache.get(request_hash))
    monkeypatch.setattr(
        gemini_module,
        "write_cached_response",
        lambda provider, request_hash, payload: cache.setdefault(request_hash, payload),
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(
            200,
            json={
                "request_id": "request-safe-id",
                "output_text": json.dumps(
                    {
                        "intervals": [
                            {
                                "id": "OCR_0009",
                                "source_chinese": "太阳已经落下",
                                "english": "The sun has already set.",
                                "confidence": 0.93,
                                "evidence": {"visual": True, "audio": True, "context": True},
                                "uncertain_characters": [],
                            }
                        ]
                    }
                ),
            },
        )

    resolver = GeminiMultimodalCaptionResolver(_config(tmp_path), transport=httpx.MockTransport(handler))
    result = resolver.resolve(
        {
            "id": "OCR_0009",
            "prompt": "Resolve OCR_0009 with context.",
            "source_chinese": "太阳已经落下",
            "previous_source_chinese": "天色开始暗了",
            "next_source_chinese": "我们该回去了",
            "source_interval": {"start_time": 12.0, "end_time": 14.0},
            "source_bbox": {"left_x": 100, "top_y": 60, "right_x": 240, "bottom_y": 118},
            "visual_crops": [
                {"label": "start", "time": 12.0, "mime_type": "image/jpeg", "data": "ZmFrZQ=="},
                {"label": "mid", "time": 13.0, "mime_type": "image/jpeg", "data": "ZmFrZQ=="},
                {"label": "end", "time": 14.0, "mime_type": "image/jpeg", "data": "ZmFrZQ=="},
            ],
            "audio": {
                "data": "ZmFrZQ==",
                "mime_type": "audio/wav",
                "start_seconds": 11.75,
                "duration_seconds": 2.5,
            },
        }
    )

    assert result.request_count == 1
    assert result.request_ids == ["request-safe-id"]
    assert result.intervals[0]["id"] == "OCR_0009"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    parts = seen["body"]["contents"][0]["parts"]
    assert len(parts) == 5
    assert set(parts[0]) == {"text"}
    assert [item["inlineData"]["mimeType"] for item in parts[1:]] == [
        "image/jpeg",
        "image/jpeg",
        "image/jpeg",
        "audio/wav",
    ]
