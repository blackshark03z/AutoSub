from __future__ import annotations

from pathlib import Path

from tools.run_cp08e2_decoupled_suppression_english_plate import (
    build_english_plate_layouts,
    english_plate_config,
    english_plate_qa,
    provider_call_guard,
    source_suppression_config,
    source_suppression_qa,
    write_decoupled_ass,
)


def _layout(segment_id: str, text: str, width: int, *, start: float = 1.0, end: float = 2.0) -> dict:
    return {
        "segment_id": segment_id,
        "render_text": text,
        "center_x": 640,
        "anchor_y": 650,
        "line_count": max(1, text.count("\\N") + 1),
        "plate": {"x": 300, "y": 620, "width": width, "height": 58, "right_x": 300 + width - 1, "bottom_y": 677},
        "start_time": start,
        "end_time": end,
    }


def test_cp08e2_source_geometry_independent_from_english_geometry():
    source_event = {"event_id": "seg_long", "plate_geometry": {"width": 900, "height": 132}}
    layout = build_english_plate_layouts([_layout("seg_long", "Run!", 90)])[0]
    assert layout["plate"]["width"] < source_event["plate_geometry"]["width"]
    assert layout["source_independent"] is True


def test_cp08e2_short_english_cue_creates_compact_plate():
    layout = build_english_plate_layouts([_layout("seg_short", "Run!", 80)])[0]
    assert 180 <= layout["plate"]["width"] <= 260


def test_cp08e2_long_english_cue_creates_larger_plate():
    short = build_english_plate_layouts([_layout("short", "Run!", 80)])[0]
    long = build_english_plate_layouts([_layout("long", "This is a much longer English subtitle.", 760)])[0]
    assert long["plate"]["width"] > short["plate"]["width"]


def test_cp08e2_source_active_without_english_cue_creates_no_visible_english_plate():
    layouts = build_english_plate_layouts([])
    assert layouts == []


def test_cp08e2_english_cue_after_source_ends_still_has_plate():
    source_event = {"event_id": "seg_source", "start_time": 1.0, "end_time": 2.0, "plate_geometry": {"width": 400, "height": 90}}
    layout = build_english_plate_layouts([_layout("seg_english", "Still talking.", 260, start=2.2, end=3.0)])[0]
    qa = english_plate_qa([layout], [source_event])
    assert qa["english_only_plate_missing_count"] == 0
    assert qa["status"] == "PASS"


def test_cp08e2_source_suppression_remains_active_independently():
    source_events = [{"event_id": "seg_source", "start_time": 1.0, "end_time": 2.0}]
    qa = source_suppression_qa(source_events)
    assert qa["source_suppression_active_full_interval"] is True
    assert qa["visible_hard_black_rectangle_frames"] == 0


def test_cp08e2_no_hard_source_sized_rectangle_under_normal_mode():
    config = source_suppression_config()
    assert config["normal_mode_hard_black_rectangle"] is False
    assert config["emergency_opaque_fallback"]["enabled"] is False
    assert config["emergency_opaque_fallback"]["operator_visible"] is True


def test_cp08e2_no_empty_plate_and_no_timing_coupling():
    layout = build_english_plate_layouts([_layout("seg_text", "Hello.", 180)])[0]
    qa = english_plate_qa([layout], [])
    assert qa["empty_plate_count"] == 0
    assert qa["plate_timing_source"] == "english_cue_only"


def test_cp08e2_one_line_and_two_line_stability():
    one, two = build_english_plate_layouts([_layout("one", "Hello.", 180), _layout("two", "Line one\\NLine two", 420)])
    assert one["line_count"] == 1
    assert two["line_count"] == 2
    assert two["plate"]["height"] > one["plate"]["height"]


def test_cp08e2_ass_uses_separate_plate_and_text_layers(tmp_path):
    path = tmp_path / "decoupled.ass"
    layout = build_english_plate_layouts([_layout("seg_text", "Hello.", 180)])[0]
    write_decoupled_ass([layout], path)
    ass = path.read_text(encoding="utf-8")
    assert "Style: Plate" in ass
    assert "Dialogue: 1" in ass
    assert "Dialogue: 2" in ass
    assert "\\p1" in ass


def test_cp08e2_final_composition_uses_source_suppressed_intermediate():
    script = Path("tools/run_cp08e2_decoupled_suppression_english_plate.py").read_text(encoding="utf-8")
    assert "cp08e2_decoupled_source_suppressed_720p.mp4" in script
    assert "render_final_composition(source_suppressed" in script


def test_cp08e2_canonical_layer_lineage_and_no_provider_calls():
    assert provider_call_guard() == {"gemini": 0, "elevenlabs": 0}
    assert english_plate_config()["timing"] == "English cue timing only"
