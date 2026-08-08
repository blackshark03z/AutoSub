from pathlib import Path

from PIL import ImageFont

from app.services.clean_subtitle_render import (
    build_source_replacement_filter,
    english_layout_for_interval,
    is_meaningful_subtitle_text,
    normalize_render_cues,
    wrap_subtitle_text,
    write_clean_subtitles_ass,
)


FONT = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 40)
INTERVAL = {
    "segment_id": "source_1",
    "start_time": 1.0,
    "end_time": 3.0,
    "x": 600,
    "y": 850,
    "width": 400,
    "height": 60,
    "source_bbox": {"left_x": 600, "top_y": 850, "right_x": 999, "bottom_y": 909},
}


def _layout(cue_id: str, text: str, start_ms: int, end_ms: int, *, width: int = 1920, height: int = 1080):
    layout = english_layout_for_interval(INTERVAL, text, FONT, output_width=width, output_height=height, cue_start=start_ms / 1000, cue_end=end_ms / 1000)
    layout.update({"segment_id": cue_id, "source_interval_id": "source_1"})
    return layout


def test_empty_and_punctuation_only_cues_create_no_ass_plate(tmp_path):
    cues = [
        {"cue_id": "blank", "start_ms": 0, "end_ms": 1000, "resolved_text": "   "},
        {"cue_id": "punct", "start_ms": 1000, "end_ms": 2000, "resolved_text": "...!?"},
        {"cue_id": "valid", "start_ms": 2000, "end_ms": 3000, "resolved_text": "A real subtitle"},
    ]
    valid = normalize_render_cues(cues, duration_seconds=5)
    ass = write_clean_subtitles_ass(valid, [_layout("valid", "A real subtitle", 2000, 3000)], tmp_path / "out.ass", font_size=40, output_width=1920, output_height=1080)
    dialogues = [line for line in ass.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")]
    assert len(dialogues) == 1
    assert is_meaningful_subtitle_text("A real subtitle")
    assert not is_meaningful_subtitle_text(" \t...! ")


def test_valid_timing_is_preserved_and_overlap_is_rejected():
    cues = [
        {"cue_id": "one", "start_ms": 100, "end_ms": 1100, "resolved_text": "One"},
        {"cue_id": "two", "start_ms": 1100, "end_ms": 2100, "resolved_text": "Two"},
        {"cue_id": "overlap", "start_ms": 2000, "end_ms": 2500, "resolved_text": "No plate"},
    ]
    result = normalize_render_cues(cues, duration_seconds=3)
    assert [(cue["start_ms"], cue["end_ms"]) for cue in result] == [(100, 1100), (1100, 2100)]


def test_layout_is_tight_centered_and_within_1920_safe_bounds():
    short = _layout("short", "Short sentence", 0, 1000)
    long = _layout("long", "This is a deliberately longer subtitle sentence that should be balanced into no more than two readable lines.", 1000, 3000)
    for layout in (short, long):
        plate = layout["plate"]
        assert layout["center_x"] == 960
        assert layout["line_count"] <= 2
        assert plate["x"] >= round(1920 * 0.08)
        assert plate["right_x"] <= 1920 - round(1920 * 0.08)
        assert plate["y"] > 800
        assert plate["bottom_y"] < 1080
    assert short["plate"]["width"] < 700


def test_alternate_resolution_uses_relative_safe_anchor():
    layout = _layout("alt", "Alternate resolution", 0, 1000, width=1280, height=720)
    assert layout["center_x"] == 640
    assert 0.84 < layout["anchor_y"] / 720 < 0.90
    assert layout["plate"]["right_x"] <= 1280 - round(1280 * 0.08)


def test_wrap_is_deterministic_and_never_creates_a_third_line():
    text = "A deterministic long subtitle should retain words while choosing a balanced readable line break for presentation"
    first = wrap_subtitle_text(text, FONT, 900)
    assert first == wrap_subtitle_text(text, FONT, 900)
    assert first.count("\n") <= 1


def test_filter_binds_masks_and_plates_to_the_same_cue_interval(tmp_path):
    ass = tmp_path / "out.ass"
    value = build_source_replacement_filter([INTERVAL], [_layout("cue", "Visible cue", 1200, 2400)], ass)
    assert value.count("drawbox=") == 1
    assert value.count("delogo=") == 1
    assert value.count("between(t\\,1.200\\,2.400)") == 2
    assert "black@0.760" in value
