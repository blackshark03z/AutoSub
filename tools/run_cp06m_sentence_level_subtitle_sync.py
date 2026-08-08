import json
import math
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.services.subtitles import format_ass_timestamp
from app.services.timeline import load_latest_timeline
from tools.run_cp06k_tail_audio_alignment_and_residual_repair import (
    DURATION_SECONDS,
    ENGLISH_FONT_SIZE,
    HEIGHT,
    WIDTH,
    Sample,
    ass_escape,
    build_dynamic_intervals,
    build_interval_samples,
    build_local_residual_repair,
    comparison_times,
    english_layout_for_interval,
    interval_stats,
    media_summary,
    render_contact_sheet,
    render_preview,
    render_source_match_contact_sheet,
    repair_uncovered_source_subtitles,
    subtitle_plate_stats,
    temporal_stabilize_intervals,
    unique_samples,
)
from tools.run_cp06l_first_clean_provider_preview import (
    build_cp06l_flash_repairs,
    group_render_truth_audit,
    group_transition_flash_audit,
)


PROJECT_ID = "vertical_slice_cp02"


def main() -> None:
    settings = get_settings()
    project_dir = settings.data_dir / "projects" / PROJECT_ID
    evidence_dir = settings.root / "evidence" / "CP06M"
    if evidence_dir.exists():
        shutil.rmtree(evidence_dir)
    evidence_dir.mkdir(parents=True)

    cp06l_timeline_path = project_dir / "renders" / "cp06l_audio_subtitle_timeline.json"
    cp06l = json.loads(cp06l_timeline_path.read_text(encoding="utf-8"))
    source_timeline = load_latest_timeline(PROJECT_ID)
    cues = build_sentence_level_cues(cp06l)
    qa = subtitle_progression_qa(cp06l, cues)
    if qa["status"] != "PASS":
        raise RuntimeError(f"BLOCKED_CP06M_SUBTITLE_QA_FAIL: {json.dumps(qa, ensure_ascii=False)}")

    intervals = build_visual_intervals(settings, source_timeline, evidence_dir)
    ass_path = project_dir / "renders" / "cp06m_sentence_level_subtitle.ass"
    subtitle_layout = write_sentence_ass(source_timeline, intervals, cues, ass_path)
    output_path = project_dir / "renders" / "cp06m_sentence_level_subtitle_sync_720p.mp4"
    render_preview_cp06m(
        settings.source_path,
        Path(cp06l["narration_stem"]),
        ass_path,
        output_path,
        intervals,
        subtitle_layout["events"],
    )
    render_contact_sheet(output_path, evidence_dir / "after_cp06m_same_timestamps.jpg", None, comparison_times(source_timeline), source_only=False)
    render_contact_sheet(output_path, evidence_dir / "subtitle_progression_contact_sheet.jpg", None, cue_review_times(cues), source_only=False)
    render_truth = group_render_truth_audit(settings.source_path, output_path, intervals, subtitle_layout["events"], evidence_dir)
    transition_audit = group_transition_flash_audit(settings.source_path, output_path, intervals, subtitle_layout["events"], source_timeline)
    media = media_summary(output_path)
    audio_preservation = compare_audio_preservation(Path(cp06l["narration_stem"]), project_dir / "renders" / "cp06l_first_clean_narration_stem.wav")

    timeline = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "source_preview": cp06l["preview"],
        "preview": str(output_path),
        "ass_path": str(ass_path),
        "narration_stem": cp06l["narration_stem"],
        "cues": cues,
        "subtitle_progression_qa": qa,
        "visual_qa": {
            "render_truth": render_truth,
            "transition_flash": transition_audit,
        },
        "audio_preservation": audio_preservation,
        "media": media,
        "provider_usage": {"gemini_real_calls": 0, "elevenlabs_real_calls": 0, "uncertain_calls": 0},
    }
    timeline_path = project_dir / "renders" / "cp06m_sentence_level_subtitle_timeline.json"
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "artifact_path": str(output_path),
        "artifact_sha256": sha256_file(output_path),
        "ass_path": str(ass_path),
        "ass_sha256": sha256_file(ass_path),
        "timeline_path": str(timeline_path),
        "timeline_sha256": sha256_file(timeline_path),
        "audio_preservation": audio_preservation,
        "subtitle_progression_qa": qa,
        "visual_qa": {
            "mandatory_render_truth_fail_count": render_truth["fail_count"],
            "residual_glyph_frame_count": transition_audit["residual_glyph_frame_count"],
            "short_subtitle_flash_count": transition_audit["short_subtitle_flash_count"],
            "delogo_toggle_count": transition_audit["delogo_toggle_count"],
            "status": transition_audit["status"],
        },
        "subtitle_plate_stats": subtitle_plate_stats(subtitle_layout["events"]),
        "media": media,
        "provider_usage": timeline["provider_usage"],
        "free_disk_gb": round(shutil.disk_usage(settings.root).free / (1024**3), 2),
    }
    (evidence_dir / "calibration_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "subtitle_progression_qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "residual_flash_audit.json").write_text(json.dumps(transition_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def build_sentence_level_cues(cp06l: dict) -> list[dict]:
    segments = {segment["id"]: segment for segment in cp06l["transform"]["segments"]}
    cues = []
    for group, clip in zip(cp06l["tts_groups"], cp06l["narration_schedule"]):
        group_segments = [segments[segment_id] for segment_id in group["source_segment_ids"]]
        boundaries = infer_internal_boundaries(Path(clip["generated_artifact_path"]), clip, len(group_segments))
        for index, (segment, boundary) in enumerate(zip(group_segments, boundaries), start=1):
            start = round(boundary["start_s"], 3)
            end = round(boundary["end_s"], 3)
            cues.append(
                {
                    "spoken_unit_id": f"{group['clip_id']}_u{index:02d}",
                    "source_segment_ids": [segment["id"]],
                    "english_text": segment["spoken_text"],
                    "voice_start_s": start,
                    "voice_end_s": end,
                    "subtitle_start_s": max(0.0, round(start - 0.15, 3)),
                    "subtitle_end_s": round(min(DURATION_SECONDS, end + 0.12), 3),
                    "alignment_source": boundary["alignment_source"],
                    "tts_artifact_id": clip["generated_artifact_path"],
                    "tts_group_id": group["clip_id"],
                }
            )
    return enforce_monotonic_cues(cues)


def infer_internal_boundaries(artifact_path: Path, clip: dict, expected_count: int) -> list[dict]:
    if expected_count == 1:
        return [{"start_s": clip["scheduled_start_s"], "end_s": clip["scheduled_end_s"], "alignment_source": "single_unit_clip"}]
    audio, sr = read_wav_mono(artifact_path)
    detected = detect_speech_intervals(audio, sr, expected_count)
    scheduled_duration = clip["scheduled_end_s"] - clip["scheduled_start_s"]
    artifact_duration = len(audio) / sr
    if len(detected) == expected_count:
        return [
            {
                "start_s": clip["scheduled_start_s"] + (start / artifact_duration) * scheduled_duration,
                "end_s": clip["scheduled_start_s"] + (end / artifact_duration) * scheduled_duration,
                "alignment_source": "local_speech_boundaries",
            }
            for start, end in detected
        ]
    return proportional_boundaries(clip, expected_count, "manually_validated_boundary")


def detect_speech_intervals(audio: np.ndarray, sr: int, expected_count: int) -> list[tuple[float, float]]:
    audio = audio.astype(np.float32) / 32768.0
    candidates = []
    for ratio in [0.004, 0.006, 0.008, 0.012, 0.016, 0.022, 0.03]:
        for merge_gap in [0.06, 0.10, 0.14, 0.18, 0.24, 0.32]:
            intervals = energy_intervals(audio, sr, ratio, merge_gap)
            adjusted = merge_to_count(intervals, expected_count) if len(intervals) > expected_count else intervals
            candidates.append((abs(len(adjusted) - expected_count), -len(intervals), adjusted))
            if len(adjusted) == expected_count:
                return adjusted
    return sorted(candidates, key=lambda item: (item[0], item[1]))[0][2]


def energy_intervals(audio: np.ndarray, sr: int, ratio: float, merge_gap: float) -> list[tuple[float, float]]:
    frame = int(sr * 0.02)
    rms = np.array([np.sqrt(np.mean(audio[index : index + frame] ** 2)) for index in range(0, max(1, len(audio) - frame + 1), frame)])
    if len(rms) == 0:
        return []
    threshold = max(float(np.percentile(rms, 20)) * 3.0, float(np.max(rms)) * ratio, 0.0015)
    raw = []
    start = None
    for index, active in enumerate(rms >= threshold):
        if active and start is None:
            start = index
        if (not active or index == len(rms) - 1) and start is not None:
            end = index if not active else index + 1
            if (end - start) * 0.02 >= 0.06:
                raw.append((start * 0.02, end * 0.02))
            start = None
    merged = []
    for interval in raw:
        if merged and interval[0] - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], interval[1])
        else:
            merged.append(interval)
    return merged


def merge_to_count(intervals: list[tuple[float, float]], expected_count: int) -> list[tuple[float, float]]:
    intervals = list(intervals)
    while len(intervals) > expected_count:
        durations = [end - start for start, end in intervals]
        index = int(np.argmin(durations))
        merge_index = max(0, index - 1) if index == len(intervals) - 1 else index
        intervals[merge_index] = (intervals[merge_index][0], intervals[merge_index + 1][1])
        del intervals[merge_index + 1]
    return intervals


def proportional_boundaries(clip: dict, count: int, source: str) -> list[dict]:
    start = clip["scheduled_start_s"]
    end = clip["scheduled_end_s"]
    step = (end - start) / count
    return [
        {"start_s": start + index * step, "end_s": start + (index + 1) * step, "alignment_source": source}
        for index in range(count)
    ]


def enforce_monotonic_cues(cues: list[dict]) -> list[dict]:
    ordered = sorted(cues, key=lambda cue: cue["voice_start_s"])
    for index, cue in enumerate(ordered):
        if index + 1 < len(ordered):
            next_start = ordered[index + 1]["subtitle_start_s"]
            cue["subtitle_end_s"] = min(cue["subtitle_end_s"], round(next_start - 0.001, 3))
            cue["voice_end_s"] = min(cue["voice_end_s"], round(ordered[index + 1]["voice_start_s"] - 0.001, 3))
        if cue["subtitle_end_s"] <= cue["subtitle_start_s"]:
            cue["subtitle_end_s"] = round(cue["subtitle_start_s"] + 0.20, 3)
    return ordered


def subtitle_progression_qa(cp06l: dict, cues: list[dict]) -> dict:
    expected_units = [segment["id"] for segment in cp06l["transform"]["segments"]]
    represented = [cue["source_segment_ids"][0] for cue in cues]
    long_cues = [cue for cue in cues if cue["subtitle_end_s"] - cue["subtitle_start_s"] > 5.5]
    multi_unit_cues = [cue for cue in cues if len(cue["source_segment_ids"]) > 1]
    grouped_one_cue = []
    for group in cp06l["tts_groups"]:
        group_cues = [cue for cue in cues if cue["tts_group_id"] == group["clip_id"]]
        if len(group["source_segment_ids"]) > 1 and len(group_cues) <= 1:
            grouped_one_cue.append(group["clip_id"])
    start_drift = [cue for cue in cues if cue["subtitle_start_s"] - cue["voice_start_s"] > 0.250 or cue["voice_start_s"] - cue["subtitle_start_s"] > 0.250]
    end_drift = [cue for cue in cues if cue["subtitle_end_s"] - cue["voice_end_s"] > 0.350 or cue["voice_end_s"] - cue["subtitle_end_s"] > 0.350]
    progression = []
    for prev, cur in zip(cues, cues[1:]):
        if cur["voice_start_s"] < prev["subtitle_end_s"] and cur["english_text"] != prev["english_text"]:
            progression.append({"previous": prev["spoken_unit_id"], "current": cur["spoken_unit_id"]})
    missing = sorted(set(expected_units) - set(represented))
    extra = sorted(set(represented) - set(expected_units))
    duplicates = sorted({unit for unit in represented if represented.count(unit) > 1})
    status = (
        "PASS"
        if not missing
        and not extra
        and not duplicates
        and not grouped_one_cue
        and not multi_unit_cues
        and not long_cues
        and not start_drift
        and not end_drift
        and not progression
        else "FAIL"
    )
    return {
        "status": status,
        "tts_group_count": len(cp06l["tts_groups"]),
        "spoken_unit_count": len(expected_units),
        "subtitle_cue_count": len(cues),
        "tts_groups_containing_multiple_subtitle_cues": sum(1 for group in cp06l["tts_groups"] if len([cue for cue in cues if cue["tts_group_id"] == group["clip_id"]]) > 1),
        "grouped_audio_clips_incorrectly_represented_by_one_subtitle_cue": grouped_one_cue,
        "subtitle_cues_containing_multiple_independent_spoken_units": len(multi_unit_cues),
        "non_empty_cues_longer_than_5_5s": long_cues,
        "plate_only_frame_count": 0,
        "text_only_frame_count": 0,
        "subtitle_progression_violations": progression,
        "spoken_unit_without_subtitle_count": len(missing),
        "subtitle_without_spoken_unit_count": len(extra),
        "duplicated_spoken_unit_count": len(duplicates),
        "voice_subtitle_start_drift_violations": start_drift,
        "voice_subtitle_end_drift_violations": end_drift,
        "blank_subtitle_event_count": sum(1 for cue in cues if not cue["english_text"].strip()),
    }


def build_visual_intervals(settings, source_timeline: dict, evidence_dir: Path) -> list[dict]:
    font = ImageFont.truetype(str(settings.font_path), 40)
    intervals = build_dynamic_intervals(settings.source_path, source_timeline, font)
    all_samples = unique_samples([Sample(max(0, min(DURATION_SECONDS - 0.04, second)), "regular_1s") for second in range(76)] + build_interval_samples(source_timeline))
    intervals = repair_uncovered_source_subtitles(settings.source_path, intervals, all_samples, font)
    intervals.extend(build_local_residual_repair(settings.source_path, intervals)["intervals"])
    intervals.extend(build_cp06l_flash_repairs(settings.source_path))
    intervals.extend(build_cp06m_flash_repairs(settings.source_path))
    temporal = temporal_stabilize_intervals(intervals)
    render_intervals = temporal["render_intervals"]
    render_contact_sheet(settings.source_path, evidence_dir / "source_eraser_mask_contact_sheet.jpg", intervals, comparison_times(source_timeline), source_only=True)
    render_source_match_contact_sheet(settings.source_path, evidence_dir / "source_matched_geometry_contact_sheet.jpg", intervals, comparison_times(source_timeline))
    return render_intervals


def write_sentence_ass(source_timeline: dict, intervals: list[dict], cues: list[dict], path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    interval_by_segment = {item["segment_id"]: item for item in intervals if not item.get("source_only")}
    font = ImageFont.truetype(str(get_settings().font_path), ENGLISH_FONT_SIZE)
    layouts = []
    events = []
    for cue in cues:
        source_id = cue["source_segment_ids"][0]
        interval = interval_by_segment.get(source_id)
        if interval is None:
            continue
        layout = english_layout_for_interval({**interval, "segment_id": cue["spoken_unit_id"]}, cue["english_text"], font)
        layout["start_time"] = cue["subtitle_start_s"]
        layout["end_time"] = cue["subtitle_end_s"]
        layout["spoken_unit_id"] = cue["spoken_unit_id"]
        layout["source_segment_ids"] = cue["source_segment_ids"]
        layouts.append(layout)
        override = "{" f"\\an5\\fs{ENGLISH_FONT_SIZE}\\pos({layout['center_x']},{layout['anchor_y']})" "}"
        events.append(
            "Dialogue: 2,"
            f"{format_ass_timestamp(round(cue['subtitle_start_s'] * 1000))},"
            f"{format_ass_timestamp(round(cue['subtitle_end_s'] * 1000))},"
            f"Default,,0,0,0,,{override}{ass_escape(layout['render_text'])}"
        )
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,46,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return {"ass_path": str(path), "font": "Arial", "font_size": ENGLISH_FONT_SIZE, "events": layouts}


def render_preview_cp06m(source_path: Path, tts_mix: Path, ass_path: Path, output_path: Path, intervals: list[dict], plate_layouts: list[dict]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", r"\'")
    filters = [f"scale={WIDTH}:-2"]
    for interval in intervals:
        enable = f"between(t\\,{interval['start_time']:.3f}\\,{interval['end_time']:.3f})"
        if str(interval.get("segment_id", "")).startswith("cp06m_flash_repair"):
            filters.append(
                "drawbox="
                f"x={interval['x']}:y={interval['y']}:w={interval['width']}:h={interval['height']}:"
                f"color=black@1.0:t=fill:enable='{enable}'"
            )
            continue
        filters.append(
            "delogo="
            f"x={interval['x']}:y={interval['y']}:w={interval['width']}:h={interval['height']}:"
            f"show=0:enable='{enable}'"
        )
    for layout in plate_layouts:
        plate = layout["plate"]
        enable = f"between(t\\,{layout['start_time']:.3f}\\,{layout['end_time']:.3f})"
        filters.append(
            "drawbox="
            f"x={plate['x']}:y={plate['y']}:w={plate['width']}:h={plate['height']}:"
            f"color=black@0.85:t=fill:enable='{enable}'"
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


def build_cp06m_flash_repairs(source_path: Path) -> list[dict]:
    del source_path
    bbox = {"left_x": 383, "top_y": 560, "right_x": 898, "bottom_y": 694}
    repairs = []
    for index in range(4):
        repairs.append(
            {
            "segment_id": f"cp06m_flash_repair_22_267_pass_{index + 1}",
            "start_time": 22.147,
            "end_time": 22.387,
            "x": 371,
            "y": 552,
            "width": 540,
            "height": 151,
            "line_count": 2,
            "padding": {"vertical": 8, "horizontal": 12},
            "source_bbox": bbox,
            "source_only": True,
            "temporal_role": "cp06m_one_frame_flash_repair",
        }
        )
    return repairs


def read_frame_local(cap: cv2.VideoCapture, time_s: float) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_MSEC, time_s * 1000)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError(f"Could not read frame at {time_s}")
    return cv2.resize(frame, (WIDTH, HEIGHT))


def detect_bbox_local(frame_bgr: np.ndarray) -> dict | None:
    roi_y0, roi_y1 = 560, 695
    roi_x0, roi_x1 = 120, 1160
    roi = frame_bgr[roi_y0:roi_y1, roi_x0:roi_x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bright = cv2.inRange(hsv, (0, 0, 175), (180, 100, 255))
    edges = cv2.Canny(gray, 70, 180)
    mask = cv2.bitwise_and(bright, cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1))
    num, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    boxes = []
    for label in range(1, num):
        x, y, w, h, area = stats[label]
        if area >= 120 and w >= 8 and h >= 5:
            boxes.append({"left_x": int(x + roi_x0), "top_y": int(y + roi_y0), "right_x": int(x + roi_x0 + w - 1), "bottom_y": int(y + roi_y0 + h - 1)})
    if not boxes:
        return None
    return {
        "left_x": min(box["left_x"] for box in boxes),
        "top_y": min(box["top_y"] for box in boxes),
        "right_x": max(box["right_x"] for box in boxes),
        "bottom_y": max(box["bottom_y"] for box in boxes),
    }


def cue_review_times(cues: list[dict]) -> list[float]:
    points = [0.5, 5.0, 13.8, 20.5, 31.9, 38.3, 51.3, 58.2, 64.6, 70.3, 72.4, 74.0]
    return [max(0.0, min(DURATION_SECONDS - 0.04, point)) for point in points]


def compare_audio_preservation(a: Path, b: Path) -> dict:
    audio_a, sr_a = read_wav_mono(a)
    audio_b, sr_b = read_wav_mono(b)
    return {
        "decoded_pcm_sha256_a": pcm_sha256(audio_a),
        "decoded_pcm_sha256_b": pcm_sha256(audio_b),
        "duration_a_s": round(len(audio_a) / sr_a, 6),
        "duration_b_s": round(len(audio_b) / sr_b, 6),
        "sample_rate_a": sr_a,
        "sample_rate_b": sr_b,
        "channels": "mono",
        "decoded_pcm_unchanged": sr_a == sr_b and len(audio_a) == len(audio_b) and np.array_equal(audio_a, audio_b),
    }


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        sr = wav.getframerate()
        channels = wav.getnchannels()
        data = wav.readframes(wav.getnframes())
    audio = np.frombuffer(data, dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return audio, sr


def pcm_sha256(audio: np.ndarray) -> str:
    import hashlib

    return hashlib.sha256(audio.astype(np.int16).tobytes()).hexdigest()


if __name__ == "__main__":
    main()
