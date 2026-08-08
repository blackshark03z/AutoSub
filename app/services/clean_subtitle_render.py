from __future__ import annotations

import math
import statistics
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import ImageFont

from app.core.media import media_summary
from app.services.cjk_cleanup import source_region_union


DETECTION_WIDTH = 1280
DETECTION_HEIGHT = 720
PREROLL_FRAMES = 4
POSTROLL_FRAMES = 5
MERGE_GAP_FRAMES = 6
TRANSITION_TOLERANCE_FRAMES = 3
MAX_LINE_GAP_PX = 6
DEFAULT_FONT_RATIO = 0.037
LOWER_SAFE_ANCHOR_RATIO = 0.88
HORIZONTAL_SAFE_MARGIN_RATIO = 0.08
PLATE_OPACITY = 0.76


def build_source_replacement_plan(
    source_path: Path,
    cues: list[dict[str, Any]],
    *,
    font_path: Path,
    font_size: int | None = None,
) -> dict[str, Any]:
    media = media_summary(source_path)
    output_width = int(media["video"].get("width") or 0) or DETECTION_WIDTH
    output_height = int(media["video"].get("height") or 0) or DETECTION_HEIGHT
    fps = _fps_value(media["video"].get("avg_frame_rate"))
    duration_seconds = float(media["duration_seconds"])
    font_size = font_size or max(32, round(output_height * DEFAULT_FONT_RATIO))
    font = ImageFont.truetype(str(font_path), font_size)
    render_cues = normalize_render_cues(cues, duration_seconds=duration_seconds)

    intervals: list[dict[str, Any]] = []
    layouts: list[dict[str, Any]] = []
    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open source video: {source_path}")

    try:
        intervals = detect_source_caption_intervals(
            cap,
            output_width=output_width,
            output_height=output_height,
            fps=fps,
            duration_seconds=duration_seconds,
        )
        for cue in render_cues:
            text = cue["render_text"]
            start_ms = cue["start_ms"]
            end_ms = cue["end_ms"]
            cue_start = start_ms / 1000.0
            cue_end = end_ms / 1000.0
            cue_segment_id = str(cue.get("cue_id") or cue.get("id") or f"segment_{len(layouts) + 1:04d}")
            interval = nearest_source_interval(intervals, cue_start, cue_end)
            layout = english_layout_for_interval(
                interval,
                text,
                font,
                output_width=output_width,
                output_height=output_height,
                cue_start=cue_start,
                cue_end=cue_end,
            )
            layout["segment_id"] = cue_segment_id
            layout["source_interval_id"] = interval["segment_id"]
            layouts.append(layout)
    finally:
        cap.release()

    layouts = stabilize_layouts(layouts)
    interval_stats_payload = interval_stats(
        intervals,
        output_width=output_width,
        output_height=output_height,
        output_duration_seconds=duration_seconds,
    )
    plate_stats_payload = subtitle_plate_stats(layouts, output_width=output_width, output_height=output_height)
    return {
        "media": media,
        "output_width": output_width,
        "output_height": output_height,
        "fps": fps,
        "font_size": font_size,
        "render_cues": render_cues,
        "dropped_cue_count": len(cues) - len(render_cues),
        "intervals": intervals,
        "layouts": layouts,
        "interval_stats": interval_stats_payload,
        "plate_stats": plate_stats_payload,
        "review_times": review_times(intervals, output_duration_seconds=duration_seconds),
    }


def detect_source_caption_intervals(
    cap: cv2.VideoCapture,
    *,
    output_width: int,
    output_height: int,
    fps: float,
    duration_seconds: float,
) -> list[dict[str, Any]]:
    sample_step = 0.25
    gap_tolerance = max(0.45, MERGE_GAP_FRAMES / max(fps, 1.0))
    detections: list[dict[str, Any]] = []
    time_s = 0.0
    while time_s <= duration_seconds + 0.001:
        try:
            frame = read_frame(cap, time_s, output_width, output_height)
        except RuntimeError:
            break
        bbox, _ = detect_source_subtitle_bbox(frame, output_width=output_width, output_height=output_height)
        if bbox:
            detections.append({"time": round(time_s, 3), "bbox": bbox})
        time_s += sample_step

    groups: list[list[dict[str, Any]]] = []
    for detection in detections:
        if not groups or detection["time"] - groups[-1][-1]["time"] > gap_tolerance:
            groups.append([detection])
        else:
            groups[-1].append(detection)

    intervals: list[dict[str, Any]] = []
    pad_x = max(12, round(output_width * 0.01))
    pad_y = max(8, round(output_height * 0.0075))
    preroll = PREROLL_FRAMES / max(fps, 1.0)
    postroll = POSTROLL_FRAMES / max(fps, 1.0)
    for index, group in enumerate(groups, start=1):
        raw = robust_union_boxes([item["bbox"] for item in group])
        if raw is None:
            continue
        mask = source_region_union([raw], width=output_width, height=output_height, padding_x=pad_x, padding_y=pad_y)
        base_start = max(0.0, group[0]["time"] - sample_step / 2)
        base_end = min(duration_seconds, group[-1]["time"] + sample_step / 2)
        intervals.append(
            {
                "segment_id": f"source_caption_{index:04d}",
                "start_time": clamp_time(base_start - preroll),
                "end_time": clamp_time(base_end + postroll),
                "base_start_time": round(base_start, 3),
                "base_end_time": round(base_end, 3),
                "x": mask["x"],
                "y": mask["y"],
                "width": mask["width"],
                "height": mask["height"],
                "line_count": infer_line_count(raw, output_height),
                "padding": {"vertical": pad_y, "horizontal": pad_x},
                "source_bbox": raw,
                "source_only": True,
                "detection_sample_count": len(group),
            }
        )
    return intervals


def detect_source_subtitle_bbox(frame_bgr: np.ndarray, *, output_width: int, output_height: int) -> tuple[dict[str, int] | None, dict[str, int]]:
    detection_frame = cv2.resize(frame_bgr, (DETECTION_WIDTH, DETECTION_HEIGHT))
    roi_y0, roi_y1 = 555, 690
    roi_x0, roi_x1 = 160, 1120
    roi = detection_frame[roi_y0:roi_y1, roi_x0:roi_x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bright = cv2.inRange(hsv, (0, 0, 170), (180, 110, 255))
    edges = cv2.Canny(gray, 70, 180)
    mask = cv2.bitwise_and(bright, cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 3), np.uint8), iterations=2)
    num, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    components = []
    for label in range(1, num):
        x, y, w, h, area = stats[label]
        abs_x = int(x + roi_x0)
        abs_y = int(y + roi_y0)
        if area < 8 or h < 3 or abs_y < 558 or abs_y > 681:
            continue
        components.append((abs_x, abs_y, int(w), int(h), int(area)))
    if not components:
        return None, {"component_count": 0, "pixel_count": int(mask.sum() // 255)}

    scale_x = output_width / DETECTION_WIDTH
    scale_y = output_height / DETECTION_HEIGHT
    bbox = {
        "left_x": round(min(x for x, _, _, _, _ in components) * scale_x),
        "top_y": round(min(y for _, y, _, _, _ in components) * scale_y),
        "right_x": round(max((x + w - 1) for x, _, w, _, _ in components) * scale_x),
        "bottom_y": round(max((y + h - 1) for _, y, _, h, _ in components) * scale_y),
    }
    width = bbox["right_x"] - bbox["left_x"] + 1
    height = bbox["bottom_y"] - bbox["top_y"] + 1
    min_width = max(100, round(output_width * 0.06))
    min_height = max(24, round(output_height * 0.02))
    if width < min_width or height < min_height:
        return None, {"component_count": len(components), "pixel_count": int(mask.sum() // 255), "rejected": "bbox_too_small"}
    return bbox, {"component_count": len(components), "pixel_count": int(mask.sum() // 255)}


def english_layout_for_interval(
    interval: dict[str, Any],
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    output_width: int,
    output_height: int,
    cue_start: float | None = None,
    cue_end: float | None = None,
) -> dict[str, Any]:
    bbox = interval.get("source_bbox") or default_source_bbox(output_width, output_height)
    # Presentation geometry is intentionally independent of the detected source
    # caption position.  Detection remains useful only for suppressing source text.
    # This gives adjacent English cues a stable, player-safe lower-third anchor.
    center_x = output_width // 2
    horizontal_margin = max(24, round(output_width * HORIZONTAL_SAFE_MARGIN_RATIO))
    max_text_width = output_width - (horizontal_margin * 2)
    render_text = wrap_subtitle_text(text, font, max_text_width)
    text_box = multiline_text_box(render_text, font)
    line_count = max(1, render_text.count("\n") + 1) if render_text else 1
    anchor_y = round(output_height * LOWER_SAFE_ANCHOR_RATIO)
    padding_x = max(18, round(font.size * 0.55))
    padding_y = max(10, round(font.size * 0.28))
    plate_width = min(output_width - (horizontal_margin * 2), max(text_box["width"] + padding_x * 2, font.size * 3))
    plate_height = text_box["height"] + padding_y * 2
    plate_x = max(horizontal_margin, min(output_width - horizontal_margin - plate_width, center_x - plate_width // 2))
    plate_y = max(24, min(output_height - 24 - plate_height, anchor_y - plate_height // 2))
    return {
        "render_text": render_text,
        "center_x": center_x,
        "anchor_y": anchor_y,
        "start_time": round(cue_start if cue_start is not None else interval["start_time"], 3),
        "end_time": round(cue_end if cue_end is not None else interval["end_time"], 3),
        "line_count": line_count,
        "font_size": font.size,
        "text_width": text_box["width"],
        "text_height": text_box["height"],
        "plate": {
            "x": plate_x,
            "y": plate_y,
            "width": plate_width,
            "height": plate_height,
            "right_x": plate_x + plate_width - 1,
            "bottom_y": plate_y + plate_height - 1,
        },
        "source_baseline_zone": [max(0, bbox["bottom_y"] - 12), bbox["bottom_y"]],
        "plate_area_frame_percent": round(plate_width * plate_height * 100 / (output_width * output_height), 4),
        "clipping": plate_x <= 0 or (plate_x + plate_width - 1) >= output_width - 1 or plate_y <= 0 or (plate_y + plate_height - 1) >= output_height - 1,
    }


def nearest_source_interval(intervals: list[dict[str, Any]], cue_start: float, cue_end: float) -> dict[str, Any]:
    if not intervals:
        return default_source_interval(cue_start, cue_end)
    cue_mid = (cue_start + cue_end) / 2

    def score(interval: dict[str, Any]) -> tuple[float, float]:
        overlap = max(0.0, min(cue_end, interval["end_time"]) - max(cue_start, interval["start_time"]))
        interval_mid = (interval["start_time"] + interval["end_time"]) / 2
        # Prefer actual overlap; otherwise choose the nearest source caption zone.
        return (-overlap, abs(interval_mid - cue_mid))

    nearest = min(intervals, key=score)
    if score(nearest)[1] > 2.0 and score(nearest)[0] == 0.0:
        return default_source_interval(cue_start, cue_end)
    return nearest


def default_source_interval(cue_start: float, cue_end: float) -> dict[str, Any]:
    return {
        "segment_id": f"cue_fallback_{round(cue_start * 1000):08d}",
        "start_time": round(cue_start, 3),
        "end_time": round(cue_end, 3),
        "source_bbox": None,
        "source_only": False,
    }


def default_source_bbox(output_width: int, output_height: int) -> dict[str, int]:
    band_width = round(output_width * 0.72)
    band_height = round(output_height * 0.11)
    center_x = output_width // 2
    center_y = round(output_height * 0.84)
    left = max(0, center_x - band_width // 2)
    top = max(0, center_y - band_height // 2)
    right = min(output_width - 1, left + band_width - 1)
    bottom = min(output_height - 1, top + band_height - 1)
    return {"left_x": left, "top_y": top, "right_x": right, "bottom_y": bottom}


def write_clean_subtitles_ass(
    cues: list[dict[str, Any]],
    layouts: list[dict[str, Any]],
    path: Path,
    *,
    font_size: int,
    output_width: int,
    output_height: int,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    layout_by_segment = {layout["segment_id"]: layout for layout in layouts}
    events = []
    for cue in cues:
        text = cue_text(cue)
        if not is_meaningful_subtitle_text(text):
            continue
        layout = layout_by_segment.get(str(cue.get("cue_id") or cue.get("id") or ""))
        if layout is None:
            continue
        # Layouts are created from validated source cue times.  Do not retime for
        # visual presentation; ASS centisecond serialization is the only precision
        # conversion in this path.
        start_ms = int(cue.get("start_ms") or 0)
        end_ms = int(cue.get("end_ms") or 0)
        if end_ms <= start_ms:
            continue
        override = f"{{\\an5\\fs{layout.get('font_size', font_size)}\\bord2\\shad1\\pos({layout['center_x']},{layout['anchor_y']})}}"
        events.append(
            "Dialogue: 2,"
            f"{format_ass_timestamp(start_ms)},{format_ass_timestamp(end_ms)},"
            f"Default,,0,0,0,,{override}{ass_escape(layout['render_text'])}"
        )
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {output_width}
PlayResY: {output_height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


def render_source_replacement_video(
    source_path: Path,
    ass_path: Path,
    output_path: Path,
    intervals: list[dict[str, Any]],
    layouts: list[dict[str, Any]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_value = build_source_replacement_filter(intervals, layouts, ass_path)
    temp_output = output_path.with_suffix(".tmp.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
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


def build_source_replacement_filter(
    intervals: list[dict[str, Any]],
    layouts: list[dict[str, Any]],
    ass_path: Path,
) -> str:
    ass = _ffmpeg_filter_path(ass_path)
    filters = []
    intervals_by_id = {str(interval.get("segment_id") or ""): interval for interval in intervals}
    for layout in layouts:
        # A source mask is only allowed while its corresponding valid English cue
        # is rendered.  The old interval-wide mask produced empty black boxes in
        # gaps between cues.
        interval = intervals_by_id.get(str(layout.get("source_interval_id") or ""))
        if interval is None and len(intervals) == 1:
            interval = intervals[0]
        if interval is None:
            continue
        enable = f"between(t\\,{layout['start_time']:.3f}\\,{layout['end_time']:.3f})"
        filters.append(
            "delogo="
            f"x={interval['x']}:y={interval['y']}:w={interval['width']}:h={interval['height']}:"
            f"show=0:enable='{enable}'"
        )
    for layout in layouts:
        plate = layout["plate"]
        enable = f"between(t\\,{layout['start_time']:.3f}\\,{layout['end_time']:.3f})"
        filters.append(
            "drawbox="
            f"x={plate['x']}:y={plate['y']}:w={plate['width']}:h={plate['height']}:"
            f"color=black@{PLATE_OPACITY:.3f}:t=fill:enable='{enable}'"
        )
    filters.append(f"subtitles='{ass}'")
    return ",".join(filters)


def _ffmpeg_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def interval_stats(
    intervals: list[dict[str, Any]],
    *,
    output_width: int,
    output_height: int,
    output_duration_seconds: float,
) -> dict[str, Any]:
    if not intervals:
        return {
            "interval_count": 0,
            "masked_duration_seconds": 0.0,
            "masked_duration_percent": 0.0,
            "masked_average_area_frame_percent": 0.0,
        }
    widths = [item["width"] for item in intervals]
    heights = [item["height"] for item in intervals]
    areas = [item["width"] * item["height"] for item in intervals]
    merged_duration = merged_interval_duration(intervals)
    return {
        "interval_count": len(intervals),
        "median_width": round(statistics.median(widths), 3),
        "median_height": round(statistics.median(heights), 3),
        "p95_width": percentile(widths, 95),
        "p95_height": percentile(heights, 95),
        "min_width": min(widths),
        "max_width": max(widths),
        "min_height": min(heights),
        "max_height": max(heights),
        "one_line_count": sum(1 for item in intervals if item["line_count"] == 1),
        "two_line_count": sum(1 for item in intervals if item["line_count"] >= 2),
        "masked_duration_seconds": round(merged_duration, 3),
        "masked_duration_percent": round(merged_duration * 100 / max(output_duration_seconds, 1.0), 3),
        "average_area_px": round(statistics.mean(areas), 3),
        "average_area_frame_percent": round(statistics.mean(areas) * 100 / (output_width * output_height), 3),
        "min_area_px": min(areas),
        "max_area_px": max(areas),
    }


def merged_interval_duration(intervals: list[dict[str, Any]]) -> float:
    ordered = sorted(
        ((float(item["start_time"]), float(item["end_time"])) for item in intervals if item.get("end_time", 0) > item.get("start_time", 0)),
        key=lambda pair: pair[0],
    )
    if not ordered:
        return 0.0
    total = 0.0
    current_start, current_end = ordered[0]
    for start_time, end_time in ordered[1:]:
        if start_time <= current_end:
            current_end = max(current_end, end_time)
        else:
            total += max(0.0, current_end - current_start)
            current_start, current_end = start_time, end_time
    total += max(0.0, current_end - current_start)
    return total


def subtitle_plate_stats(layouts: list[dict[str, Any]], *, output_width: int, output_height: int) -> dict[str, Any]:
    if not layouts:
        return {
            "font_size": None,
            "plate_median_width": 0,
            "plate_median_height": 0,
            "plate_p95_width": 0,
            "plate_p95_height": 0,
            "plate_average_frame_area_percent": 0.0,
        }
    widths = [item["plate"]["width"] for item in layouts]
    heights = [item["plate"]["height"] for item in layouts]
    areas = [item["plate"]["width"] * item["plate"]["height"] for item in layouts]
    anchor_ys = [item["anchor_y"] for item in layouts]
    return {
        "font_size": layouts[0]["font_size"] if layouts else None,
        "source_baseline_zone_median": [
            round(statistics.median(item["source_baseline_zone"][0] for item in layouts), 3),
            round(statistics.median(item["source_baseline_zone"][1] for item in layouts), 3),
        ],
        "subtitle_anchor_y_median": round(statistics.median(anchor_ys), 3),
        "plate_median_width": round(statistics.median(widths), 3),
        "plate_median_height": round(statistics.median(heights), 3),
        "plate_p95_width": percentile(widths, 95),
        "plate_p95_height": percentile(heights, 95),
        "plate_average_frame_area_percent": round(statistics.mean(areas) * 100 / (output_width * output_height), 4),
        "one_line_count": sum(1 for item in layouts if item["line_count"] == 1),
        "two_line_count": sum(1 for item in layouts if item["line_count"] == 2),
        "subtitle_clipping_count": sum(1 for item in layouts if item["clipping"]),
        "max_line_count": max(item["line_count"] for item in layouts),
    }


def render_contact_sheet(
    video_path: Path,
    output_path: Path,
    intervals: list[dict[str, Any]] | None,
    times: list[float],
) -> None:
    frames = []
    cap = cv2.VideoCapture(str(video_path))
    try:
        for time_s in times[:12]:
            frame = read_frame(cap, time_s)
            if intervals:
                for interval in active_intervals(intervals, time_s):
                    cv2.rectangle(
                        frame,
                        (interval["x"], interval["y"]),
                        (interval["x"] + interval["width"], interval["y"] + interval["height"]),
                        (0, 0, 255),
                        2,
                    )
            frame = cv2.resize(frame, (320, 180))
            cv2.putText(frame, f"{time_s:.2f}s", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            frames.append(frame)
    finally:
        cap.release()
    while len(frames) < 12:
        frames.append(np.zeros((180, 320, 3), dtype=np.uint8))
    rows = [np.hstack(frames[0:4]), np.hstack(frames[4:8]), np.hstack(frames[8:12])]
    cv2.imwrite(str(output_path), np.vstack(rows))


def render_source_match_contact_sheet(video_path: Path, output_path: Path, intervals: list[dict[str, Any]], times: list[float]) -> None:
    render_contact_sheet(video_path, output_path, intervals, times)


def active_intervals(intervals: list[dict[str, Any]], time_s: float) -> list[dict[str, Any]]:
    return [item for item in intervals if item["start_time"] <= time_s <= item["end_time"]]


def merge_close_intervals(intervals: list[dict[str, Any]], *, fps: float) -> list[dict[str, Any]]:
    merge_gap_s = MERGE_GAP_FRAMES / fps
    ordered = sorted(intervals, key=lambda item: item["start_time"])
    if not ordered:
        return ordered
    merged: list[dict[str, Any]] = [dict(ordered[0])]
    for current in ordered[1:]:
        previous = merged[-1]
        if current["start_time"] - previous["end_time"] <= merge_gap_s:
            previous["end_time"] = max(previous["end_time"], current["end_time"])
            previous["x"] = min(previous["x"], current["x"])
            previous["y"] = min(previous["y"], current["y"])
            right = max(previous["x"] + previous["width"] - 1, current["x"] + current["width"] - 1)
            bottom = max(previous["y"] + previous["height"] - 1, current["y"] + current["height"] - 1)
            previous["width"] = right - previous["x"] + 1
            previous["height"] = bottom - previous["y"] + 1
            previous["source_bbox"] = union_boxes([previous.get("source_bbox"), current.get("source_bbox")]) or previous.get("source_bbox")
        else:
            merged.append(dict(current))
    return merged


def stabilize_layouts(layouts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stabilized = []
    by_segment: dict[str, list[dict[str, Any]]] = {}
    for layout in layouts:
        by_segment.setdefault(layout["segment_id"], []).append(layout)
    for items in by_segment.values():
        if len(items) == 1:
            stabilized.append(items[0])
            continue
        widths = [item["plate"]["width"] for item in items]
        heights = [item["plate"]["height"] for item in items]
        if max(widths) - min(widths) <= 48 and max(heights) - min(heights) <= 48:
            stable = {
                "x": min(item["plate"]["x"] for item in items),
                "y": min(item["plate"]["y"] for item in items),
                "right_x": max(item["plate"]["right_x"] for item in items),
                "bottom_y": max(item["plate"]["bottom_y"] for item in items),
            }
            stable["width"] = stable["right_x"] - stable["x"] + 1
            stable["height"] = stable["bottom_y"] - stable["y"] + 1
            for item in items:
                clone = dict(item)
                clone["plate"] = dict(stable)
                clone["stable_per_sequence_geometry"] = True
                stabilized.append(clone)
        else:
            stabilized.extend(items)
    return sorted(stabilized, key=lambda item: (item.get("start_time", 0.0), item["segment_id"]))


def read_frame(cap: cv2.VideoCapture, time_s: float, output_width: int | None = None, output_height: int | None = None) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_s) * 1000)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError(f"Could not read frame at {time_s:.3f}s")
    if output_width and output_height:
        frame = cv2.resize(frame, (output_width, output_height))
    return frame


def robust_union_boxes(boxes: list[dict[str, int]]) -> dict[str, int] | None:
    if not boxes:
        return None
    filtered = [box for box in boxes if box]
    if not filtered:
        return None
    center_ys = [box["top_y"] + (box["bottom_y"] - box["top_y"]) / 2 for box in filtered]
    median_center = statistics.median(center_ys)
    nearby = [box for box, center_y in zip(filtered, center_ys) if abs(center_y - median_center) <= 48]
    selected = nearby or filtered
    return union_boxes(selected)


def union_boxes(boxes: list[dict[str, int] | None]) -> dict[str, int] | None:
    filtered = [box for box in boxes if box]
    if not filtered:
        return None
    return {
        "left_x": min(box["left_x"] for box in filtered),
        "top_y": min(box["top_y"] for box in filtered),
        "right_x": max(box["right_x"] for box in filtered),
        "bottom_y": max(box["bottom_y"] for box in filtered),
    }


def infer_line_count(raw: dict[str, int], output_height: int) -> int:
    height = raw["bottom_y"] - raw["top_y"] + 1
    threshold = max(42, round(output_height * 0.12))
    return 2 if height >= threshold else 1


def estimate_text_width(text: str, font: ImageFont.FreeTypeFont) -> int:
    if not text:
        return 0
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def wrap_subtitle_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    if estimate_text_width(cleaned, font) <= max_width:
        return cleaned
    words = cleaned.split()
    if len(words) <= 1:
        return cleaned
    best: tuple[tuple[int, int], str, str] | None = None
    for split_index in range(1, len(words)):
        first = " ".join(words[:split_index])
        second = " ".join(words[split_index:])
        first_width = estimate_text_width(first, font)
        second_width = estimate_text_width(second, font)
        max_line_width = max(first_width, second_width)
        if max_line_width > max_width:
            continue
        score = (max_line_width, abs(first_width - second_width))
        if best is None or score < best[0]:
            best = (score, first, second)
    if best:
        return best[1] + "\n" + best[2]
    # If no split satisfies the preferred width, retain every word and choose the
    # most balanced two-line split.  We never create a third line or split a word.
    balanced = min(
        (
            (
                abs(estimate_text_width(" ".join(words[:index]), font) - estimate_text_width(" ".join(words[index:]), font)),
                " ".join(words[:index]),
                " ".join(words[index:]),
            )
            for index in range(1, len(words))
        ),
        key=lambda item: item[0],
    )
    return balanced[1] + "\n" + balanced[2]


def multiline_text_box(text: str, font: ImageFont.FreeTypeFont) -> dict[str, int]:
    if not text:
        return {"width": 0, "height": 0}
    lines = text.split("\n")
    widths = [estimate_text_width(line, font) for line in lines]
    heights = []
    for line in lines:
        bbox = font.getbbox(line or " ")
        heights.append(bbox[3] - bbox[1])
    return {
        "width": max(widths),
        "height": sum(heights) + max(0, len(lines) - 1) * MAX_LINE_GAP_PX,
    }


def clean_text(value: str) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized.replace("\u00ad", "")


def cue_text(cue: dict[str, Any]) -> str:
    return clean_text(cue.get("resolved_text") or cue.get("text") or cue.get("subtitle_text") or "")


def is_meaningful_subtitle_text(text: str) -> bool:
    """Reject whitespace and punctuation-only cues before any plate is generated."""
    return any(unicodedata.category(character)[0] in {"L", "N"} for character in clean_text(text))


def normalize_render_cues(cues: list[dict[str, Any]], *, duration_seconds: float) -> list[dict[str, Any]]:
    """Return renderable, monotonic cues without changing valid authoritative times."""
    duration_ms = max(0, round(duration_seconds * 1000))
    normalized: list[dict[str, Any]] = []
    previous_start = -1
    previous_end = -1
    for cue in cues:
        text = cue_text(cue)
        if not is_meaningful_subtitle_text(text):
            continue
        try:
            start_ms = int(cue.get("start_ms"))
            end_ms = int(cue.get("end_ms"))
        except (TypeError, ValueError):
            continue
        # Boundary clipping is timing-safety normalization, not a layout decision.
        start_ms = min(max(start_ms, 0), duration_ms)
        end_ms = min(max(end_ms, 0), duration_ms)
        if end_ms <= start_ms or start_ms < previous_start or start_ms < previous_end:
            continue
        normalized.append({**cue, "start_ms": start_ms, "end_ms": end_ms, "render_text": text})
        previous_start = start_ms
        previous_end = end_ms
    return normalized


def ass_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", r"\N")
    )


def format_ass_timestamp(milliseconds: int) -> str:
    total_centiseconds = max(0, milliseconds) // 10
    centiseconds = total_centiseconds % 100
    total_seconds = total_centiseconds // 100
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _fps_value(avg_frame_rate: str | None) -> float:
    value = str(avg_frame_rate or "").strip()
    if not value:
        return 30.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        try:
            numerator_value = float(numerator)
            denominator_value = float(denominator)
            if denominator_value:
                return max(1.0, numerator_value / denominator_value)
        except Exception:
            return 30.0
    try:
        return max(1.0, float(value))
    except Exception:
        return 30.0


def _segment_samples(start_s: float, end_s: float, fps: float, *, duration_seconds: float | None = None) -> list[float]:
    safe_fps = max(fps, 1.0)
    midpoint = (start_s + end_s) / 2
    video_tail = max(0.08, TRANSITION_TOLERANCE_FRAMES / safe_fps)
    upper_bound = end_s + max(0.25, TRANSITION_TOLERANCE_FRAMES / safe_fps)
    if duration_seconds is not None and duration_seconds > 0:
        upper_bound = min(upper_bound, max(0.0, duration_seconds - video_tail))
    samples = [
        clamp_time(start_s + 0.04),
        clamp_time(midpoint),
        clamp_time(max(start_s, end_s - 0.04)),
        clamp_time(max(0.0, start_s - TRANSITION_TOLERANCE_FRAMES / safe_fps)),
        clamp_time(start_s + TRANSITION_TOLERANCE_FRAMES / safe_fps),
        clamp_time(max(0.0, end_s - TRANSITION_TOLERANCE_FRAMES / safe_fps)),
        clamp_time(end_s + TRANSITION_TOLERANCE_FRAMES / safe_fps),
    ]
    current = math.floor(start_s * 2) / 2
    while current <= end_s + 0.001:
        if start_s <= current <= end_s:
            samples.append(clamp_time(current))
        current += 0.5
    return sorted({round(item, 3) for item in samples if 0 <= item <= upper_bound})


def clamp_time(value: float) -> float:
    return round(max(0.0, value), 3)


def percentile(values: list[int], pct: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * pct / 100
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return float(ordered[low])
    return round(ordered[low] * (high - index) + ordered[high] * (index - low), 3)


def review_times(intervals: list[dict[str, Any]], *, output_duration_seconds: float) -> list[float]:
    times = {0.5, 2.0, 5.0, 10.0, 16.0, 22.0, 38.0, 42.0, 46.0, 50.0, 60.0, 90.0, 150.0, 210.0, 240.0, 300.0, max(0.5, output_duration_seconds - 1.0)}
    for interval in intervals[:10]:
        times.add(interval["start_time"])
        times.add((interval["start_time"] + interval["end_time"]) / 2)
        times.add(interval["end_time"])
    return sorted(round(clamp_time(time_s), 3) for time_s in times)
