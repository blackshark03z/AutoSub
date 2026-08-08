import json
import math
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.services.subtitles import format_ass_timestamp
from app.services.timeline import load_latest_timeline


WIDTH = 1280
HEIGHT = 720
DURATION_SECONDS = 75.0
TRANSITION_TOLERANCE_SECONDS = 0.10
FPS = 30
PREROLL_FRAMES = 4
POSTROLL_FRAMES = 5
HYSTERESIS_OFF_FRAMES = 4
MERGE_GAP_FRAMES = 6
TRANSITION_UNION_FRAMES = 3
VERTICAL_PADDING = 8
HORIZONTAL_PADDING = 12
ENGLISH_FONT_SIZE = 44
CP06G_FONT_SIZE = 40
CP06G_MARGIN = "per_event_pos"
CP06D_STATIC_MASK = {"x": 168, "y": 554, "width": 944, "height": 134}


@dataclass(frozen=True)
class Sample:
    time_s: float
    reason: str
    segment_id: str | None = None


def main() -> None:
    settings = get_settings()
    project_id = "vertical_slice_cp02"
    project_dir = settings.data_dir / "projects" / project_id
    evidence_dir = settings.root / "evidence" / "CP06I"
    if evidence_dir.exists():
        shutil.rmtree(evidence_dir)
    evidence_dir.mkdir(parents=True)

    timeline = load_latest_timeline(project_id)
    font = ImageFont.truetype(str(settings.font_path), 40)
    intervals = build_dynamic_intervals(settings.source_path, timeline, font)
    regular_samples = [Sample(clamp_time(second), "regular_1s") for second in range(int(DURATION_SECONDS) + 1)]
    interval_samples = build_interval_samples(timeline)
    all_samples = unique_samples(regular_samples + interval_samples)
    intervals = repair_uncovered_source_subtitles(settings.source_path, intervals, all_samples, font)
    temporal = temporal_stabilize_intervals(intervals)
    render_intervals = temporal["render_intervals"]

    before_contact = evidence_dir / "before_cp06h_same_timestamps.jpg"
    after_contact = evidence_dir / "after_cp06i_same_timestamps.jpg"
    source_contact = evidence_dir / "source_eraser_mask_contact_sheet.jpg"
    layout_contact = evidence_dir / "english_layout_contact_sheet.jpg"
    source_match_contact = evidence_dir / "source_matched_geometry_contact_sheet.jpg"
    render_contact_sheet(
        project_dir / "renders" / "cp06h_render_truth_subtitle_vertical_slice_720p.mp4",
        before_contact,
        None,
        comparison_times(timeline),
        source_only=False,
    )
    render_contact_sheet(settings.source_path, source_contact, intervals, comparison_times(timeline), source_only=True)
    render_source_match_contact_sheet(settings.source_path, source_match_contact, intervals, comparison_times(timeline))

    ass_path = project_dir / "subtitles" / "cp06i_temporally_stable_english.ass"
    write_source_matched_ass(timeline, intervals, ass_path)
    output_path = project_dir / "renders" / "cp06i_temporally_stable_subtitle_vertical_slice_720p.mp4"
    tts_mix = project_dir / "audio" / "cp06b_grouped_tts_mix.wav"
    render_preview(settings.source_path, tts_mix, ass_path, output_path, render_intervals)
    render_contact_sheet(output_path, after_contact, None, comparison_times(timeline), source_only=False)
    render_contact_sheet(output_path, layout_contact, None, layout_review_times(timeline), source_only=False)
    render_truth = render_truth_audit(settings.source_path, output_path, render_intervals, evidence_dir)
    transition_audit = transition_flash_audit(settings.source_path, output_path, render_intervals, timeline, evidence_dir)
    strips = render_transition_strips(output_path, evidence_dir, timeline)

    coverage = count_exposed_pixels(settings.source_path, all_samples, render_intervals)
    stats = interval_stats(render_intervals)
    media = media_summary(output_path)
    summary = {
        "project_id": project_id,
        "artifact_path": str(output_path),
        "artifact_sha256": sha256_file(output_path),
        "media": media,
        "cp06d_static_mask": CP06D_STATIC_MASK,
        "eraser_interval_count": len(render_intervals),
        "base_eraser_intervals": intervals,
        "eraser_intervals": render_intervals,
        "eraser_stats": stats,
        "temporal_settings": temporal["settings"],
        "temporal_stabilization": temporal["summary"],
        "source_subtitle_geometry": source_geometry_summary(intervals),
        "english_subtitle_layout": english_layout_summary(timeline, intervals, font),
        "render_truth_audit": render_truth,
        "transition_flash_audit": transition_audit,
        "coverage": coverage,
        "sample_count": len(all_samples),
        "comparison_timestamps": comparison_times(timeline),
        "contact_sheets": {
            "before_cp06h_same_timestamps": str(before_contact),
            "source_eraser_mask": str(source_contact),
            "source_matched_geometry": str(source_match_contact),
            "after_cp06i_same_timestamps": str(after_contact),
            "english_layout": str(layout_contact),
            "transition_strips": strips,
        },
        "audio_reused_from": str(tts_mix),
        "audio_reused_sha256": sha256_file(tts_mix),
        "provider_calls": {"gemini": 0, "elevenlabs": 0},
        "free_disk_gb": round(shutil.disk_usage(settings.root).free / (1024**3), 2),
    }
    (evidence_dir / "eraser_mask_intervals.json").write_text(json.dumps(render_intervals, indent=2), encoding="utf-8")
    (evidence_dir / "base_eraser_mask_intervals.json").write_text(json.dumps(intervals, indent=2), encoding="utf-8")
    (evidence_dir / "merged_interval_list.json").write_text(json.dumps(temporal, indent=2), encoding="utf-8")
    (evidence_dir / "temporal_settings.json").write_text(json.dumps(temporal["settings"], indent=2), encoding="utf-8")
    (evidence_dir / "sample_list.json").write_text(
        json.dumps([sample.__dict__ for sample in all_samples], indent=2), encoding="utf-8"
    )
    (evidence_dir / "coverage_audit.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    (evidence_dir / "english_layout_audit.json").write_text(
        json.dumps(english_layout_summary(timeline, intervals, font), indent=2), encoding="utf-8"
    )
    (evidence_dir / "source_subtitle_geometry.json").write_text(
        json.dumps(source_geometry_summary(intervals), indent=2), encoding="utf-8"
    )
    (evidence_dir / "calibration_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (evidence_dir / "ffprobe.json").write_text(json.dumps(media, indent=2), encoding="utf-8")
    (evidence_dir / "residual_flash_audit.json").write_text(json.dumps(transition_audit, indent=2), encoding="utf-8")
    (evidence_dir / "bbox_jump_audit.json").write_text(
        json.dumps(transition_audit["bbox_jump_audit"], indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


def build_dynamic_intervals(source_path: Path, timeline: dict, font: ImageFont.FreeTypeFont) -> list[dict]:
    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open source video: {source_path}")
    intervals: list[dict] = []
    for segment in timeline["segments"]:
        if not segment.get("enabled", True):
            continue
        samples = segment_samples(segment)
        boxes = []
        for sample in samples:
            frame = read_frame(cap, sample.time_s)
            bbox, _ = detect_source_subtitle_bbox(frame)
            if bbox:
                boxes.append(bbox)
        raw = robust_union_boxes(boxes)
        if raw is None:
            continue
        text = (segment.get("subtitle_text") or segment.get("spoken_text") or segment.get("translated_text") or "").strip()
        line_count = infer_line_count(raw)
        mask = interval_mask(raw, text, font, line_count)
        start = clamp_time(segment["start_ms"] / 1000 - TRANSITION_TOLERANCE_SECONDS)
        end = clamp_time(segment["end_ms"] / 1000 + TRANSITION_TOLERANCE_SECONDS)
        intervals.append(
            {
                "segment_id": segment["id"],
                "start_time": start,
                "end_time": end,
                "x": mask["x"],
                "y": mask["y"],
                "width": mask["width"],
                "height": mask["height"],
                "line_count": line_count,
                "padding": {"vertical": VERTICAL_PADDING, "horizontal": HORIZONTAL_PADDING},
                "source_bbox": raw,
                "english_text_width_estimate": estimate_text_width(text, font),
            }
        )
    cap.release()
    return merge_close_intervals(intervals)


def detect_source_subtitle_bbox(frame_bgr: np.ndarray) -> tuple[dict | None, dict]:
    frame = cv2.resize(frame_bgr, (WIDTH, HEIGHT))
    roi_y0, roi_y1 = 560, 695
    roi_x0, roi_x1 = 120, 1160
    roi = frame[roi_y0:roi_y1, roi_x0:roi_x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bright = cv2.inRange(hsv, (0, 0, 175), (180, 100, 255))
    edges = cv2.Canny(gray, 70, 180)
    mask = cv2.bitwise_and(bright, cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 2), np.uint8), iterations=1)
    num, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    components = []
    for label in range(1, num):
        x, y, w, h, area = stats[label]
        abs_x = int(x + roi_x0)
        abs_y = int(y + roi_y0)
        if area < 180 or w < 8 or h < 5 or h > 70 or abs_y < 560 or abs_y > 690:
            continue
        components.append((abs_x, abs_y, int(w), int(h), int(area)))
    if not components:
        return None, {"component_count": 0, "pixel_count": int(mask.sum() // 255)}
    center_ys = np.array([y + h / 2 for _, y, _, h, _ in components])
    median_center_y = float(np.median(center_ys))
    components = [item for item in components if abs((item[1] + item[3] / 2) - median_center_y) <= 45]
    return {
        "left_x": min(x for x, _, _, _, _ in components),
        "top_y": min(y for _, y, _, _, _ in components),
        "right_x": max(x + w - 1 for x, _, w, _, _ in components),
        "bottom_y": max(y + h - 1 for _, y, _, h, _ in components),
    }, {"component_count": len(components), "pixel_count": int(mask.sum() // 255)}


def interval_mask(raw: dict, text: str, font: ImageFont.FreeTypeFont, line_count: int) -> dict:
    del text, font, line_count
    x = max(0, raw["left_x"] - HORIZONTAL_PADDING)
    y = max(0, raw["top_y"] - VERTICAL_PADDING)
    right = min(WIDTH - 1, raw["right_x"] + HORIZONTAL_PADDING)
    bottom = min(HEIGHT - 1, raw["bottom_y"] + VERTICAL_PADDING)
    return {"x": round(x), "y": round(y), "width": round(right - x + 1), "height": round(bottom - y + 1)}


def merge_close_intervals(intervals: list[dict]) -> list[dict]:
    # Keep geometry per subtitle; only clamp overlap timing to avoid impossible FFmpeg enable overlaps.
    ordered = sorted(intervals, key=lambda item: item["start_time"])
    for index in range(1, len(ordered)):
        previous = ordered[index - 1]
        current = ordered[index]
        if previous["end_time"] > current["start_time"]:
            midpoint = round((previous["end_time"] + current["start_time"]) / 2, 3)
            previous["end_time"] = midpoint
            current["start_time"] = midpoint
    return ordered


def temporal_stabilize_intervals(intervals: list[dict]) -> dict:
    preroll_s = PREROLL_FRAMES / FPS
    postroll_s = POSTROLL_FRAMES / FPS
    merge_gap_s = MERGE_GAP_FRAMES / FPS
    transition_s = TRANSITION_UNION_FRAMES / FPS
    ordered = sorted(intervals, key=lambda item: item["start_time"])
    expanded: list[dict] = []
    short_gaps = []
    transition_unions = []

    for interval in ordered:
        item = dict(interval)
        item["base_start_time"] = interval["start_time"]
        item["base_end_time"] = interval["end_time"]
        item["start_time"] = clamp_time(interval["start_time"] - preroll_s)
        item["end_time"] = clamp_time(interval["end_time"] + postroll_s)
        item["temporal_padding_frames"] = {"preroll": PREROLL_FRAMES, "postroll": POSTROLL_FRAMES}
        item["hysteresis_off_frames"] = HYSTERESIS_OFF_FRAMES
        item["temporal_role"] = "expanded_source_eraser"
        expanded.append(item)

    for previous, current in zip(expanded, expanded[1:]):
        base_gap = current["base_start_time"] - previous["base_end_time"]
        if 0 <= base_gap <= merge_gap_s:
            previous["end_time"] = max(previous["end_time"], current["start_time"])
            current["start_time"] = min(current["start_time"], previous["end_time"])
            short_gaps.append(
                {
                    "previous_segment_id": previous["segment_id"],
                    "next_segment_id": current["segment_id"],
                    "base_gap_seconds": round(base_gap, 3),
                    "bridged": True,
                }
            )

        transition_start = max(0.0, current["base_start_time"] - transition_s)
        transition_end = min(DURATION_SECONDS - 0.04, current["base_start_time"] + transition_s)
        if transition_end <= previous["end_time"] and transition_start >= previous["start_time"]:
            union = union_interval(previous, current, transition_start, transition_end)
            transition_unions.append(union)

    render_intervals = sorted(expanded + transition_unions, key=lambda item: (item["start_time"], item["x"]))
    return {
        "settings": {
            "fps": FPS,
            "preroll_frames": PREROLL_FRAMES,
            "postroll_frames": POSTROLL_FRAMES,
            "hysteresis_off_frames": HYSTERESIS_OFF_FRAMES,
            "merge_gap_frames": MERGE_GAP_FRAMES,
            "merge_gap_seconds": round(merge_gap_s, 3),
            "transition_union_frames": TRANSITION_UNION_FRAMES,
            "transition_union_seconds": round(transition_s, 3),
        },
        "summary": {
            "base_interval_count": len(intervals),
            "expanded_interval_count": len(expanded),
            "transition_union_interval_count": len(transition_unions),
            "render_interval_count": len(render_intervals),
            "merged_short_gap_count": len(short_gaps),
            "short_gaps": short_gaps,
            "note": "English ASS event timing is unchanged; temporal padding applies only to delogo.",
        },
        "render_intervals": render_intervals,
    }


def union_interval(previous: dict, current: dict, start_time: float, end_time: float) -> dict:
    x0 = min(previous["x"], current["x"])
    y0 = min(previous["y"], current["y"])
    x1 = max(previous["x"] + previous["width"] - 1, current["x"] + current["width"] - 1)
    y1 = max(previous["y"] + previous["height"] - 1, current["y"] + current["height"] - 1)
    source_bbox = union_boxes([previous.get("source_bbox"), current.get("source_bbox")])
    return {
        "segment_id": f"transition_union_{current['segment_id']}",
        "start_time": round(start_time, 3),
        "end_time": round(end_time, 3),
        "x": x0,
        "y": y0,
        "width": x1 - x0 + 1,
        "height": y1 - y0 + 1,
        "line_count": max(previous.get("line_count", 1), current.get("line_count", 1)),
        "padding": {"vertical": VERTICAL_PADDING, "horizontal": HORIZONTAL_PADDING},
        "source_bbox": source_bbox,
        "english_text_width_estimate": 0,
        "source_only": True,
        "temporal_role": "short_transition_union",
        "previous_segment_id": previous["segment_id"],
        "next_segment_id": current["segment_id"],
    }


def count_exposed_pixels(source_path: Path, samples: list[Sample], intervals: list[dict]) -> dict:
    cap = cv2.VideoCapture(str(source_path))
    exposed = []
    for sample in samples:
        active = active_intervals(intervals, sample.time_s)
        frame = read_frame(cap, sample.time_s)
        bbox, _ = detect_source_subtitle_bbox(frame)
        if not bbox:
            continue
        if not active:
            exposed.append({"time_s": sample.time_s, "reason": sample.reason, "bbox": bbox, "issue": "missing_active_mask"})
            continue
        if not any(box_contains(interval, bbox) for interval in active):
            exposed.append({"time_s": sample.time_s, "reason": sample.reason, "bbox": bbox, "issue": "bbox_outside_mask"})
    cap.release()
    return {"exposed_sample_count": len(exposed), "exposed_samples": exposed[:30]}


def repair_uncovered_source_subtitles(
    source_path: Path,
    intervals: list[dict],
    samples: list[Sample],
    font: ImageFont.FreeTypeFont,
) -> list[dict]:
    repaired = list(intervals)
    for _ in range(2):
        coverage = count_exposed_pixels(source_path, samples, repaired)
        if coverage["exposed_sample_count"] == 0:
            return sorted(repaired, key=lambda item: item["start_time"])
        for item in coverage["exposed_samples"]:
            if item["issue"] != "missing_active_mask":
                continue
            bbox = item["bbox"]
            line_count = infer_line_count(bbox)
            mask = interval_mask(bbox, "", font, line_count)
            repaired.append(
                {
                    "segment_id": f"source_only_{item['time_s']:.3f}",
                    "start_time": clamp_time(item["time_s"] - 0.55),
                    "end_time": clamp_time(item["time_s"] + 0.55),
                    "x": mask["x"],
                    "y": mask["y"],
                    "width": mask["width"],
                    "height": mask["height"],
                    "line_count": line_count,
                    "padding": {"vertical": VERTICAL_PADDING, "horizontal": HORIZONTAL_PADDING},
                    "source_bbox": bbox,
                    "english_text_width_estimate": 0,
                    "source_only": True,
                }
            )
    return sorted(repaired, key=lambda item: item["start_time"])


def render_preview(source_path: Path, tts_mix: Path, ass_path: Path, output_path: Path, intervals: list[dict]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", r"\'")
    filters = [f"scale={WIDTH}:-2"]
    for interval in intervals:
        enable = f"between(t\\,{interval['start_time']:.3f}\\,{interval['end_time']:.3f})"
        filters.append(
            "delogo="
            f"x={interval['x']}:y={interval['y']}:w={interval['width']}:h={interval['height']}:"
            f"show=0:enable='{enable}'"
        )
    filters.append(f"subtitles='{ass}'")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0",
            "-t",
            f"{DURATION_SECONDS:.3f}",
            "-i",
            str(source_path),
            "-i",
            str(tts_mix),
            "-filter_complex",
            f"[0:v]{','.join(filters)}[v]",
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "27",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ],
        check=True,
    )


def write_source_matched_ass(timeline: dict, intervals: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    interval_by_segment = {item["segment_id"]: item for item in intervals if not item.get("source_only")}
    events = []
    for segment in timeline["segments"]:
        text = subtitle_text(segment)
        if not text:
            continue
        interval = interval_by_segment.get(segment["id"])
        if interval is None:
            continue
        layout = english_layout_for_interval(interval)
        override = (
            "{"
            f"\\an2\\fs{ENGLISH_FONT_SIZE}\\pos({layout['center_x']},{layout['anchor_y']})"
            "}"
        )
        events.append(
            "Dialogue: 1,"
            f"{format_ass_timestamp(segment['start_ms'])},{format_ass_timestamp(segment['end_ms'])},"
            f"Default,,0,0,0,,{override}{ass_escape(text)}"
        )
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


def english_layout_for_interval(interval: dict) -> dict:
    bbox = interval["source_bbox"]
    center_x = round((bbox["left_x"] + bbox["right_x"]) / 2)
    anchor_y = max(500, min(640, bbox["top_y"] - 10))
    return {
        "segment_id": interval["segment_id"],
        "center_x": center_x,
        "anchor_y": round(anchor_y),
        "source_center_x": center_x,
        "source_top_y": bbox["top_y"],
        "source_bottom_y": bbox["bottom_y"],
        "source_width": bbox["right_x"] - bbox["left_x"] + 1,
        "source_height": bbox["bottom_y"] - bbox["top_y"] + 1,
        "baseline_zone": [max(0, bbox["bottom_y"] - 12), bbox["bottom_y"]],
        "font_size": ENGLISH_FONT_SIZE,
    }


def render_contact_sheet(video_path: Path, output_path: Path, intervals: list[dict] | None, times: list[float], *, source_only: bool) -> None:
    frames = []
    cap = cv2.VideoCapture(str(video_path))
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
    cap.release()
    while len(frames) < 12:
        frames.append(np.zeros((180, 320, 3), dtype=np.uint8))
    rows = [np.hstack(frames[0:4]), np.hstack(frames[4:8]), np.hstack(frames[8:12])]
    cv2.imwrite(str(output_path), np.vstack(rows))


def render_source_match_contact_sheet(video_path: Path, output_path: Path, intervals: list[dict], times: list[float]) -> None:
    frames = []
    cap = cv2.VideoCapture(str(video_path))
    for time_s in times[:12]:
        frame = read_frame(cap, time_s)
        for interval in active_intervals(intervals, time_s):
            bbox = interval["source_bbox"]
            layout = english_layout_for_interval(interval)
            cv2.rectangle(
                frame,
                (bbox["left_x"], bbox["top_y"]),
                (bbox["right_x"], bbox["bottom_y"]),
                (0, 255, 255),
                2,
            )
            cv2.rectangle(
                frame,
                (interval["x"], interval["y"]),
                (interval["x"] + interval["width"], interval["y"] + interval["height"]),
                (0, 0, 255),
                2,
            )
            cv2.circle(frame, (layout["center_x"], layout["anchor_y"]), 5, (255, 0, 0), -1)
            cv2.line(frame, (layout["center_x"], 540), (layout["center_x"], 700), (255, 0, 0), 1)
        frame = cv2.resize(frame, (320, 180))
        cv2.putText(frame, f"{time_s:.2f}s", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        frames.append(frame)
    cap.release()
    while len(frames) < 12:
        frames.append(np.zeros((180, 320, 3), dtype=np.uint8))
    rows = [np.hstack(frames[0:4]), np.hstack(frames[4:8]), np.hstack(frames[8:12])]
    cv2.imwrite(str(output_path), np.vstack(rows))


def interval_stats(intervals: list[dict]) -> dict:
    widths = [item["width"] for item in intervals]
    heights = [item["height"] for item in intervals]
    areas = [item["width"] * item["height"] for item in intervals]
    durations = [item["end_time"] - item["start_time"] for item in intervals]
    static_area = CP06D_STATIC_MASK["width"] * CP06D_STATIC_MASK["height"]
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
        "masked_duration_seconds": round(sum(durations), 3),
        "masked_duration_percent": round(sum(durations) * 100 / DURATION_SECONDS, 3),
        "average_area_px": round(statistics.mean(areas), 3),
        "average_area_frame_percent": round(statistics.mean(areas) * 100 / (WIDTH * HEIGHT), 3),
        "max_area_frame_percent": round(max(areas) * 100 / (WIDTH * HEIGHT), 3),
        "cp06d_static_area_px": static_area,
        "cp06d_static_area_frame_percent": round(static_area * 100 / (WIDTH * HEIGHT), 3),
        "average_area_reduction_vs_cp06d_percent": round((1 - statistics.mean(areas) / static_area) * 100, 3),
        "cp06e_average_area_px": 66611.588,
        "average_area_reduction_vs_cp06e_percent": round((1 - statistics.mean(areas) / 66611.588) * 100, 3),
        "cp06f_average_area_px": 58252.922,
        "average_area_delta_vs_cp06f_percent": round((statistics.mean(areas) / 58252.922 - 1) * 100, 3),
    }


def build_interval_samples(timeline: dict) -> list[Sample]:
    samples = []
    for segment in timeline["segments"]:
        if not segment.get("enabled", True):
            continue
        samples.extend(segment_samples(segment))
    return unique_samples(samples)


def segment_samples(segment: dict) -> list[Sample]:
    start = segment["start_ms"] / 1000
    end = segment["end_ms"] / 1000
    midpoint = (start + end) / 2
    samples = [
        Sample(clamp_time(start + 0.04), "subtitle_start", segment["id"]),
        Sample(clamp_time(midpoint), "subtitle_midpoint", segment["id"]),
        Sample(clamp_time(end - 0.04), "subtitle_end", segment["id"]),
        Sample(clamp_time(start - TRANSITION_TOLERANCE_SECONDS), "transition_before", segment["id"]),
        Sample(clamp_time(start + TRANSITION_TOLERANCE_SECONDS), "transition_after", segment["id"]),
        Sample(clamp_time(end - TRANSITION_TOLERANCE_SECONDS), "transition_before_end", segment["id"]),
        Sample(clamp_time(end + TRANSITION_TOLERANCE_SECONDS), "transition_after_end", segment["id"]),
    ]
    current = math.floor(start * 2) / 2
    while current <= end:
        if start <= current <= end:
            samples.append(Sample(clamp_time(current), "interval_half_second", segment["id"]))
        current += 0.5
    return unique_samples(samples)


def comparison_times(timeline: dict) -> list[float]:
    times = [0.5, 2.0, 5.0, 11.0, 13.55, 25.0, 30.3, 38.0, 45.0, 55.0, 65.0, 70.0, 73.0]
    # Add widest/highest subtitle representatives after intervals are measurable via the fixed known review points.
    return [clamp_time(time_s) for time_s in times]


def layout_review_times(timeline: dict) -> list[float]:
    times = set(comparison_times(timeline))
    widest = sorted(
        (
            (len(subtitle_text(segment)), (segment["start_ms"] + segment["end_ms"]) / 2000)
            for segment in timeline["segments"]
            if subtitle_text(segment)
        ),
        reverse=True,
    )
    for _, time_s in widest[:4]:
        times.add(clamp_time(time_s))
    return sorted(times)[:12]


def active_intervals(intervals: list[dict], time_s: float) -> list[dict]:
    return [item for item in intervals if item["start_time"] <= time_s <= item["end_time"]]


def box_contains(interval: dict, bbox: dict) -> bool:
    return (
        bbox["left_x"] >= interval["x"]
        and bbox["right_x"] <= interval["x"] + interval["width"] - 1
        and bbox["top_y"] >= interval["y"]
        and bbox["bottom_y"] <= interval["y"] + interval["height"] - 1
    )


def static_interval(mask: dict, start: float, end: float) -> dict:
    return {"start_time": start, "end_time": end, **mask}


def infer_line_count(raw: dict) -> int:
    height = raw["bottom_y"] - raw["top_y"] + 1
    return 2 if height >= 88 else 1


def estimate_text_width(text: str, font: ImageFont.FreeTypeFont) -> int:
    if not text:
        return 0
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def english_layout_summary(timeline: dict, intervals: list[dict], font: ImageFont.FreeTypeFont) -> dict:
    del font
    render_font = ImageFont.truetype(str(get_settings().font_path), ENGLISH_FONT_SIZE)
    widths = [estimate_text_width(subtitle_text(segment), render_font) for segment in timeline["segments"] if subtitle_text(segment)]
    layouts = [english_layout_for_interval(interval) for interval in intervals if not interval.get("source_only")]
    anchor_ys = [item["anchor_y"] for item in layouts]
    return {
        "layer": "source_matched_per_event_ass_text",
        "background_plate": "none",
        "font": "Arial",
        "old_font_size": CP06G_FONT_SIZE,
        "font_size": ENGLISH_FONT_SIZE,
        "style": "white_text_black_outline_shadow",
        "alignment": "per_event_bottom_center_pos",
        "old_margin_v": CP06G_MARGIN,
        "margin_v": "per_event_pos",
        "median_anchor_y": round(statistics.median(anchor_ys), 3),
        "p95_anchor_y": percentile(anchor_ys, 95),
        "max_estimated_text_width": max(widths),
        "median_estimated_text_width": round(statistics.median(widths), 3),
        "p95_estimated_text_width": percentile(widths, 95),
        "background_plate_stats": "none",
        "layout_count": len(layouts),
    }


def source_geometry_summary(intervals: list[dict]) -> dict:
    active = [item for item in intervals if not item.get("source_only")]
    widths = [item["source_bbox"]["right_x"] - item["source_bbox"]["left_x"] + 1 for item in active]
    heights = [item["source_bbox"]["bottom_y"] - item["source_bbox"]["top_y"] + 1 for item in active]
    center_xs = [(item["source_bbox"]["left_x"] + item["source_bbox"]["right_x"]) / 2 for item in active]
    center_offsets = [center_x - WIDTH / 2 for center_x in center_xs]
    top_ys = [item["source_bbox"]["top_y"] for item in active]
    bottom_ys = [item["source_bbox"]["bottom_y"] for item in active]
    deciding = max(active, key=lambda item: abs((item["source_bbox"]["left_x"] + item["source_bbox"]["right_x"]) / 2 - WIDTH / 2))
    return {
        "active_interval_count": len(active),
        "median_bbox": {
            "width": round(statistics.median(widths), 3),
            "height": round(statistics.median(heights), 3),
            "top_y": round(statistics.median(top_ys), 3),
            "bottom_y": round(statistics.median(bottom_ys), 3),
        },
        "p95_bbox": {"width": percentile(widths, 95), "height": percentile(heights, 95)},
        "min_bbox": {"width": min(widths), "height": min(heights)},
        "max_bbox": {"width": max(widths), "height": max(heights)},
        "median_center_x": round(statistics.median(center_xs), 3),
        "median_center_offset_from_frame": round(statistics.median(center_offsets), 3),
        "max_abs_center_offset_from_frame": round(max(abs(value) for value in center_offsets), 3),
        "alignment_deciding_segment": deciding["segment_id"],
        "alignment_deciding_center_x": round(
            (deciding["source_bbox"]["left_x"] + deciding["source_bbox"]["right_x"]) / 2, 3
        ),
    }


def render_truth_audit(source_path: Path, output_path: Path, intervals: list[dict], evidence_dir: Path) -> dict:
    mandatory_times = [5.0, 22.0, 38.0, 50.0, 65.0]
    paired_dir = evidence_dir / "paired_frames"
    paired_dir.mkdir(parents=True, exist_ok=True)
    source_cap = cv2.VideoCapture(str(source_path))
    output_cap = cv2.VideoCapture(str(output_path))
    results = []
    for time_s in mandatory_times:
        source_frame = read_frame(source_cap, time_s)
        output_frame = read_frame(output_cap, time_s)
        active = active_intervals(intervals, time_s)
        source_bbox = union_boxes([item["source_bbox"] for item in active]) if active else None
        source_count = glyph_pixel_count(source_frame, source_bbox) if source_bbox else 0
        output_count = glyph_pixel_count(output_frame, source_bbox) if source_bbox else 0
        ratio = round(output_count / source_count, 4) if source_count else 0
        status = "PASS" if ratio <= 0.18 else "FAIL"
        pair = np.hstack(
            [
                cv2.resize(source_frame, (640, 360)),
                cv2.resize(output_frame, (640, 360)),
            ]
        )
        cv2.putText(pair, f"{time_s:.2f}s SOURCE", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(pair, f"{time_s:.2f}s CP06I {status}", (652, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        pair_path = paired_dir / f"paired_{int(time_s):02d}s.jpg"
        cv2.imwrite(str(pair_path), pair)
        results.append(
            {
                "time_s": time_s,
                "source_bbox": source_bbox,
                "source_glyph_pixels": source_count,
                "final_glyph_pixels_in_source_bbox": output_count,
                "residual_ratio": ratio,
                "status": status,
                "paired_frame": str(pair_path),
            }
        )
    source_cap.release()
    output_cap.release()
    return {
        "mandatory_times": mandatory_times,
        "fail_count": sum(1 for item in results if item["status"] != "PASS"),
        "results": results,
    }


def transition_flash_audit(
    source_path: Path,
    output_path: Path,
    intervals: list[dict],
    timeline: dict,
    evidence_dir: Path,
) -> dict:
    windows = transition_windows(timeline)
    source_cap = cv2.VideoCapture(str(source_path))
    output_cap = cv2.VideoCapture(str(output_path))
    residual_frames = []
    inspected = []
    active_states = []
    bbox_jumps = []
    previous_active_bbox = None
    previous_time = None

    for time_s in audited_frame_times(windows):
        source_frame = read_frame(source_cap, time_s)
        output_frame = read_frame(output_cap, time_s)
        active = active_intervals(intervals, time_s)
        active_states.append({"time_s": time_s, "active": bool(active)})
        planned_bbox = union_boxes([item.get("source_bbox") for item in active])
        detected_bbox, detected_meta = detect_source_subtitle_bbox(source_frame)
        source_bbox = planned_bbox if planned_bbox else detected_bbox
        english_boxes = active_english_boxes(timeline, intervals, time_s)
        source_count = glyph_pixel_count(source_frame, source_bbox) if source_bbox else 0
        raw_output_count = glyph_pixel_count(output_frame, source_bbox) if source_bbox else 0
        output_count = matched_glyph_pixel_count(source_frame, output_frame, source_bbox, english_boxes) if source_bbox else 0
        residual_ratio = round(output_count / source_count, 4) if source_count else 0.0
        status = "PASS"
        if source_count >= 50 and residual_ratio > 0.18:
            status = "FAIL"
            residual_frames.append(
                {
                    "time_s": time_s,
                    "source_bbox": source_bbox,
                    "source_glyph_pixels": source_count,
                    "final_glyph_pixels_in_source_bbox": raw_output_count,
                    "final_glyph_pixels_matching_source": output_count,
                    "residual_ratio": residual_ratio,
                }
            )
        if (
            planned_bbox
            and previous_active_bbox
            and previous_time is not None
            and time_s - previous_time <= (1.5 / FPS)
            and not any(item.get("temporal_role") == "short_transition_union" for item in active)
        ):
            jump = bbox_jump(previous_active_bbox, planned_bbox)
            if jump["warning"]:
                bbox_jumps.append({"time_s": time_s, "previous_time_s": previous_time, **jump})
        if planned_bbox:
            previous_active_bbox = planned_bbox
            previous_time = time_s
        inspected.append(
            {
                "time_s": time_s,
                "active_delogo": bool(active),
                "detected_source_bbox": detected_bbox,
                "detected_meta": detected_meta,
                "audited_bbox": source_bbox,
                "ignored_english_boxes": english_boxes,
                "source_glyph_pixels": source_count,
                "final_glyph_pixels_in_source_bbox": raw_output_count,
                "final_glyph_pixels_matching_source": output_count,
                "residual_ratio": residual_ratio,
                "status": status,
            }
        )

    source_cap.release()
    output_cap.release()
    flash_runs = residual_runs([item["time_s"] for item in residual_frames])
    toggle_audit = delogo_toggle_audit(active_states)
    result = {
        "windows": windows,
        "inspected_frame_count": len(inspected),
        "residual_glyph_frame_count": len(residual_frames),
        "residual_frames": residual_frames[:50],
        "short_subtitle_flash_count": sum(1 for run in flash_runs if 1 <= run["frame_count"] <= 5),
        "residual_runs": flash_runs,
        "delogo_toggle_count": toggle_audit["unnecessary_delogo_toggle_count"],
        "toggle_audit": toggle_audit,
        "bbox_jump_warning_count": len(bbox_jumps),
        "bbox_jump_audit": {"warning_count": len(bbox_jumps), "warnings": bbox_jumps[:50]},
        "mandatory_31_70_32_30": window_result(inspected, 31.70, 32.30),
        "status": "PASS" if not residual_frames and not flash_runs and toggle_audit["unnecessary_delogo_toggle_count"] == 0 else "FAIL",
    }
    return result


def transition_windows(timeline: dict) -> list[dict]:
    windows = [{"label": "mandatory_31_70_32_30", "start": 31.70, "end": 32.30}]
    for center in [5.0, 22.0, 38.0, 50.0, 65.0]:
        windows.append({"label": f"mandatory_{int(center):02d}s", "start": center - 0.30, "end": center + 0.30})
    for segment in timeline["segments"]:
        if not segment.get("enabled", True):
            continue
        for kind, value_ms in [("start", segment["start_ms"]), ("end", segment["end_ms"])]:
            center = value_ms / 1000
            if 0.0 <= center <= DURATION_SECONDS:
                windows.append(
                    {
                        "label": f"{segment['id']}_{kind}",
                        "start": max(0.0, center - 0.30),
                        "end": min(DURATION_SECONDS - 0.04, center + 0.30),
                    }
                )
    return merge_windows(windows)


def merge_windows(windows: list[dict]) -> list[dict]:
    ordered = sorted(windows, key=lambda item: item["start"])
    merged: list[dict] = []
    for window in ordered:
        if not merged or window["start"] > merged[-1]["end"] + (1 / FPS):
            merged.append(dict(window))
            continue
        merged[-1]["end"] = max(merged[-1]["end"], window["end"])
        merged[-1]["label"] += f"+{window['label']}"
    return [{"label": item["label"], "start": round(item["start"], 3), "end": round(item["end"], 3)} for item in merged]


def audited_frame_times(windows: list[dict]) -> list[float]:
    frames = set()
    for window in windows:
        start_frame = max(0, math.floor(window["start"] * FPS))
        end_frame = min(math.floor((DURATION_SECONDS - 0.04) * FPS), math.ceil(window["end"] * FPS))
        for frame_index in range(start_frame, end_frame + 1):
            frames.add(round(frame_index / FPS, 3))
    return sorted(frames)


def residual_runs(times: list[float]) -> list[dict]:
    if not times:
        return []
    frame_numbers = sorted({round(time_s * FPS) for time_s in times})
    runs = []
    start = previous = frame_numbers[0]
    for frame in frame_numbers[1:]:
        if frame == previous + 1:
            previous = frame
            continue
        runs.append({"start_time": round(start / FPS, 3), "end_time": round(previous / FPS, 3), "frame_count": previous - start + 1})
        start = previous = frame
    runs.append({"start_time": round(start / FPS, 3), "end_time": round(previous / FPS, 3), "frame_count": previous - start + 1})
    return runs


def delogo_toggle_audit(active_states: list[dict]) -> dict:
    unnecessary = []
    ordered = sorted(active_states, key=lambda item: item["time_s"])
    off_start = None
    previous_on_time = None
    for item in ordered:
        if item["active"]:
            if off_start is not None and previous_on_time is not None:
                gap_frames = round((item["time_s"] - off_start) * FPS)
                if gap_frames <= MERGE_GAP_FRAMES:
                    unnecessary.append(
                        {
                            "off_start_time": off_start,
                            "on_again_time": item["time_s"],
                            "gap_frames": gap_frames,
                            "previous_on_time": previous_on_time,
                        }
                    )
            off_start = None
            previous_on_time = item["time_s"]
        elif previous_on_time is not None and off_start is None:
            off_start = item["time_s"]
    return {"unnecessary_delogo_toggle_count": len(unnecessary), "unnecessary_toggles": unnecessary[:50]}


def bbox_jump(previous: dict, current: dict) -> dict:
    prev_center = ((previous["left_x"] + previous["right_x"]) / 2, (previous["top_y"] + previous["bottom_y"]) / 2)
    curr_center = ((current["left_x"] + current["right_x"]) / 2, (current["top_y"] + current["bottom_y"]) / 2)
    prev_w = previous["right_x"] - previous["left_x"] + 1
    curr_w = current["right_x"] - current["left_x"] + 1
    prev_h = previous["bottom_y"] - previous["top_y"] + 1
    curr_h = current["bottom_y"] - current["top_y"] + 1
    center_delta = math.hypot(curr_center[0] - prev_center[0], curr_center[1] - prev_center[1])
    width_delta = abs(curr_w - prev_w)
    height_delta = abs(curr_h - prev_h)
    return {
        "center_delta_px": round(center_delta, 3),
        "width_delta_px": width_delta,
        "height_delta_px": height_delta,
        "warning": center_delta > 160 or width_delta > 320 or height_delta > 100,
    }


def window_result(inspected: list[dict], start: float, end: float) -> dict:
    frames = [item for item in inspected if start <= item["time_s"] <= end]
    return {
        "start": start,
        "end": end,
        "inspected_frame_count": len(frames),
        "residual_glyph_frame_count": sum(1 for item in frames if item["status"] != "PASS"),
        "status": "PASS" if all(item["status"] == "PASS" for item in frames) else "FAIL",
    }


def active_english_boxes(timeline: dict, intervals: list[dict], time_s: float) -> list[dict]:
    interval_by_segment = {
        item["segment_id"]: item
        for item in intervals
        if not item.get("source_only") and item.get("temporal_role") != "short_transition_union"
    }
    font = ImageFont.truetype(str(get_settings().font_path), ENGLISH_FONT_SIZE)
    boxes = []
    for segment in timeline["segments"]:
        if not segment.get("enabled", True):
            continue
        if not (segment["start_ms"] / 1000 <= time_s <= segment["end_ms"] / 1000):
            continue
        interval = interval_by_segment.get(segment["id"])
        if not interval:
            continue
        text = subtitle_text(segment)
        if not text:
            continue
        layout = english_layout_for_interval(interval)
        text_width = max(estimate_text_width(text, font), 80)
        line_count = max(1, text.count("\n") + 1)
        text_height = 84 if line_count == 1 else 132
        boxes.append(
            {
                "left_x": max(0, round(layout["center_x"] - text_width / 2 - 35)),
                "top_y": max(0, round(layout["anchor_y"] - text_height)),
                "right_x": min(WIDTH - 1, round(layout["center_x"] + text_width / 2 + 35)),
                "bottom_y": min(HEIGHT - 1, round(layout["anchor_y"] + 42)),
                "reason": "english_subtitle_overlay",
            }
        )
    return boxes


def render_transition_strips(output_path: Path, evidence_dir: Path, timeline: dict) -> dict:
    strip_dir = evidence_dir / "transition_strips"
    strip_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(output_path))
    strips = {}
    for label, start, end in [
        ("mandatory_31_70_32_30", 31.70, 32.30),
        ("mandatory_05s", 4.70, 5.30),
        ("mandatory_22s", 21.70, 22.30),
        ("mandatory_38s", 37.70, 38.30),
        ("mandatory_50s", 49.70, 50.30),
        ("mandatory_65s", 64.70, 65.30),
    ]:
        path = strip_dir / f"{label}.jpg"
        write_frame_strip(cap, start, end, path, label)
        strips[label] = str(path)
    boundary_path = strip_dir / "all_boundaries_sampled.jpg"
    write_boundary_sheet(cap, timeline, boundary_path)
    strips["all_boundaries_sampled"] = str(boundary_path)
    cap.release()
    return strips


def write_frame_strip(cap: cv2.VideoCapture, start: float, end: float, path: Path, label: str) -> None:
    frames = []
    start_frame = math.floor(start * FPS)
    end_frame = math.ceil(end * FPS)
    for frame_index in range(start_frame, end_frame + 1):
        time_s = frame_index / FPS
        frame = read_frame(cap, time_s)
        frame = cv2.resize(frame, (160, 90))
        cv2.putText(frame, f"{time_s:.2f}s", (4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
        frames.append(frame)
    cols = 7
    while len(frames) % cols:
        frames.append(np.zeros((90, 160, 3), dtype=np.uint8))
    rows = [np.hstack(frames[index : index + cols]) for index in range(0, len(frames), cols)]
    sheet = np.vstack(rows)
    cv2.putText(sheet, label, (8, sheet.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
    cv2.imwrite(str(path), sheet)


def write_boundary_sheet(cap: cv2.VideoCapture, timeline: dict, path: Path) -> None:
    frames = []
    boundaries = []
    for segment in timeline["segments"]:
        if not segment.get("enabled", True):
            continue
        boundaries.extend([segment["start_ms"] / 1000, segment["end_ms"] / 1000])
    for center in sorted(set(round(value, 3) for value in boundaries))[:60]:
        for offset in [-2 / FPS, 0, 2 / FPS]:
            time_s = clamp_time(center + offset)
            frame = read_frame(cap, time_s)
            frame = cv2.resize(frame, (160, 90))
            cv2.putText(frame, f"{time_s:.2f}s", (4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
            frames.append(frame)
    cols = 6
    while len(frames) % cols:
        frames.append(np.zeros((90, 160, 3), dtype=np.uint8))
    rows = [np.hstack(frames[index : index + cols]) for index in range(0, len(frames), cols)]
    cv2.imwrite(str(path), np.vstack(rows))


def glyph_pixel_count(frame_bgr: np.ndarray, bbox: dict | None) -> int:
    mask = glyph_mask(frame_bgr, bbox)
    return int(mask.sum() // 255) if mask is not None else 0


def matched_glyph_pixel_count(
    source_bgr: np.ndarray,
    output_bgr: np.ndarray,
    bbox: dict | None,
    ignore_boxes: list[dict] | None = None,
) -> int:
    source_mask = glyph_mask(source_bgr, bbox)
    output_mask = glyph_mask(output_bgr, bbox)
    if source_mask is None or output_mask is None:
        return 0
    if ignore_boxes:
        erase_ignored_boxes(output_mask, bbox, ignore_boxes)
    source_mask = cv2.dilate(source_mask, np.ones((3, 3), np.uint8), iterations=1)
    matched = cv2.bitwise_and(source_mask, output_mask)
    return int(matched.sum() // 255)


def erase_ignored_boxes(mask: np.ndarray, audited_bbox: dict, ignore_boxes: list[dict]) -> None:
    pad = 2
    x0 = max(0, audited_bbox["left_x"] - pad)
    y0 = max(0, audited_bbox["top_y"] - pad)
    for box in ignore_boxes:
        ix0 = max(0, box["left_x"] - x0)
        iy0 = max(0, box["top_y"] - y0)
        ix1 = min(mask.shape[1] - 1, box["right_x"] - x0)
        iy1 = min(mask.shape[0] - 1, box["bottom_y"] - y0)
        if ix0 <= ix1 and iy0 <= iy1:
            mask[iy0 : iy1 + 1, ix0 : ix1 + 1] = 0


def glyph_mask(frame_bgr: np.ndarray, bbox: dict | None) -> np.ndarray | None:
    if not bbox:
        return None
    pad = 2
    x0 = max(0, bbox["left_x"] - pad)
    y0 = max(0, bbox["top_y"] - pad)
    x1 = min(WIDTH - 1, bbox["right_x"] + pad)
    y1 = min(HEIGHT - 1, bbox["bottom_y"] + pad)
    roi = frame_bgr[y0 : y1 + 1, x0 : x1 + 1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bright = cv2.inRange(hsv, (0, 0, 175), (180, 115, 255))
    edges = cv2.Canny(gray, 70, 180)
    glyph = cv2.bitwise_and(bright, cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1))
    return glyph


def subtitle_text(segment: dict) -> str:
    if not segment.get("enabled", True):
        return ""
    return (segment.get("subtitle_text") or segment.get("spoken_text") or segment.get("translated_text") or "").strip()


def ass_escape(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(ch for ch in normalized if ch == "\n" or ord(ch) >= 32)
    return normalized.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", r"\N")


def read_frame(cap: cv2.VideoCapture, time_s: float) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_s) * 1000)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError(f"Could not read frame at {time_s:.3f}s")
    return cv2.resize(frame, (WIDTH, HEIGHT))


def union_boxes(boxes: list[dict]) -> dict | None:
    boxes = [box for box in boxes if box]
    if not boxes:
        return None
    return {
        "left_x": min(box["left_x"] for box in boxes),
        "top_y": min(box["top_y"] for box in boxes),
        "right_x": max(box["right_x"] for box in boxes),
        "bottom_y": max(box["bottom_y"] for box in boxes),
    }


def robust_union_boxes(boxes: list[dict]) -> dict | None:
    if not boxes:
        return None
    if len(boxes) < 4:
        return union_boxes(boxes)
    lefts = [box["left_x"] for box in boxes]
    tops = [box["top_y"] for box in boxes]
    rights = [box["right_x"] for box in boxes]
    bottoms = [box["bottom_y"] for box in boxes]
    return {
        "left_x": round(percentile(lefts, 25)),
        "top_y": round(percentile(tops, 20)),
        "right_x": round(percentile(rights, 75)),
        "bottom_y": round(percentile(bottoms, 80)),
    }


def unique_samples(samples: list[Sample]) -> list[Sample]:
    unique = {}
    for sample in samples:
        unique.setdefault(round(sample.time_s, 3), sample)
    return [unique[key] for key in sorted(unique)]


def percentile(values: list[int], pct: int) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct / 100
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return float(ordered[low])
    return round(ordered[low] * (high - index) + ordered[high] * (index - low), 3)


def clamp_time(value: float) -> float:
    return round(max(0.0, min(DURATION_SECONDS - 0.04, value)), 3)


if __name__ == "__main__":
    main()
