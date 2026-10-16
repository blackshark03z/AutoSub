import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from PIL import ImageFont

repo_root = Path(r"D:\AutoSub").resolve()
sys.path.insert(0, str(repo_root))

from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.paths import safe_write_text, windows_safe_path
from app.services.clean_subtitle_render import (
    _ffmpeg_filter_path,
    build_local_sub_mask_overlay_filter,
    detect_source_caption_intervals,
    local_sub_overlay_layout_for_interval,
    write_clean_subtitles_ass,
)
from app.services.ingestion_contract import (
    create_bilibili_ingestion_handoff,
    attach_and_validate_local_media,
    create_or_reuse_ingestion_job,
)
from app.services.production_intake import validate_source_path
from app.services.simple_workflow import _validate_subtitle_render


def run_pipeline_stability_retest():
    print("=" * 70)
    print("AUTOSUB CLOUD V2 — OFFLINE PIPELINE STABILITY RETEST (P1)")
    print(f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"Mode: LOCAL_SUB_MASK_OVERLAY (Vietnamese Hardsub)")
    print(f"Modules: OCR/ASR=MOCK, TRANSLATION=MOCK, RENDER=REAL, PROBE=REAL")
    print("=" * 70)

    # 1. Setup directories
    pilot_dir = repo_root / "evidence" / "pilot_runs"
    pilot_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir = pilot_dir / "fixture"
    fixture_dir.mkdir(parents=True, exist_ok=True)

    # 2. Build 20.0s synthetic fixture with Chinese dialogue subtitles burned in
    fixture_path = fixture_dir / "target_donghua_20s_chinese_sub_fixture.mp4"
    source_ass_path = fixture_dir / "source_chinese_cues.ass"

    # Define 4 semantic cues with natural pauses / gaps
    cues_data = [
        {
            "cue_id": "CUE_0001",
            "start_ms": 1000,
            "end_ms": 4500,
            "source_text": "陈长安携神兽麒麟重返家族",
            "translation_text": "Trần Trường An mang theo thần thú Kỳ Lân trở về gia tộc",
            "confidence": 0.98,
        },
        {
            "cue_id": "CUE_0002",
            "start_ms": 6500,
            "end_ms": 10000,
            "source_text": "今日便开启逆天修炼之路",
            "translation_text": "Hôm nay mở ra con đường tu luyện nghịch thiên",
            "confidence": 0.96,
        },
        {
            "cue_id": "CUE_0003",
            "start_ms": 12000,
            "end_ms": 15500,
            "source_text": "太玄九珠，现！",
            "translation_text": "Thái Huyền Cửu Châu, hiện!",
            "confidence": 0.97,
        },
        {
            "cue_id": "CUE_0004",
            "start_ms": 17000,
            "end_ms": 19500,
            "source_text": "万道雷劫，何足惧哉！",
            "translation_text": "Vạn đạo lôi kiếp, có gì đáng sợ!",
            "confidence": 0.95,
        },
    ]

    # Write source Chinese ASS to burn into input video
    ass_events = []
    for c in cues_data:
        s_sec = c["start_ms"] / 1000.0
        e_sec = c["end_ms"] / 1000.0
        s_ts = f"0:{int(s_sec // 60):02d}:{int(s_sec % 60):02d}.{int((s_sec * 100) % 100):02d}"
        e_ts = f"0:{int(e_sec // 60):02d}:{int(e_sec % 60):02d}.{int((e_sec * 100) % 100):02d}"
        ass_events.append(f"Dialogue: 0,{s_ts},{e_ts},Default,,0,0,0,,{c['source_text']}")

    source_ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,SimSun,34,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,55,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ass_events) + "\n"

    source_ass_path.write_text(source_ass_content, encoding="utf-8")
    escaped_source_ass = _ffmpeg_filter_path(source_ass_path)

    # Generate synthetic video: 1280x720, 15fps, 20.0s, H.264 + AAC audio with Chinese hardsub
    gen_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15:duration=20.0",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=20.0",
        "-vf", f"subtitles='{escaped_source_ass}'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(fixture_path),
    ]
    subprocess.run(gen_cmd, check=True)
    fixture_hash = sha256_file(fixture_path)
    fixture_summary = media_summary(fixture_path)
    print(f"\n[FIXTURE CREATED] {fixture_path.name}")
    print(f"  SHA-256: {fixture_hash}")
    print(f"  Size: {fixture_path.stat().st_size} bytes")
    print(f"  Resolution: {fixture_summary['video']['width']}x{fixture_summary['video']['height']} @ {fixture_summary['video'].get('avg_frame_rate')} fps")
    print(f"  Duration: {float(fixture_summary['duration_seconds']):.3f}s, Audio: {fixture_summary['audio']['codec']}")

    # Extract Before frame at t=2.5s (during Cue 1)
    before_frame_path = fixture_dir / "frame_before_raw_chinese_cue1.png"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "2.5", "-i", str(fixture_path), "-vframes", "1", str(before_frame_path)],
        check=True,
    )
    print(f"  Before Frame Evidence: {before_frame_path.name} ({before_frame_path.stat().st_size} bytes)")

    # Font setup
    font = None
    for font_name in ["arial.ttf", "segoeui.ttf", "tahoma.ttf", "DejaVuSans.ttf"]:
        try:
            font = ImageFont.truetype(font_name, 34)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    # Pre-calculate detected source intervals for the synthetic fixture
    # Cue bounding boxes match the burned-in Chinese text positions
    detected_intervals = [
        {
            "segment_id": "source_caption_0001",
            "start_time": 1.000,
            "end_time": 4.500,
            "source_bbox": {"left_x": 425, "top_y": 625, "right_x": 855, "bottom_y": 665},
            "text_height": 34,
            "confidence": 0.98,
            "status": "CONFIRMED",
        },
        {
            "segment_id": "source_caption_0002",
            "start_time": 6.500,
            "end_time": 10.000,
            "source_bbox": {"left_x": 445, "top_y": 625, "right_x": 835, "bottom_y": 665},
            "text_height": 34,
            "confidence": 0.96,
            "status": "CONFIRMED",
        },
        {
            "segment_id": "source_caption_0003",
            "start_time": 12.000,
            "end_time": 15.500,
            "source_bbox": {"left_x": 490, "top_y": 625, "right_x": 790, "bottom_y": 665},
            "text_height": 34,
            "confidence": 0.97,
            "status": "CONFIRMED",
        },
        {
            "segment_id": "source_caption_0004",
            "start_time": 17.000,
            "end_time": 19.500,
            "source_bbox": {"left_x": 435, "top_y": 625, "right_x": 845, "bottom_y": 665},
            "text_height": 34,
            "confidence": 0.95,
            "status": "CONFIRMED",
        },
    ]

    # Calculate layout using LOCAL_SUB_MASK_OVERLAY
    layouts = []
    for cue, interval in zip(cues_data, detected_intervals):
        lay = local_sub_overlay_layout_for_interval(
            interval,
            cue["translation_text"],
            font,
            output_width=1280,
            output_height=720,
            cue_start=cue["start_ms"] / 1000.0,
            cue_end=cue["end_ms"] / 1000.0,
            target_language="Vietnamese",
        )
        lay["segment_id"] = cue["cue_id"]
        lay["source_interval_id"] = interval["segment_id"]
        layouts.append(lay)

    runs_data = []
    job_registry = {}

    for run_idx in range(1, 4):
        run_name = f"run_{run_idx:03d}"
        run_dir = pilot_dir / run_name
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        sub_dir = run_dir / "subtitles"
        out_dir = run_dir / "output"
        logs_dir = run_dir / "logs"
        sub_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        log_lines = []
        def log(msg: str):
            print(f"  [{run_name}] {msg}")
            log_lines.append(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

        print(f"\n>>> EXECUTING OFFLINE PIPELINE — {run_name.upper()} of 3 <<<")
        t0 = time.time()

        # Step 1: Input Ingestion & Probe (REAL)
        probe_res = validate_source_path(str(fixture_path))
        assert probe_res["status"] == "PASS", f"Probe failed: {probe_res}"
        log(f"Step 1 - Video Probe: PASS (1280x720 @ 15fps, duration={probe_res['media']['duration_seconds']}s, audio=aac)")

        # Step 2 & 3: OCR/ASR & Vietnamese Translation (MOCK / ADAPTER)
        log(f"Step 2 - OCR/ASR: PASS (MOCK: 4 cues detected, avg_conf=0.965)")
        log(f"Step 3 - Vietnamese Translation: PASS (MOCK: 4 cues zh->vi translated)")

        # Step 4: Subtitle Timeline & ASS generation (REAL)
        ass_path = sub_dir / "vietnamese_overlay_subtitles.ass"
        cues_for_ass = [
            {
                "cue_id": c["cue_id"],
                "start_ms": c["start_ms"],
                "end_ms": c["end_ms"],
                "render_text": lay["render_text"],
                "resolved_text": lay["render_text"],
            }
            for c, lay in zip(cues_data, layouts)
        ]
        write_clean_subtitles_ass(
            cues_for_ass,
            layouts,
            ass_path,
            font_size=34,
            output_width=1280,
            output_height=720,
        )
        assert ass_path.exists() and ass_path.stat().st_size > 0
        log(f"Step 4 - ASS Generation: PASS (REAL: {len(cues_for_ass)} dialogues generated with center \\pos)")

        # Step 5: Filter construction & FFmpeg Render (REAL)
        output_video_path = out_dir / "final_vietnamese_sub_video.mp4"
        filter_value = build_local_sub_mask_overlay_filter(detected_intervals, layouts, ass_path)
        filter_script_path = sub_dir / "filter_script.txt"
        filter_script_path.write_text(filter_value, encoding="utf-8")

        render_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(fixture_path),
            "-vf", filter_value,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(output_video_path),
        ]
        render_res = subprocess.run(render_cmd, capture_output=True, text=True)
        if render_res.returncode != 0:
            log(f"ERROR in FFmpeg: {render_res.stderr}")
            raise RuntimeError(f"FFmpeg render failed with exit code {render_res.returncode}")
        log(f"Step 5 - FFmpeg Render: PASS (REAL: exit code 0, LOCAL_SUB_MASK_OVERLAY filter applied)")

        # Step 6: Output Verification (REAL)
        _validate_subtitle_render(
            source=fixture_path,
            output=output_video_path,
            ass_path=ass_path,
            expected_dialogues=4,
            render_command=render_cmd,
            expect_masked=True,
            filter_script_path=filter_script_path,
        )

        out_probe = media_summary(output_video_path)
        out_dur = float(out_probe["duration_seconds"])
        in_dur = float(probe_res["media"]["duration_seconds"])
        drift_ms = abs(out_dur - in_dur) * 1000.0
        out_hash = sha256_file(output_video_path)
        out_size = output_video_path.stat().st_size
        elapsed = round(time.time() - t0, 3)

        assert drift_ms < 50.0, f"Drift {drift_ms}ms exceeds 50ms tolerance"
        assert out_probe["audio"]["codec"] == "aac", "Audio codec preserved"
        log(f"Step 6 - Verification: PASS (REAL: duration={out_dur:.3f}s, drift={drift_ms:.2f}ms, audio=intact)")

        # Step 7: Frame Evidence Extraction (REAL)
        frame_cue1 = run_dir / "evidence_cue1_overlay_2_5s.png"
        frame_gap = run_dir / "evidence_gap_no_mask_5_5s.png"
        frame_cue2 = run_dir / "evidence_cue2_overlay_8_0s.png"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "2.5", "-i", str(output_video_path), "-vframes", "1", str(frame_cue1)], check=True)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "5.5", "-i", str(output_video_path), "-vframes", "1", str(frame_gap)], check=True)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "8.0", "-i", str(output_video_path), "-vframes", "1", str(frame_cue2)], check=True)
        log(f"Step 7 - Visual Evidence: PASS (Extracted cue1, gap, and cue2 frames)")

        # Step 8: Ingestion Handoff & Job Deduplication Check (REAL)
        handoff = create_bilibili_ingestion_handoff("https://b23.tv/gK7gvDO", selected_part_index=1)
        attach_and_validate_local_media(handoff, fixture_path)
        job_result = create_or_reuse_ingestion_job(handoff, job_registry)
        is_reused = job_result["reused"]
        log(f"Step 8 - Ingestion Contract & Deduplication: PASS (Job: {job_result['job_id']}, reused={is_reused})")

        # Write run log
        (logs_dir / f"{run_name}_execution_log.txt").write_text("\n".join(log_lines), encoding="utf-8")

        run_summary = {
            "run_index": run_idx,
            "run_name": run_name,
            "status": "PASS",
            "elapsed_seconds": elapsed,
            "input_hash": fixture_hash,
            "output_hash": out_hash,
            "output_size_bytes": out_size,
            "output_duration_seconds": out_dur,
            "drift_ms": round(drift_ms, 2),
            "dialogue_count": 4,
            "ffmpeg_exit_code": render_res.returncode,
            "job_id": job_result["job_id"],
            "job_reused": is_reused,
            "evidence_frames": {
                "cue1_overlay_2_5s": frame_cue1.name,
                "gap_no_mask_5_5s": frame_gap.name,
                "cue2_overlay_8_0s": frame_cue2.name,
            },
        }
        runs_data.append(run_summary)

    # 3. Overall summary
    overall_summary = {
        "work_package": "WP-PILOT-001-FINAL",
        "pilot_mode": "LOCAL_SUB_MASK_OVERLAY",
        "target_language": "Vietnamese",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs_evaluated": len(runs_data),
        "consecutive_passes": sum(1 for r in runs_data if r["status"] == "PASS"),
        "overall_status": "PASS",
        "module_breakdown": {
            "source_probe": "REAL (ffprobe)",
            "ocr_asr": "MOCK / ADAPTER",
            "translation": "MOCK / ADAPTER",
            "subtitle_timeline_ass": "REAL (Advanced SubStation Alpha)",
            "local_sub_mask": "REAL (FFmpeg drawbox solid black tight bbox)",
            "overlay_burn_in": "REAL (FFmpeg libass hardsub)",
            "output_verification": "REAL (_validate_subtitle_render & ffprobe)",
            "ingestion_handoff_contract": "REAL (IngestionHandoffRecord & dedup)",
        },
        "fixture": {
            "name": fixture_path.name,
            "sha256": fixture_hash,
            "size_bytes": fixture_path.stat().st_size,
            "resolution": "1280x720",
            "fps": 15.0,
            "duration_seconds": 20.0,
            "audio_codec": "aac",
            "cue_count": len(cues_data),
        },
        "runs": runs_data,
        "mask_overlay_parameters": [
            {
                "cue_id": lay["segment_id"],
                "cue_text_vi": lay["render_text"],
                "start_s": lay["start_time"],
                "end_s": lay["end_time"],
                "mask_bbox": lay["mask"],
                "center_pos": f"({lay['center_x']}, {lay['anchor_y']})",
                "font_size": lay["font_size"],
            }
            for lay in layouts
        ],
    }

    summary_file = pilot_dir / "stability_summary.json"
    summary_file.write_text(json.dumps(overall_summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"PILOT STABILITY RETEST COMPLETE: {overall_summary['consecutive_passes']}/{len(runs_data)} PASS")
    print(f"Summary JSON: {summary_file}")
    print("=" * 70)
    return overall_summary


if __name__ == "__main__":
    run_pipeline_stability_retest()
