import json
import subprocess
from pathlib import Path
import cv2
import numpy as np
import pytest
from PIL import ImageFont

from app.core.media import media_summary
from app.services.clean_subtitle_render import (
    detect_source_caption_intervals,
    build_source_replacement_plan,
    build_source_replacement_filter,
    render_source_replacement_video,
    write_clean_subtitles_ass,
    _ffmpeg_filter_path,
)


@pytest.fixture
def multi_cue_chinese_sub_video(tmp_path: Path) -> Path:
    """Fixture video:
    - Cue 1 (1-line): 1.000s - 3.500s ('陈长安携神兽麒麟重返家族')
    - Silence gap (no text): 3.500s - 5.500s
    - Cue 2 (2-line): 5.500s - 8.500s ('太玄九珠逆天现\\N万道雷劫何足惧')
    - Silence gap: 8.500s - 10.000s
    Total: 10.0s, 1280x720 @ 15fps with AAC audio.
    """
    ass_content = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,SimSun,34,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,55,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,陈长安携神兽麒麟重返家族
Dialogue: 0,0:00:05.50,0:00:08.50,Default,,0,0,0,,太玄九珠逆天现\\N万道雷劫何足惧
"""
    ass_path = tmp_path / "chinese_multi_cue.ass"
    ass_path.write_text(ass_content, encoding="utf-8")
    escaped_ass = _ffmpeg_filter_path(ass_path)

    vid_path = tmp_path / "fixture_chinese_multicue.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x282828:s=1280x720:d=10.0:r=15",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10.0",
        "-vf", f"subtitles='{escaped_ass}'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(vid_path),
    ]
    subprocess.run(cmd, check=True)
    return vid_path


def test_automatic_detector_e2e_integration(multi_cue_chinese_sub_video: Path, tmp_path: Path):
    """P0 Integration Proof:
    Real automatic detector runs on video fixture (NO hardcoded bboxes),
    determines 1-line and 2-line coordinates and silence gaps, builds
    LOCAL_SUB_MASK_OVERLAY plan with tight mask, generates Vietnamese ASS,
    renders with FFmpeg, and verifies output QA.
    """
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # 1. Step 1: Run automatic detector directly
    cap = cv2.VideoCapture(str(multi_cue_chinese_sub_video))
    intervals = detect_source_caption_intervals(
        cap,
        output_width=1280,
        output_height=720,
        fps=15.0,
        duration_seconds=10.0,
    )
    cap.release()

    # Verify detector found exactly 2 intervals (Cue 1 and Cue 2)
    assert len(intervals) == 2, f"Expected 2 detected intervals, found {len(intervals)}"
    
    # Cue 1: 1-line subtitle
    inv1 = intervals[0]
    assert inv1["status"] == "CONFIRMED"
    assert inv1["confidence"] >= 0.60
    assert 0.5 <= inv1["start_time"] <= 1.2
    assert 3.3 <= inv1["end_time"] <= 4.0
    bbox1 = inv1["source_bbox"]
    assert bbox1 is not None
    assert 380 <= (bbox1["right_x"] - bbox1["left_x"] + 1) <= 460
    assert 24 <= (bbox1["bottom_y"] - bbox1["top_y"] + 1) <= 45

    # Silence gap check: no intervals between 4.0s and 5.2s
    assert inv1["end_time"] < 4.2
    assert intervals[1]["start_time"] > 5.0

    # Cue 2: 2-line subtitle
    inv2 = intervals[1]
    assert inv2["status"] == "CONFIRMED"
    bbox2 = inv2["source_bbox"]
    assert bbox2 is not None
    # Height of 2 lines is greater than 1 line
    assert (bbox2["bottom_y"] - bbox2["top_y"] + 1) >= 48

    # 2. Step 2: Build plan through build_source_replacement_plan (auto-detection mode)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    if not font_path.exists():
        font_path = Path("C:/Windows/Fonts/segoeui.ttf")

    cues = [
        {
            "cue_id": "CUE_0001",
            "start_ms": 1000,
            "end_ms": 3500,
            "text": "陈长安携神兽麒麟重返家族",
            "resolved_text": "Trần Trường An mang theo thần thú Kỳ Lân trở về gia tộc",
        },
        {
            "cue_id": "CUE_0002",
            "start_ms": 5500,
            "end_ms": 8500,
            "text": "太玄九珠逆天现\n万道雷劫何足惧",
            "resolved_text": "Thái Huyền Cửu Châu nghịch thiên hiện\nVạn đạo lôi kiếp có gì sợ",
        },
    ]

    plan = build_source_replacement_plan(
        multi_cue_chinese_sub_video,
        cues,
        font_path=font_path,
        mode="LOCAL_SUB_MASK_OVERLAY",
        target_language="Vietnamese",
    )

    layouts = plan["layouts"]
    assert len(layouts) == 2

    # Check Cue 1 layout
    lay1 = layouts[0]
    assert lay1["status"] == "PASS"
    mask1 = lay1["mask"]
    assert mask1 is not None
    orig_w1 = bbox1["right_x"] - bbox1["left_x"] + 1
    assert mask1["width"] == orig_w1 + 2 * mask1["padding_px"]
    assert mask1["width"] < 450, "Cue 1 mask must not blowout to 841px"
    # Font size within ~15% of detected text height
    assert abs(lay1["font_size"] - inv1["text_height"]) <= 6

    # 3. Step 3: Write ASS and render with FFmpeg
    ass_path = tmp_path / "dialogue_subtitles_vi.ass"
    write_clean_subtitles_ass(
        plan["render_cues"],
        layouts,
        ass_path,
        font_size=plan["font_size"],
        output_width=1280,
        output_height=720,
    )
    assert ass_path.exists()

    output_video = tmp_path / "final_rendered_video.mp4"
    render_source_replacement_video(
        multi_cue_chinese_sub_video,
        ass_path,
        output_video,
        plan["intervals"],
        layouts,
        mode="LOCAL_SUB_MASK_OVERLAY",
    )
    assert output_video.exists()
    assert output_video.stat().st_size > 0

    # 4. Step 4: Extract Before and After Evidence Frames
    before_frame_cue1 = evidence_dir / "evidence_before_cue1_detected.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "2.0", "-i", str(multi_cue_chinese_sub_video), "-vframes", "1", str(before_frame_cue1)],
        check=True,
    )
    after_frame_cue1 = evidence_dir / "evidence_after_cue1_overlay.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "2.0", "-i", str(output_video), "-vframes", "1", str(after_frame_cue1)],
        check=True,
    )
    gap_frame = evidence_dir / "evidence_gap_no_mask.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "4.5", "-i", str(output_video), "-vframes", "1", str(gap_frame)],
        check=True,
    )

    assert before_frame_cue1.exists() and before_frame_cue1.stat().st_size > 0
    assert after_frame_cue1.exists() and after_frame_cue1.stat().st_size > 0
    assert gap_frame.exists() and gap_frame.stat().st_size > 0

    # Gap frame check: ensure silence gap has no black box mask
    # Background is 0x282828 (approx 40,40,40 in BGR)
    gap_img = cv2.imread(str(gap_frame))
    roi_bottom = gap_img[600:680, 400:880]
    # Check that it is NOT a solid black box (black box color=0, background=40)
    assert np.mean(roi_bottom) > 20.0, "Gap frame should not have a solid black box at silence"


def test_automatic_detector_failure_case_blocks_render(tmp_path: Path):
    """P0 Failure Case: When video has no detectable subtitle or confidence is poor,
    the pipeline reports SUBTITLE_REGION_UNCONFIRMED and blocks render execution
    rather than silently rendering text over unmasked Chinese.
    """
    blank_video = tmp_path / "blank_video.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x282828:s=1280x720:d=2.0:r=15",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(blank_video),
    ]
    subprocess.run(cmd, check=True)

    font_path = Path("C:/Windows/Fonts/arial.ttf")
    cues = [
        {"cue_id": "CUE_0001", "start_ms": 500, "end_ms": 1500, "text": "无字画面", "resolved_text": "Khung hình không có chữ"}
    ]

    plan = build_source_replacement_plan(
        blank_video,
        cues,
        font_path=font_path,
        mode="LOCAL_SUB_MASK_OVERLAY",
        target_language="Vietnamese",
    )

    assert len(plan["layouts"]) == 1
    layout = plan["layouts"][0]
    assert layout["status"] == "SUBTITLE_REGION_UNCONFIRMED"
    assert layout["mask"] is None

    # Render attempt MUST be blocked with ValueError
    output_video = tmp_path / "should_not_exist.mp4"
    ass_path = tmp_path / "temp.ass"
    write_clean_subtitles_ass(plan["render_cues"], plan["layouts"], ass_path, font_size=32, output_width=1280, output_height=720)

    with pytest.raises(ValueError, match="SUBTITLE_REGION_UNCONFIRMED"):
        render_source_replacement_video(
            blank_video,
            ass_path,
            output_video,
            plan["intervals"],
            plan["layouts"],
            mode="LOCAL_SUB_MASK_OVERLAY",
        )
