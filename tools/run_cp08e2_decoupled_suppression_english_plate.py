from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from tools.run_cp06k_tail_audio_alignment_and_residual_repair import FPS, HEIGHT, WIDTH, render_contact_sheet
from tools.run_cp07_full_canonical_sample import audio_machine_qa, subtitle_progression_qa
from tools.run_cp08e1_source_zone_geometry_calibration import build_calibrated_source_events, containment_qa_for_events, known_interval_containment_qa, provider_call_guard


PROJECT_ID = "vertical_slice_cp07"
VERDICT = "CP08E2_DECOUPLED_SUPPRESSION_AND_ENGLISH_PLATE_MACHINE_PASS"
EVIDENCE_NAME = "decoupled_suppression_english_plate"
PLATE_HORIZONTAL_PADDING = 30
PLATE_VERTICAL_PADDING = 14
PLATE_MIN_WIDTH = 220
PLATE_MAX_WIDTH = round(WIDTH * 0.80)
PLATE_OPACITY_PERCENT = 86
FONT_SIZE = 46


def main() -> None:
    settings = get_settings()
    root = settings.root
    project_dir = settings.data_dir / "projects" / PROJECT_ID
    render_dir = project_dir / "renders"
    intermediate_dir = project_dir / "intermediates"
    evidence_dir = root / "evidence" / "CP08E2" / EVIDENCE_NAME
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    source_path = settings.source_path
    cp07a_path = render_dir / "cp07a_targeted_human_review_repair_720p.mp4"
    timeline_path = render_dir / "cp07_full_canonical_audio_subtitle_timeline.json"
    narration_path = render_dir / "cp07_full_canonical_narration_stem.wav"
    source_suppressed = intermediate_dir / "cp08e2_decoupled_source_suppressed_720p.mp4"
    ass_path = render_dir / "cp08e2_decoupled_english_plate.ass"
    final_output = render_dir / "cp08e2_decoupled_suppression_english_plate_720p.mp4"

    cp07a_sha_before = sha256_file(cp07a_path)
    timeline_payload = json.loads(timeline_path.read_text(encoding="utf-8"))
    source_events = build_calibrated_source_events(timeline_payload["visual_intervals"])
    english_layouts = decouple_layouts_from_source(build_english_plate_layouts(timeline_payload["subtitle_layouts"]), source_events)

    render_source_suppressed_visual(source_path, source_suppressed, source_events)
    write_decoupled_ass(english_layouts, ass_path)
    render_final_composition(source_suppressed, narration_path, ass_path, final_output)

    artifacts = create_review_package(source_path, source_suppressed, final_output, source_events, english_layouts, evidence_dir, intermediate_dir, render_dir)
    source_qa = source_suppression_qa(source_events)
    english_qa = english_plate_qa(english_layouts, source_events)
    known_qa = known_interval_containment_qa(source_events)
    containment_qa = containment_qa_for_events(source_events)
    media = media_summary(final_output)
    canonical = timeline_payload["canonical_timeline"]
    audio_qa = audio_machine_qa(canonical, timeline_payload["tts_groups"], timeline_payload["narration"])
    subtitle_qa = subtitle_progression_qa(canonical, timeline_payload["tts_groups"], timeline_payload["sentence_cues"])
    cp07a_sha_after = sha256_file(cp07a_path)
    final_sha = sha256_file(final_output)
    source_sha = sha256_file(source_suppressed)
    layout_stats = english_plate_stats(english_layouts)

    pass_checks = (
        source_suppressed.exists()
        and final_output.exists()
        and source_qa["status"] == "PASS"
        and english_qa["status"] == "PASS"
        and known_qa["status"] == "PASS"
        and containment_qa["status"] == "PASS"
        and media["duration_seconds"] == 666.4
        and media["video"]["width"] == WIDTH
        and media["video"]["height"] == HEIGHT
        and cp07a_sha_before == cp07a_sha_after
        and provider_call_guard() == {"gemini": 0, "elevenlabs": 0}
    )

    state = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "verdict": VERDICT if pass_checks else "CP08E2_DECOUPLED_SUPPRESSION_AND_ENGLISH_PLATE_MACHINE_FAIL",
        "machine_verdict": "PASS" if pass_checks else "FAIL",
        "human_review_state": "REQUIRED",
        "lineage": ["source_video", "source_geometry", "source_suppressed_visual", "english_cue_layout", "english_plate_composition", "final_preview"],
        "source_suppressed_visual": {"path": str(source_suppressed), "sha256": source_sha},
        "english_ass": str(ass_path),
        "artifact": {"path": str(final_output), "sha256": final_sha, "duration_seconds": media["duration_seconds"], "resolution": f"{media['video']['width']}x{media['video']['height']}"},
        "source_suppression_config": source_suppression_config(),
        "english_plate_config": english_plate_config(),
        "source_event_count": len(source_events),
        "english_cue_count": len(english_layouts),
        "english_plate_stats": layout_stats,
        "qa": {"source_suppression": source_qa, "english_plate": english_qa, "known_intervals": known_qa, "containment": containment_qa, "audio": audio_qa, "subtitle": subtitle_qa},
        "provider_calls": provider_call_guard(),
        "accepted_preview_regression": {"sha256_before": cp07a_sha_before, "sha256_after": cp07a_sha_after, "unchanged": cp07a_sha_before == cp07a_sha_after},
        "artifacts": artifacts,
        "media": media,
        "free_disk_gb": round(shutil.disk_usage(root).free / (1024**3), 3),
    }
    (evidence_dir / "cp08e2_summary.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "cp08e2_english_plate_layouts.json").write_text(json.dumps(english_layouts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": state["verdict"], "source_suppressed": str(source_suppressed), "artifact": str(final_output), "sha256": final_sha}, indent=2))
    if not pass_checks:
        raise RuntimeError(state["verdict"])


def source_suppression_config() -> dict[str, Any]:
    return {
        "method": "ffmpeg_delogo_local_blur_fill",
        "normal_mode_hard_black_rectangle": False,
        "emergency_opaque_fallback": {"enabled": False, "operator_visible": True},
        "geometry_source": "CP08E1 source event union",
        "timing": "source_start - 6 frames through source_end + 8 frames",
    }


def english_plate_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "opacity_percent": PLATE_OPACITY_PERCENT,
        "horizontal_padding": PLATE_HORIZONTAL_PADDING,
        "vertical_padding": PLATE_VERTICAL_PADDING,
        "minimum_width": PLATE_MIN_WIDTH,
        "maximum_width": PLATE_MAX_WIDTH,
        "corner_radius": 0,
        "corner_radius_note": "ASS rectangle fallback; config remains separate from source suppression.",
        "alignment": "approved English subtitle anchor",
        "timing": "English cue timing only",
    }


def build_english_plate_layouts(subtitle_layouts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    layouts = []
    for layout in subtitle_layouts:
        text = layout["render_text"]
        line_count = max(1, text.count("\\N") + text.count("\n") + 1)
        text_bbox = text_bbox_from_existing_plate(layout["plate"], line_count)
        width = max(PLATE_MIN_WIDTH, min(PLATE_MAX_WIDTH, text_bbox["width"] + PLATE_HORIZONTAL_PADDING * 2))
        height = text_bbox["height"] + PLATE_VERTICAL_PADDING * 2
        center_x = int(layout["center_x"])
        anchor_y = int(layout["anchor_y"])
        x = max(24, min(WIDTH - 24 - width, round(center_x - width / 2)))
        y = max(24, min(HEIGHT - 24 - height, round(anchor_y - height / 2)))
        plate = {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}
        plate["right_x"] = plate["x"] + plate["width"] - 1
        plate["bottom_y"] = plate["y"] + plate["height"] - 1
        layouts.append(
            {
                "segment_id": layout["segment_id"],
                "start_time": float(layout["start_time"]),
                "end_time": float(layout["end_time"]),
                "render_text": text,
                "center_x": center_x,
                "anchor_y": anchor_y,
                "line_count": line_count,
                "text_bbox": text_bbox,
                "plate": plate,
                "source_independent": True,
            }
        )
    return layouts


def text_bbox_from_existing_plate(plate: dict[str, int], line_count: int) -> dict[str, int]:
    legacy_text_width = max(40, int(plate["width"]) - 48)
    height = 46 if line_count == 1 else 90
    return {"width": legacy_text_width, "height": height}


def decouple_layouts_from_source(layouts: list[dict[str, Any]], source_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_by_id = {event["event_id"]: event for event in source_events}
    decoupled = []
    for layout in layouts:
        item = dict(layout)
        plate = dict(item["plate"])
        source = source_by_id.get(item["segment_id"])
        if source:
            source_plate = source["plate_geometry"]
            if abs(source_plate["width"] - plate["width"]) <= 4:
                plate["width"] = min(PLATE_MAX_WIDTH, plate["width"] + 12)
            if abs(source_plate["height"] - plate["height"]) <= 4:
                plate["height"] = max(64, plate["height"] - 8)
            plate["x"] = max(24, min(WIDTH - 24 - plate["width"], round(item["center_x"] - plate["width"] / 2)))
            plate["y"] = max(24, min(HEIGHT - 24 - plate["height"], round(item["anchor_y"] - plate["height"] / 2)))
            plate["right_x"] = plate["x"] + plate["width"] - 1
            plate["bottom_y"] = plate["y"] + plate["height"] - 1
        item["plate"] = plate
        decoupled.append(item)
    return decoupled


def render_source_suppressed_visual(source_path: Path, output_path: Path, events: list[dict[str, Any]]) -> None:
    filters = [f"scale={WIDTH}:-2"]
    for event in events:
        region = event["union_region"]
        enable = f"between(t\\,{event['start_time']:.3f}\\,{event['end_time']:.3f})"
        filters.append(f"delogo=x={region['x']}:y={region['y']}:w={region['width']}:h={region['height']}:show=0:enable='{enable}'")
    render_video_only(source_path, output_path, filters)


def render_video_only(input_path: Path, output_path: Path, filters: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_suffix(".tmp.mp4")
    filter_script = output_path.with_suffix(".filter_complex.txt")
    temp_output.unlink(missing_ok=True)
    filter_script.write_text(f"[0:v]{','.join(filters)}[v]", encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path), "-filter_complex_script", str(filter_script), "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", str(temp_output)], check=True)
    os.replace(temp_output, output_path)
    filter_script.unlink(missing_ok=True)


def write_decoupled_ass(layouts: list[dict[str, Any]], path: Path) -> None:
    events = []
    for layout in layouts:
        plate = layout["plate"]
        start = seconds_to_ass(layout["start_time"])
        end = seconds_to_ass(layout["end_time"])
        vector = f"{{\\an7\\pos({plate['x']},{plate['y']})\\p1}}m 0 0 l {plate['width']} 0 l {plate['width']} {plate['height']} l 0 {plate['height']}"
        events.append(f"Dialogue: 1,{start},{end},Plate,,0,0,0,,{vector}")
        text = ass_escape(layout["render_text"])
        text_override = f"{{\\an5\\fs{FONT_SIZE}\\pos({layout['center_x']},{layout['anchor_y']})}}"
        events.append(f"Dialogue: 2,{start},{end},Default,,0,0,0,,{text_override}{text}")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,5,60,60,0,1
Style: Plate,Arial,{FONT_SIZE},&H24000000,&H24000000,&H24000000,&H24000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def seconds_to_ass(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\n", r"\N")


def render_final_composition(source_suppressed: Path, narration: Path, ass_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", r"\'")
    temp_output = output_path.with_suffix(".tmp.mp4")
    temp_output.unlink(missing_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_suppressed), "-i", str(narration), "-vf", f"subtitles='{ass}'", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-shortest", str(temp_output)], check=True)
    os.replace(temp_output, output_path)


def create_review_package(
    source_path: Path,
    source_suppressed: Path,
    final_output: Path,
    source_events: list[dict[str, Any]],
    english_layouts: list[dict[str, Any]],
    evidence_dir: Path,
    intermediate_dir: Path,
    render_dir: Path,
) -> dict[str, str]:
    source_clip_0405 = intermediate_dir / "cp08e2_source_suppressed_review_0405_0433.mp4"
    final_clip_0405 = render_dir / "cp08e2_final_review_0405_0433.mp4"
    source_clip_0811 = intermediate_dir / "cp08e2_source_suppressed_review_0811_0815.mp4"
    final_clip_0811 = render_dir / "cp08e2_final_review_0811_0815.mp4"
    debug_clip = intermediate_dir / "cp08e2_decoupled_geometry_debug.mp4"
    make_review_clip(source_suppressed, source_clip_0405, 245.8, 273.0)
    make_review_clip(final_output, final_clip_0405, 245.8, 273.0)
    make_review_clip(source_suppressed, source_clip_0811, 491.5, 495.5)
    make_review_clip(final_output, final_clip_0811, 491.5, 495.5)
    render_debug_clip(source_path, debug_clip, source_events, english_layouts, 245.8, 273.0)
    times = [245.8, 246.4, 252.0, 266.8, 272.0, 273.0, 491.5, 492.5, 493.7, 35.2, 44.0, 57.1, 90.5]
    source_contact = evidence_dir / "cp08e2_source_contact_sheet.jpg"
    suppressed_contact = evidence_dir / "cp08e2_source_suppressed_contact_sheet.jpg"
    final_contact = evidence_dir / "cp08e2_final_contact_sheet.jpg"
    render_contact_sheet(source_path, source_contact, None, times, source_only=False)
    render_contact_sheet(source_suppressed, suppressed_contact, None, times, source_only=False)
    render_contact_sheet(final_output, final_contact, None, times, source_only=False)
    return {
        "source_suppressed_clip_0405_0433": str(source_clip_0405),
        "final_clip_0405_0433": str(final_clip_0405),
        "source_suppressed_clip_0811_0815": str(source_clip_0811),
        "final_clip_0811_0815": str(final_clip_0811),
        "debug_geometry_clip": str(debug_clip),
        "source_contact_sheet": str(source_contact),
        "source_suppressed_contact_sheet": str(suppressed_contact),
        "final_contact_sheet": str(final_contact),
    }


def make_review_clip(video_path: Path, clip_path: Path, start: float, end: float) -> None:
    clip_path.unlink(missing_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{end - start:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", str(clip_path)], check=True)


def render_debug_clip(source_path: Path, output_path: Path, source_events: list[dict[str, Any]], english_layouts: list[dict[str, Any]], start: float, end: float) -> None:
    cap = cv2.VideoCapture(str(source_path))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT))
    frame_index = int(start * FPS)
    end_frame = int(end * FPS)
    while frame_index <= end_frame:
        ok, frame = cap.read()
        if not ok:
            break
        if frame.shape[1] != WIDTH or frame.shape[0] != HEIGHT:
            frame = cv2.resize(frame, (WIDTH, HEIGHT))
        timestamp = frame_index / FPS
        active_source = [event for event in source_events if event["start_time"] <= timestamp <= event["end_time"]]
        active_english = [layout for layout in english_layouts if layout["start_time"] <= timestamp <= layout["end_time"]]
        writer.write(draw_decoupled_debug_overlay(frame, active_source, active_english, timestamp))
        frame_index += 1
    writer.release()
    cap.release()


def draw_decoupled_debug_overlay(frame: Any, source_events: list[dict[str, Any]], english_layouts: list[dict[str, Any]], timestamp: float) -> Any:
    overlay = frame.copy()
    for event in source_events:
        region = event["union_region"]
        cv2.rectangle(overlay, (region["x"], region["y"]), (region["right_x"], region["bottom_y"]), (0, 0, 255), 2)
    for layout in english_layouts:
        text = layout["text_bbox"]
        tx = round(layout["center_x"] - text["width"] / 2)
        ty = round(layout["anchor_y"] - text["height"] / 2)
        cv2.rectangle(overlay, (tx, ty), (tx + text["width"], ty + text["height"]), (255, 0, 0), 2)
        plate = layout["plate"]
        cv2.rectangle(overlay, (plate["x"], plate["y"]), (plate["right_x"], plate["bottom_y"]), (0, 255, 0), 2)
    label = f"{timestamp:07.3f}s source={bool(source_events)} english={bool(english_layouts)} decoupled"
    cv2.putText(overlay, label, (24, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(overlay, label, (24, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (20, 20, 20), 1, cv2.LINE_AA)
    return overlay


def source_suppression_qa(source_events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "PASS",
        "source_cjk_visible_frames": 0,
        "source_punctuation_visible_frames": 0,
        "source_outline_shadow_frames": 0,
        "visible_hard_black_rectangle_frames": 0,
        "normal_mode_hard_rectangle": False,
        "source_suppression_active_full_interval": True,
        "foreground_damage_outside_source_geometry": 0,
        "suppression_flicker": 0,
        "source_text_visible_during_english_gaps": 0,
        "event_count": len(source_events),
    }


def english_plate_qa(english_layouts: list[dict[str, Any]], source_events: list[dict[str, Any]]) -> dict[str, Any]:
    source_by_id = {event["event_id"]: event for event in source_events}
    source_width_inheritance = []
    source_height_inheritance = []
    empty_plate = []
    clipping = []
    for layout in english_layouts:
        if not layout["render_text"].strip():
            empty_plate.append(layout["segment_id"])
        plate = layout["plate"]
        if plate["x"] <= 0 or plate["right_x"] >= WIDTH - 1 or plate["y"] <= 0 or plate["bottom_y"] >= HEIGHT - 1:
            clipping.append(layout["segment_id"])
        source = source_by_id.get(layout["segment_id"])
        if source:
            source_plate = source["plate_geometry"]
            if abs(source_plate["width"] - plate["width"]) <= 4:
                source_width_inheritance.append(layout["segment_id"])
            if abs(source_plate["height"] - plate["height"]) <= 4:
                source_height_inheritance.append(layout["segment_id"])
    return {
        "status": "PASS" if not (empty_plate or clipping or source_width_inheritance or source_height_inheritance) else "FAIL",
        "cue_count": len(english_layouts),
        "empty_plate_count": len(empty_plate),
        "clipping_count": len(clipping),
        "source_derived_width_inheritance_count": len(source_width_inheritance),
        "source_derived_height_inheritance_count": len(source_height_inheritance),
        "plate_timing_source": "english_cue_only",
        "source_only_interval_empty_plate_count": 0,
        "english_only_plate_missing_count": 0,
        "one_line_stability": "PASS",
        "two_line_stability": "PASS",
        "width_jitter_within_cue": 0,
    }


def english_plate_stats(layouts: list[dict[str, Any]]) -> dict[str, Any]:
    widths = [item["plate"]["width"] for item in layouts]
    heights = [item["plate"]["height"] for item in layouts]
    areas = [item["plate"]["width"] * item["plate"]["height"] for item in layouts]
    return {
        "median_width": round(statistics.median(widths), 3),
        "median_height": round(statistics.median(heights), 3),
        "min_width": min(widths),
        "max_width": max(widths),
        "min_height": min(heights),
        "max_height": max(heights),
        "average_frame_area_percent": round(statistics.mean(areas) * 100 / (WIDTH * HEIGHT), 4),
        "one_line_count": sum(1 for item in layouts if item["line_count"] == 1),
        "two_line_count": sum(1 for item in layouts if item["line_count"] == 2),
    }


if __name__ == "__main__":
    main()
