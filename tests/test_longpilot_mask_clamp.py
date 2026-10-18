"""Focused tests for the protected-ROI mask clamp used by the long pilot."""
from __future__ import annotations

from pathlib import Path

import pytest
from app.services.e2_dynamic_sub_mask import (
    PROTECTED_ROIS_REFERENCE,
    apply_protected_roi_clamp,
    clamp_mask_clear_of_rois,
    protected_roi_hits,
    scale_protected_rois,
)

FONT = Path(r"C:\Windows\Fonts\arial.ttf")

ROIS = scale_protected_rois(1280, 720)


def _mask(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h, "right_x": x + w - 1, "bottom_y": y + h - 1, "padding_px": 3}


def test_clamp_prefers_vertical_and_clears_badge():
    # wide cue whose padded bottom grazes the AI badge (top_y=649)
    mask = _mask(268, 579, 768, 82)  # bottom 660, right 1035
    assert protected_roi_hits({"mask": mask}, ROIS) == ["mask:ai_content_badge"]
    out, ops = clamp_mask_clear_of_rois(mask, ROIS)
    assert out is not None
    assert ops == ["ai_content_badge:clamp_bottom"]
    assert out["bottom_y"] == ROIS["ai_content_badge"]["top_y"] - 1
    assert out["width"] == mask["width"]  # full width preserved
    assert protected_roi_hits({"mask": out}, ROIS) == []


def test_clamp_leaves_unintersecting_mask_untouched():
    mask = _mask(400, 600, 480, 50)  # centered, far from both ROIs
    out, ops = clamp_mask_clear_of_rois(mask, ROIS)
    assert out == mask and ops == []


@pytest.mark.skipif(not FONT.exists(), reason="arial font unavailable")
def test_apply_clamp_promotes_recoverable_layout_with_valid_fit():
    good = {"segment_id": "cue_a", "status": "PROTECTED_ROI_INTERSECTION", "render_text": "Short",
            "raw_text": "Short", "target_font_size": 32, "min_font_size": 28,
            "mask": _mask(268, 579, 768, 82)}
    plan = {"font_size": 27, "protected_rois": ROIS, "layouts": [good], "output_width": 1280, "output_height": 720}
    plan, res = apply_protected_roi_clamp(plan, font_path=FONT)
    assert res["clamped"] == ["cue_a"] and res["unrecoverable"] == []
    assert good["status"] == "PASS"
    assert good["text_width"] <= good["mask"]["width"]
    assert plan["statuses"] == {"PASS": 1}


def test_apply_clamp_needs_review_when_no_fit_evidence():
    # No font provided -> no measured fit -> must NOT be promoted to a silent PASS.
    layout = {"segment_id": "cue_a", "status": "PROTECTED_ROI_INTERSECTION", "render_text": "Short",
              "mask": _mask(268, 579, 768, 82)}
    plan = {"font_size": 27, "protected_rois": ROIS, "layouts": [layout], "output_width": 1280,
            "output_height": 720}
    plan, res = apply_protected_roi_clamp(plan)
    assert res["clamped"] == ["cue_a"]
    assert layout["status"] == "NEEDS_REVIEW"
    assert plan["statuses"] == {"NEEDS_REVIEW": 1}


def test_apply_clamp_fails_closed_when_mask_cannot_clear():
    # mask fully covers the badge -> no single-edge clamp can clear it
    bad = {"segment_id": "cue_b", "status": "PROTECTED_ROI_INTERSECTION", "mask": _mask(940, 640, 330, 75)}
    plan = {"protected_rois": ROIS, "layouts": [bad], "output_width": 1280, "output_height": 720}
    plan, res = apply_protected_roi_clamp(plan)
    assert res["unrecoverable"] == ["cue_b"]
    assert bad["status"] == "PROTECTED_ROI_INTERSECTION"
    assert plan["statuses"] == {"PROTECTED_ROI_INTERSECTION": 1}


def test_uploader_logo_still_protected():
    mask = _mask(20, 40, 300, 90)  # overlaps uploader logo corner
    assert any("uploader_logo" in h for h in protected_roi_hits({"mask": mask}, ROIS))
    out, ops = clamp_mask_clear_of_rois(mask, ROIS)
    assert out is not None
    assert protected_roi_hits({"mask": out}, ROIS) == []
    assert any("uploader_logo" in o for o in ops)
    assert PROTECTED_ROIS_REFERENCE["uploader_logo"]["right_x"] == 272


@pytest.mark.skipif(not FONT.exists(), reason="arial font unavailable")
def test_clamp_never_silently_downscales_below_floor():
    # A long translation cannot fit the narrow clamped mask at an equivalent glyph
    # size; the layout must stay NEEDS_REVIEW, never be shrunk below the floor to PASS.
    layout = {
        "segment_id": "cue_wide", "status": "NEEDS_REVIEW",
        "render_text": "A very long English translation that cannot fit the narrow source mask at all",
        "raw_text": "A very long English translation that cannot fit the narrow source mask at all",
        "target_font_size": 40, "min_font_size": 35,
        "mask": _mask(268, 579, 768, 82), "source_bbox": {"left_x": 271, "top_y": 582, "right_x": 1032, "bottom_y": 657},
    }
    plan = {"font_size": 27, "safe_text_width": 1152, "protected_rois": ROIS, "layouts": [layout],
            "output_width": 1280, "output_height": 720}
    plan, res = apply_protected_roi_clamp(plan, font_path=FONT)
    assert layout["status"] == "NEEDS_REVIEW"
    assert res["unrecoverable"] == []
    if layout.get("font_size"):
        assert layout["font_size"] >= layout["min_font_size"]

