from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.narration_pipeline import build_narration_timeline
from app.services.narration_repair import NarrationProjectionRepairError, repair_narration_spoken_projection
from app.services.tts_units import attach_tts_synthesis_units


class _Translator:
    provider_name = "gemini_caption_translation"

    def __init__(self, outputs: dict[str, str]):
        self.outputs = outputs
        self.requests = []

    def translate(self, captions):
        self.requests.extend(captions)
        return SimpleNamespace(
            translations=[{"id": item["id"], "english": self.outputs[item["id"]]} for item in captions],
            model="gemini-test",
            request_count=1,
            retry_count=0,
            cache_hits=0,
            cache_misses=1,
        )


def _resolved():
    return {
        "subtitle_provenance": "provider_transcription",
        "cues": [
            {
                "cue_id": "c1",
                "start_ms": 0,
                "end_ms": 4000,
                "source_text": "这会直接增加2500点吸引力。",
                "translation_text": "It'll directly boost my attractiveness by 2500.",
                "resolved_text": "It'll directly boost my attractiveness by 2500.",
            },
            {
                "cue_id": "c2",
                "start_ms": 4050,
                "end_ms": 8000,
                "source_text": "看，我这边脸部轮廓很清晰，看起来很帅。",
                "translation_text": "Look, my face here is well-defined. It looks really handsome.",
                "resolved_text": "Look, my face here is well-defined. It looks really handsome.",
            },
        ],
    }


def _failure(resolved, *, fit_fraction=0.99):
    timeline = build_narration_timeline(resolved, source_duration_ms=8050)
    unit = attach_tts_synthesis_units(timeline)[0]
    duration = 8000.0
    return {
        "tts_unit_id": unit["id"],
        "available_ms": duration * fit_fraction,
        "tts_duration_ms": duration,
        "overflow_ms": duration * (1 - fit_fraction),
    }


def test_bounded_repair_changes_only_spoken_projection_and_preserves_numbers():
    resolved = _resolved()
    translator = _Translator({
        "c1": "It'll boost my attractiveness by 2500.",
        "c2": "Look, my face is defined and really handsome.",
    })
    result = repair_narration_spoken_projection(
        resolved,
        [_failure(resolved)],
        source_duration_ms=8050,
        translator=translator,
    )

    cues = result["resolved"]["cues"]
    assert cues[0]["spoken_text"] == "It'll boost my attractiveness by 2500."
    assert cues[0]["resolved_text"] == resolved["cues"][0]["resolved_text"]
    assert cues[0]["translation_text"] == resolved["cues"][0]["translation_text"]
    assert "2500" in cues[0]["spoken_text"]
    assert result["evidence"]["status"] == "PASS"
    assert result["evidence"]["repaired_cue_count"] == 2
    assert result["evidence"]["unit_budgets"]
    unit_budget = next(iter(result["evidence"]["unit_budgets"].values()))
    assert unit_budget["after_characters"] <= unit_budget["hard_max_characters"]
    assert unit_budget["after_characters"] < unit_budget["before_characters"]


def test_unit_budget_allows_small_per_cue_budget_miss_when_the_synthesized_unit_is_short_enough():
    resolved = _resolved()
    translator = _Translator({
        "c1": "It directly boosts my attractiveness by 2500.",
        "c2": "Look, my face is defined and really handsome.",
    })
    result = repair_narration_spoken_projection(
        resolved,
        [_failure(resolved)],
        source_duration_ms=8050,
        translator=translator,
    )
    unit_budget = next(iter(result["evidence"]["unit_budgets"].values()))
    assert unit_budget["after_characters"] <= unit_budget["hard_max_characters"]


def test_bounded_repair_rejects_numeric_material_fact_change():
    resolved = _resolved()
    translator = _Translator({
        "c1": "It'll boost my attractiveness by 2000.",
        "c2": "Look, my face is defined and really handsome.",
    })
    with pytest.raises(NarrationProjectionRepairError) as exc:
        repair_narration_spoken_projection(
            resolved,
            [_failure(resolved)],
            source_duration_ms=8050,
            translator=translator,
        )
    assert exc.value.code == "narration_repair_material_fact_changed"


def test_bounded_repair_rejects_large_condensation_requirement_before_provider_call():
    resolved = _resolved()
    translator = _Translator({})
    with pytest.raises(NarrationProjectionRepairError) as exc:
        repair_narration_spoken_projection(
            resolved,
            [_failure(resolved, fit_fraction=0.70)],
            source_duration_ms=8050,
            translator=translator,
        )
    assert exc.value.code == "narration_repair_too_large"
    assert translator.requests == []
