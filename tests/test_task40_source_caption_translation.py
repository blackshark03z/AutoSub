from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.offline_translation import OfflineTranslationError, load_translation_config, translate_source_captions
from app.services.source_caption_translation import (
    LOCAL_AUDIO_MODE,
    SOURCE_CAPTION_MODE,
    SourceCaptionUnavailableError,
    _dense_caption_crop,
    _ocr_candidates,
    _target_region_detections,
    _validate_translations,
    build_caption_intervals,
    build_source_caption_render_plan,
    normalize_source_text,
    select_caption_track,
)


def _detection(time_s: float, text: str, x: int, y: int, width: int, height: int, confidence: float = 0.98):
    bbox = {"left_x": x, "top_y": y, "right_x": x + width - 1, "bottom_y": y + height - 1}
    return {
        "time": time_s,
        "text": text,
        "confidence": confidence,
        "bbox": bbox,
        "center_x": x + width / 2,
        "center_y": y + height / 2,
        "height": height,
    }


def test_top_center_caption_track_wins_over_static_bottom_hud():
    detections = [
        _detection(0.0, "你好", 760, 64, 400, 54),
        _detection(0.5, "你好", 762, 65, 398, 53),
        _detection(1.0, "我来了", 880, 64, 158, 55),
        _detection(1.5, "我来了", 882, 65, 157, 54),
    ]
    detections.extend(_detection(index * 0.5, "固定库存", 700, 950, 520, 96) for index in range(8))

    selected = select_caption_track(detections, width=1920, height=1080)

    assert selected is not None
    assert selected["region"]["top_y"] < 150
    summary = selected["rejection_summary"]
    assert summary["static_hud_clusters_rejected"] + summary["sparse_noise_clusters_rejected"] >= 1


def test_large_changing_game_labels_cannot_outvote_smaller_caption_typography():
    captions = [
        _detection(9.5, "第一句", 700, 65, 520, 54),
        _detection(10.0, "第一句", 700, 65, 520, 54),
        _detection(21.5, "我来了", 880, 64, 158, 55),
        _detection(22.0, "我来了", 880, 64, 158, 55),
    ]
    game = []
    for index in range(30):
        text = f"游戏文字{index // 2}"
        game.append(_detection(index * 0.5, text, 730, 950, 460, 98))
    selected = select_caption_track(captions + game, width=1920, height=1080)
    assert selected is not None
    assert selected["region"]["top_y"] < 150
    assert selected["rejection_summary"]["oversized_hud_clusters_rejected"] >= 1


def test_no_caption_and_static_hud_do_not_create_source_caption_mode():
    hud = [_detection(index, "固定库存", 700, 950, 520, 96) for index in range(6)]
    assert select_caption_track([], width=1920, height=1080) is None
    assert select_caption_track(hud, width=1920, height=1080) is None


def test_small_changing_ui_text_does_not_outvote_caption_typography():
    caption = [
        _detection(0.0, "caption one", 700, 65, 520, 48),
        _detection(0.5, "caption two", 700, 65, 520, 48),
        _detection(1.0, "caption three", 700, 65, 520, 48),
    ]
    small_ui = [
        _detection(index * 0.5, f"label{index}", 900, 650, 100, 32)
        for index in range(8)
    ]
    selected = select_caption_track(caption + small_ui, width=1920, height=1080)
    assert selected is not None
    assert selected["region"]["top_y"] < 150
    assert selected["rejection_summary"]["sparse_noise_clusters_rejected"] >= 1


def test_text_changes_form_source_timed_intervals_and_merge_duplicates():
    detections = [
        _detection(0.0, "你好", 760, 64, 400, 54),
        _detection(0.5, "你好", 761, 64, 399, 54),
        _detection(1.0, "我来了", 880, 64, 158, 55),
        _detection(1.5, "我来了", 881, 64, 158, 55),
    ]
    intervals = build_caption_intervals(
        detections,
        sample_seconds=0.5,
        duration_seconds=3.0,
        width=1920,
        height=1080,
    )
    assert [item["source_text"] for item in intervals] == ["你好", "我来了"]
    assert intervals[0]["end_time"] <= intervals[1]["start_time"]
    assert intervals[1]["source_bbox"]["top_y"] == 64


def test_ocr_punctuation_variants_merge_into_one_caption_interval():
    detections = [
        _detection(0.0, "这是同一句", 600, 64, 700, 54),
        _detection(0.5, "这是同一句.", 600, 64, 700, 54),
        _detection(1.0, "这是同一句", 600, 64, 700, 54),
    ]
    intervals = build_caption_intervals(
        detections,
        sample_seconds=0.5,
        duration_seconds=2.0,
        width=1920,
        height=1080,
    )
    assert len(intervals) == 1
    assert intervals[0]["source_text"] == "这是同一句"


def test_adjacent_ocr_variants_with_same_bbox_are_merged():
    detections = [
        _detection(0.000, "孩子们在玩要吗", 700, 64, 520, 54),
        _detection(0.125, "孩子们在玩耍吗", 700, 64, 520, 54),
        _detection(0.250, "孩子们在玩要吗", 700, 64, 520, 54),
        _detection(0.375, "孩子们在玩耍吗", 700, 64, 520, 54),
    ]
    intervals = build_caption_intervals(
        detections,
        sample_seconds=0.125,
        duration_seconds=1.0,
        width=1920,
        height=1080,
    )
    assert len(intervals) == 1
    assert intervals[0]["start_time"] == 0.0
    assert intervals[0]["end_time"] >= 0.375


def test_ocr_filters_low_confidence_and_preserves_semantic_anchor():
    frames = [{"index": 0, "time": 22.0, "path": Path("unused.jpg")}]
    payload = {
        "frames": [{
            "items": [
                {"text": " 我 来 了 ", "confidence": 0.992, "contains_cjk": True, "box": [[880, 64], [1038, 64], [1038, 119], [880, 119]]},
                {"text": "噪声", "confidence": 0.2, "contains_cjk": True, "box": [[10, 10], [100, 10], [100, 50], [10, 50]]},
            ]
        }]
    }
    candidates = _ocr_candidates(frames, payload, width=1920, height=1080)
    assert len(candidates) == 1
    assert candidates[0]["text"] == "我来了"
    assert normalize_source_text(" 我 来 了 ") == "我来了"


def test_dense_caption_crop_restores_full_frame_coordinates():
    crop = _dense_caption_crop(
        {"left_x": 700, "top_y": 64, "right_x": 1200, "bottom_y": 119},
        width=1920,
        height=1080,
    )
    frames = [{
        "index": 0,
        "time": 22.0,
        "path": Path("unused.jpg"),
        "origin_x": crop[0],
        "origin_y": crop[1],
    }]
    relative_top = 64 - crop[1]
    payload = {"frames": [{"items": [{
        "text": "\u6211\u6765\u4e86",
        "confidence": 0.99,
        "contains_cjk": True,
        "box": [[880, relative_top], [1038, relative_top], [1038, relative_top + 55], [880, relative_top + 55]],
    }]}]}
    candidates = _ocr_candidates(frames, payload, width=1920, height=1080)
    assert candidates[0]["bbox"]["top_y"] == 64

    selected = {
        "detections": [
            _detection(21.5, "anchor one", 880, 64, 158, 55),
            _detection(22.0, "anchor two", 880, 64, 158, 55),
        ]
    }
    target = _target_region_detections(candidates, selected, height=1080)
    assert len(target) == 1


def test_translation_quality_guards_accept_anchor_and_reject_empty_or_generic():
    intervals = [{"source_text": "我来了"}, {"source_text": "你好吗"}]
    _validate_translations(intervals, [
        {"translated_text": "I'm here."},
        {"translated_text": "How are you?"},
    ])
    with pytest.raises(SourceCaptionUnavailableError):
        _validate_translations(intervals, [{"translated_text": ""}, {"translated_text": "How are you?"}])
    with pytest.raises(SourceCaptionUnavailableError):
        _validate_translations(
            [{"source_text": str(index)} for index in range(6)],
            [{"translated_text": "Generic line."} for _ in range(6)],
        )


def test_translation_model_missing_fails_closed(tmp_path):
    config = tmp_path / "operator" / "translation_runtime_config.local.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "python_path": str(tmp_path / "python.exe"),
        "packages_root": str(tmp_path / "packages"),
        "model_id": "translate-zh_en-1_9",
    }), encoding="utf-8")
    with pytest.raises(OfflineTranslationError, match="Python runtime is missing"):
        load_translation_config(config)


def test_offline_translation_uses_one_local_worker_and_no_provider(monkeypatch, tmp_path):
    python = tmp_path / "runtime" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    packages = tmp_path / "packages"
    model = packages / "translate-zh_en-1_9" / "model"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"fixture")
    config = tmp_path / "operator" / "translation_runtime_config.local.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "python_path": str(python),
        "packages_root": str(packages),
        "model_id": "translate-zh_en-1_9",
    }), encoding="utf-8")
    calls = []

    class Result:
        returncode = 0
        stdout = json.dumps({
            "ok": True,
            "translations": [{"text": "I'm coming.", "confidence": None}],
            "runtime": "argostranslate",
            "model": "translate-zh_en-1_9",
            "external_calls": 0,
        })
        stderr = ""

    monkeypatch.setattr("app.services.offline_translation.subprocess.run", lambda command, **kwargs: calls.append(command) or Result())
    output = translate_source_captions(["我来了"], config)
    assert output[0]["translated_text"] == "I'm coming."
    assert len(calls) == 1
    assert "whisper" not in " ".join(calls[0]).lower()


def test_ocr_render_plan_masks_detected_top_region_and_scales(monkeypatch):
    monkeypatch.setattr("app.services.source_caption_translation.media_summary", lambda _: {
        "duration_seconds": 30.0,
        "video": {"width": 1920, "height": 1080},
    })
    cues = [{
        "cue_id": "OCR_0001",
        "start_ms": 21000,
        "end_ms": 23000,
        "resolved_text": "I'm here.",
        "source_bbox": {"left_x": 880, "top_y": 64, "right_x": 1038, "bottom_y": 119},
        "source_interval": {"start_time": 21.0, "end_time": 23.0},
        "line_count": 1,
    }]
    plan = build_source_caption_render_plan(Path("source.mp4"), cues, font_path=Path(r"C:\Windows\Fonts\arial.ttf"))
    interval = plan["intervals"][0]
    layout = plan["layouts"][0]
    assert interval["y"] < 64
    assert interval["y"] + interval["height"] < 200
    assert layout["anchor_y"] < 200
    assert interval["start_time"] < 21.0
    assert 20.0 < layout["start_time"] < 21.0
    assert (interval["x"], interval["y"], interval["width"], interval["height"]) == (
        layout["plate"]["x"],
        layout["plate"]["y"],
        layout["plate"]["width"],
        layout["plate"]["height"],
    )
    assert plan["subtitle_provenance"] == SOURCE_CAPTION_MODE


def test_ui_wording_and_separate_modes_keep_one_button_contract():
    html = Path("app/static/simple/index.html").read_text(encoding="utf-8")
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")
    assert "Dịch và thay phụ đề có sẵn (Gemini free tier)" in html
    assert "Phần chữ phụ đề được gửi tới Gemini bằng credential an toàn. Video không được tải lên." in html
    assert "Cần cấu hình Gemini free tier trước khi tạo video." in js
    assert "Che phụ đề gốc phía dưới" not in html
    assert 'id="startBtn"' in html
    assert 'caption_mode: $("cleanupMode").value' in js
    assert '"x-idempotency-key"' in js
    assert "model selector" not in html.lower()
    assert SOURCE_CAPTION_MODE != LOCAL_AUDIO_MODE
