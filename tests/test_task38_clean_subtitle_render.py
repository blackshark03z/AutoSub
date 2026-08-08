from __future__ import annotations

import json
from pathlib import Path

from app.core.config import get_settings
from app.services import asr_models
from app.services.clean_subtitle_render import (
    build_source_replacement_filter,
    interval_stats,
    nearest_source_interval,
)


def test_task38_model_selection_uses_run_config_and_resolves_local_model(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_AUTO_SUB_ROOT", str(tmp_path))
    monkeypatch.setenv("TOOL_AUTO_SUB_DATA_DIR", str(tmp_path / "data"))
    run_config_dir = tmp_path / "operator"
    run_config_dir.mkdir(parents=True, exist_ok=True)
    (run_config_dir / "run_config.json").write_text(
        json.dumps(
            {
                "source": {"path": "input/source.mp4"},
                "subtitle": {"font_path": "C:/Windows/Fonts/arial.ttf"},
                "hardware": {
                    "whisper_model": "base",
                }
            }
        ),
        encoding="utf-8",
    )
    model_dir = tmp_path / "models" / "faster-whisper-base"
    model_dir.mkdir(parents=True)
    for filename in asr_models.ASR_MODEL_REQUIRED_FILES:
        (model_dir / filename).write_text(filename, encoding="utf-8")
    get_settings.cache_clear()

    assert asr_models.selected_model_name(default="tiny") == "base"
    assert asr_models.model_directory_name("base") == "faster-whisper-base"
    assert asr_models.resolve_local_model_path("base") == model_dir


def test_task38_source_replacement_filter_places_mask_before_subtitles(tmp_path):
    ass_path = tmp_path / "dialogue_subtitles_en.ass"
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

    filter_value = build_source_replacement_filter(intervals, layouts, ass_path)

    assert filter_value.startswith("delogo=")
    assert filter_value.count("drawbox=") == 1
    assert filter_value.count("delogo=") == 1
    assert filter_value.rsplit(",", 1)[-1].startswith("subtitles='")
    assert filter_value.index("x=120:y=500:w=320:h=80") < filter_value.index("subtitles='")
    assert filter_value.index("x=200:y=540:w=240:h=72") < filter_value.index("subtitles='")


def test_task38_interval_stats_use_video_duration_for_percentage():
    stats = interval_stats(
        [{"width": 100, "height": 20, "start_time": 0.0, "end_time": 5.0, "line_count": 1}],
        output_width=1280,
        output_height=720,
        output_duration_seconds=20.0,
    )

    assert stats["masked_duration_seconds"] == 5.0
    assert stats["masked_duration_percent"] == 25.0


def test_task38_interval_stats_merge_overlapping_source_masks():
    stats = interval_stats(
        [
            {"width": 100, "height": 20, "start_time": 0.0, "end_time": 5.0, "line_count": 1},
            {"width": 100, "height": 20, "start_time": 3.0, "end_time": 9.0, "line_count": 1},
        ],
        output_width=1280,
        output_height=720,
        output_duration_seconds=20.0,
    )

    assert stats["masked_duration_seconds"] == 9.0
    assert stats["masked_duration_percent"] == 45.0


def test_task38_english_cue_uses_nearest_source_interval_without_requiring_same_id():
    intervals = [
        {
            "segment_id": "source_caption_0001",
            "start_time": 10.0,
            "end_time": 12.0,
            "source_bbox": {"left_x": 200, "top_y": 600, "right_x": 900, "bottom_y": 660},
        }
    ]

    selected = nearest_source_interval(intervals, 10.2, 11.1)

    assert selected["segment_id"] == "source_caption_0001"
