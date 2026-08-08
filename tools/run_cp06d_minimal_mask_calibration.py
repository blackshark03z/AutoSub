import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.services.subtitles import write_ass
from app.services.timeline import load_latest_timeline


WIDTH = 1280
HEIGHT = 720
DURATION_SECONDS = 75.0
PREVIOUS_MASK = {"x": 64, "y": 547, "width": 1152, "height": 151}


@dataclass(frozen=True)
class Sample:
    time_s: float
    reason: str
    segment_id: str | None = None


def main() -> None:
    settings = get_settings()
    project_id = "vertical_slice_cp02"
    project_dir = settings.data_dir / "projects" / project_id
    evidence_dir = settings.root / "evidence" / "CP06D"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    timeline = load_latest_timeline(project_id)
    samples = build_samples(timeline)
    frames_dir = evidence_dir / "source_samples"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    measurements = measure_source_subtitles(settings.source_path, samples, frames_dir)
    raw_union = union_boxes([item["bbox"] for item in measurements if item.get("bbox")])
    if raw_union is None:
        raise RuntimeError("Could not detect source subtitle geometry in sampled source frames")
    rounds = []
    selected_mask = None
    for index, (vertical_pad, horizontal_pad) in enumerate(((6, 12), (8, 16), (12, 24)), start=1):
        candidate = padded_mask(raw_union, vertical_pad, horizontal_pad)
        exposed = count_exposed_pixels(settings.source_path, samples, candidate)
        rounds.append(
            {
                "iteration": index,
                "vertical_padding_px": vertical_pad,
                "horizontal_padding_px": horizontal_pad,
                "mask": candidate,
                "exposed_sample_count": exposed["sample_count"],
                "max_exposed_pixels": exposed["max_pixels"],
                "exposed_samples": exposed["samples"][:20],
            }
        )
        render_contact_sheet(
            settings.source_path,
            evidence_dir / f"iteration_{index}_mask_contact_sheet.jpg",
            candidate,
            samples,
            source_only=True,
        )
        if exposed["sample_count"] == 0:
            selected_mask = candidate
            break
    if selected_mask is None:
        selected_mask = rounds[-1]["mask"]
        if rounds[-1]["exposed_sample_count"] != 0:
            raise RuntimeError("Minimal mask calibration failed to cover detected source subtitle pixels")

    before_contact = evidence_dir / "before_cp06b_contact_sheet.jpg"
    after_contact = evidence_dir / "after_cp06d_contact_sheet.jpg"
    source_edges = evidence_dir / "source_edge_case_contact_sheet.jpg"
    render_contact_sheet(settings.source_path, before_contact, PREVIOUS_MASK, samples, source_only=True)
    render_contact_sheet(settings.source_path, source_edges, selected_mask, edge_samples(measurements), source_only=True)

    cp06d_ass = project_dir / "subtitles" / "cp06d_minimal_mask.ass"
    write_ass(timeline, cp06d_ass)
    output_path = project_dir / "renders" / "cp06d_minimal_mask_vertical_slice_720p.mp4"
    tts_mix = project_dir / "audio" / "cp06b_grouped_tts_mix.wav"
    render_preview(settings.source_path, tts_mix, cp06d_ass, output_path, selected_mask)
    render_contact_sheet(output_path, after_contact, None, samples, source_only=False)

    media = media_summary(output_path)
    summary = {
        "project_id": project_id,
        "sample_count": len(samples),
        "measurement_count": len(measurements),
        "detected_bbox_raw": raw_union,
        "previous_mask": PREVIOUS_MASK,
        "new_mask": selected_mask,
        "reduction": {
            "height_px": PREVIOUS_MASK["height"] - selected_mask["height"],
            "area_px": PREVIOUS_MASK["width"] * PREVIOUS_MASK["height"] - selected_mask["width"] * selected_mask["height"],
            "frame_height_percent_points": round(
                (PREVIOUS_MASK["height"] - selected_mask["height"]) * 100 / HEIGHT, 3
            ),
        },
        "top_edge_deciding_frame": top_edge_deciding_frame(measurements),
        "iterations": rounds,
        "artifact_path": str(output_path),
        "artifact_sha256": sha256_file(output_path),
        "media": media,
        "contact_sheets": {
            "before": str(before_contact),
            "after": str(after_contact),
            "source_edges": str(source_edges),
        },
        "audio_reused_from": str(tts_mix),
        "audio_reused_sha256": sha256_file(tts_mix),
        "provider_calls": {"gemini": 0, "elevenlabs": 0},
        "free_disk_gb": round(shutil.disk_usage(settings.root).free / (1024**3), 2),
    }
    (evidence_dir / "sample_list.json").write_text(
        json.dumps([sample.__dict__ for sample in samples], indent=2), encoding="utf-8"
    )
    (evidence_dir / "geometry_measurements.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
    (evidence_dir / "calibration_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (evidence_dir / "ffprobe.json").write_text(json.dumps(media, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def build_samples(timeline: dict) -> list[Sample]:
    samples: list[Sample] = []
    for second in range(int(DURATION_SECONDS) + 1):
        samples.append(Sample(clamp_time(second), "regular_1s"))
    for segment in timeline["segments"]:
        if not segment.get("enabled", True):
            continue
        start = segment["start_ms"] / 1000
        end = segment["end_ms"] / 1000
        midpoint = (start + end) / 2
        samples.extend(
            [
                Sample(clamp_time(start + 0.05), "subtitle_block_start", segment["id"]),
                Sample(clamp_time(midpoint), "subtitle_block_midpoint", segment["id"]),
                Sample(clamp_time(end - 0.05), "subtitle_block_end", segment["id"]),
                Sample(clamp_time(start - 0.35), "transition_before", segment["id"]),
                Sample(clamp_time(start + 0.35), "transition_after", segment["id"]),
                Sample(clamp_time(end - 0.35), "transition_before_end", segment["id"]),
                Sample(clamp_time(end + 0.35), "transition_after_end", segment["id"]),
            ]
        )
        if "\n" in (segment.get("source_text") or ""):
            samples.append(Sample(clamp_time(midpoint), "source_two_line_subtitle", segment["id"]))
    unique: dict[float, Sample] = {}
    for sample in samples:
        unique.setdefault(round(sample.time_s, 3), sample)
    return [unique[key] for key in sorted(unique)]


def measure_source_subtitles(source_path: Path, samples: list[Sample], frames_dir: Path) -> list[dict]:
    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open source video: {source_path}")
    measurements = []
    for index, sample in enumerate(samples):
        frame = read_frame(cap, sample.time_s)
        bbox, stats = detect_subtitle_bbox(frame)
        frame_path = frames_dir / f"sample_{index:04d}_{sample.time_s:08.3f}.jpg"
        if bbox:
            x, y, w, h = bbox_to_xywh(bbox)
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 0, 255), 2)
        cv2.imwrite(str(frame_path), frame)
        measurements.append(
            {
                "index": index,
                "time_s": sample.time_s,
                "reason": sample.reason,
                "segment_id": sample.segment_id,
                "bbox": bbox,
                "stats": stats,
                "frame_path": str(frame_path),
            }
        )
    cap.release()
    return measurements


def detect_subtitle_bbox(frame_bgr: np.ndarray) -> tuple[dict | None, dict]:
    frame = cv2.resize(frame_bgr, (WIDTH, HEIGHT))
    roi_y0, roi_y1 = 555, 682
    roi_x0, roi_x1 = 180, 1100
    roi = frame[roi_y0:roi_y1, roi_x0:roi_x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # The burned-in source captions are high-brightness white glyphs with dark outline/shadow.
    bright = cv2.inRange(hsv, (0, 0, 178), (180, 90, 255))
    edges = cv2.Canny(gray, 80, 180)
    mask = cv2.bitwise_and(bright, cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 3), np.uint8), iterations=2)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    components = []
    for label in range(1, num):
        x, y, w, h, area = stats[label]
        if area < 8 or h < 3:
            continue
        abs_x = int(x + roi_x0)
        abs_y = int(y + roi_y0)
        if abs_y < 560 or abs_y > 674:
            continue
        components.append((abs_x, abs_y, int(w), int(h), int(area)))
    if not components:
        return None, {"component_count": 0, "pixel_count": int(mask.sum() // 255)}
    xs = [x for x, _, _, _, _ in components]
    ys = [y for _, y, _, _, _ in components]
    rights = [x + w - 1 for x, _, w, _, _ in components]
    bottoms = [y + h - 1 for _, y, _, h, _ in components]
    bbox = {"left_x": min(xs), "top_y": min(ys), "right_x": max(rights), "bottom_y": max(bottoms)}
    return bbox, {"component_count": len(components), "pixel_count": int(mask.sum() // 255)}


def count_exposed_pixels(source_path: Path, samples: list[Sample], mask: dict) -> dict:
    cap = cv2.VideoCapture(str(source_path))
    exposed = []
    for sample in samples:
        frame = read_frame(cap, sample.time_s)
        bbox, _ = detect_subtitle_bbox(frame)
        if not bbox:
            continue
        inside = (
            bbox["left_x"] >= mask["x"]
            and bbox["right_x"] <= mask["x"] + mask["width"] - 1
            and bbox["top_y"] >= mask["y"]
            and bbox["bottom_y"] <= mask["y"] + mask["height"] - 1
        )
        if not inside:
            exposed.append({"time_s": sample.time_s, "reason": sample.reason, "bbox": bbox})
    cap.release()
    return {"sample_count": len(exposed), "max_pixels": 1 if exposed else 0, "samples": exposed}


def render_preview(source_path: Path, tts_mix: Path, ass_path: Path, output_path: Path, mask: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", r"\'")
    vf = (
        f"scale={WIDTH}:-2,"
        f"drawbox=x={mask['x']}:y={mask['y']}:w={mask['width']}:h={mask['height']}:color=black@1.000:t=fill,"
        f"subtitles='{ass}'"
    )
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
            f"[0:v]{vf}[v]",
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


def render_contact_sheet(
    video_path: Path,
    output_path: Path,
    mask: dict | None,
    samples: list[Sample],
    *,
    source_only: bool,
) -> None:
    times = [0.5, 5, 10, 15, 25, 35, 45, 55, 65, 72]
    if samples and source_only:
        times = [sample.time_s for sample in samples[:: max(1, len(samples) // 10)]][:10]
    frames = []
    cap = cv2.VideoCapture(str(video_path))
    for time_s in times:
        frame = read_frame(cap, time_s)
        frame = cv2.resize(frame, (320, 180))
        if mask is not None:
            scaled = {
                "x": round(mask["x"] * 320 / WIDTH),
                "y": round(mask["y"] * 180 / HEIGHT),
                "width": round(mask["width"] * 320 / WIDTH),
                "height": round(mask["height"] * 180 / HEIGHT),
            }
            cv2.rectangle(
                frame,
                (scaled["x"], scaled["y"]),
                (scaled["x"] + scaled["width"], scaled["y"] + scaled["height"]),
                (0, 0, 255),
                1,
            )
        frames.append(frame)
    cap.release()
    while len(frames) < 10:
        frames.append(np.zeros((180, 320, 3), dtype=np.uint8))
    rows = [np.hstack(frames[0:5]), np.hstack(frames[5:10])]
    cv2.imwrite(str(output_path), np.vstack(rows))


def edge_samples(measurements: list[dict]) -> list[Sample]:
    with_boxes = [item for item in measurements if item.get("bbox")]
    selected = sorted(with_boxes, key=lambda item: item["bbox"]["top_y"])[:5]
    selected += sorted(with_boxes, key=lambda item: item["bbox"]["right_x"] - item["bbox"]["left_x"], reverse=True)[:5]
    return [Sample(item["time_s"], item["reason"], item.get("segment_id")) for item in selected]


def read_frame(cap: cv2.VideoCapture, time_s: float) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_s) * 1000)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError(f"Could not read frame at {time_s:.3f}s")
    return cv2.resize(frame, (WIDTH, HEIGHT))


def padded_mask(bbox: dict, vertical_pad: int, horizontal_pad: int) -> dict:
    x = max(0, bbox["left_x"] - horizontal_pad)
    y = max(0, bbox["top_y"] - vertical_pad)
    right = min(WIDTH - 1, bbox["right_x"] + horizontal_pad)
    bottom = min(HEIGHT - 1, bbox["bottom_y"] + vertical_pad)
    return {"x": x, "y": y, "width": right - x + 1, "height": bottom - y + 1}


def union_boxes(boxes: list[dict]) -> dict | None:
    if not boxes:
        return None
    return {
        "left_x": min(box["left_x"] for box in boxes),
        "top_y": min(box["top_y"] for box in boxes),
        "right_x": max(box["right_x"] for box in boxes),
        "bottom_y": max(box["bottom_y"] for box in boxes),
    }


def bbox_to_xywh(bbox: dict) -> tuple[int, int, int, int]:
    return (
        bbox["left_x"],
        bbox["top_y"],
        bbox["right_x"] - bbox["left_x"] + 1,
        bbox["bottom_y"] - bbox["top_y"] + 1,
    )


def top_edge_deciding_frame(measurements: list[dict]) -> dict:
    with_boxes = [item for item in measurements if item.get("bbox")]
    return min(with_boxes, key=lambda item: item["bbox"]["top_y"])


def clamp_time(value: float) -> float:
    return round(max(0.0, min(DURATION_SECONDS - 0.04, value)), 3)


if __name__ == "__main__":
    main()
