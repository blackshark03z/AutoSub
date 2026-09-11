from pathlib import Path

from app.services.visual_replacement import (
    build_tier_a_filter,
    build_tier_a_replacement_plan,
    write_tier_a_ass,
)

FONT = Path(r"C:\Windows\Fonts\arial.ttf")


def _track(track_id: str, text: str, *, confidence: float = 0.98, moving: bool = False) -> dict:
    boxes = [[100, 100, 260, 140], [102, 100, 262, 140], [103, 101, 263, 141]]
    if moving:
        boxes[-1] = [450, 300, 610, 340]
    return {
        "track_id": track_id,
        "canonical_text": "管理员",
        "display_text": text,
        "confidence": confidence,
        "start_ms": 1000,
        "end_ms": 2000,
        "classification": {"action": "localize"},
        "observations": [{"bbox": box} for box in boxes],
    }


def test_stable_track_builds_source_matched_tier_a_plan() -> None:
    plan = build_tier_a_replacement_plan(
        {"tracks": [_track("t1", "Administrator")]},
        output_width=1280,
        output_height=720,
        font_path=FONT,
        sample_seconds=0.5,
    )
    assert plan["replacement_count"] == 1
    assert plan["escalation_count"] == 0
    item = plan["replacements"][0]
    assert item["start_ms"] == 750
    assert item["end_ms"] == 2250
    assert item["tier"] == "A"
    assert item["center_y"] < 200


def test_unstable_track_uses_piecewise_geometry_while_low_confidence_still_escalates() -> None:
    plan = build_tier_a_replacement_plan(
        {"tracks": [_track("moving", "Administrator", moving=True), _track("weak", "Menu", confidence=0.5)]},
        output_width=1280,
        output_height=720,
        font_path=FONT,
        sample_seconds=0.5,
    )
    assert plan["replacement_count"] == 3
    assert {item["reason"] for item in plan["escalations"]} == {"low_ocr_confidence"}
    moving = [item for item in plan["replacements"] if item.get("parent_track_id") == "moving"]
    assert len(moving) == 3
    assert len({item["center_x"] for item in moving}) > 1


def test_verified_low_confidence_track_is_not_rejected_by_attention_threshold() -> None:
    track = _track("verified", "Administrator", confidence=0.76)
    track["recognition"] = {"status": "verified", "method": "crop_transform_consensus", "votes": 3}
    plan = build_tier_a_replacement_plan(
        {"tracks": [track]},
        output_width=1280,
        output_height=720,
        font_path=FONT,
        sample_seconds=0.5,
    )
    assert plan["replacement_count"] == 1
    assert plan["escalation_count"] == 0


def test_stationary_track_with_ocr_box_size_jitter_remains_tier_a() -> None:
    track = _track("jitter", "Everything")
    track["recognition"] = {"status": "verified", "method": "whole_frame_confidence"}
    track["observations"] = [
        {"bbox": [1485, 319, 1761, 394]},
        {"bbox": [1498, 326, 1706, 381]},
        {"bbox": [1496, 323, 1706, 386]},
        {"bbox": [1496, 323, 1706, 386]},
    ]
    plan = build_tier_a_replacement_plan(
        {"tracks": [track]},
        output_width=1920,
        output_height=1080,
        font_path=FONT,
        sample_seconds=0.5,
    )
    assert plan["replacement_count"] == 1
    assert plan["escalation_count"] == 0
    assert plan["replacements"][0]["mask"]["width"] >= 210


def test_tiny_source_ui_label_can_fit_at_source_relative_readable_floor() -> None:
    track = _track("tiny", "Protein building")
    track["recognition"] = {"status": "verified", "method": "crop_transform_consensus"}
    track["observations"] = [{"bbox": [948, 1048, 972, 1062]}]
    plan = build_tier_a_replacement_plan(
        {"tracks": [track]},
        output_width=1920,
        output_height=1080,
        font_path=FONT,
        sample_seconds=0.5,
    )
    assert plan["replacement_count"] == 1
    assert plan["escalation_count"] == 0
    item = plan["replacements"][0]
    assert item["font_size"] >= 9
    assert "\n" in item["render_text"]


def test_tier_a_ass_and_filter_use_track_position_and_bounded_mask(tmp_path) -> None:
    plan = build_tier_a_replacement_plan(
        {"tracks": [_track("t1", "Administrator")]},
        output_width=1280,
        output_height=720,
        font_path=FONT,
        sample_seconds=0.5,
    )
    ass = write_tier_a_ass(tmp_path / "visual.ass", plan, output_width=1280, output_height=720)
    text = ass.read_text(encoding="utf-8")
    assert "Administrator" in text
    assert "\\pos(" in text
    value = build_tier_a_filter(plan, ass)
    assert "delogo=" in value
    assert "between(t\\," in value
    assert "subtitles='" in value
