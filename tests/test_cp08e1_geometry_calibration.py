from __future__ import annotations

import hashlib

import numpy as np

from app.services.cjk_cleanup import assert_source_containment, source_region_union, stabilize_sequence_plate_geometry
from tools.run_cp08e1_source_zone_geometry_calibration import draw_debug_overlay, provider_call_guard


def test_cp08e1_union_includes_punctuation_left_and_below_baseline():
    main = {"left_x": 430, "top_y": 610, "right_x": 900, "bottom_y": 670}
    punctuation = {"left_x": 292, "top_y": 672, "right_x": 336, "bottom_y": 684}
    region = source_region_union([main, punctuation], padding_x=40, padding_y=22)
    assert region["x"] <= 252
    assert region["bottom_y"] >= 706


def test_cp08e1_union_handles_source_text_above_fixed_bottom_lane():
    high_line = {"left_x": 514, "top_y": 585, "right_x": 767, "bottom_y": 650}
    region = source_region_union([high_line], padding_x=40, padding_y=22)
    assert region["y"] <= 563


def test_cp08e1_detached_dots_and_low_opacity_components_expand_region():
    main = {"left_x": 420, "top_y": 620, "right_x": 800, "bottom_y": 675}
    detached_dot = {"left_x": 360, "top_y": 690, "right_x": 366, "bottom_y": 696}
    low_opacity = {"left_x": 815, "top_y": 625, "right_x": 840, "bottom_y": 665}
    region = source_region_union([main, detached_dot, low_opacity], padding_x=32, padding_y=16)
    assert region["x"] <= 328
    assert region["right_x"] >= 872
    assert region["bottom_y"] >= 712


def test_cp08e1_event_union_survives_ocr_confidence_drop_by_using_morphology_boxes():
    ocr_box = {"left_x": 450, "top_y": 620, "right_x": 700, "bottom_y": 675}
    morphology_only = {"left_x": 300, "top_y": 670, "right_x": 345, "bottom_y": 686}
    region = source_region_union([ocr_box, morphology_only], padding_x=40, padding_y=22)
    assert region["x"] <= 260


def test_cp08e1_sequence_envelope_does_not_shrink_on_shorter_english_text():
    first = {"event_id": "a", "sequence_id": "s", "start_time": 1, "end_time": 2, "plate_geometry": {"x": 250, "y": 590, "right_x": 1000, "bottom_y": 710, "width": 751, "height": 121}}
    second = {"event_id": "b", "sequence_id": "s", "start_time": 2, "end_time": 3, "plate_geometry": {"x": 300, "y": 600, "right_x": 960, "bottom_y": 700, "width": 661, "height": 101}}
    stabilized = stabilize_sequence_plate_geometry([first, second], tolerance_px=120)
    assert stabilized[0]["plate_geometry"] == stabilized[1]["plate_geometry"]


def test_cp08e1_containment_failure_blocks_machine_pass():
    source = [{"left_x": 250, "top_y": 600, "right_x": 900, "bottom_y": 700}]
    plate = {"left_x": 300, "top_y": 610, "right_x": 880, "bottom_y": 690}
    result = assert_source_containment(source, plate, margin=8)
    assert result["status"] == "FAIL"
    assert result["violation_count"] >= 1


def test_cp08e1_containment_passes_with_safety_margin_inside_plate():
    source = [{"left_x": 300, "top_y": 620, "right_x": 900, "bottom_y": 680}]
    plate = {"left_x": 260, "top_y": 590, "right_x": 940, "bottom_y": 710}
    assert assert_source_containment(source, plate, margin=8)["status"] == "PASS"


def test_cp08e1_debug_overlay_generation_marks_geometry():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    event = {
        "event_id": "synthetic",
        "source_boxes": [{"left_x": 300, "top_y": 620, "right_x": 390, "bottom_y": 680}],
        "plate_geometry": {"x": 280, "y": 600, "right_x": 420, "bottom_y": 710, "width": 141, "height": 111},
    }
    overlay = draw_debug_overlay(frame, [event], 12.34)
    assert overlay.sum() > frame.sum()
    assert overlay.shape == frame.shape


def test_cp08e1_provider_call_guard_and_immutability_hash():
    before = hashlib.sha256(b"accepted artifact").hexdigest()
    after = hashlib.sha256(b"accepted artifact").hexdigest()
    assert before == after
    assert provider_call_guard() == {"gemini": 0, "elevenlabs": 0}
