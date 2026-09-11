from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.localization_quality import evaluate_english_dialogue_cues
from app.services.subtitle_projection_repair import SubtitleProjectionRepairError, repair_subtitle_display_projection


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


def _cue(cue_id: str, start_ms: int, end_ms: int, source: str, target: str) -> dict:
    return {"cue_id": cue_id, "start_ms": start_ms, "end_ms": end_ms, "source_text": source, "translation_text": target, "resolved_text": target}


def test_real_120s_style_reading_speed_failure_repairs_display_only() -> None:
    item = _cue("ASR_00055", 94240, 95630, "他们就没有我帅气了", "They won't be as handsome as me.")
    evaluation = evaluate_english_dialogue_cues([item])
    translator = _Translator({"ASR_00055": "They won't match my looks."})
    result = repair_subtitle_display_projection({"cues": [item]}, evaluation, translator=translator)
    repaired = result["resolved"]["cues"][0]
    assert repaired["translation_text"] == "They won't be as handsome as me."
    assert repaired["resolved_text"] == "They won't match my looks."
    assert repaired["subtitle_text"] == "They won't match my looks."
    assert evaluate_english_dialogue_cues([repaired])["eligible"] is True
    assert len(translator.requests) == 1


def test_numeric_material_fact_must_be_preserved() -> None:
    item = _cue("A", 0, 1200, "增加2500点吸引力", "Boost charm by 2500 points.")
    evaluation = evaluate_english_dialogue_cues([item])
    translator = _Translator({"A": "Boost charm by 2000 pts."})
    with pytest.raises(SubtitleProjectionRepairError) as exc:
        repair_subtitle_display_projection({"cues": [item]}, evaluation, translator=translator)
    assert exc.value.code == "subtitle_projection_repair_material_fact_changed"


def test_non_reading_speed_failure_refuses_provider_call() -> None:
    item = _cue("A", 0, 8000, "来源", "Line one\nLine two\nLine three")
    evaluation = evaluate_english_dialogue_cues([item])
    translator = _Translator({})
    with pytest.raises(SubtitleProjectionRepairError) as exc:
        repair_subtitle_display_projection({"cues": [item]}, evaluation, translator=translator)
    assert exc.value.code == "subtitle_projection_repair_not_applicable"
    assert translator.requests == []


def test_one_provider_pass_then_fail_closed_if_budget_is_still_missed() -> None:
    item = _cue("A", 0, 1390, "他们就没有我帅气了", "They won't be as handsome as me.")
    evaluation = evaluate_english_dialogue_cues([item])
    translator = _Translator({"A": "They definitely will not be as handsome as me."})
    with pytest.raises(SubtitleProjectionRepairError) as exc:
        repair_subtitle_display_projection({"cues": [item]}, evaluation, translator=translator)
    assert exc.value.code == "subtitle_projection_repair_budget_missed"
    assert len(translator.requests) == 1



def test_participant_polarity_and_modality_must_be_preserved() -> None:
    item = _cue("A", 0, 1410, "他们就没有我帅气了", "They won't be as handsome as me.")
    evaluation = evaluate_english_dialogue_cues([item])
    translator = _Translator({"A": "They're not as handsome."})
    with pytest.raises(SubtitleProjectionRepairError) as exc:
        repair_subtitle_display_projection({"cues": [item]}, evaluation, translator=translator)
    assert exc.value.code == "subtitle_projection_repair_material_fact_changed"
