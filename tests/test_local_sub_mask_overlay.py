import cv2
import numpy as np
import pytest
from pathlib import Path
from PIL import ImageFont

from app.services.clean_subtitle_render import (
    detect_source_subtitle_bbox,
    local_sub_overlay_layout_for_interval,
    build_local_sub_mask_overlay_filter,
    build_source_replacement_filter,
    write_clean_subtitles_ass,
    _font_with_size,
)
from app.services.simple_workflow import _validate_subtitle_render


def _create_synthetic_frame(
    width: int,
    height: int,
    rects: list[tuple[int, int, int, int]] | None = None,
) -> np.ndarray:
    """Create a synthetic video frame with bright text-like rectangles simulating Chinese subtitles."""
    # Dark/natural background
    frame = np.full((height, width, 3), 40, dtype=np.uint8)
    if rects:
        for x, y, w, h in rects:
            # Draw bright white text block with high contrast edge
            cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 255, 255), -1)
            # Add thin black border to simulate subtitle stroke
            cv2.rectangle(frame, (x - 1, y - 1), (x + w + 1, y + h + 1), (10, 10, 10), 1)
    return frame


def _get_test_font(size: int = 32) -> ImageFont.FreeTypeFont:
    for font_name in ["arial.ttf", "segoeui.ttf", "tahoma.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(font_name, size)
        except Exception:
            continue
    return ImageFont.load_default()


# NOTE: Synthetic frame tests in this file serve as UNIT TESTS for layout and geometry math.
# They are NOT integration proof for end-to-end subtitle detection or workflow execution.


def test_sub_01_single_line_chinese_sub_covered_vietnamese_matched():
    """SUB-01: Single-line Chinese sub is tightly masked with 2-4px padding,
    Vietnamese sub positioned at matching center and equivalent font size."""
    font = _get_test_font(32)
    # Simulated 1280x720 video with single-line Chinese subtitle
    # Chinese: 陈长安携神兽麒麟重返家族 (approx 360px wide, 34px high at y=615)
    frame = _create_synthetic_frame(1280, 720, [(460, 615, 360, 34)])
    bbox, metrics = detect_source_subtitle_bbox(frame, output_width=1280, output_height=720)
    
    assert bbox is not None, "Subtitle bbox should be detected"
    assert metrics["status"] == "CONFIRMED"
    assert metrics["confidence"] >= 0.60
    assert 450 <= bbox["left_x"] <= 470
    assert 605 <= bbox["top_y"] <= 625
    
    interval = {
        "segment_id": "source_caption_0001",
        "start_time": 1.0,
        "end_time": 3.0,
        "source_bbox": bbox,
        "text_height": metrics.get("text_height", 34),
        "confidence": metrics["confidence"],
        "status": "CONFIRMED",
    }
    
    # 1. Concise text that fits inside original width and height remains 1 line PASS
    vi_text_single = "Trần Trường An về nhà"
    layout_single = local_sub_overlay_layout_for_interval(
        interval,
        vi_text_single,
        font,
        output_width=1280,
        output_height=720,
        cue_start=1.0,
        cue_end=3.0,
    )
    assert layout_single["status"] == "PASS"
    assert layout_single["line_count"] == 1
    assert layout_single["text_width"] <= layout_single["mask"]["width"]
    assert layout_single["text_height"] <= layout_single["mask"]["height"]
    
    # Check font size within ~10-15% of source character height
    source_h = interval["text_height"]
    assert abs(layout_single["font_size"] - source_h) / source_h <= 0.15
    # Check mask tightly covers source bbox (+2-4px padding)
    mask = layout_single["mask"]
    assert mask["padding_px"] in [2, 3, 4]
    assert mask["x"] <= bbox["left_x"]
    assert mask["right_x"] >= bbox["right_x"]
    assert mask["y"] <= bbox["top_y"]
    assert mask["bottom_y"] >= bbox["bottom_y"]
    assert mask["width"] == (bbox["right_x"] - bbox["left_x"] + 1) + 2 * mask["padding_px"]
    assert mask["height"] == (bbox["bottom_y"] - bbox["top_y"] + 1) + 2 * mask["padding_px"]
    assert mask["width"] < 1280 * 0.70
    assert layout_single["center_x"] == mask["x"] + mask["width"] // 2
    assert layout_single["anchor_y"] == mask["y"] + mask["height"] // 2

    # 2. Text longer than original 1-line source height cannot fit 2 lines without overflowing mask height -> NEEDS_REVIEW
    vi_text_longer = "Trần Trường An cùng Kỳ Lân về gia tộc"
    layout_longer = local_sub_overlay_layout_for_interval(
        interval,
        vi_text_longer,
        font,
        output_width=1280,
        output_height=720,
        cue_start=1.0,
        cue_end=3.0,
    )
    assert layout_longer["status"] == "NEEDS_REVIEW"
    assert "translation_exceeds" in layout_longer["review_reason"]
    # Mask must strictly remain source bbox + padding (never expanded)
    assert layout_longer["mask"]["width"] == (bbox["right_x"] - bbox["left_x"] + 1) + 2 * layout_longer["mask"]["padding_px"]
    assert layout_longer["mask"]["height"] == (bbox["bottom_y"] - bbox["top_y"] + 1) + 2 * layout_longer["mask"]["padding_px"]


def test_sub_02_two_line_chinese_sub_covered():
    """SUB-02: Two-line Chinese subtitle has both lines completely covered by mask."""
    font = _get_test_font(32)
    # Simulated two-line Chinese subtitle: line 1 at y=585, line 2 at y=630
    frame = _create_synthetic_frame(
        1280, 720,
        [(400, 585, 480, 32), (430, 630, 420, 32)]
    )
    bbox, metrics = detect_source_subtitle_bbox(frame, output_width=1280, output_height=720)
    
    assert bbox is not None
    assert bbox["top_y"] <= 590
    assert bbox["bottom_y"] >= 660
    
    interval = {
        "segment_id": "source_caption_0002",
        "start_time": 2.0,
        "end_time": 5.0,
        "source_bbox": bbox,
        "text_height": 32,
        "confidence": 0.95,
        "status": "CONFIRMED",
    }
    
    vi_text = "Hôm nay mở đường tu luyện\nQuyết tâm đột phá cảnh giới"
    layout = local_sub_overlay_layout_for_interval(
        interval,
        vi_text,
        font,
        output_width=1280,
        output_height=720,
    )
    
    assert layout["status"] == "PASS"
    mask = layout["mask"]
    # Covers both lines from y <= 585 to y >= 660
    assert mask["y"] <= 585
    assert mask["bottom_y"] >= 660
    assert mask["height"] >= 70


def test_sub_03_mask_active_only_during_cue():
    """SUB-03: Mask only exists between cue start and end time; no lingering mask."""
    ass_path = Path("fake_subtitles.ass")
    intervals = []
    layouts = [
        {
            "segment_id": "CUE_01",
            "start_time": 1.250,
            "end_time": 3.750,
            "mask": {"x": 420, "y": 610, "width": 440, "height": 42},
            "status": "PASS",
        }
    ]
    
    filter_str = build_local_sub_mask_overlay_filter(intervals, layouts, ass_path)
    
    # Must use drawbox with exact timing
    assert "drawbox=" in filter_str
    assert "enable='between(t\\,1.250\\,3.750)'" in filter_str
    assert "delogo=" not in filter_str
    assert "color=black@1.0" in filter_str
    assert "t=fill" in filter_str


def test_sub_04_long_vietnamese_translation_wrapping_and_review():
    """SUB-04: Long Vietnamese translation wraps cleanly into two lines,
    or triggers NEEDS_REVIEW if exceeding safe boundary without wide band blowout."""
    font = _get_test_font(30)
    bbox = {"left_x": 480, "top_y": 620, "right_x": 800, "bottom_y": 654}
    interval = {
        "source_bbox": bbox,
        "text_height": 32,
        "start_time": 0.0,
        "end_time": 3.0,
        "confidence": 0.95,
        "status": "CONFIRMED",
    }
    
    # Case A: Two-line source subtitle region cleanly contains two-line Vietnamese translation
    bbox_2line = {"left_x": 380, "top_y": 585, "right_x": 900, "bottom_y": 655}
    interval_2line = {
        "source_bbox": bbox_2line,
        "text_height": 30,
        "start_time": 0.0,
        "end_time": 3.0,
        "confidence": 0.95,
        "status": "CONFIRMED",
        "line_count": 2,
    }
    long_text = "Trần Trường An mang theo Kỳ Lân\ntrở về Trần gia sau mười năm"
    layout_wrap = local_sub_overlay_layout_for_interval(
        interval_2line,
        long_text,
        font,
        output_width=1280,
        output_height=720,
    )
    assert layout_wrap["status"] == "PASS"
    assert layout_wrap["line_count"] == 2
    assert "\n" in layout_wrap["render_text"]
    assert layout_wrap["text_width"] <= layout_wrap["mask"]["width"]
    assert layout_wrap["text_height"] <= layout_wrap["mask"]["height"]
    assert layout_wrap["mask"]["width"] <= 530
    
    # Case B: 1-line source height cannot accommodate 2 lines -> NEEDS_REVIEW without expanding mask
    layout_1line_overflow = local_sub_overlay_layout_for_interval(
        interval,
        long_text,
        font,
        output_width=1280,
        output_height=720,
    )
    assert layout_1line_overflow["status"] == "NEEDS_REVIEW"
    assert "translation_exceeds" in layout_1line_overflow["review_reason"]
    assert layout_1line_overflow["mask"]["width"] <= 335
    assert layout_1line_overflow["mask"]["height"] <= 45

    # Case C: Extremely long unbreakable string or massive text exceeding maximum safe screen width
    extreme_text = "Từ_xưa_đến_nay_trong_toàn_bộ_thiên_địa_chưa_từng_có_ai_dám_làm_ra_hành_động_ngông_cuồng_nghịch_thiên_như_thế_này_cả_khi_một_mình_khiêu_chiến_toàn_thể_chư_thần"
    layout_review = local_sub_overlay_layout_for_interval(
        interval,
        extreme_text,
        font,
        output_width=1280,
        output_height=720,
    )
    assert layout_review["status"] == "NEEDS_REVIEW"
    assert "translation_exceeds" in layout_review["review_reason"]
    # Even on overflow, mask width is strictly source bbox + padding (never expands into full screen)
    assert layout_review["mask"]["width"] <= 335


def test_sub_cue1_strict_mask_width_verification():
    """Verify Cue 1: source_bbox width is 431px.
    Mask width must be source_bbox width + 2*pad_px (max ~439px at 720p).
    Must strictly REJECT 841px expanded mask from old faulty behavior.
    """
    font = _get_test_font(34)
    # Cue 1 source bbox: left_x: 425, top_y: 625, right_x: 855, bottom_y: 665
    # width = 855 - 425 + 1 = 431 px
    bbox = {"left_x": 425, "top_y": 625, "right_x": 855, "bottom_y": 665}
    interval = {
        "segment_id": "source_caption_0001",
        "start_time": 1.000,
        "end_time": 4.500,
        "source_bbox": bbox,
        "text_height": 34,
        "confidence": 0.98,
        "status": "CONFIRMED",
    }
    
    vi_text = "Trần Trường An mang theo thần thú Kỳ Lân trở về gia tộc"
    layout = local_sub_overlay_layout_for_interval(
        interval,
        vi_text,
        font,
        output_width=1280,
        output_height=720,
        cue_start=1.000,
        cue_end=4.500,
        target_language="Vietnamese",
    )
    
    mask = layout["mask"]
    assert mask is not None
    # 1. source_bbox width is 431px
    orig_w = bbox["right_x"] - bbox["left_x"] + 1
    assert orig_w == 431
    
    # 2. Mask width must be orig_w + 2 * pad_px (pad_px is 2-4px, at 720p pad_px=3)
    pad_px = mask["padding_px"]
    assert pad_px in [2, 3, 4]
    expected_mask_w = 431 + (2 * pad_px)
    assert mask["width"] == expected_mask_w
    assert mask["width"] <= 439
    
    # 3. Explicitly reject 841px from legacy blowout
    assert mask["width"] != 841, "Mask width must not blowout to 841px based on Vietnamese translation!"
    assert mask["width"] < 450


def test_sub_05_detection_failure_fail_safe():
    """SUB-05: When no subtitle is detected, return SUBTITLE_REGION_UNCONFIRMED
    without creating a blind full-bottom black box."""
    # Blank frame (no subtitle text)
    blank_frame = np.full((720, 1280, 3), 30, dtype=np.uint8)
    bbox, metrics = detect_source_subtitle_bbox(blank_frame, output_width=1280, output_height=720)
    
    assert bbox is None
    assert metrics["status"] == "SUBTITLE_REGION_UNCONFIRMED"
    
    interval = {
        "source_bbox": None,
        "confidence": 0.0,
        "status": "SUBTITLE_REGION_UNCONFIRMED",
        "start_time": 1.0,
        "end_time": 2.0,
    }
    font = _get_test_font(30)
    layout = local_sub_overlay_layout_for_interval(
        interval,
        "Lời thoại không rõ vị trí gốc",
        font,
        output_width=1280,
        output_height=720,
    )
    
    assert layout["status"] == "SUBTITLE_REGION_UNCONFIRMED"
    assert layout["mask"] is None
    
    # Filter builder ignores unconfirmed layouts and does NOT emit a blind drawbox
    filter_str = build_local_sub_mask_overlay_filter([], [layout], Path("sub.ass"))
    assert "drawbox=" not in filter_str
    assert filter_str.startswith("subtitles='")


def test_sub_06_video_16_9_landscape_coordinates():
    """SUB-06: 16:9 landscape video subtitle coordinates properly mapped."""
    frame_16_9 = _create_synthetic_frame(1280, 720, [(440, 620, 400, 35)])
    bbox, metrics = detect_source_subtitle_bbox(frame_16_9, output_width=1280, output_height=720)
    
    assert bbox is not None
    assert metrics["status"] == "CONFIRMED"
    assert 430 <= bbox["left_x"] <= 450
    assert 610 <= bbox["top_y"] <= 630
    assert 830 <= bbox["right_x"] <= 850
    assert 645 <= bbox["bottom_y"] <= 665


def test_sub_07_video_9_16_portrait_coordinates():
    """SUB-07: 9:16 portrait video (720x1280) uses vertical ROI, not landscape ROI."""
    # In portrait 720x1280, subtitle is placed at bottom, e.g. y=1050, height=40
    frame_9_16 = _create_synthetic_frame(720, 1280, [(160, 1050, 400, 40)])
    bbox, metrics = detect_source_subtitle_bbox(frame_9_16, output_width=720, output_height=1280)
    
    assert bbox is not None, "Portrait 9:16 subtitle should be detected"
    assert metrics["status"] == "CONFIRMED"
    assert 145 <= bbox["left_x"] <= 175
    assert 1030 <= bbox["top_y"] <= 1070
    assert 545 <= bbox["right_x"] <= 575
    assert 1080 <= bbox["bottom_y"] <= 1105


def test_sub_08_logo_or_corner_text_not_masked():
    """SUB-08: Logo or watermark at top/corner of frame is not mistakenly detected as subtitle."""
    # Frame has logo at top-left (x=40, y=40, w=150, h=40) and NO subtitle at bottom
    frame_with_logo_only = _create_synthetic_frame(1280, 720, [(40, 40, 150, 40)])
    bbox, metrics = detect_source_subtitle_bbox(frame_with_logo_only, output_width=1280, output_height=720)
    
    # Subtitle detector should NOT detect the logo
    assert bbox is None or metrics["status"] == "SUBTITLE_REGION_UNCONFIRMED"
    
    # Frame with both logo at top and subtitle at bottom: only subtitle bbox is detected
    frame_with_both = _create_synthetic_frame(
        1280, 720,
        [(40, 40, 150, 40), (450, 620, 380, 36)]
    )
    bbox_sub, metrics_sub = detect_source_subtitle_bbox(frame_with_both, output_width=1280, output_height=720)
    assert bbox_sub is not None
    assert bbox_sub["top_y"] >= 550, "Bbox must be in subtitle ROI, not top logo"
    assert bbox_sub["left_x"] >= 350


def test_sub_09_consecutive_cues_no_black_box_gap():
    """SUB-09: Back-to-back cues transition cleanly without flickering or empty black boxes."""
    ass_path = Path("fake_cues.ass")
    layouts = [
        {
            "segment_id": "CUE_01",
            "start_time": 1.000,
            "end_time": 2.000,
            "mask": {"x": 400, "y": 615, "width": 480, "height": 40},
            "status": "PASS",
        },
        {
            "segment_id": "CUE_02",
            "start_time": 2.000,
            "end_time": 3.000,
            "mask": {"x": 420, "y": 615, "width": 440, "height": 40},
            "status": "PASS",
        },
    ]
    
    filter_str = build_local_sub_mask_overlay_filter([], layouts, ass_path)
    assert filter_str.count("drawbox=") == 2
    assert "between(t\\,1.000\\,2.000)" in filter_str
    assert "between(t\\,2.000\\,3.000)" in filter_str


def test_sub_10_regression_legacy_workflow_preserved(tmp_path: Path):
    """SUB-10: Existing workflow in legacy mode preserves delogo and lower-third plate."""
    ass_path = tmp_path / "legacy.ass"
    ass_path.write_text("dummy", encoding="utf-8")
    intervals = [{"x": 120, "y": 500, "width": 320, "height": 80, "start_time": 1.0, "end_time": 2.0}]
    layouts = [
        {
            "segment_id": "ASR_00001",
            "start_time": 1.0,
            "end_time": 2.0,
            "plate": {"x": 200, "y": 540, "width": 240, "height": 72},
        }
    ]
    
    filter_value = build_source_replacement_filter(intervals, layouts, ass_path, mode="legacy")
    assert filter_value.startswith("delogo=")
    assert filter_value.count("drawbox=") == 1
    assert filter_value.count("delogo=") == 1
    assert filter_value.rsplit(",", 1)[-1].startswith("subtitles='")
