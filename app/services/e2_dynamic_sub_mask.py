"""E2 focused dynamic per-cue subtitle mask (OD-03 part A).

Replaces the legacy whole-clip union mask (which collapsed all detections into
one 966x136 band that covered the platform AI label ``视频内容由AI生成``) with one
solid-black ``black@1.0`` mask per *real* OCR dialogue cue, plus an independent
English/Vietnamese overlay.

Design invariants enforced here:
- One mask per actual source dialogue cue, using that cue's own ``bbox`` and its
  own start/end timestamps.  Adjacent cues are never unioned.
- Mask padding is 2-4 px; mask colour is exactly ``black@1.0`` (validated finite).
- No mask is emitted during truly empty intervals (gaps between cues).
- Uploader logo (top-left) and the AI-content badge (bottom-right) are protected
  regions; if any mask OR translated-text rectangle intersects a protected region
  the layout becomes ``PROTECTED_ROI_INTERSECTION`` and rendering fails closed.
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

# Owner-confirmed sizing rule (see Product SoT / WP-M1-001 §A3): the en-US
# overlay must keep the *apparent* glyph height of the source Chinese caption.
# Target is ~96% of the source character height; an unfit line may reduce to at
# most ~88% of that target and no further. Cues whose text cannot fit the
# confirmed source mask within that band must fail into evidence-bearing review
# instead of silently shrinking or widening the mask.
SOURCE_FONT_TARGET_RATIO = 0.96
SOURCE_FONT_MIN_RATIO = 0.88
# Widest text budget only used to render an *evidence* overlay for cues that are
# already flagged NEEDS_REVIEW (never to award PASS).
SAFE_TEXT_WIDTH_RATIO = 0.90

STATUS_PASS = "PASS"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_PROTECTED_ROI_INTERSECTION = "PROTECTED_ROI_INTERSECTION"
STATUS_UNCONFIRMED = "SUBTITLE_REGION_UNCONFIRMED"

# Protected platform provenance regions, measured from decoded 1280x720 source
# frames (see acceptance report evidence).  A small guard is added outward.
PROTECTED_ROIS_REFERENCE: dict[str, dict[str, int]] = {
    "uploader_logo": {"left_x": 0, "top_y": 0, "right_x": 272, "bottom_y": 110},
    "ai_content_badge": {"left_x": 956, "top_y": 649, "right_x": 1279, "bottom_y": 719},
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


def _clamp_covers_bbox(mask: dict[str, int], bbox: dict[str, int] | None) -> bool:
    """A clamped mask must still cover the detected source text (not a degenerate sliver)."""
    if mask["width"] < 10 or mask["height"] < 10:
        return False
    if not bbox:
        return True
    ix = max(0, min(mask["right_x"], int(bbox["right_x"])) - max(mask["x"], int(bbox["left_x"])) + 1)
    iy = max(0, min(mask["bottom_y"], int(bbox["bottom_y"])) - max(mask["y"], int(bbox["top_y"])) + 1)
    inter = ix * iy
    bbox_area = (int(bbox["right_x"]) - int(bbox["left_x"]) + 1) * (
        int(bbox["bottom_y"]) - int(bbox["top_y"]) + 1
    )
    return bbox_area > 0 and inter / bbox_area >= 0.6


def _text_roi_validator(
    center_x: int,
    center_y: int,
    rois: dict[str, dict[str, int]],
) -> Any:
    def _ok(_render_text: str, _size: int, tw: int, th: int) -> bool:
        rect = {
            "left_x": center_x - tw // 2,
            "top_y": center_y - th // 2,
            "right_x": center_x - tw // 2 + tw - 1,
            "bottom_y": center_y - th // 2 + th - 1,
        }
        return not any(rects_intersect(rect, roi) for roi in rois.values())

    return _ok


def fit_layout_text(
    text: str,
    base_font: ImageFont.FreeTypeFont,
    *,
    target_size: int,
    min_size: int,
    max_width: int,
    max_lines: int,
    rois: dict[str, dict[str, int]],
    center_x: int,
    center_y: int,
) -> dict[str, Any] | None:
    """Fit ``text`` at an equivalent source glyph size inside ``max_width``.

    The font is never reduced below ``min_size`` (the ~88% equivalent-size floor),
    so an over-long translation returns ``None`` instead of a silently downscaled
    PASS.  A validator rejects any candidate that would intrude on a protected ROI.
    """
    return fit_overlay_text(
        text,
        base_font,
        target_size=target_size,
        min_size=min_size,
        max_width=max_width,
        max_lines=max_lines,
        validator=_text_roi_validator(center_x, center_y, rois),
    )


def apply_protected_roi_clamp(
    plan: dict[str, Any],
    *,
    font_path: Path | None = None,
    max_lines: int = 2,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Clamp padded masks that graze a protected ROI, then re-validate each layout.

    A clamped mask is *narrower* than the confirmed source mask and is recentered, so
    the overlay is re-fit against the clamped width. A layout only returns to ``PASS``
    when it still has a valid, measured text fit at equivalent size inside the clamped
    mask AND clears every protected ROI (including its text rectangle). A layout whose
    text no longer fits becomes ``NEEDS_REVIEW`` (never a false PASS); a mask that
    cannot be cleared stays ``PROTECTED_ROI_INTERSECTION`` (fail closed).
    """
    rois = plan["protected_rois"]
    base_font: ImageFont.FreeTypeFont | None = None
    if font_path is not None:
        base_font = ImageFont.truetype(str(font_path), max(18, int(plan["font_size"])))
    safe_width = int(plan.get("safe_text_width") or 0)

    clamped: list[str] = []
    unrecoverable: list[str] = []
    for layout in plan["layouts"]:
        if not layout.get("mask") or layout["status"] == STATUS_UNCONFIRMED:
            continue
        hits = protected_roi_hits(layout, rois)
        if not hits:
            continue
        was_protected = layout["status"] == STATUS_PROTECTED_ROI_INTERSECTION
        new_mask, ops = clamp_mask_clear_of_rois(layout["mask"], rois)
        if new_mask is None or not _clamp_covers_bbox(new_mask, layout.get("source_bbox")):
            unrecoverable.append(layout["segment_id"])
            continue
        layout["mask"] = new_mask
        layout["plate"] = new_mask
        layout["center_x"] = new_mask["x"] + new_mask["width"] // 2
        layout["anchor_y"] = new_mask["y"] + new_mask["height"] // 2
        layout["clamp_ops"] = ops

        target_size = int(layout.get("target_font_size") or plan.get("font_size") or 27)
        min_size = int(layout.get("min_font_size") or target_size)
        # PASS-candidate layouts must fit the (clamped) source mask; flagged layouts
        # keep their evidence-overlay budget so the render stays reviewable.
        budget = new_mask["width"] if was_protected else (safe_width or new_mask["width"])
        refit = None
        if base_font is not None:
            refit = fit_layout_text(
                layout.get("raw_text") or layout["render_text"],
                base_font,
                target_size=target_size,
                min_size=min_size,
                max_width=budget,
                max_lines=max_lines,
                rois=rois,
                center_x=layout["center_x"],
                center_y=layout["anchor_y"],
            )
        if refit is not None:
            layout.update(refit)

        hits_after = protected_roi_hits(layout, rois)
        if hits_after:
            # Fail closed: a mask that still grazes a protected ROI is never drawn,
            # even for a flagged layout, so the logo/watermark can never be covered.
            layout["status"] = STATUS_PROTECTED_ROI_INTERSECTION
            layout["review_reason"] = ("PROTECTED_ROI_INTERSECTION_AFTER_CLAMP"
                                       if was_protected else "PROTECTED_ROI_INTERSECTION")
            layout["protected_roi_hits"] = hits_after
            unrecoverable.append(layout["segment_id"])
            continue
        if was_protected:
            layout["max_text_width"] = new_mask["width"]
            if refit is not None and int(refit["text_width"]) <= new_mask["width"]:
                layout["status"] = STATUS_PASS
                layout["review_reason"] = None
                layout.pop("protected_roi_hits", None)
                clamped.append(layout["segment_id"])
            else:
                layout["status"] = STATUS_NEEDS_REVIEW
                layout["review_reason"] = "translation_exceeds_clamped_source_mask"
                clamped.append(layout["segment_id"])
        else:
            clamped.append(layout["segment_id"])

    statuses: dict[str, int] = {}
    for layout in plan["layouts"]:
        statuses[layout["status"]] = statuses.get(layout["status"], 0) + 1
    plan["statuses"] = statuses
    mask_widths = [layout["mask"]["width"] for layout in plan["layouts"] if layout.get("mask")]
    plan["max_mask_width_px"] = max(mask_widths) if mask_widths else None
    return plan, {"clamped": clamped, "unrecoverable": unrecoverable}


def build_dynamic_mask_plan(
    source_path: Path,
    cues: list[dict[str, Any]],
    cue_boxes: dict[str, dict[str, Any]],
    *,
    font_path: Path,
    max_lines: int = 2,
    target_ratio: float = SOURCE_FONT_TARGET_RATIO,
    min_ratio: float = SOURCE_FONT_MIN_RATIO,
    safe_width_ratio: float = SAFE_TEXT_WIDTH_RATIO,
) -> dict[str, Any]:
    media = media_summary(source_path)
    output_width = int(media["video"].get("width") or 0) or REFERENCE_WIDTH
    output_height = int(media["video"].get("height") or 0) or REFERENCE_HEIGHT
    rois = scale_protected_rois(output_width, output_height)
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
            "min_font_size": None,
            "max_text_width": None,
            "source_char_height": None,
        }
        if bbox:
            mask = build_tight_mask(bbox, output_width=output_width, output_height=output_height, pad_px=pad_px)
            char_h = int(box.get("text_height") or (bbox["bottom_y"] - bbox["top_y"] + 1))
            target_size = max(18, round(char_h * target_ratio))
            min_size = max(14, round(target_size * min_ratio))
            center_x = mask["x"] + mask["width"] // 2
            center_y = mask["y"] + mask["height"] // 2

            layout["mask"] = mask
            layout["plate"] = mask
            layout["source_bbox"] = bbox
            layout["confidence"] = float(box.get("confidence", 1.0) or 1.0)
            layout["center_x"] = center_x
            layout["anchor_y"] = center_y
            layout["target_font_size"] = target_size
            layout["min_font_size"] = min_size
            layout["max_text_width"] = mask["width"]
            layout["source_char_height"] = char_h

            # PASS requires a real, measured fit inside the confirmed source mask at
            # equivalent glyph size. Fitting against the whole frame width (or a
            # below-floor font) is what created false PASS layouts before.
            fit = fit_layout_text(
                text,
                base_font,
                target_size=target_size,
                min_size=min_size,
                max_width=mask["width"],
                max_lines=max_lines,
                rois=rois,
                center_x=center_x,
                center_y=center_y,
            )
            if fit is not None:
                layout.update(fit)
                layout["status"] = STATUS_PASS
                layout["review_reason"] = None
            else:
                layout["status"] = STATUS_NEEDS_REVIEW
                layout["review_reason"] = "translation_exceeds_source_mask_at_equivalent_size"
                # Best-effort evidence overlay (safe frame width) so a review artifact
                # still exists; the cue is never promoted to PASS from this.
                review_fit = fit_layout_text(
                    text,
                    base_font,
                    target_size=target_size,
                    min_size=min_size,
                    max_width=safe_text_width,
                    max_lines=max_lines,
                    rois=rois,
                    center_x=center_x,
                    center_y=center_y,
                )
                if review_fit is not None:
                    layout.update(review_fit)
                    layout["review_fit"] = True
                else:
                    layout["review_fit"] = False

        hits = protected_roi_hits(layout, rois) if layout["mask"] else []
        if hits:
            # Any mask that grazes a protected ROI fails closed (never drawn), even if
            # the translation could not fit — this is resolved later by the clamp pass.
            layout["protected_roi_hits"] = hits
            layout["status"] = STATUS_PROTECTED_ROI_INTERSECTION
            layout["review_reason"] = "PROTECTED_ROI_INTERSECTION"
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
        "fit_budget": "confirmed_source_mask_width",
        "safe_text_width": safe_text_width,
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
        filter_layouts_plan = plan
        duration_args: list[str] = []
    else:
        render_cues = window_cues(cues, start_s=start_s, end_s=end_s)
        filter_layouts_plan = plan
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
    )
    filter_value = build_mask_filter(filter_layouts_plan, ass_path, offset_seconds=offset)
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
