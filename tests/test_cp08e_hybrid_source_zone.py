from __future__ import annotations

from pathlib import Path

from app.services.cjk_cleanup import build_hybrid_source_event, source_event_to_interval, source_region_union, stabilize_sequence_plate_geometry


def test_cp08e_source_region_union_includes_punctuation_outline_and_padding():
    chinese = {"left_x": 420, "top_y": 622, "right_x": 880, "bottom_y": 682}
    punctuation = {"left_x": 310, "top_y": 668, "right_x": 350, "bottom_y": 686}
    shadow = {"left_x": 416, "top_y": 626, "right_x": 884, "bottom_y": 690}
    region = source_region_union([chinese, punctuation, shadow], padding_x=32, padding_y=16)
    assert region["x"] == 278
    assert region["right_x"] == 916
    assert region["y"] == 606
    assert region["bottom_y"] == 706


def test_cp08e_source_timing_uses_preroll_and_postroll_not_english_timing():
    event = build_hybrid_source_event(
        event_id="source_001",
        sequence_id="seq_a",
        start_time=10.0,
        end_time=12.0,
        source_boxes=[{"left_x": 300, "top_y": 620, "right_x": 900, "bottom_y": 690}],
        preroll_frames=5,
        postroll_frames=7,
    )
    assert event["start_time"] == 9.833
    assert event["end_time"] == 12.233
    interval = source_event_to_interval(event)
    assert interval["start_time"] == event["start_time"]
    assert interval["end_time"] == event["end_time"]


def test_cp08e_plate_does_not_shrink_for_short_english_translation():
    long_source = {"left_x": 220, "top_y": 620, "right_x": 1010, "bottom_y": 690}
    event = build_hybrid_source_event(event_id="source_long", sequence_id="seq_a", start_time=20, end_time=22, source_boxes=[long_source])
    short_english_width = 80
    assert event["plate_geometry"]["width"] > short_english_width * 4


def test_cp08e_sequence_stable_geometry_for_similar_one_line_and_two_line_events():
    first = build_hybrid_source_event(
        event_id="source_001",
        sequence_id="seq_stable",
        start_time=1,
        end_time=2,
        source_boxes=[{"left_x": 400, "top_y": 620, "right_x": 820, "bottom_y": 680}],
    )
    second = build_hybrid_source_event(
        event_id="source_002",
        sequence_id="seq_stable",
        start_time=2.1,
        end_time=3,
        source_boxes=[{"left_x": 410, "top_y": 618, "right_x": 835, "bottom_y": 686}],
    )
    stabilized = stabilize_sequence_plate_geometry([first, second])
    assert stabilized[0]["plate_geometry"] == stabilized[1]["plate_geometry"]
    assert all(event["stable_per_sequence_geometry"] for event in stabilized)


def test_cp08e_plate_active_during_english_cue_gap():
    event = build_hybrid_source_event(
        event_id="source_gap",
        sequence_id="seq_gap",
        start_time=30.0,
        end_time=33.0,
        source_boxes=[{"left_x": 350, "top_y": 620, "right_x": 930, "bottom_y": 688}],
    )
    english_end = 31.0
    assert event["end_time"] > english_end


def test_cp08e_no_full_width_plate_by_default():
    event = build_hybrid_source_event(
        event_id="source_local",
        sequence_id="seq_local",
        start_time=1,
        end_time=2,
        source_boxes=[{"left_x": 500, "top_y": 620, "right_x": 760, "bottom_y": 680}],
    )
    assert event["plate_geometry"]["width"] < 900


def test_cp08e_automatic_opacity_configuration_and_opaque_fallback_fields():
    event = build_hybrid_source_event(
        event_id="source_opacity",
        sequence_id="seq_opacity",
        start_time=1,
        end_time=2,
        source_boxes=[{"left_x": 300, "top_y": 620, "right_x": 900, "bottom_y": 688}],
        plate_opacity=0.92,
    )
    assert 0.82 <= event["plate_opacity"] <= 0.92
    assert event["suppression_method"] == "bounded_local_delogo_plus_stable_source_zone_plate"


def test_cp08e_operator_ui_exposes_hybrid_suppression_controls():
    js = Path("app/static/operator/app.js").read_text(encoding="utf-8")
    for label in [
        "Source Suppression",
        "Local blur/fill source suppression",
        "Emergency opaque fallback",
        "Preview source-suppressed layer",
        "English Subtitle Plate",
        "Plate enabled",
        "Opacity",
        "Horizontal padding",
        "Vertical padding",
        "Minimum width",
        "Maximum width",
        "Preview English composition",
        "Source suppression -> English subtitle layout -> final preview",
        "Preview source-suppressed visual",
        "Preview final composition",
        "Seek to source event",
        "Approve source suppression",
    ]:
        assert label in js
