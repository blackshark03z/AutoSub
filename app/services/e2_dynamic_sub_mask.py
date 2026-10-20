"""E2 focused dynamic per-cue subtitle mask (OD-03 part A).

Replaces the legacy whole-clip union mask (which collapsed all detections into
one 966x136 band that covered the platform AI label ``视频内容由AI生成``) with one
solid-black ``black@1.0`` mask per *real* OCR dialogue cue, plus an independent
English/Vietnamese overlay.

Design invariants enforced here:
- One adaptive plate per actual source dialogue cue, using that cue's own ``bbox``
  and start/end timestamps.  Adjacent cues are never unioned.
- Each plate is the tight union of the source Chinese glyph box and the measured
  rendered English glyph box at source-equivalent size, plus 2-4 px padding.  The
  font is never silently shrunk and the meaning is never truncated; a long
  translation wraps to at most two balanced lines.
- No mask is emitted during truly empty intervals (gaps between cues).
- Uploader logo (top-left) and the AI-content badge (bottom-right) are protected
  regions.  A plate is placed to clear them; a cue whose caption/layout cannot be
  placed safely becomes ``NEEDS_REVIEW`` with measured evidence (never a false
  PASS and never a plate drawn over provenance).
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

from PIL import ImageFont

from app.core.media import media_summary
from app.services.clean_subtitle_render import (
    build_local_sub_mask_overlay_filter,
    build_subtitles_filter,
    clean_text,
    multiline_text_box,
    wrap_subtitle_text,
    write_clean_subtitles_ass,
)

REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 720
MASK_PADDING_MIN = 2
MASK_PADDING_MAX = 4
MASK_COLOR = "black@1.0"

# Owner-confirmed sizing rule (see Product SoT "M1 subtitle replacement
# presentation", 2026-10-18): the en-US overlay keeps the *apparent* glyph height
# of the source Chinese caption (``SOURCE_FONT_TARGET_RATIO`` of the measured
# source character height). The solid-black plate is the tight UNION of the source
# Chinese glyph rectangle and the measured rendered English glyph/outline
# rectangle, plus small padding. English may wrap to a balanced 1-2 lines; the
# font is never silently shrunk and the meaning is never truncated. A cue whose
# English layout or union plate cannot fit the safe frame while clearing every
# protected region is reported NEEDS_REVIEW with measured geometry.
SOURCE_FONT_TARGET_RATIO = 0.96
# Widest text budget for the union plate; the plate must never become a full-width
# or lower-third band, so a union wider than this fails into NEEDS_REVIEW.
SAFE_TEXT_WIDTH_RATIO = 0.90
# Safety margin added to the union plate of a multi-line overlay so the rendered
# libass line spacing can never poke outside the measured PIL text box.
MULTILINE_MASK_SAFETY_PX = 4

STATUS_PASS = "PASS"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_PROTECTED_ROI_INTERSECTION = "PROTECTED_ROI_INTERSECTION"
STATUS_UNCONFIRMED = "SUBTITLE_REGION_UNCONFIRMED"

# Protected platform provenance regions, measured from decoded 1280x720 source
# frames (see acceptance report evidence).  A small guard is added outward.
# The AI badge glyphs were re-measured on subtitle-free frames: white text spans
# x 962..1278, y 655..696; the ROI keeps a 2px guard above/left of that glyph box
# so a caption whose glyph bottom sits just above the badge (e.g. y=649..652) is
# not wrongly rejected while the badge itself is still fully protected.
PROTECTED_ROIS_REFERENCE: dict[str, dict[str, int]] = {
    "uploader_logo": {"left_x": 0, "top_y": 0, "right_x": 272, "bottom_y": 110},
    "ai_content_badge": {"left_x": 960, "top_y": 653, "right_x": 1279, "bottom_y": 719},
}


def scale_protected_rois(output_width: int, output_height: int) -> dict[str, dict[str, int]]:
    sx = output_width / REFERENCE_WIDTH
    sy = output_height / REFERENCE_HEIGHT
    return {
        name: {
            "left_x": int(round(r["left_x"] * sx)),
            "top_y": int(round(r["top_y"] * sy)),
            "right_x": int(round(r["right_x"] * sx)),
            "bottom_y": int(round(r["bottom_y"] * sy)),
        }
        for name, r in PROTECTED_ROIS_REFERENCE.items()
    }


def rects_intersect(a: dict[str, int], b: dict[str, int]) -> bool:
    return not (
        a["right_x"] < b["left_x"]
        or b["right_x"] < a["left_x"]
        or a["bottom_y"] < b["top_y"]
        or b["bottom_y"] < a["top_y"]
    )


def snap_ms_to_centisecond(milliseconds: int) -> int:
    """Align cue times to the ASS centisecond grid so mask and text switch together."""
    return max(0, int(milliseconds) // 10 * 10)


def mask_padding_px(output_height: int) -> int:
    return max(MASK_PADDING_MIN, min(MASK_PADDING_MAX, round(3.0 * output_height / REFERENCE_HEIGHT)))


def build_tight_mask(
    bbox: dict[str, int],
    *,
    output_width: int,
    output_height: int,
    pad_px: int,
) -> dict[str, int]:
    width = (bbox["right_x"] - bbox["left_x"] + 1) + 2 * pad_px
    height = (bbox["bottom_y"] - bbox["top_y"] + 1) + 2 * pad_px
    x = max(0, min(output_width - width, bbox["left_x"] - pad_px))
    y = max(0, min(output_height - height, bbox["top_y"] - pad_px))
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "right_x": x + width - 1,
        "bottom_y": y + height - 1,
        "padding_px": pad_px,
    }


def _font(font: ImageFont.FreeTypeFont, size: int) -> ImageFont.FreeTypeFont:
    if getattr(font, "size", None) == size:
        return font
    font_path = getattr(font, "path", None)
    if font_path and Path(font_path).exists():
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception:
            pass
    return ImageFont.truetype("arial.ttf", size)


def _one_line(text: str) -> str | None:
    return text if "\n" not in text else None


def _two_line(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str | None:
    if "\n" in text:
        return text if text.count("\n") == 1 else None
    wrapped = wrap_subtitle_text(text, font, max_width)
    return wrapped if wrapped.count("\n") == 1 else None


def fit_overlay_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    target_size: int,
    min_size: int,
    max_width: int,
    max_lines: int = 2,
    validator: Any = None,
) -> dict[str, Any] | None:
    """Pick the largest font (target..min) whose wrapped text fits max_width.

    ``validator`` (if provided) receives ``(render_text, font_size, text_width,
    text_height)`` and returns True to accept the candidate; this lets callers
    reject candidates that would intrude on a protected region.
    """
    for size in range(target_size, min_size - 1, -1):
        f = _font(font, size)
        for line_cap in range(1, max_lines + 1):
            candidate = _one_line(text) if line_cap == 1 else _two_line(text, f, max_width)
            if candidate is None:
                continue
            box = multiline_text_box(candidate, f)
            if box["width"] <= max_width and candidate.count("\n") + 1 <= line_cap:
                if validator is not None and not validator(candidate, size, box["width"], box["height"]):
                    continue
                return {
                    "render_text": candidate,
                    "font_size": size,
                    "line_count": candidate.count("\n") + 1,
                    "text_width": box["width"],
                    "text_height": box["height"],
                }
    return None


def text_rect(layout: dict[str, Any]) -> dict[str, int]:
    width = int(layout.get("text_width", 0))
    height = int(layout.get("text_height", 0))
    x = int(layout["center_x"]) - width // 2
    y = int(layout["anchor_y"]) - height // 2
    return {"left_x": x, "top_y": y, "right_x": x + width - 1, "bottom_y": y + height - 1}


def protected_roi_hits(layout: dict[str, Any], rois: dict[str, dict[str, int]]) -> list[str]:
    hits: list[str] = []
    rects: list[tuple[str, dict[str, int]]] = []
    mask = layout.get("mask")
    if mask:
        rects.append(("mask", {"left_x": mask["x"], "top_y": mask["y"], "right_x": mask["right_x"], "bottom_y": mask["bottom_y"]}))
    if layout.get("text_width"):
        rects.append(("text", text_rect(layout)))
    for roi_name, roi in rois.items():
        for kind, rect in rects:
            if rects_intersect(rect, roi):
                hits.append(f"{kind}:{roi_name}")
    return hits


def clamp_mask_clear_of_rois(
    mask: dict[str, int],
    rois: dict[str, dict[str, int]],
) -> tuple[dict[str, int] | None, list[str]]:
    """Shrink ``mask`` on one edge so it no longer intersects any protected ROI.

    The clamp keeps the largest remaining area that clears the ROI, so a wide
    one-line subtitle stays fully covered when only its padded bottom grazes the
    badge. Returns ``(clamped_mask, ops)`` or ``(None, ops)`` if a mask cannot be
    made clear of every ROI. Callers must re-validate afterwards.
    """
    ops: list[str] = []
    current = dict(mask)
    for name, roi in rois.items():
        rect = {
            "left_x": current["x"],
            "top_y": current["y"],
            "right_x": current["right_x"],
            "bottom_y": current["bottom_y"],
        }
        if not rects_intersect(rect, roi):
            continue
        candidates: list[tuple[str, int]] = []
        if rect["top_y"] <= roi["top_y"] <= rect["bottom_y"]:
            candidates.append(("clamp_bottom", roi["top_y"] - 1))
        if rect["top_y"] <= roi["bottom_y"] <= rect["bottom_y"]:
            candidates.append(("clamp_top", roi["bottom_y"] + 1))
        if rect["left_x"] <= roi["left_x"] <= rect["right_x"]:
            candidates.append(("clamp_right", roi["left_x"] - 1))
        if rect["left_x"] <= roi["right_x"] <= rect["right_x"]:
            candidates.append(("clamp_left", roi["right_x"] + 1))
        best: tuple[Any, dict[str, int], str] | None = None
        for kind, value in candidates:
            cand = dict(current)
            if kind == "clamp_bottom":
                cand["height"] = value - cand["y"] + 1
            elif kind == "clamp_top":
                new_y = value
                cand["height"] = cand["y"] + cand["height"] - new_y
                cand["y"] = new_y
            elif kind == "clamp_right":
                cand["width"] = value - cand["x"] + 1
            elif kind == "clamp_left":
                new_x = value
                cand["width"] = cand["x"] + cand["width"] - new_x
                cand["x"] = new_x
            if cand["width"] <= 0 or cand["height"] <= 0:
                continue
            cand["right_x"] = cand["x"] + cand["width"] - 1
            cand["bottom_y"] = cand["y"] + cand["height"] - 1
            area = cand["width"] * cand["height"]
            # Prefer a vertical clamp (0) over horizontal (1): subtitles are
            # horizontal, so preserving full width keeps the glyph run covered and
            # only trims the padded bottom/top that grazed the corner ROI.
            rank = (0 if kind in ("clamp_bottom", "clamp_top") else 1, -area)
            if best is None or rank < best[0]:
                best = (rank, cand, kind)  # type: ignore[assignment]
        if best is None:
            return None, ops
        current = best[1]
        ops.append(f"{name}:{best[2]}")
    return current, ops


def _rect_from_bbox(bbox: dict[str, Any]) -> dict[str, int]:
    return {
        "left_x": int(bbox["left_x"]),
        "top_y": int(bbox["top_y"]),
        "right_x": int(bbox["right_x"]),
        "bottom_y": int(bbox["bottom_y"]),
    }


def _union_rect(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {
        "left_x": min(a["left_x"], b["left_x"]),
        "top_y": min(a["top_y"], b["top_y"]),
        "right_x": max(a["right_x"], b["right_x"]),
        "bottom_y": max(a["bottom_y"], b["bottom_y"]),
    }


def _rect_contains(outer: dict[str, int], inner: dict[str, int]) -> bool:
    return (
        outer["left_x"] <= inner["left_x"]
        and outer["top_y"] <= inner["top_y"]
        and outer["right_x"] >= inner["right_x"]
        and outer["bottom_y"] >= inner["bottom_y"]
    )


def _mask_dict(rect: dict[str, int], pad_px: int, *, v_extra: int = 0) -> dict[str, int]:
    left = rect["left_x"] - pad_px
    top = rect["top_y"] - pad_px - v_extra
    right = rect["right_x"] + pad_px
    bottom = rect["bottom_y"] + pad_px + v_extra
    return {
        "x": left,
        "y": top,
        "left_x": left,
        "top_y": top,
        "width": right - left + 1,
        "height": bottom - top + 1,
        "right_x": right,
        "bottom_y": bottom,
        "padding_px": pad_px,
    }


def _clip_mask_to_frame(mask: dict[str, int], output_width: int, output_height: int) -> dict[str, int] | None:
    left = max(0, mask["x"])
    top = max(0, mask["y"])
    right = min(output_width - 1, mask["right_x"])
    bottom = min(output_height - 1, mask["bottom_y"])
    if right < left or bottom < top:
        return None
    return {"x": left, "y": top, "left_x": left, "top_y": top,
            "width": right - left + 1, "height": bottom - top + 1,
            "right_x": right, "bottom_y": bottom, "padding_px": mask.get("padding_px", 0)}


def _candidate_overlays(text: str, font: ImageFont.FreeTypeFont, *, max_lines: int,
                        wrap_width: int) -> list[str]:
    single = clean_text(text)
    if not single:
        return []
    candidates = [single]
    if max_lines >= 2 and " " in single:
        wrapped = wrap_subtitle_text(single, font, wrap_width)
        if wrapped and wrapped not in candidates and wrapped.count("\n") + 1 <= max_lines:
            candidates.append(wrapped)
    return candidates


def _clear_of_rois(
    mask: dict[str, int],
    rois: dict[str, dict[str, int]],
    must_cover: list[dict[str, int]],
) -> tuple[dict[str, int] | None, list[str]]:
    """Return a mask cleared of every protected ROI that still covers ``must_cover``.

    Tries the exact union mask first, then a clamped variant.  Returns ``(mask, ops)``
    or ``(None, [])`` when no ROI-clear mask can cover the required glyph rectangles
    (the cue is then reported NEEDS_REVIEW rather than covering a protected region).
    """
    if not protected_roi_hits({"mask": mask}, rois):
        return mask, []
    clamped, ops = clamp_mask_clear_of_rois(mask, rois)
    if clamped is None:
        return None, []
    clamped = {"x": clamped["x"], "y": clamped["y"], "left_x": clamped["x"], "top_y": clamped["y"],
               "width": clamped["width"], "height": clamped["height"],
               "right_x": clamped["right_x"], "bottom_y": clamped["bottom_y"],
               "padding_px": mask.get("padding_px", 0)}
    if protected_roi_hits({"mask": clamped}, rois):
        return None, []
    if not all(_rect_contains(clamped, rect) for rect in must_cover):
        return None, []
    return clamped, ops


def layout_union_overlay(
    text: str,
    *,
    src_bbox: dict[str, Any],
    base_font: ImageFont.FreeTypeFont,
    target_size: int,
    rois: dict[str, dict[str, int]],
    output_width: int,
    output_height: int,
    pad_px: int,
    safe_text_width: int,
    max_lines: int = 2,
    max_nudge_px: int = 6,
) -> dict[str, Any] | None:
    """Adaptive black plate tightly covering the Chinese ∪ English glyph union.

    The English overlay keeps the source-equivalent glyph height (``target_size``);
    it is never shrunk.  The plate is the union of the measured source glyph box and
    the measured rendered English glyph box plus small padding, bottom-anchored to
    the source caption and nudged up by at most ``max_nudge_px`` to clear a protected
    ROI.  Returns ``None`` when no 1-2 line layout keeps the plate inside the safe
    frame while clearing every protected region (the cue then becomes NEEDS_REVIEW).
    """
    src = _rect_from_bbox(src_bbox)
    font = _font(base_font, target_size)
    center_x = (src["left_x"] + src["right_x"]) // 2
    for rtext in _candidate_overlays(text, font, max_lines=max_lines, wrap_width=safe_text_width):
        tb = multiline_text_box(rtext, font)
        tw, th = int(tb["width"]), int(tb["height"])
        if tw <= 0 or th <= 0:
            continue
        line_count = rtext.count("\n") + 1
        v_extra = MULTILINE_MASK_SAFETY_PX if line_count > 1 else 0
        for nudge in range(0, max_nudge_px + 1):
            bottom = src["bottom_y"] - nudge
            top = bottom - th + 1
            left = center_x - tw // 2
            trect = {"left_x": left, "top_y": top, "right_x": left + tw - 1, "bottom_y": bottom}
            mask = _mask_dict(_union_rect(src, trect), pad_px, v_extra=v_extra)
            if mask["x"] < 0 or mask["y"] < 0 or mask["right_x"] >= output_width or mask["bottom_y"] >= output_height:
                continue
            if mask["width"] > safe_text_width or mask["height"] > output_height:
                continue
            cleared, ops = _clear_of_rois(mask, rois, must_cover=[src, trect])
            if cleared is None:
                continue
            return {
                "render_text": rtext,
                "font_size": font.size,
                "line_count": line_count,
                "text_width": tw,
                "text_height": th,
                "mask": cleared,
                "plate": cleared,
                "center_x": center_x,
                "anchor_y": top + th // 2,
                "placement_nudge_px": nudge,
                "clamp_ops": ops,
            }
    return None


def layout_review_evidence(
    text: str,
    *,
    src_bbox: dict[str, Any],
    base_font: ImageFont.FreeTypeFont,
    target_size: int,
    rois: dict[str, dict[str, int]],
    output_width: int,
    output_height: int,
    pad_px: int,
    safe_text_width: int,
    max_lines: int = 2,
) -> dict[str, Any]:
    """Best-effort measured placement for a NEEDS_REVIEW cue.

    Renders a readable review overlay (the largest 1-2 line fit within the safe frame
    width, never above the equivalent size) on a plate that covers the Chinese source
    plus as much of the English block as can be covered WITHOUT intruding on a
    protected region or leaving the frame.  This shrink-for-review is never used to
    award PASS; the cue stays NEEDS_REVIEW and its equivalent-size geometry stays in
    the report.
    """
    src = _rect_from_bbox(src_bbox)
    center_x = (src["left_x"] + src["right_x"]) // 2
    fit = fit_overlay_text(
        clean_text(text), base_font, target_size=target_size, min_size=12,
        max_width=safe_text_width, max_lines=max_lines,
    )
    if fit is not None:
        rtext = fit["render_text"]
        font = _font(base_font, fit["font_size"])
        tw, th = int(fit["text_width"]), int(fit["text_height"])
        line_count = int(fit["line_count"])
    else:
        font = _font(base_font, target_size)
        candidates = _candidate_overlays(text, font, max_lines=max_lines, wrap_width=safe_text_width)
        rtext = candidates[-1] if candidates else clean_text(text)
        tb = multiline_text_box(rtext, font)
        tw, th = int(tb["width"]), int(tb["height"])
        line_count = rtext.count("\n") + 1
    v_extra = MULTILINE_MASK_SAFETY_PX if line_count > 1 else 0

    src_mask = build_tight_mask(src_bbox, output_width=output_width, output_height=output_height, pad_px=pad_px)
    bottom = src["bottom_y"]
    top = bottom - th + 1
    left = center_x - tw // 2
    trect = {"left_x": left, "top_y": top, "right_x": left + tw - 1, "bottom_y": bottom}

    mask: dict[str, int] | None = None
    if tw > 0 and th > 0:
        union = _mask_dict(_union_rect(src, trect), pad_px, v_extra=v_extra)
        clipped = _clip_mask_to_frame(union, output_width, output_height)
        if clipped is not None:
            cleared, _ = _clear_of_rois(clipped, rois, must_cover=[src])
            mask = cleared
    if mask is None:
        cleared, _ = _clear_of_rois(src_mask, rois, must_cover=[src])
        mask = cleared
    if mask is None:
        # Partial plate: the source box itself overlaps a protected region, so a full
        # cover is impossible.  Mask as much as can be drawn without intruding (e.g.
        # clamp the plate just above the AI badge) rather than leaving it uncovered.
        if protected_roi_hits({"mask": src_mask}, rois):
            clamped, _ = clamp_mask_clear_of_rois(src_mask, rois)
            mask = {**clamped, "padding_px": pad_px} if clamped is not None else None
        else:
            mask = src_mask

    # Never draw review text over a protected region either: if the overlay glyph box
    # would intrude on the logo/badge, suppress the text and keep only the plate.
    text_on_roi = tw > 0 and th > 0 and any(rects_intersect(trect, roi) for roi in rois.values())
    placement: dict[str, Any] = {
        "render_text": "" if text_on_roi else rtext,
        "font_size": font.size,
        "line_count": line_count,
        "text_width": tw,
        "text_height": th,
        "mask": mask,
        "plate": mask,
        "center_x": center_x,
        "anchor_y": top + th // 2,
        "review_evidence_overlay": True,
        "review_overlay_suppressed": bool(text_on_roi),
    }
    return placement


def build_dynamic_mask_plan(
    source_path: Path,
    cues: list[dict[str, Any]],
    cue_boxes: dict[str, dict[str, Any]],
    *,
    font_path: Path,
    max_lines: int = 2,
    target_ratio: float = SOURCE_FONT_TARGET_RATIO,
    safe_width_ratio: float = SAFE_TEXT_WIDTH_RATIO,
    max_nudge_px: int = 6,
    review_only_ids: set[str] | None = None,
    protected_rois: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    media = media_summary(source_path)
    output_width = int(media["video"].get("width") or 0) or REFERENCE_WIDTH
    output_height = int(media["video"].get("height") or 0) or REFERENCE_HEIGHT
    rois = (dict(protected_rois) if protected_rois is not None
            else scale_protected_rois(output_width, output_height))
    pad_px = mask_padding_px(output_height)
    safe_text_width = round(output_width * safe_width_ratio)
    base_font = ImageFont.truetype(str(font_path), max(18, round(output_height * 0.037)))

    layouts: list[dict[str, Any]] = []
    for cue in cues:
        cue_id = str(cue.get("cue_id"))
        text = clean_text(cue.get("text") or "")
        start_time = round(snap_ms_to_centisecond(int(cue.get("start_ms", 0))) / 1000.0, 3)
        end_time = round(snap_ms_to_centisecond(int(cue.get("end_ms", 0))) / 1000.0, 3)
        box = cue_boxes.get(cue_id)
        bbox = box.get("bbox") if box else None

        layout: dict[str, Any] = {
            "segment_id": cue_id,
            "overlay_mode": "LOCAL_SUB_MASK_OVERLAY",
            "status": STATUS_UNCONFIRMED,
            "review_reason": "missing_source_box" if not bbox else None,
            "render_text": text,
            "raw_text": text,
            "font_size": base_font.size,
            "line_count": 1,
            "text_width": 0,
            "text_height": 0,
            "mask": None,
            "plate": None,
            "start_time": start_time,
            "end_time": end_time,
            "target_font_size": None,
            "max_text_width": None,
            "source_char_height": None,
        }
        if bbox:
            char_h = int(box.get("text_height") or (bbox["bottom_y"] - bbox["top_y"] + 1))
            target_size = max(18, round(char_h * target_ratio))
            layout["source_bbox"] = bbox
            layout["confidence"] = float(box.get("confidence", 1.0) or 1.0)
            layout["target_font_size"] = target_size
            layout["source_char_height"] = char_h

            # Adaptive union plate: the black rectangle tightly covers the source
            # Chinese glyph box UNION the measured rendered English glyph box at the
            # source-equivalent size. A cue that cannot be placed safely becomes
            # NEEDS_REVIEW with measured evidence rather than a false PASS.
            forced_review = review_only_ids is not None and cue_id in review_only_ids
            placement = None if forced_review else layout_union_overlay(
                text,
                src_bbox=bbox,
                base_font=base_font,
                target_size=target_size,
                rois=rois,
                output_width=output_width,
                output_height=output_height,
                pad_px=pad_px,
                safe_text_width=safe_text_width,
                max_lines=max_lines,
                max_nudge_px=max_nudge_px,
            )
            if placement is not None:
                layout.update(placement)
                layout["max_text_width"] = placement["mask"]["width"]
                layout["status"] = STATUS_PASS
                layout["review_reason"] = None
            else:
                evidence = layout_review_evidence(
                    text,
                    src_bbox=bbox,
                    base_font=base_font,
                    target_size=target_size,
                    rois=rois,
                    output_width=output_width,
                    output_height=output_height,
                    pad_px=pad_px,
                    safe_text_width=safe_text_width,
                    max_lines=max_lines,
                )
                layout.update(evidence)
                layout["status"] = STATUS_NEEDS_REVIEW
                layout["review_reason"] = (
                    "translation_unresolved_or_rejected"
                    if forced_review
                    else "translation_exceeds_safe_frame_at_equivalent_size"
                )

        layouts.append(layout)

    statuses: dict[str, int] = {}
    for layout in layouts:
        statuses[layout["status"]] = statuses.get(layout["status"], 0) + 1
    mask_widths = [layout["mask"]["width"] for layout in layouts if layout.get("mask")]
    return {
        "output_width": output_width,
        "output_height": output_height,
        "fps": media["video"].get("avg_frame_rate"),
        "font_size": base_font.size,
        "mask_padding_px": pad_px,
        "mask_model": "adaptive_union_source_and_english",
        "fit_budget": "adaptive_union_plate_u_within_safe_frame",
        "safe_text_width": safe_text_width,
        "max_nudge_px": max_nudge_px,
        "protected_rois": rois,
        "layouts": layouts,
        "statuses": statuses,
        "max_mask_width_px": max(mask_widths) if mask_widths else None,
    }


def build_mask_filter(plan: dict[str, Any], ass_path: Path, *, offset_seconds: float = 0.0) -> str:
    layouts = [_shift_layout(layout, offset_seconds) for layout in plan["layouts"]]
    return build_local_sub_mask_overlay_filter([], layouts, ass_path, render_review=True)


def _shift_layout(layout: dict[str, Any], offset: float) -> dict[str, Any]:
    clone = dict(layout)
    clone["start_time"] = round(snap_ms_to_centisecond(round(offset * 1000)) / 1000.0, 3)
    clone["end_time"] = clone["start_time"]
    start_ms = snap_ms_to_centisecond(int(round(float(layout["start_time"]) * 1000)))
    end_ms = snap_ms_to_centisecond(int(round(float(layout["end_time"]) * 1000)))
    clone["start_time"] = round(max(0, start_ms - round(offset * 1000)) / 1000.0, 3)
    clone["end_time"] = round(max(0, end_ms - round(offset * 1000)) / 1000.0, 3)
    return clone


def window_cues(cues: list[dict[str, Any]], *, start_s: float, end_s: float) -> list[dict[str, Any]]:
    out = []
    for cue in cues:
        start = int(cue.get("start_ms", 0))
        end = int(cue.get("end_ms", 0))
        if end <= int(start_s * 1000) or start >= int(end_s * 1000):
            continue
        clone = dict(cue)
        clone["start_ms"] = snap_ms_to_centisecond(max(start, int(start_s * 1000)) - int(start_s * 1000))
        clone["end_ms"] = snap_ms_to_centisecond(min(end, int(end_s * 1000)) - int(start_s * 1000))
        out.append(clone)
    return out


def render_dynamic_mask_window(
    source_path: Path,
    plan: dict[str, Any],
    cues: list[dict[str, Any]],
    *,
    output_path: Path,
    start_s: float | None = None,
    end_s: float | None = None,
) -> dict[str, Any]:
    bad = [l for l in plan["layouts"] if l["status"] == STATUS_PROTECTED_ROI_INTERSECTION]
    if bad:
        raise ValueError(
            "PROTECTED_ROI_INTERSECTION: refusing to render over protected region(s): "
            + ", ".join(sorted({h for l in bad for h in l.get("protected_roi_hits", [])}))
        )
    unconfirmed = [l for l in plan["layouts"] if l["status"] == STATUS_UNCONFIRMED]
    if unconfirmed:
        raise ValueError(
            "Cannot render dynamic mask: "
            + ", ".join(f"{l['segment_id']}={l['status']}" for l in unconfirmed)
        )
    # NEEDS_REVIEW cues are still rendered (mask over the confirmed source region plus
    # the flagged evidence overlay) so the job produces a reviewable artifact and the
    # source caption never leaks; the terminal job status is reported as NEEDS_REVIEW.

    offset = float(start_s or 0.0)
    if start_s is None or end_s is None:
        render_cues = list(cues)
        duration_args: list[str] = []
    else:
        render_cues = window_cues(cues, start_s=start_s, end_s=end_s)
        duration_args = ["-t", f"{max(0.0, end_s - start_s):.3f}"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = output_path.with_suffix(".ass")
    write_clean_subtitles_ass(
        render_cues,
        [_shift_layout(l, offset) for l in plan["layouts"]],
        ass_path,
        font_size=plan["font_size"],
        output_width=plan["output_width"],
        output_height=plan["output_height"],
        render_review=True,
        emit_local_sub_masks=True,
    )
    # The black plates are burned in as ASS vector events inside ``ass_path``, so the
    # filtergraph is a constant-size ``subtitles`` filter.  A per-cue drawbox graph would
    # exceed the Windows CreateProcess argv cap (~32k) for long media, and this ffmpeg
    # build exposes no -filter_script / -filter_complex_script file option.
    filter_value = build_subtitles_filter(ass_path)
    temp_output = output_path.with_suffix(".tmp.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *(["-ss", f"{offset:.3f}"] if offset else []),
            "-i",
            str(source_path),
            *duration_args,
            "-vf",
            filter_value,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            str(temp_output),
        ],
        check=True,
    )
    temp_output.replace(output_path)
    return {"output": str(output_path), "ass": str(ass_path), "filter": filter_value}


def load_cue_boxes(cues_json_path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(Path(cues_json_path).read_text(encoding="utf-8"))
    boxes: dict[str, dict[str, Any]] = {}
    for cue in data.get("detected_cues", []):
        boxes[str(cue["cue_id"])] = cue
    return boxes


def load_render_cues(translations_json_path: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(translations_json_path).read_text(encoding="utf-8"))
    cues = []
    for r in data.get("results", []):
        if r.get("status") != "TRANSLATED" or not r.get("translation"):
            continue
        cues.append(
            {
                "cue_id": str(r["cue_id"]),
                "start_ms": int(r["start_ms"]),
                "end_ms": int(r["end_ms"]),
                "text": r["translation"],
            }
        )
    return cues


def load_render_cues_with_review(
    translations_json_path: Path,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Load every verified source cue, flagging those without a usable translation.

    Unlike :func:`load_render_cues`, unresolved cues (empty or quality-rejected
    translations) are NOT dropped.  They are returned with empty text and their ids in
    the review set, so the render still draws a source-covering plate and reports the
    cue ``NEEDS_REVIEW`` instead of leaking the Chinese caption or faking a PASS.
    """
    data = json.loads(Path(translations_json_path).read_text(encoding="utf-8"))
    cues: list[dict[str, Any]] = []
    review_only: set[str] = set()
    for r in data.get("results", []):
        cue_id = str(r["cue_id"])
        translated = r.get("status") == "TRANSLATED" and bool(r.get("translation"))
        cues.append(
            {
                "cue_id": cue_id,
                "start_ms": int(r["start_ms"]),
                "end_ms": int(r["end_ms"]),
                "text": r["translation"] if translated else "",
            }
        )
        if not translated:
            review_only.add(cue_id)
    return cues, review_only


def mask_color_components(filter_value: str) -> list[float]:
    """Extract numeric opacity values from drawbox color=black@X tokens."""
    values: list[float] = []
    for token in filter_value.split(","):
        if token.startswith("drawbox=") and "black@" in token:
            fragment = token.split("black@", 1)[1].split(":", 1)[0]
            try:
                values.append(float(fragment))
            except ValueError:
                values.append(math.nan)
    return values
