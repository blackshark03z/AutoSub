"""Focused tests for the protected-ROI mask clamp used by the long pilot."""
from __future__ import annotations

from pathlib import Path

import pytest
from app.services.e2_dynamic_sub_mask import (
    PROTECTED_ROIS_REFERENCE,
    apply_protected_roi_clamp,
    clamp_mask_clear_of_rois,
    protected_roi_hits,
    refit_needs_review_layouts,
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


def test_apply_clamp_promotes_only_recoverable_layouts():
    good = {"segment_id": "cue_a", "status": "PROTECTED_ROI_INTERSECTION", "mask": _mask(268, 579, 768, 82)}
    plan = {"protected_rois": ROIS, "layouts": [good], "output_width": 1280, "output_height": 720}
    plan, res = apply_protected_roi_clamp(plan)
    assert res["clamped"] == ["cue_a"] and res["unrecoverable"] == []
    assert good["status"] == "PASS"
    assert plan["statuses"] == {"PASS": 1}


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
def test_refit_recovers_cue_that_would_collide_at_full_font():
    # Inflated tall source box -> over-large target font; a centered wide translation
    # would reach the AI badge at font 100 but fits at a capped size.
    layout = {
        "segment_id": "cue_wide", "status": "NEEDS_REVIEW", "render_text": "Great Sage, Noble Emperor, Great Emperor",
        "mask": {"x": 443, "y": 556, "width": 392, "height": 110, "right_x": 834, "bottom_y": 665, "padding_px": 3},
        "center_x": 639, "anchor_y": 611, "source_bbox": {"left_x": 446, "top_y": 559, "right_x": 831, "bottom_y": 662},
    }
    plan = {"font_size": 27, "max_text_width": 1152, "protected_rois": ROIS, "layouts": [layout]}
    done = refit_needs_review_layouts(plan, FONT)
    assert done == ["cue_wide"]
    assert layout["status"] == "PASS" and layout["refit"] is True
    assert layout["text_width"] <= 1152


@pytest.mark.skipif(not FONT.exists(), reason="arial font unavailable")
def test_refit_leaves_unfittable_cue_needs_review():
    layout = {"segment_id": "cue_x", "status": "NEEDS_REVIEW", "render_text": "x" * 400,
              "mask": _mask(400, 600, 480, 50), "center_x": 640, "anchor_y": 625, "source_bbox": _mask(400, 600, 480, 50)}
    plan = {"font_size": 27, "max_text_width": 200, "protected_rois": ROIS, "layouts": [layout]}
    done = refit_needs_review_layouts(plan, FONT)
    assert done == [] and layout["status"] == "NEEDS_REVIEW"

