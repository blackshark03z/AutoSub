from __future__ import annotations

from types import SimpleNamespace

import app.services.simple_workflow as workflow
from app.providers.translation.gemini import GeminiCaptionTranslationError, GeminiTranslationConfig


def _source_result():
    return {
        "cues": [
            {"cue_id": "A", "start_ms": 0, "end_ms": 1000, "text": "你好"},
            {"cue_id": "B", "start_ms": 1200, "end_ms": 2200, "text": "世界"},
        ],
        "metadata": {"source_language": "zh", "asr_provider": "fake_asr"},
    }


def test_quality_auto_full_mode_prefers_gemini_and_records_exact_provider(monkeypatch) -> None:
    captured = {}

    class FakeTranslator:
        provider_name = "gemini_caption_translation"
        model = "gemini-test"

        def __init__(self, _config):
            pass

        def translate(self, captions):
            assert [item["source_text"] for item in captions] == ["你好", "世界"]
            assert [item["duration_ms"] for item in captions] == ["1000", "1000"]
            assert [item["preferred_max_characters"] for item in captions] == ["17", "17"]
            assert [item["hard_max_characters"] for item in captions] == ["20", "20"]
            return SimpleNamespace(
                translations=[{"id": "A", "english": "Hello"}, {"id": "B", "english": "World"}],
                model=self.model,
                request_count=1,
                retry_count=0,
                cache_hits=0,
                cache_misses=1,
            )

    monkeypatch.setattr(workflow, "ensure_gemini_caption_ready", lambda: {"selected_model": "gemini-test"})
    monkeypatch.setattr(
        workflow,
        "load_gemini_translation_config",
        lambda: GeminiTranslationConfig(
            base_url="https://example.invalid/",
            model="old",
            timeout_seconds=1.0,
            key_file=__import__("pathlib").Path("unused.key"),
        ),
    )
    monkeypatch.setattr(workflow, "GeminiCaptionTranslator", FakeTranslator)
    monkeypatch.setattr(workflow, "translate_source_captions", lambda _texts: (_ for _ in ()).throw(AssertionError("offline fallback must not run")))

    def persist(_run_id, *, source_cues, translated_texts, metadata):
        captured.update(source_cues=source_cues, translated_texts=translated_texts, metadata=metadata)
        return {"track_id": "translated"}

    monkeypatch.setattr(workflow, "create_provider_translation_track", persist)
    result = workflow._create_source_result_translation_track(
        "run-1",
        _source_result(),
        "English",
        {"localization_mode": "full_visual_localization", "translation_policy": "quality_auto"},
    )
    assert result["track_id"] == "translated"
    assert captured["translated_texts"] == ["Hello", "World"]
    assert captured["metadata"]["translation_provider"] == "gemini_caption_translation"
    assert captured["metadata"]["translation_model"] == "gemini-test"
    assert captured["metadata"]["translation_fallback"] is False


def test_quality_auto_records_explicit_offline_fallback_when_gemini_unavailable(monkeypatch) -> None:
    captured = {}

    def gemini_unavailable():
        raise GeminiCaptionTranslationError("GEMINI_PROVIDER_UNAVAILABLE", "temporary failure")

    monkeypatch.setattr(workflow, "ensure_gemini_caption_ready", gemini_unavailable)
    monkeypatch.setattr(
        workflow,
        "translate_source_captions",
        lambda texts: [{"translated_text": "Local Hello"}, {"translated_text": "Local World"}],
    )

    def persist(_run_id, *, source_cues, translated_texts, metadata):
        captured.update(translated_texts=translated_texts, metadata=metadata)
        return {"track_id": "fallback"}

    monkeypatch.setattr(workflow, "create_provider_translation_track", persist)
    result = workflow._create_source_result_translation_track(
        "run-2",
        _source_result(),
        "English",
        {"localization_mode": "full_localization_voice", "translation_policy": "quality_auto"},
    )
    assert result["track_id"] == "fallback"
    assert captured["translated_texts"] == ["Local Hello", "Local World"]
    assert captured["metadata"]["translation_provider"] == "offline_translation"
    assert captured["metadata"]["translation_fallback"] is True
    assert captured["metadata"]["translation_fallback_reason"] == "GEMINI_PROVIDER_UNAVAILABLE"


def test_public_subtitle_mode_does_not_activate_v2_quality_translation_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AUTOSUB_ENABLE_QUALITY_TRANSLATION_V2", raising=False)
    assert workflow._quality_translation_v2_enabled({
        "localization_mode": "subtitle_localization",
        "translation_policy": "quality_auto",
    }) is False
