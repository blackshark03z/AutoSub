from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any

from PIL import ImageFont

from app.services.ass_utils import ass_escape, format_ass_timestamp


def _median_bbox(track: dict[str, Any]) -> tuple[int, int, int, int]:
    boxes = [item.get("bbox") for item in list(track.get("observations") or [])]
    valid = [box for box in boxes if isinstance(box, list) and len(box) == 4]
    if not valid:
        raise ValueError("TextTrack has no usable geometry")
    return tuple(round(statistics.median(float(box[index]) for box in valid)) for index in range(4))  # type: ignore[return-value]


def _geometry_stability(track: dict[str, Any]) -> float:
    """Measure surface-position stability, not OCR box-size jitter.

    OCR often expands/contracts the recognized glyph box across frames even when
    the text is visually stationary. Tier-A uses a bounded envelope for removal,
    so center drift is the relevant signal for whether a static replacement is
    safe. Truly moving text still fails this gate.
    """
    boxes = [item.get("bbox") for item in list(track.get("observations") or [])]
    valid = [box for box in boxes if isinstance(box, list) and len(box) == 4]
    if len(valid) <= 1:
        return 1.0
    centers = [((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0) for box in valid]
    diagonals = [
        max(1.0, math.hypot(float(box[2] - box[0]), float(box[3] - box[1])))
        for box in valid
    ]
    scale = max(1.0, statistics.median(diagonals))
    median_x = statistics.median(point[0] for point in centers)
    median_y = statistics.median(point[1] for point in centers)
    drift = max(math.hypot(x - median_x, y - median_y) for x, y in centers) / scale
    return max(0.0, 1.0 - drift)


def _bounded_observation_envelope(
    track: dict[str, Any],
    median_box: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    boxes = [item.get("bbox") for item in list(track.get("observations") or [])]
    valid = [box for box in boxes if isinstance(box, list) and len(box) == 4]
    if len(valid) <= 1:
        return median_box
    left, top, right, bottom = median_box
    width = max(1, right - left)
    height = max(1, bottom - top)
    expand_x = max(4, round(width * 0.35))
    expand_y = max(3, round(height * 0.35))
    observed_left = min(int(box[0]) for box in valid)
    observed_top = min(int(box[1]) for box in valid)
    observed_right = max(int(box[2]) for box in valid)
    observed_bottom = max(int(box[3]) for box in valid)
    return (
        max(left - expand_x, observed_left),
        max(top - expand_y, observed_top),
        min(right + expand_x, observed_right),
        min(bottom + expand_y, observed_bottom),
    )


def _fit_text(text: str, box_width: int, box_height: int, font_path: Path) -> tuple[int, str, bool]:
    max_width = max(24, int(box_width * 1.5))
    max_height = max(18, int(box_height * 1.7))
    start_size = max(14, min(72, round(box_height * 0.9)))
    # Tiny source UI labels are legitimately smaller than dialogue text. Keep
    # ordinary labels at the 12 px floor, but allow a source-relative floor down
    # to 9 px when the original role itself is tiny. This is preferable to
    # semantically condensing a translation that already fits at source scale.
    minimum_font_size = max(9, min(12, round(box_height * 0.70)))
    words = text.split()
    candidates = [text]
    if len(words) > 1:
        for index in range(1, len(words)):
            candidates.append(" ".join(words[:index]) + "\n" + " ".join(words[index:]))
    for font_size in range(start_size, minimum_font_size - 1, -1):
        font = ImageFont.truetype(str(font_path), font_size)
        for candidate in candidates:
            lines = candidate.split("\n")
            widths = [font.getbbox(line or " ")[2] - font.getbbox(line or " ")[0] for line in lines]
            heights = [font.getbbox(line or " ")[3] - font.getbbox(line or " ")[1] for line in lines]
            total_height = sum(heights) + max(0, len(lines) - 1) * max(2, font_size // 8)
            if max(widths, default=0) <= max_width and total_height <= max_height and len(lines) <= 2:
                return font_size, candidate, False
    return minimum_font_size, text, True


def build_tier_a_replacement_plan(
    localized: dict[str, Any],
    *,
    output_width: int,
    output_height: int,
    font_path: Path,
    sample_seconds: float,
    minimum_track_confidence: float = 0.90,
    minimum_geometry_stability: float = 0.70,
) -> dict[str, Any]:
    if output_width <= 0 or output_height <= 0 or sample_seconds <= 0:
        raise ValueError("invalid Tier-A visual replacement geometry")
    replacements: list[dict[str, Any]] = []
    escalations: list[dict[str, Any]] = []
    half_sample_ms = round(sample_seconds * 500)
    for track in list(localized.get("tracks") or []):
        classification = track.get("classification") or {}
        if classification.get("action") != "localize":
            continue
        track_id = str(track.get("track_id") or "")
        display_text = str(track.get("display_text") or "").strip()
        confidence = float(track.get("confidence") or 0)
        stability = _geometry_stability(track)
        try:
            left, top, right, bottom = _median_bbox(track)
        except ValueError:
            escalations.append({"track_id": track_id, "reason": "missing_geometry"})
            continue
        if not display_text:
            escalations.append({"track_id": track_id, "reason": "missing_target_text"})
            continue
        recognition = track.get("recognition") if isinstance(track.get("recognition"), dict) else {}
        recognition_status = str(recognition.get("status") or "").strip().lower()
        if recognition_status and recognition_status != "verified":
            escalations.append({"track_id": track_id, "reason": "recognition_unverified", "confidence": confidence})
            continue
        # 0.90 is an OCR attention threshold in the frozen contract, not a
        # standalone replacement reject. Legacy callers without recognition
        # evidence keep the conservative old behavior; composed V2 analysis
        # supplies explicit verification from whole-frame or crop consensus.
        if not recognition_status and confidence < minimum_track_confidence:
            escalations.append({"track_id": track_id, "reason": "low_ocr_confidence", "confidence": confidence})
            continue
        moving_track = stability < minimum_geometry_stability
        box_width = max(1, right - left)
        box_height = max(1, bottom - top)
        font_size, render_text, overflow = _fit_text(display_text, box_width, box_height, font_path)
        if overflow:
            escalations.append({"track_id": track_id, "reason": "target_text_does_not_fit"})
            continue
        envelope_left, envelope_top, envelope_right, envelope_bottom = _bounded_observation_envelope(
            track, (left, top, right, bottom)
        )
        envelope_width = max(1, envelope_right - envelope_left)
        envelope_height = max(1, envelope_bottom - envelope_top)
        pad_x = max(3, round(envelope_width * 0.08))
        pad_y = max(2, round(envelope_height * 0.12))
        x = max(0, envelope_left - pad_x)
        y = max(0, envelope_top - pad_y)
        x2 = min(output_width - 1, envelope_right + pad_x)
        y2 = min(output_height - 1, envelope_bottom + pad_y)
        start_ms = max(0, int(track.get("start_ms") or 0) - half_sample_ms)
        end_ms = max(start_ms + 1, int(track.get("end_ms") or 0) + half_sample_ms)
        base_item = {
            "track_id": track_id,
            "source_text": str(track.get("canonical_text") or ""),
            "display_text": display_text,
            "track_confidence": confidence,
            "geometry_stability": round(stability, 6),
            "tier": "A",
        }
        if moving_track:
            observations = [item for item in list(track.get("observations") or []) if isinstance(item.get("bbox"), list) and len(item.get("bbox")) == 4]
            if len(observations) < 2:
                escalations.append({"track_id": track_id, "reason": "unstable_geometry", "geometry_stability": round(stability, 6)})
                continue
            for ordinal, observation in enumerate(observations):
                box = [int(value) for value in observation["bbox"]]
                ol, ot, oright, obottom = box
                ow, oh = max(1, oright - ol), max(1, obottom - ot)
                ofont, otext, ooverflow = _fit_text(display_text, ow, oh, font_path)
                if ooverflow:
                    escalations.append({"track_id": track_id, "reason": "moving_target_text_does_not_fit", "observation": ordinal})
                    break
                pad_x = max(3, round(ow * 0.08))
                pad_y = max(2, round(oh * 0.12))
                timestamp = int(observation.get("time_ms") or track.get("start_ms") or 0)
                replacements.append({**base_item, "track_id": f"{track_id}__{ordinal:03d}", "parent_track_id": track_id, "start_ms": max(0, timestamp - half_sample_ms), "end_ms": timestamp + half_sample_ms, "source_bbox": box, "mask": {"x": max(0, ol-pad_x), "y": max(0, ot-pad_y), "width": min(output_width-1, oright+pad_x)-max(0, ol-pad_x)+1, "height": min(output_height-1, obottom+pad_y)-max(0, ot-pad_y)+1}, "center_x": round((ol+oright)/2), "center_y": round((ot+obottom)/2), "font_size": ofont, "render_text": otext, "motion_segment": True})
            if escalations and escalations[-1].get("track_id") == track_id:
                replacements[:] = [item for item in replacements if item.get("parent_track_id") != track_id]
            continue
        replacements.append({**base_item, "start_ms": start_ms, "end_ms": end_ms, "source_bbox": [left, top, right, bottom], "mask": {"x": x, "y": y, "width": x2 - x + 1, "height": y2 - y + 1}, "center_x": round((left + right) / 2), "center_y": round((top + bottom) / 2), "font_size": font_size, "render_text": render_text})
    return {
        "schema_version": 1,
        "replacement_count": len(replacements),
        "escalation_count": len(escalations),
        "replacements": replacements,
        "escalations": escalations,
    }


def write_tier_a_ass(
    path: Path,
    plan: dict[str, Any],
    *,
    output_width: int,
    output_height: int,
) -> Path:
    events: list[str] = []
    for item in plan.get("replacements", []):
        start = format_ass_timestamp(int(item["start_ms"]))
        end = format_ass_timestamp(int(item["end_ms"]))
        override = (
            f"{{\\an5\\fs{int(item['font_size'])}\\bord2\\shad1"
            f"\\pos({int(item['center_x'])},{int(item['center_y'])})}}"
        )
        events.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{override}{ass_escape(str(item['render_text']))}"
        )
    header = f"""[Script Info]\nScriptType: v4.00+\nPlayResX: {output_width}\nPlayResY: {output_height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,32,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,5,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


def build_tier_a_filter(plan: dict[str, Any], ass_path: Path) -> str:
    filters: list[str] = []
    for item in plan.get("replacements", []):
        mask = item["mask"]
        start = int(item["start_ms"]) / 1000.0
        end = int(item["end_ms"]) / 1000.0
        enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
        filters.append(
            "delogo="
            f"x={mask['x']}:y={mask['y']}:w={mask['width']}:h={mask['height']}:"
            f"show=0:enable='{enable}'"
        )
    escaped = ass_path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
    filters.append(f"subtitles='{escaped}'")
    return ",".join(filters)
