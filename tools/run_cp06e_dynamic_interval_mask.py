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
from app.services.subtitles import write_ass
from app.services.timeline import load_latest_timeline


WIDTH = 1280
HEIGHT = 720
DURATION_SECONDS = 75.0
TRANSITION_TOLERANCE_SECONDS = 0.10
VERTICAL_PADDING = 8
HORIZONTAL_PADDING = 12
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
    evidence_dir = settings.root / "evidence" / "CP06E"
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

    before_contact = evidence_dir / "before_cp06d_same_timestamps.jpg"
    after_contact = evidence_dir / "after_cp06e_same_timestamps.jpg"
    source_contact = evidence_dir / "source_dynamic_mask_contact_sheet.jpg"
    render_contact_sheet(
        settings.source_path,
        before_contact,
        [static_interval(CP06D_STATIC_MASK, 0.0, DURATION_SECONDS)],
        comparison_times(timeline),
        source_only=True,
    )
    render_contact_sheet(settings.source_path, source_contact, intervals, comparison_times(timeline), source_only=True)

    ass_path = project_dir / "subtitles" / "cp06e_dynamic_mask.ass"
    write_ass(timeline, ass_path)
    output_path = project_dir / "renders" / "cp06e_dynamic_mask_vertical_slice_720p.mp4"
    tts_mix = project_dir / "audio" / "cp06b_grouped_tts_mix.wav"
    render_preview(settings.source_path, tts_mix, ass_path, output_path, intervals)
    render_contact_sheet(output_path, after_contact, None, comparison_times(timeline), source_only=False)

    coverage = count_exposed_pixels(settings.source_path, all_samples, intervals)
    stats = interval_stats(intervals)
    media = media_summary(output_path)
    summary = {
        "project_id": project_id,
        "artifact_path": str(output_path),
        "artifact_sha256": sha256_file(output_path),
        "media": media,
        "cp06d_static_mask": CP06D_STATIC_MASK,
        "interval_count": len(intervals),
        "intervals": intervals,
        "stats": stats,
        "coverage": coverage,
        "sample_count": len(all_samples),
        "comparison_timestamps": comparison_times(timeline),
        "contact_sheets": {
            "before_cp06d_same_timestamps": str(before_contact),
            "source_dynamic_mask": str(source_contact),
            "after_cp06e_same_timestamps": str(after_contact),
        },
        "audio_reused_from": str(tts_mix),
        "audio_reused_sha256": sha256_file(tts_mix),
        "provider_calls": {"gemini": 0, "elevenlabs": 0},
        "free_disk_gb": round(shutil.disk_usage(settings.root).free / (1024**3), 2),
    }
    (evidence_dir / "dynamic_mask_intervals.json").write_text(json.dumps(intervals, indent=2), encoding="utf-8")
    (evidence_dir / "sample_list.json").write_text(
        json.dumps([sample.__dict__ for sample in all_samples], indent=2), encoding="utf-8"
    )
    (evidence_dir / "coverage_audit.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    (evidence_dir / "calibration_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (evidence_dir / "ffprobe.json").write_text(json.dumps(media, indent=2), encoding="utf-8")
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
        raw = union_boxes(boxes)
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
    roi_y0, roi_y1 = 555, 690
    roi_x0, roi_x1 = 160, 1120
    roi = frame[roi_y0:roi_y1, roi_x0:roi_x1]
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
    return {
        "left_x": min(x for x, _, _, _, _ in components),
        "top_y": min(y for _, y, _, _, _ in components),
        "right_x": max(x + w - 1 for x, _, w, _, _ in components),
        "bottom_y": max(y + h - 1 for _, y, _, h, _ in components),
    }, {"component_count": len(components), "pixel_count": int(mask.sum() // 255)}


def interval_mask(raw: dict, text: str, font: ImageFont.FreeTypeFont, line_count: int) -> dict:
    source_width = raw["right_x"] - raw["left_x"] + 1 + HORIZONTAL_PADDING * 2
    source_height = raw["bottom_y"] - raw["top_y"] + 1 + VERTICAL_PADDING * 2
    english_width = estimate_text_width(text, font) + 72
    target_height = max(source_height, 76 if line_count == 1 else 104)
    width = min(WIDTH - 96, max(source_width, english_width))
    center_x = (raw["left_x"] + raw["right_x"]) / 2
    x = round(max(48, min(WIDTH - 48 - width, center_x - width / 2)))
    bottom = min(692, raw["bottom_y"] + VERTICAL_PADDING)
    y = round(max(532, bottom - target_height + 1))
    return {"x": x, "y": y, "width": round(width), "height": round(target_height)}


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
            "drawbox="
            f"x={interval['x']}:y={interval['y']}:w={interval['width']}:h={interval['height']}:"
            f"color=black@1.000:t=fill:enable='{enable}'"
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
        "cp06d_static_area_px": static_area,
        "cp06d_static_area_frame_percent": round(static_area * 100 / (WIDTH * HEIGHT), 3),
        "average_area_reduction_vs_cp06d_percent": round((1 - statistics.mean(areas) / static_area) * 100, 3),
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
    times = [0.5, 2.0, 5.0, 11.0, 13.55, 25.0, 38.0, 45.0, 55.0, 65.0, 70.0, 73.0]
    # Add widest/highest subtitle representatives after intervals are measurable via the fixed known review points.
    return [clamp_time(time_s) for time_s in times]


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


def read_frame(cap: cv2.VideoCapture, time_s: float) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_s) * 1000)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError(f"Could not read frame at {time_s:.3f}s")
    return cv2.resize(frame, (WIDTH, HEIGHT))


def union_boxes(boxes: list[dict]) -> dict | None:
    if not boxes:
        return None
    return {
        "left_x": min(box["left_x"] for box in boxes),
        "top_y": min(box["top_y"] for box in boxes),
        "right_x": max(box["right_x"] for box in boxes),
        "bottom_y": max(box["bottom_y"] for box in boxes),
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
