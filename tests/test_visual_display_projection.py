from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.visual_display_projection import (
    VisualDisplayProjectionRepairError,
    repair_visual_display_projection,
)
from app.services.visual_replacement import build_tier_a_replacement_plan


FONT = Path(r"C:\Windows\Fonts\arial.ttf")


class _ProjectionTranslator:
    provider_name = "fake_projection"
    model = "fake-model"

    def __init__(self) -> None:
        self.requests = []

    def translate(self, captions, **_kwargs):
        self.requests = [dict(item) for item in captions]
        outputs = []
        for item in captions:
            current = item["current_english"]
            if current == "Country.":
                english = "Nation"
            elif current == "Protein building.":
                english = "Protein Bldg."
            else:
                raise AssertionError(current)
            outputs.append({"id": item["id"], "english": english})
        return SimpleNamespace(
            translations=outputs,
            model=self.model,
            request_count=1,
            retry_count=0,
            cache_hits=0,
            cache_misses=1,
        )


def _track(track_id: str, source: str, display: str, box: list[int]) -> dict:
    return {
        "track_id": track_id,
        "canonical_text": source,
        "semantic_translation": display,
        "display_text": display,
        "confidence": 0.98,
        "start_ms": 1000,
        "end_ms": 1500,
        "recognition": {"status": "verified", "method": "crop_transform_consensus"},
        "classification": {"action": "localize"},
        "observations": [{"bbox": box, "time_ms": 1000}],
    }


def test_visual_display_projection_repairs_only_fit_escalations_and_reuses_identical_labels() -> None:
    localized = {
        "tracks": [
            _track("country-a", "国", "Country.", [8, 471, 34, 504]),
            _track("country-b", "国", "Country.", [8, 471, 34, 504]),
            _track("protein-a", "蛋白楼", "Protein building.", [950, 1048, 972, 1062]),
            _track("protein-b", "蛋白楼", "Protein building.", [950, 1048, 974, 1062]),
            _track("protein-c", "蛋白楼", "Protein building.", [948, 1048, 972, 1062]),
        ]
    }
    initial = build_tier_a_replacement_plan(
        localized,
        output_width=1920,
        output_height=1080,
        font_path=FONT,
        sample_seconds=0.5,
    )
    assert initial["escalation_count"] == 5
    assert {item["reason"] for item in initial["escalations"]} == {"target_text_does_not_fit"}

    translator = _ProjectionTranslator()
    repaired = repair_visual_display_projection(localized, initial, translator=translator)
    assert len(translator.requests) == 2
    assert repaired["evidence"]["repaired_track_count"] == 5
    assert repaired["evidence"]["unique_projection_request_count"] == 2

    by_id = {item["track_id"]: item for item in repaired["localized"]["tracks"]}
    assert by_id["country-a"]["display_text"] == "Nation"
    assert by_id["country-a"]["semantic_translation"] == "Country."
    assert by_id["protein-a"]["display_text"] == "Protein Bldg."
    assert by_id["protein-a"]["semantic_translation"] == "Protein building."

    final = build_tier_a_replacement_plan(
        repaired["localized"],
        output_width=1920,
        output_height=1080,
        font_path=FONT,
        sample_seconds=0.5,
    )
    assert final["escalation_count"] == 0
    assert final["replacement_count"] == 5


def test_visual_display_projection_refuses_mixed_escalation_classes() -> None:
    localized = {"tracks": [_track("country", "国", "Country.", [8, 471, 34, 504])]}
    translator = _ProjectionTranslator()
    with pytest.raises(VisualDisplayProjectionRepairError) as exc:
        repair_visual_display_projection(
            localized,
            {"escalations": [{"track_id": "country", "reason": "unstable_geometry"}]},
            translator=translator,
        )
    assert exc.value.code == "visual_display_projection_not_applicable"
    assert translator.requests == []
