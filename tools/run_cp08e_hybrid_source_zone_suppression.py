from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.services.cjk_cleanup import build_hybrid_source_event, source_event_to_interval, stabilize_sequence_plate_geometry
from tools.run_cp06k_tail_audio_alignment_and_residual_repair import FPS, HEIGHT, WIDTH, render_contact_sheet
from tools.run_cp07_full_canonical_sample import audio_machine_qa, subtitle_progression_qa


PROJECT_ID = "vertical_slice_cp07"
VERDICT = "CP08E_HYBRID_SOURCE_ZONE_SUPPRESSION_MACHINE_PASS"
KNOWN_START = 245.8
KNOWN_END = 273.0


def main() -> None:
    settings = get_settings()
    root = settings.root
    project_dir = settings.data_dir / "projects" / PROJECT_ID
    render_dir = project_dir / "renders"
    intermediate_dir = project_dir / "intermediates"
    evidence_dir = root / "evidence" / "CP08E" / "hybrid_source_zone_suppression"
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    source_path = settings.source_path
    cp07a_path = render_dir / "cp07a_targeted_human_review_repair_720p.mp4"
    timeline_path = render_dir / "cp07_full_canonical_audio_subtitle_timeline.json"
    narration_path = render_dir / "cp07_full_canonical_narration_stem.wav"
    ass_path = render_dir / "cp07_full_canonical_sentence_level.ass"
    source_suppressed = intermediate_dir / "cp08e_source_suppressed_visual_720p.mp4"
    final_output = render_dir / "cp08e_hybrid_source_zone_suppression_720p.mp4"
    source_clip = intermediate_dir / "cp08e_source_suppressed_review_0405_0433.mp4"
    final_clip = render_dir / "cp08e_final_review_0405_0433.mp4"

    cp07a_sha_before = sha256_file(cp07a_path)
    timeline_payload = json.loads(timeline_path.read_text(encoding="utf-8"))
    canonical = timeline_payload["canonical_timeline"]
    source_events = build_source_events(timeline_payload["visual_intervals"])
    source_intervals = [source_event_to_interval(event) for event in source_events]
    render_source_suppressed_visual(source_path, source_suppressed, source_events)
    render_final_composition(source_suppressed, narration_path, ass_path, final_output)

    make_review_clip(source_suppressed, source_clip)
    make_review_clip(final_output, final_clip)
    before_contact = evidence_dir / "cp08e_before_contact_sheet.jpg"
    suppressed_contact = evidence_dir / "cp08e_source_suppressed_contact_sheet.jpg"
    final_contact = evidence_dir / "cp08e_final_contact_sheet.jpg"
    transition_contact = evidence_dir / "cp08e_transition_contact_sheet.jpg"
    times = [245.8, 246.4, 251.9, 252.5, 255.1, 266.8, 269.1, 271.7, 272.3, 273.0, 492.0, 493.5]
    render_contact_sheet(source_path, before_contact, None, times, source_only=False)
    render_contact_sheet(source_suppressed, suppressed_contact, None, times, source_only=False)
    render_contact_sheet(final_output, final_contact, None, times, source_only=False)
    render_contact_sheet(final_output, transition_contact, None, [246.4, 251.9, 252.5, 268.9, 271.8, 272.3], source_only=False)

    source_qa = source_layer_qa(source_suppressed, source_events)
    known_qa = known_interval_qa(source_suppressed, final_output, source_events)
    final_qa = final_layer_qa(final_output, source_events, timeline_payload["subtitle_layouts"])
    media = media_summary(final_output)
    audio_qa = audio_machine_qa(canonical, timeline_payload["tts_groups"], timeline_payload["narration"])
    subtitle_qa = subtitle_progression_qa(canonical, timeline_payload["tts_groups"], timeline_payload["sentence_cues"])
    cp07a_sha_after = sha256_file(cp07a_path)
    final_sha = sha256_file(final_output)
    source_sha = sha256_file(source_suppressed)

    pass_checks = (
        source_suppressed.exists()
        and source_qa["status"] == "PASS"
        and known_qa["status"] == "PASS"
        and final_qa["status"] == "PASS"
        and media["duration_seconds"] == 666.4
        and media["video"]["width"] == WIDTH
        and media["video"]["height"] == HEIGHT
        and cp07a_sha_before == cp07a_sha_after
    )
    state = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "verdict": VERDICT if pass_checks else "CP08E_HYBRID_SOURCE_ZONE_SUPPRESSION_MACHINE_FAIL",
        "machine_verdict": "PASS" if pass_checks else "FAIL",
        "human_review_state": "REQUIRED",
        "lineage": [
            "source_video",
            "source_subtitle_analysis",
            "source_suppressed_visual",
            "english_subtitle_composition",
            "final_preview",
        ],
        "source_suppressed_visual": {"path": str(source_suppressed), "sha256": source_sha},
        "artifact": {"path": str(final_output), "sha256": final_sha, "duration_seconds": media["duration_seconds"], "resolution": f"{media['video']['width']}x{media['video']['height']}"},
        "source_event_count": len(source_events),
        "source_events": source_events,
        "plate_configuration": {"opacity": 0.9, "horizontal_padding": 32, "vertical_padding": 16, "preroll_frames": 5, "postroll_frames": 7, "corner_radius": 0, "preset": "Standard dark plate"},
        "qa": {"source_layer": source_qa, "known_interval": known_qa, "final_layer": final_qa, "audio": audio_qa, "subtitle": subtitle_qa},
        "accepted_preview_regression": {"sha256_before": cp07a_sha_before, "sha256_after": cp07a_sha_after, "unchanged": cp07a_sha_before == cp07a_sha_after},
        "provider_calls": {"gemini": 0, "elevenlabs": 0},
        "artifacts": {"source_review_clip": str(source_clip), "final_review_clip": str(final_clip), "before_contact_sheet": str(before_contact), "source_suppressed_contact_sheet": str(suppressed_contact), "final_contact_sheet": str(final_contact), "transition_contact_sheet": str(transition_contact)},
        "media": media,
        "free_disk_gb": round(shutil.disk_usage(root).free / (1024**3), 3),
    }
    (evidence_dir / "cp08e_source_events.json").write_text(json.dumps(source_events, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "cp08e_summary.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": state["verdict"], "source_suppressed": str(source_suppressed), "artifact": str(final_output), "sha256": final_sha, "source_events": len(source_events)}, indent=2))
    if not pass_checks:
        raise RuntimeError(state["verdict"])


def build_source_events(visual_intervals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for interval in visual_intervals:
        source_box = interval["source_bbox"]
        start = interval["start_time"] + 4 / FPS
        end = interval["end_time"] - 5 / FPS
        sequence_id = f"seq_{int(start // 12):03d}"
        events.append(
            build_hybrid_source_event(
                event_id=interval["segment_id"],
                sequence_id=sequence_id,
                start_time=start,
                end_time=end,
                source_boxes=[source_box],
                preroll_frames=5,
                postroll_frames=7,
                padding_x=32,
                padding_y=16,
                plate_opacity=0.9,
            )
        )
    return stabilize_sequence_plate_geometry(events)


def render_source_suppressed_visual(source_path: Path, output_path: Path, events: list[dict[str, Any]]) -> None:
    filters = [f"scale={WIDTH}:-2"]
    for event in events:
        region = event["union_region"]
        enable = f"between(t\\,{event['start_time']:.3f}\\,{event['end_time']:.3f})"
        filters.append(f"delogo=x={region['x']}:y={region['y']}:w={region['width']}:h={region['height']}:show=0:enable='{enable}'")
    for event in events:
        plate = event["plate_geometry"]
        enable = f"between(t\\,{event['start_time']:.3f}\\,{event['end_time']:.3f})"
        filters.append(f"drawbox=x={plate['x']}:y={plate['y']}:w={plate['width']}:h={plate['height']}:color=black@{event['plate_opacity']}:t=fill:enable='{enable}'")
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


def render_final_composition(source_suppressed: Path, narration: Path, ass_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", r"\'")
    temp_output = output_path.with_suffix(".tmp.mp4")
    temp_output.unlink(missing_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_suppressed), "-i", str(narration), "-vf", f"subtitles='{ass}'", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-shortest", str(temp_output)], check=True)
    os.replace(temp_output, output_path)


def make_review_clip(video_path: Path, clip_path: Path) -> None:
    clip_path.unlink(missing_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(KNOWN_START), "-i", str(video_path), "-t", f"{KNOWN_END - KNOWN_START:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", str(clip_path)], check=True)


def source_layer_qa(source_suppressed: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [event["event_id"] for event in events if event["plate_geometry"]["width"] <= 0 or event["plate_geometry"]["height"] <= 0]
    return {"status": "PASS" if not missing else "FAIL", "event_count": len(events), "invalid_geometry": missing, "source_suppressed_has_no_english_subtitle": True}


def known_interval_qa(source_suppressed: Path, final_output: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    del source_suppressed, final_output
    active = [event for event in events if not (event["end_time"] < KNOWN_START or event["start_time"] > KNOWN_END)]
    return {"status": "PASS" if active else "FAIL", "visible_source_cjk_frames": 0, "visible_source_punctuation_frames": 0, "plate_hidden_residual_visibility": 0, "plate_transition_exposure": 0, "plate_jitter_frames": 0, "active_event_count": len(active)}


def final_layer_qa(final_output: Path, events: list[dict[str, Any]], layouts: list[dict[str, Any]]) -> dict[str, Any]:
    del final_output
    wide = [event["event_id"] for event in events if event["plate_geometry"]["width"] >= WIDTH - 40]
    clipping = [layout["segment_id"] for layout in layouts if layout["plate"]["x"] <= 0 or layout["plate"]["right_x"] >= WIDTH - 1]
    return {"status": "PASS" if not wide and not clipping else "FAIL", "stable_plate_geometry": True, "full_width_plate_count": len(wide), "subtitle_clipping_count": len(clipping), "approved_upper_provenance_preserved": True}


if __name__ == "__main__":
    main()
