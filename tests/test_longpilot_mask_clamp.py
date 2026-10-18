"""Focused tests for the adaptive-union mask plate and protected-ROI clamp.

The plate for every cue is the tight union of the source Chinese glyph box and the
measured rendered English glyph box at source-equivalent size.  It must always cover
the source caption and the overlay text, stay inside the safe frame (never a
full-width band), and never intrude on a protected region (uploader logo / AI badge).
A cue that cannot satisfy all three is reported NEEDS_REVIEW with measured evidence.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import ImageFont

from app.services.e2_dynamic_sub_mask import (
    PROTECTED_ROIS_REFERENCE,
    clamp_mask_clear_of_rois,
    layout_review_evidence,
    layout_union_overlay,
    multiline_text_box,
    protected_roi_hits,
    scale_protected_rois,
)

FONT = Path(r"C:\Windows\Fonts\arial.ttf")

ROIS = scale_protected_rois(1280, 720)


def _mask(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h, "right_x": x + w - 1, "bottom_y": y + h - 1, "padding_px": 3}


def _bbox(left, top, right, bottom):
    return {"left_x": left, "top_y": top, "right_x": right, "bottom_y": bottom,
            "width": right - left + 1, "height": bottom - top + 1}


def _font(size: int = 27) -> ImageFont.FreeTypeFont:
    if FONT.exists():
        return ImageFont.truetype(str(FONT), size)
    return ImageFont.load_default()


def _contains(outer, inner):
    return (outer["left_x"] <= inner["left_x"] and outer["top_y"] <= inner["top_y"]
            and outer["right_x"] >= inner["right_x"] and outer["bottom_y"] >= inner["bottom_y"])


def test_clamp_prefers_vertical_and_clears_badge():
    # wide cue whose padded bottom grazes the AI badge (top_y=653)
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


def test_badge_roi_matches_measured_glyph_box():
    badge = PROTECTED_ROIS_REFERENCE["ai_content_badge"]
    assert badge["left_x"] == 960 and badge["top_y"] == 653
    assert badge["right_x"] == 1279 and badge["bottom_y"] == 719


@pytest.mark.skipif(not FONT.exists(), reason="arial font unavailable")
def test_union_plate_covers_source_and_english_at_equivalent_size():
    src = _bbox(590, 599, 688, 649)  # short Chinese caption, char height 51
    placement = layout_union_overlay(
        "Hi", src_bbox=src, base_font=_font(27), target_size=49, rois=ROIS,
        output_width=1280, output_height=720, pad_px=3, safe_text_width=1152,
    )
    assert placement is not None
    mask, font_size = placement["mask"], placement["font_size"]
    # Equivalent size: never silently shrunk below the source glyph height.
    assert font_size == 49
    assert mask["width"] > 0 and mask["height"] > 0
    assert mask["width"] <= 1152
    # Plate is the tight UNION: covers the full source box ...
    assert _contains(mask, src)
    # ... and the measured English glyph box.
    tw, th = placement["text_width"], placement["text_height"]
    trect = {"left_x": placement["center_x"] - tw // 2, "top_y": placement["anchor_y"] - th // 2,
             "right_x": placement["center_x"] - tw // 2 + tw - 1, "bottom_y": placement["anchor_y"] - th // 2 + th - 1}
    assert _contains(mask, trect)
    # Tight union, not a full-width band.
    assert mask["width"] < 1280
    assert protected_roi_hits({"mask": mask}, ROIS) == []


@pytest.mark.skipif(not FONT.exists(), reason="arial font unavailable")
def test_union_plate_wraps_long_translation_to_two_lines():
    src = _bbox(409, 587, 609, 638)
    text = "This is a deliberately long english sentence that must wrap onto two lines eventually"
    placement = layout_union_overlay(
        text, src_bbox=src, base_font=_font(27), target_size=49, rois=ROIS,
        output_width=1280, output_height=720, pad_px=3, safe_text_width=1152,
    )
    assert placement is not None
    assert placement["line_count"] == 2
    assert placement["font_size"] == 49  # wrapped, never shrunk
    assert placement["mask"]["width"] <= 1152
    assert _contains(placement["mask"], src)


@pytest.mark.skipif(not FONT.exists(), reason="arial font unavailable")
def test_union_plate_returns_none_when_overlong_translation_cannot_fit():
    src = _bbox(409, 587, 1009, 658)
    text = "x" * 600  # a single unbreakable token cannot wrap
    placement = layout_union_overlay(
        text, src_bbox=src, base_font=_font(27), target_size=60, rois=ROIS,
        output_width=1280, output_height=720, pad_px=3, safe_text_width=1152,
    )
    assert placement is None


@pytest.mark.skipif(not FONT.exists(), reason="arial font unavailable")
def test_union_plate_never_draws_over_badge():
    # Source caption sits on the left but a wide translation would reach the badge;
    # the plate must be clamped clear of the badge while still covering the source.
    src = _bbox(400, 600, 900, 649)
    placement = layout_union_overlay(
        "A reasonably long caption that reaches toward the platform badge region",
        src_bbox=src, base_font=_font(27), target_size=49, rois=ROIS,
        output_width=1280, output_height=720, pad_px=3, safe_text_width=1152,
    )
    assert placement is not None
    mask = placement["mask"]
    assert protected_roi_hits({"mask": mask}, ROIS) == []
    assert _contains(mask, src)


@pytest.mark.skipif(not FONT.exists(), reason="arial font unavailable")
def test_review_evidence_never_covers_badge_and_flags_review():
    # Source caption whose OCR box itself intrudes into the badge: cannot be masked
    # without covering provenance, so it fails into NEEDS_REVIEW evidence.
    src = _bbox(1140, 640, 1275, 700)
    placement = layout_review_evidence(
        "Overlap", src_bbox=src, base_font=_font(27), target_size=60, rois=ROIS,
        output_width=1280, output_height=720, pad_px=3, safe_text_width=1152,
    )
    assert placement["review_evidence_overlay"] is True
    mask = placement["mask"]
    # A partial plate is still preferred over leaving the caption fully visible.
    assert mask is not None
    assert protected_roi_hits({"mask": mask}, ROIS) == []
    # Review text must never be drawn over the badge either.
    assert placement["review_overlay_suppressed"] is True
    assert placement["render_text"] == ""


@pytest.mark.skipif(not FONT.exists(), reason="arial font unavailable")
def test_review_evidence_keeps_text_when_it_clears_protected_regions():
    # A caption on the far left whose overlay stays left of the badge: the review
    # text is kept (not suppressed) and the plate clears both protected regions.
    src = _bbox(40, 600, 500, 649)
    placement = layout_review_evidence(
        "Overlap with the left logo region only, far from the badge",
        src_bbox=src, base_font=_font(27), target_size=49, rois=ROIS,
        output_width=1280, output_height=720, pad_px=3, safe_text_width=1152,
    )
    assert placement["review_evidence_overlay"] is True
    assert placement["review_overlay_suppressed"] is False
    assert placement["render_text"]
    assert placement["mask"] is None or protected_roi_hits({"mask": placement["mask"]}, ROIS) == []


def test_uploader_logo_still_protected():
    mask = _mask(20, 40, 300, 90)  # overlaps uploader logo corner
    assert any("uploader_logo" in h for h in protected_roi_hits({"mask": mask}, ROIS))
    out, ops = clamp_mask_clear_of_rois(mask, ROIS)
    assert out is not None
    assert protected_roi_hits({"mask": out}, ROIS) == []
    assert any("uploader_logo" in o for o in ops)
    assert PROTECTED_ROIS_REFERENCE["uploader_logo"]["right_x"] == 272
