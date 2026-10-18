"""Focused tests for the E2 dynamic per-cue subtitle mask (OD-03).

These tests pin the behaviours that fix the legacy giant-union mask:
- one mask per real cue (never a union), tight to that cue's bbox + 2-4px padding
- solid ``black@1.0`` mask colour, padding clamped to 2..4 px
- no mask during the gaps between cues
- protected platform regions (uploader logo, AI-content badge) fail closed
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import ImageFont

from app.services.e2_dynamic_sub_mask import (
    MASK_COLOR,
    STATUS_NEEDS_REVIEW,
    STATUS_PASS,
    STATUS_PROTECTED_ROI_INTERSECTION,
    build_dynamic_mask_plan,
    build_mask_filter,
    build_tight_mask,
    fit_overlay_text,
    load_cue_boxes,
    load_render_cues,
    mask_color_components,
    mask_padding_px,
    protected_roi_hits,
    rects_intersect,
    render_dynamic_mask_window,
    scale_protected_rois,
    window_cues,
)

REPO = Path(__file__).resolve().parents[1]
FONT_PATH = Path(r"C:\Windows\Fonts\arial.ttf")
SOURCE = Path(r"D:\AutoSub\scratch\live_pilot_run\source_part1_60s.mp4")
OCR_JSON = REPO / "scratch" / "e2_runtime" / "ocr_audit_60s_results.json"
EN_JSON = REPO / "scratch" / "e2_runtime" / "live" / "translations_en-US.json"


def _font(size: int = 27) -> ImageFont.FreeTypeFont:
    if FONT_PATH.exists():
        return ImageFont.truetype(str(FONT_PATH), size)
    return ImageFont.load_default()


def _bbox(left, top, right, bottom):
    return {"left_x": left, "top_y": top, "right_x": right, "bottom_y": bottom, "width": right - left + 1, "height": bottom - top + 1}


def _cues(*specs):
    return [
        {"cue_id": cid, "start_ms": start, "end_ms": end, "text": text}
        for cid, start, end, text in specs
    ]


def test_mask_padding_clamped_between_2_and_4_px():
    assert mask_padding_px(720) == 3
    assert mask_padding_px(1) == 2
    assert mask_padding_px(100000) == 4


def test_tight_mask_matches_bbox_plus_padding_exactly():
    bbox = _bbox(590, 599, 688, 649)
    mask = build_tight_mask(bbox, output_width=1280, output_height=720, pad_px=3)
    assert mask["width"] == (688 - 590 + 1) + 6
    assert mask["height"] == (649 - 599 + 1) + 6
    assert mask["x"] == 587 and mask["y"] == 596
    assert mask["padding_px"] == 3


def test_tight_mask_clamps_to_frame_edges():
    bbox = _bbox(0, 0, 40, 30)
    mask = build_tight_mask(bbox, output_width=1280, output_height=720, pad_px=3)
    assert mask["x"] == 0 and mask["y"] == 0


def test_rects_intersect_edge_touching_is_intersection():
    a = {"left_x": 0, "top_y": 0, "right_x": 10, "bottom_y": 10}
    b = {"left_x": 10, "top_y": 5, "right_x": 20, "bottom_y": 15}
    assert rects_intersect(a, b)
    c = {"left_x": 11, "top_y": 0, "right_x": 20, "bottom_y": 10}
    assert not rects_intersect(a, c)


def test_scale_protected_rois_identity_at_reference_resolution():
    rois = scale_protected_rois(1280, 720)
    assert rois["ai_content_badge"]["left_x"] == 956
    assert rois["uploader_logo"]["right_x"] == 272


def test_protected_roi_hits_detects_text_and_mask():
    rois = scale_protected_rois(1280, 720)
    layout = {
        "mask": {"x": 900, "y": 600, "width": 120, "height": 60, "right_x": 1019, "bottom_y": 659},
        "center_x": 960,
        "anchor_y": 630,
        "text_width": 200,
        "text_height": 40,
    }
    hits = protected_roi_hits(layout, rois)
    assert "mask:ai_content_badge" in hits


def test_fit_overlay_text_prefers_single_line_then_wraps():
    font = _font(27)
    single = fit_overlay_text("Short line", font, target_size=42, min_size=28, max_width=1152)
    assert single is not None and single["line_count"] == 1
    long_text = "This is a deliberately long english sentence that must wrap onto two lines eventually"
    wrapped = fit_overlay_text(long_text, font, target_size=48, min_size=20, max_width=420)
    assert wrapped is not None and wrapped["line_count"] == 2
    assert wrapped["text_width"] <= 420


def test_fit_overlay_text_returns_none_when_unfittable():
    font = _font(27)
    result = fit_overlay_text("x" * 400, font, target_size=60, min_size=40, max_width=100)
    assert result is None


def _plan_fixture():
    cues = _cues(
        ("cue_a", 0, 2000, "Hello there"),
        ("cue_b", 5000, 7000, "Second line here"),
    )
    boxes = {
        "cue_a": {"bbox": _bbox(500, 600, 600, 650), "confidence": 0.9},
        "cue_b": {"bbox": _bbox(400, 600, 700, 650), "confidence": 0.9},
    }
    return cues, boxes


def test_plan_emits_one_mask_per_cue_not_a_union(tmp_path: Path):
    if not SOURCE.exists():
        pytest.skip("source fixture unavailable")
    cues, boxes = _plan_fixture()
    plan = build_dynamic_mask_plan(SOURCE, cues, boxes, font_path=FONT_PATH)
    masks = [l["mask"] for l in plan["layouts"]]
    assert len(masks) == 2
    assert masks[0]["width"] == 101 + 6
    assert masks[1]["width"] == 301 + 6
    assert masks[0] != masks[1]
    assert plan["max_mask_width_px"] < 966


def test_no_mask_during_gap_between_cues(tmp_path: Path):
    if not SOURCE.exists():
        pytest.skip("source fixture unavailable")
    cues, boxes = _plan_fixture()
    plan = build_dynamic_mask_plan(SOURCE, cues, boxes, font_path=FONT_PATH)
    windows = [(l["start_time"], l["end_time"]) for l in plan["layouts"]]
    assert windows == [(0.0, 2.0), (5.0, 7.0)]
    # The 2s..5s gap carries no layout, so no mask can be drawn there.
    for start, end in windows:
        assert not (start < 3.0 < end)


def test_filter_uses_solid_black_exactly_one_opacity(tmp_path: Path):
    if not SOURCE.exists():
        pytest.skip("source fixture unavailable")
    cues, boxes = _plan_fixture()
    plan = build_dynamic_mask_plan(SOURCE, cues, boxes, font_path=FONT_PATH)
    assert all(l["status"] == STATUS_PASS for l in plan["layouts"])
    filter_value = build_mask_filter(plan, tmp_path / "x.ass")
    values = mask_color_components(filter_value)
    assert values, "expected drawbox masks in filter"
    assert all(v == 1.0 for v in values)
    assert MASK_COLOR == "black@1.0"


def test_render_fails_closed_on_protected_roi_intersection(tmp_path: Path):
    if not SOURCE.exists():
        pytest.skip("source fixture unavailable")
    cues = _cues(("cue_badge", 0, 2000, "Text that runs into the badge"))
    boxes = {"cue_badge": {"bbox": _bbox(1140, 640, 1275, 700), "confidence": 0.9}}
    plan = build_dynamic_mask_plan(SOURCE, cues, boxes, font_path=FONT_PATH)
    assert plan["layouts"][0]["status"] == STATUS_PROTECTED_ROI_INTERSECTION
    with pytest.raises(ValueError, match="PROTECTED_ROI_INTERSECTION"):
        render_dynamic_mask_window(SOURCE, plan, cues, output_path=tmp_path / "out.mp4")


def test_render_fails_closed_on_unconfirmed_cue(tmp_path: Path):
    if not SOURCE.exists():
        pytest.skip("source fixture unavailable")
    cues = _cues(("cue_missing", 0, 2000, "No box for me"))
    plan = build_dynamic_mask_plan(SOURCE, cues, {}, font_path=FONT_PATH)
    assert plan["layouts"][0]["status"] != STATUS_PASS
    with pytest.raises(ValueError):
        render_dynamic_mask_window(SOURCE, plan, cues, output_path=tmp_path / "out.mp4")


def test_window_cues_shifts_and_clips_to_window():
    cues = _cues(("a", 1000, 3000, "x"), ("b", 4000, 6000, "y"))
    win = window_cues(cues, start_s=2.0, end_s=5.0)
    assert [c["cue_id"] for c in win] == ["a", "b"]
    assert win[0]["start_ms"] == 0 and win[0]["end_ms"] == 1000
    assert win[1]["start_ms"] == 2000 and win[1]["end_ms"] == 3000


def test_real_ocr_cues_all_pass_and_are_tight():
    if not (SOURCE.exists() and OCR_JSON.exists() and EN_JSON.exists() and FONT_PATH.exists()):
        pytest.skip("live fixtures unavailable")
    cues = load_render_cues(EN_JSON)
    boxes = load_cue_boxes(OCR_JSON)
    plan = build_dynamic_mask_plan(SOURCE, cues, boxes, font_path=FONT_PATH)
    assert plan["statuses"].get(STATUS_PASS, 0) == len(cues) >= 19
    assert plan["max_mask_width_px"] <= 700
    assert all(l["mask"]["padding_px"] in (2, 3, 4) for l in plan["layouts"])
    # No cue mask may touch the AI-content badge band.
    badge = plan["protected_rois"]["ai_content_badge"]
    for layout in plan["layouts"]:
        mask = layout["mask"]
        assert mask["bottom_y"] < badge["top_y"] or mask["right_x"] < badge["left_x"]
