from __future__ import annotations

from pathlib import Path
import pytest

from app.services.experimental_cue_ocr_tracking import (
    DEFAULT_DIALOGUE_Y_MIN,
    DEFAULT_DIALOGUE_Y_MAX,
    ExperimentalCueOCRTracker,
    TrackedCue,
    build_ffmpeg_drawbox_filter,
    compute_narrow_plate,
    is_dialogue_candidate,
)


def test_is_dialogue_candidate_excludes_corner_branding_and_ai_disclaimer():
    # Top-left branding
    top_left_box = [[18.0, 22.0], [237.0, 22.0], [237.0, 70.0], [18.0, 70.0]]
    assert is_dialogue_candidate(top_left_box, "外星动漫 bilibili", 0.95) is False

    # Bottom-right platform AI disclaimer
    bottom_right_box = [[961.0, 658.0], [1276.0, 658.0], [1276.0, 698.0], [961.0, 698.0]]
    assert is_dialogue_candidate(bottom_right_box, "视频内容由AI生成", 0.98) is False

    # Alternate disclaimer text
    assert is_dialogue_candidate([[950.0, 650.0], [1200.0, 650.0], [1200.0, 680.0], [950.0, 680.0]], "画面内容均为AI制作", 0.99) is False


def test_is_dialogue_candidate_excludes_upper_scene_text():
    # Book cover text in mid-upper frame (e.g., y=200..320)
    book_cover_box = [[695.0, 174.0], [761.0, 174.0], [761.0, 318.0], [695.0, 318.0]]
    assert is_dialogue_candidate(book_cover_box, "房术大全", 0.98) is False


def test_is_dialogue_candidate_accepts_valid_bottom_dialogue():
    # Center bottom dialogue
    cue_box = [[529.0, 603.0], [748.0, 603.0], [748.0, 645.0], [529.0, 645.0]]
    assert is_dialogue_candidate(cue_box, "我大限已至", 0.99) is True

    # Longer dialogue
    long_cue_box = [[375.0, 603.0], [904.0, 603.0], [904.0, 645.0], [375.0, 645.0]]
    assert is_dialogue_candidate(long_cue_box, "我走后你一定要守护好宗门", 0.99) is True


def test_narrow_plate_geometry_adds_tight_padding():
    raw_bbox = {"left_x": 529, "top_y": 603, "right_x": 748, "bottom_y": 645, "width": 220, "height": 43}
    plate = compute_narrow_plate(raw_bbox, padding_x=4, padding_y=3)
    assert plate["left_x"] == 525
    assert plate["top_y"] == 600
    assert plate["right_x"] == 752
    assert plate["bottom_y"] == 648
    assert plate["width"] == 228
    assert plate["height"] == 49


def test_aggregate_temporal_cues_distinguishes_consecutive_phrases_and_gaps():
    tracker = ExperimentalCueOCRTracker()
    frames = [
        # Gap at 0.0s - 1.0s
        {"timestamp_s": 0.0, "dialogue_items": []},
        {"timestamp_s": 0.5, "dialogue_items": []},
        # Cue 1 at 1.0s - 2.0s
        {
            "timestamp_s": 1.0,
            "dialogue_items": [{"text": "我大限已至", "confidence": 0.99, "bbox": {"left_x": 529, "top_y": 603, "right_x": 748, "bottom_y": 645}}],
        },
        {
            "timestamp_s": 1.5,
            "dialogue_items": [{"text": "我大限已至", "confidence": 0.99, "bbox": {"left_x": 529, "top_y": 603, "right_x": 748, "bottom_y": 645}}],
        },
        # Gap at 2.0s
        {"timestamp_s": 2.0, "dialogue_items": []},
        # Cue 2 at 2.5s - 3.5s
        {
            "timestamp_s": 2.5,
            "dialogue_items": [{"text": "老头你别嘎呀", "confidence": 0.98, "bbox": {"left_x": 506, "top_y": 601, "right_x": 773, "bottom_y": 648}}],
        },
        {
            "timestamp_s": 3.0,
            "dialogue_items": [{"text": "老头你别嘎呀", "confidence": 0.98, "bbox": {"left_x": 506, "top_y": 601, "right_x": 773, "bottom_y": 648}}],
        },
    ]

    cues = tracker.aggregate_temporal_cues(frames, frame_interval_s=0.5)
    assert len(cues) == 2
    assert cues[0].zh_text == "我大限已至"
    assert cues[0].start_s == 1.0
    assert cues[0].end_s == 2.0

    assert cues[1].zh_text == "老头你别嘎呀"
    assert cues[1].start_s == 2.5
    assert cues[1].end_s == 3.5


def test_cue_transition_at_18_5s_preserves_distinct_widths():
    tracker = ExperimentalCueOCRTracker()
    frames = [
        # Cue 1 (short phrase, width ~ 267px)
        {
            "timestamp_s": 17.0,
            "dialogue_items": [{"text": "老头你别嘎呀", "confidence": 0.98, "bbox": {"left_x": 506, "top_y": 601, "right_x": 773, "bottom_y": 648}}],
        },
        {
            "timestamp_s": 17.5,
            "dialogue_items": [{"text": "老头你别嘎呀", "confidence": 0.98, "bbox": {"left_x": 508, "top_y": 602, "right_x": 773, "bottom_y": 648}}],
        },
        # Cue 2 (longer phrase, width ~ 395px) starting at 18.0s
        {
            "timestamp_s": 18.0,
            "dialogue_items": [{"text": "你还没教我真本事呢", "confidence": 0.99, "bbox": {"left_x": 443, "top_y": 604, "right_x": 838, "bottom_y": 645}}],
        },
        {
            "timestamp_s": 18.5,
            "dialogue_items": [{"text": "你还没教我真本事呢", "confidence": 0.99, "bbox": {"left_x": 444, "top_y": 605, "right_x": 838, "bottom_y": 644}}],
        },
    ]

    cues = tracker.aggregate_temporal_cues(frames, frame_interval_s=0.5)
    assert len(cues) == 2
    cue1, cue2 = cues[0], cues[1]

    # Verify times do not bleed
    assert cue1.end_s == 18.0
    assert cue2.start_s == 18.0

    # Verify narrow box for cue1 is not blown out by cue2
    assert cue1.bbox["width"] <= 275
    assert cue2.bbox["width"] >= 390
    assert cue1.plate_box["width"] < cue2.plate_box["width"]


def test_build_ffmpeg_drawbox_filter():
    cue = TrackedCue(
        cue_id="cue_001",
        start_ms=1000,
        end_ms=2500,
        start_s=1.0,
        end_s=2.5,
        zh_text="测试",
        confidence=0.99,
        bbox={"left_x": 500, "top_y": 600, "right_x": 700, "bottom_y": 640, "width": 201, "height": 41},
        plate_box={"left_x": 496, "top_y": 597, "right_x": 704, "bottom_y": 643, "width": 209, "height": 47},
        frame_count=3,
    )
    filter_expr = build_ffmpeg_drawbox_filter(cue)
    assert "drawbox=x=496:y=597:w=209:h=47:color=black@1.0:t=fill" in filter_expr
    assert "enable='between(t,1.000,2.500)'" in filter_expr


def test_real_source_clip_tracking_if_available():
    pilot_clip = Path("scratch/live_pilot_run/source_part1_60s.mp4")
    if not pilot_clip.is_file():
        pilot_clip = Path("D:/AutoSub/scratch/live_pilot_run/source_part1_60s.mp4")
    tracker = ExperimentalCueOCRTracker()
    if not pilot_clip.is_file() or not tracker.is_available:
        pytest.skip("Pilot video clip or OCR runtime unavailable on this host")

    # Track a 12-second slice covering Cues 1 and 2 (8s to 20s)
    cues = tracker.track_video_cues(pilot_clip, start_s=8.0, end_s=20.0, sample_fps=2.0)
    assert len(cues) >= 3

    texts = [c.zh_text for c in cues]
    # Check that real Chinese dialogues are recovered
    assert any("我大限已至" in t for t in texts)
    assert any("我走后你一定要守护好宗门" in t for t in texts)
    assert any("老头你别嘎呀" in t or "你还没教我真本事呢" in t for t in texts)

    # Check that AI watermark is NEVER returned as a cue
    for c in cues:
        assert "AI生成" not in c.zh_text
        assert "外星动漫" not in c.zh_text
        assert c.plate_box["width"] > 0
        assert c.confidence >= 0.70


def _dlg(text: str, left: int, top: int, right: int, bottom: int, conf: float = 0.98) -> dict:
    return {"text": text, "confidence": conf,
            "bbox": {"left_x": left, "top_y": top, "right_x": right, "bottom_y": bottom}}


# Real source geometry: a single centered caption line sits at ~y602..645 (center_y ~624),
# while scene text (card labels / rank pyramids) touches or sits below it.
_CAPTION = _dlg("大帝修为卡可无副作用", 379, 604, 903, 643)
_LOWER_CARD_LABEL = _dlg("大帝境修为卡", 488, 642, 786, 677)
_OFF_CENTER_MERGED = _dlg("白色人物召唤+提升至大帝境初期", 268, 595, 822, 650)


def test_aggregate_selects_centered_caption_over_lower_card_label():
    tracker = ExperimentalCueOCRTracker()
    frames = []
    # Establish the dominant caption baseline with caption-only frames.
    for i in range(4):
        frames.append({"timestamp_s": i * 0.5, "dialogue_items": [dict(_CAPTION)]})
    # A frame where a card label sits just below the (still present) centered caption.
    frames.append({"timestamp_s": 2.0, "dialogue_items": [dict(_CAPTION), dict(_LOWER_CARD_LABEL)]})

    cues = tracker.aggregate_temporal_cues(frames, frame_interval_s=0.5, frame_width=1280, frame_height=720)
    assert len(cues) == 1
    assert cues[0].zh_text == "大帝修为卡可无副作用"
    # The lower card label (bottom_y 677) must not inflate the caption box.
    assert cues[0].bbox["bottom_y"] <= 645


def test_aggregate_bridges_unreliable_merged_frame():
    tracker = ExperimentalCueOCRTracker()
    frames = [
        {"timestamp_s": 1.0, "dialogue_items": [dict(_CAPTION)]},
        # Scene text merges into one tall, off-center box -> no reliable caption line.
        {"timestamp_s": 1.5, "dialogue_items": [dict(_OFF_CENTER_MERGED)]},
        {"timestamp_s": 2.0, "dialogue_items": [dict(_CAPTION)]},
    ]
    cues = tracker.aggregate_temporal_cues(frames, frame_interval_s=0.5, frame_width=1280, frame_height=720)
    assert len(cues) == 1
    assert cues[0].zh_text == "大帝修为卡可无副作用"
    assert cues[0].start_s == 1.0
    assert cues[0].end_s == 2.5


def test_aggregate_emits_no_cue_for_off_center_scene_text_only():
    tracker = ExperimentalCueOCRTracker()
    frames = [
        {"timestamp_s": 0.0, "dialogue_items": [dict(_OFF_CENTER_MERGED)]},
        {"timestamp_s": 0.5, "dialogue_items": [dict(_OFF_CENTER_MERGED)]},
    ]
    cues = tracker.aggregate_temporal_cues(frames, frame_interval_s=0.5, frame_width=1280, frame_height=720)
    assert cues == []
