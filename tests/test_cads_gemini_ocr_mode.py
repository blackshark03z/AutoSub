from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.providers.translation import gemini
from app.providers.translation.gemini import GeminiCaptionTranslationError, GeminiTranslationConfig
from app.services import asr_models
from tests.test_cp10b_simple_workflow import _make_tiny_video, configure_test_root


def test_gemini_config_uses_safe_defaults_without_env_file(monkeypatch, tmp_path):
    class Settings:
        root = tmp_path
        run_config_path = tmp_path / "operator" / "run_config.json"

    Settings.run_config_path.parent.mkdir(parents=True)
    Settings.run_config_path.write_text(json.dumps({"translation": {}}), encoding="utf-8")
    monkeypatch.setattr(gemini, "get_settings", lambda: Settings())

    config = gemini.load_gemini_translation_config()

    assert config.base_url == gemini.DEFAULT_GEMINI_BASE_URL
    assert config.model == gemini.DEFAULT_GEMINI_MODEL
    assert config.timeout_seconds == gemini.DEFAULT_GEMINI_TIMEOUT_SECONDS
    assert config.key_file == tmp_path / gemini.DEFAULT_GEMINI_KEY_FILE
    assert config.credential_name == gemini.DEFAULT_GEMINI_CREDENTIAL_NAME


def test_gemini_keys_append_to_secret_file_without_replacing_or_echoing(monkeypatch, tmp_path):
    key_file = tmp_path / "secrets" / "gemini_api.txt"
    key_file.parent.mkdir(parents=True)
    key_file.write_text("legacy-key-that-is-long-enough-0001\n", encoding="utf-8")
    config = GeminiTranslationConfig(
        base_url=gemini.DEFAULT_GEMINI_BASE_URL,
        model=gemini.DEFAULT_GEMINI_MODEL,
        timeout_seconds=180.0,
        key_file=key_file,
        credential_name="autosub-test-gemini",
    )
    monkeypatch.setattr(gemini, "load_gemini_translation_config", lambda: config)
    monkeypatch.setattr(gemini, "_read_secure_gemini_secret_lines", lambda _name: [])

    result = gemini.save_gemini_secret_lines(
        [
            "legacy-key-that-is-long-enough-0001",
            "new-key-that-is-long-enough-0002",
            "new-key-that-is-long-enough-0002",
        ],
        append=True,
    )

    assert result["configured"] is True
    assert result["count"] == 2
    assert result["added_count"] == 1
    assert result["duplicate_count"] == 2
    assert result["credential_source"] == "secret_file"
    assert result["storage_path"] == gemini.DEFAULT_GEMINI_KEY_FILE
    assert key_file.read_text(encoding="utf-8").splitlines() == [
        "legacy-key-that-is-long-enough-0001",
        "new-key-that-is-long-enough-0002",
    ]
    assert "new-key-that-is-long-enough-0002" not in json.dumps(result)


def test_simple_gemini_credentials_endpoint_accepts_key_list_without_echo(monkeypatch):
    from app.api import routes

    captured = {}

    def fake_save(lines, *, append=True):
        captured["lines"] = list(lines)
        captured["append"] = append
        return {
            "configured": True,
            "count": 3,
            "added_count": 2,
            "duplicate_count": 1,
            "credential_source": "secret_file",
            "storage_path": gemini.DEFAULT_GEMINI_KEY_FILE,
        }

    monkeypatch.setattr(routes, "save_gemini_secret_lines", fake_save)
    payload = routes.GeminiCredentialRequest(
        api_keys=[
            "first-key-that-is-long-enough-0001",
            "second-key-that-is-long-enough-0002",
        ],
    )

    result = routes.simple_save_gemini_credentials(payload)

    assert captured == {
        "lines": [
            "first-key-that-is-long-enough-0001",
            "second-key-that-is-long-enough-0002",
        ],
        "append": True,
    }
    assert result["count"] == 3
    assert "first-key-that-is-long-enough-0001" not in json.dumps(result)
    assert "second-key-that-is-long-enough-0002" not in json.dumps(result)


def test_ocr_mode_does_not_claim_an_asr_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_models, "simple_ui_model_path", lambda: tmp_path / "unused-model")
    normalized = asr_models.normalize_simple_ui_settings(
        {
            "caption_mode": "source_caption_ocr_translation",
            "asr_provider": "legacy",
            "asr_model": "tiny",
            "asr_model_path": "C:/legacy/model",
        }
    )

    for key in ("asr_provider", "asr_model", "asr_model_path", "asr_model_source", "asr_model_policy"):
        assert key not in normalized


def test_simple_readiness_is_mode_aware(monkeypatch):
    from app.api import routes

    monkeypatch.setattr(
        routes,
        "runtime_readiness",
        lambda _root: {
            "runtime_root": "D:/AutoSub/runtime/managed",
            "status": "ready",
            "autosubs_runtime": {"state": "ready"},
            "autosubs_small_model": {"state": "ready"},
            "argos_runtime": {"state": "ready"},
            "argos_zh_en_model": {"state": "ready"},
        },
    )
    monkeypatch.setattr(routes, "get_ocr_runtime_status", lambda: {"available": True, "actionable_fix_message": "OCR ready"})
    monkeypatch.setattr(
        routes,
        "gemini_credential_status",
        lambda: {"configured": False, "count": 0, "model": gemini.DEFAULT_GEMINI_MODEL, "credential_source": "missing"},
    )

    status = routes.simple_runtime_readiness()

    assert status["mode_status"]["external_audio_transcription"]["status"] == "ready"
    assert status["mode_status"]["source_caption_ocr_translation"]["status"] == "not_ready"
    assert status["gemini_runtime"]["state"] == "missing"


def _new_ocr_run(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", raising=False)
    source = tmp_path / "ocr-source.mp4"
    _make_tiny_video(source)
    from app.db.session import init_db
    from app.services import simple_workflow

    init_db()
    monkeypatch.setattr(simple_workflow, "_disk_status", lambda _source: {"ok": True})
    run = simple_workflow.create_or_reuse_run(
        str(source),
        settings={"caption_mode": "source_caption_ocr_translation", "target_language": "English"},
    )
    return simple_workflow, run


def test_missing_ocr_fails_before_caption_analysis(monkeypatch, tmp_path):
    simple_workflow, run = _new_ocr_run(monkeypatch, tmp_path)
    worker_calls = []
    monkeypatch.setattr(
        simple_workflow,
        "get_ocr_runtime_status",
        lambda: {"available": False, "actionable_fix_message": "OCR runtime missing"},
    )
    monkeypatch.setattr(simple_workflow, "run_caption_analysis_worker", lambda *_args, **_kwargs: worker_calls.append(True))

    with pytest.raises(simple_workflow.RuntimeReadinessBlockedError, match="OCR runtime missing"):
        simple_workflow.start_processing(run["run_id"])

    failed = simple_workflow.get_run(run["run_id"])
    assert worker_calls == []
    assert failed["failure_category"] == "CAPTION_OCR_RUNTIME_FAILED"
    assert failed["result_eligible"] is False


def test_missing_gemini_fails_before_caption_analysis(monkeypatch, tmp_path):
    simple_workflow, run = _new_ocr_run(monkeypatch, tmp_path)
    worker_calls = []
    monkeypatch.setattr(simple_workflow, "get_ocr_runtime_status", lambda: {"available": True})
    monkeypatch.setattr(
        simple_workflow,
        "ensure_gemini_caption_ready",
        lambda: (_ for _ in ()).throw(
            GeminiCaptionTranslationError(
                "GEMINI_CREDENTIAL_MISSING",
                "Gemini API key is not configured. Add a key in AutoSub and retry.",
            )
        ),
    )
    monkeypatch.setattr(simple_workflow, "run_caption_analysis_worker", lambda *_args, **_kwargs: worker_calls.append(True))

    with pytest.raises(simple_workflow.RuntimeReadinessBlockedError, match="Gemini API key"):
        simple_workflow.start_processing(run["run_id"])

    failed = simple_workflow.get_run(run["run_id"])
    assert worker_calls == []
    assert failed["failure_category"] == "gemini_readiness_failed"
    assert failed["result_eligible"] is False


def test_ui_truthfully_exposes_ocr_gemini_and_settings_key_list():
    html = Path("app/static/simple/index.html").read_text(encoding="utf-8")
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")

    assert "Đọc phụ đề có sẵn — OCR + Gemini" in html
    assert 'data-flow-view="settings"' in html
    assert 'id="geminiKeysInput"' in html
    assert "Mỗi key một dòng" in html
    assert "key trùng được bỏ qua" in html
    assert "gemini_api.txt" in html
    assert 'id="geminiStoredCount"' in html
    assert 'id="geminiLastSaveSummary"' in html
    assert "/api/simple/gemini/credentials" in js
    assert "api_keys: keys" in js
    assert "Đã thêm ${added} key mới" in js
    assert "PaddleOCR đọc phụ đề trên máy" in js
    assert "Gemini sửa/giải nghĩa" in js
    assert 'value="source_caption_gemini_translation"' not in html


def test_gemini_preflight_bypasses_stale_discovery_cache_and_binds_key_fingerprint(monkeypatch, tmp_path):
    config = GeminiTranslationConfig(
        base_url=gemini.DEFAULT_GEMINI_BASE_URL,
        model=gemini.DEFAULT_GEMINI_MODEL,
        timeout_seconds=10.0,
        key_file=tmp_path / "missing.txt",
        credential_name="autosub-test-gemini",
    )
    monkeypatch.setattr(gemini, "load_gemini_secret_lines", lambda _config: ["test-key-that-is-long-enough-0001"])
    monkeypatch.setattr(gemini, "_free_tier_project_verified", lambda *_args: False)
    monkeypatch.setattr(gemini, "read_cached_response", lambda *_args: {"selected_model": "stale-model"})
    monkeypatch.setattr(gemini, "write_cached_response", lambda *_args, **_kwargs: None)
    captured = {}
    real_build_hash = gemini.build_request_hash

    def capture_hash(payload):
        captured.update(payload)
        return real_build_hash(payload)

    monkeypatch.setattr(gemini, "build_request_hash", capture_hash)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
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

    result = gemini.discover_gemini_models(config, transport=httpx.MockTransport(handler), use_cache=False)

    assert calls, "live preflight must bypass a stale cached model listing"
    assert result.selected_model == "gemini-2.5-flash"
    assert captured["active_key_fingerprint"].startswith("sha256:")
    assert "test-key-that-is-long-enough-0001" not in json.dumps(captured)


def test_source_caption_policy_allows_owner_authorized_non_free_tier_model():
    source = Path("app/services/source_caption_translation.py").read_text(encoding="utf-8")
    assert "if not discovery.selected_model:" in source
    assert "if not discovery.free_tier_verified or not discovery.selected_model:" not in source
