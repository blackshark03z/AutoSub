import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

import app.services.source_caption_translation as source_caption_module
from app.services.caption_overrides import (
    HUMAN_REVIEWED_CAPTION_PROVENANCE,
    CaptionOverrideValidationError,
    caption_region_fingerprint,
    create_reviewed_caption_override,
    load_matching_caption_override,
)
from app.services.source_caption_translation import (
    SOURCE_CAPTION_HUMAN_REVIEW_MODE,
    SourceCaptionUnavailableError,
    _load_prevalidated_source_caption_evidence,
)


def _interval():
    return {
        "id": "OCR_0030",
        "start_time": 355.438,
        "end_time": 357.438,
        "source_text": "source caption",
        "ocr_candidates": [
            {"text": "source caption", "count": 2, "median_confidence": 0.9},
        ],
    }


def _create(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake source bytes")
    record, path = create_reviewed_caption_override(
        source_path=source,
        interval=_interval(),
        corrected_chinese="corrected source",
        approved_english="Approved English.",
        override_root=tmp_path / "overrides",
    )
    return source, record, path


def test_valid_hashed_override_applies_to_exact_interval(tmp_path):
    source, record, path = _create(tmp_path)
    loaded = load_matching_caption_override(
        source_path=source,
        interval=_interval(),
        override_root=path.parent,
    )
    assert loaded["approved_english"] == "Approved English."
    assert loaded["provenance"] == HUMAN_REVIEWED_CAPTION_PROVENANCE
    assert loaded["record_sha256"] == record["record_sha256"]
    assert "api_key" not in json.dumps(loaded).lower()


def test_wrong_interval_id_does_not_cross_apply(tmp_path):
    source, _, path = _create(tmp_path)
    interval = _interval()
    interval["id"] = "OCR_0031"
    assert load_matching_caption_override(
        source_path=source,
        interval=interval,
        override_root=path.parent,
    ) is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda interval: interval.update({"start_time": 355.5}), "start time"),
        (lambda interval: interval.update({"source_text": "different"}), "region fingerprint"),
    ],
)
def test_wrong_interval_identity_is_rejected(tmp_path, change, message):
    source, _, path = _create(tmp_path)
    interval = _interval()
    change(interval)
    with pytest.raises(CaptionOverrideValidationError, match=message):
        load_matching_caption_override(source_path=source, interval=interval, override_root=path.parent)


def test_wrong_video_hash_does_not_cross_apply(tmp_path):
    source, _, path = _create(tmp_path)
    other = tmp_path / "other.mp4"
    other.write_bytes(b"other source bytes")
    assert load_matching_caption_override(
        source_path=other,
        interval=_interval(),
        override_root=path.parent,
    ) is None


def test_modified_record_fails_closed(tmp_path):
    source, _, path = _create(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["approved_english"] = "Tampered."
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(CaptionOverrideValidationError, match="hash mismatch"):
        load_matching_caption_override(source_path=source, interval=_interval(), override_root=path.parent)


def test_fingerprint_is_canonical_and_does_not_store_source_bytes(tmp_path):
    source, record, _ = _create(tmp_path)
    expected_source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert record["source_video_sha256"] == expected_source_hash
    assert record["caption_region_fingerprint"] == caption_region_fingerprint(
        source_video_sha256=expected_source_hash,
        interval=_interval(),
    )
    assert "fake source bytes" not in json.dumps(record)


def _write_prevalidated_evidence(tmp_path: Path, source_hash: str) -> Path:
    subtitles = tmp_path / "subtitles"
    subtitles.mkdir(parents=True)
    path = subtitles / "source_caption_gemini_translation.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_video_sha256": source_hash,
                "mode": SOURCE_CAPTION_HUMAN_REVIEW_MODE,
                "subtitle_provenance": SOURCE_CAPTION_HUMAN_REVIEW_MODE,
                "ocr_provider": "paddleocr",
                "ocr_model": "paddleocr-2.10.0",
                "translation_provider": "gemini",
                "translation_model": "gemini-2.5-flash",
                "automatic_caption_count": 1,
                "human_reviewed_caption_count": 1,
                "provider_usage": {"request_count": 0, "cache_hits": 2},
                "caption_interval_benchmark": {
                    "total_intervals": 2,
                    "resolved_intervals": 2,
                    "status": "PASS",
                    "free_tier_verified": True,
                    "selected_model": "gemini-2.5-flash",
                },
                "intervals": [
                    {
                        "id": "OCR_0001",
                        "start_time": 1.0,
                        "end_time": 2.0,
                        "source_text": "source one",
                        "source_bbox": {"left_x": 100, "top_y": 50, "right_x": 400, "bottom_y": 90},
                        "line_count": 1,
                        "ocr_confidence": 0.99,
                    },
                    {
                        "id": "OCR_0002",
                        "start_time": 3.0,
                        "end_time": 4.0,
                        "source_text": "source two",
                        "source_bbox": {"left_x": 120, "top_y": 50, "right_x": 420, "bottom_y": 90},
                        "line_count": 1,
                        "ocr_confidence": 0.98,
                    },
                ],
                "translations": [
                    {"id": "OCR_0001", "source_text": "source one", "translated_text": "Gemini text."},
                    {"id": "OCR_0002", "source_text": "source two", "translated_text": "Reviewed text."},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_prevalidated_source_caption_evidence_reuses_generic_human_review_track(tmp_path):
    source_hash = "a" * 64
    _write_prevalidated_evidence(tmp_path, source_hash)
    result = _load_prevalidated_source_caption_evidence(
        tmp_path,
        source_video_sha256=source_hash,
        width=1280,
        height=720,
        duration=10.0,
    )
    assert result is not None
    assert len(result["cues"]) == 2
    assert result["cues"][1]["text"] == "Reviewed text."
    assert result["metadata"]["subtitle_provenance"] == SOURCE_CAPTION_HUMAN_REVIEW_MODE
    assert result["metadata"]["human_reviewed_caption_count"] == 1
    assert result["metadata"]["provider_usage"]["request_count"] == 0
    assert result["cues"][0]["needs_pixel_refresh"] is True
    assert result["cues"][0]["geometry_source"] == "prevalidated_evidence"


def test_prevalidated_source_caption_evidence_rejects_wrong_video_hash(tmp_path):
    _write_prevalidated_evidence(tmp_path, "a" * 64)
    with pytest.raises(SourceCaptionUnavailableError):
        _load_prevalidated_source_caption_evidence(
            tmp_path,
            source_video_sha256="b" * 64,
            width=1280,
            height=720,
            duration=10.0,
        )


def test_replacement_track_selection_prefers_pixel_visible_bottom_caption(tmp_path, monkeypatch):
    frames = []
    for index, time_s in enumerate((1.0, 2.0, 3.0, 4.0), start=1):
        frame_path = tmp_path / f"frame_{index}.jpg"
        cv2.imwrite(str(frame_path), np.zeros((1080, 1920, 3), dtype=np.uint8))
        frames.append({"time": time_s, "path": frame_path})
    detections = [
        {
            "time": 1.0,
            "text": "顶部字幕",
            "confidence": 0.99,
            "bbox": {"left_x": 820, "top_y": 70, "right_x": 1100, "bottom_y": 118},
            "center_x": 960,
            "center_y": 94,
            "height": 49,
        },
        {
            "time": 1.0,
            "text": "底部字幕一",
            "confidence": 0.99,
            "bbox": {"left_x": 720, "top_y": 940, "right_x": 1210, "bottom_y": 1030},
            "center_x": 965,
            "center_y": 985,
            "height": 91,
        },
        {
            "time": 2.0,
            "text": "底部字幕一",
            "confidence": 0.98,
            "bbox": {"left_x": 724, "top_y": 938, "right_x": 1208, "bottom_y": 1028},
            "center_x": 966,
            "center_y": 983,
            "height": 91,
        },
        {
            "time": 3.0,
            "text": "底部字幕二",
            "confidence": 0.97,
            "bbox": {"left_x": 726, "top_y": 940, "right_x": 1212, "bottom_y": 1030},
            "center_x": 969,
            "center_y": 985,
            "height": 91,
        },
        {
            "time": 4.0,
            "text": "底部字幕二",
            "confidence": 0.97,
            "bbox": {"left_x": 726, "top_y": 940, "right_x": 1212, "bottom_y": 1030},
            "center_x": 969,
            "center_y": 985,
            "height": 91,
        },
    ]

    monkeypatch.setattr(
        source_caption_module,
        "detect_source_subtitle_bbox",
        lambda *_args, **_kwargs: (
            {"left_x": 710, "top_y": 930, "right_x": 1220, "bottom_y": 1040},
            {"component_count": 8, "pixel_count": 500},
        ),
    )

    selected = source_caption_module.select_replacement_caption_track(
        frames,
        detections,
        width=1920,
        height=1080,
    )

    assert selected is not None
    assert {item["text"] for item in selected["detections"]} == {"底部字幕一", "底部字幕二"}
    assert selected["selection_policy"] == "pixel_visible_embedded_caption_zone"


def test_replacement_track_selection_falls_back_when_no_pixel_caption(tmp_path, monkeypatch):
    frame_path = tmp_path / "frame.jpg"
    cv2.imwrite(str(frame_path), np.zeros((1080, 1920, 3), dtype=np.uint8))
    monkeypatch.setattr(source_caption_module, "detect_source_subtitle_bbox", lambda *_args, **_kwargs: (None, {}))

    selected = source_caption_module.select_replacement_caption_track(
        [{"time": 1.0, "path": frame_path}],
        [
            {
                "time": 1.0,
                "text": "顶部字幕",
                "confidence": 0.99,
                "bbox": {"left_x": 820, "top_y": 70, "right_x": 1100, "bottom_y": 118},
                "center_x": 960,
                "center_y": 94,
                "height": 49,
            }
        ],
        width=1920,
        height=1080,
    )

    assert selected is None


def test_upper_replacement_caption_region_detects_centered_overlay_text(tmp_path):
    frame_path = tmp_path / "upper.jpg"
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        "UPPER CAPTION",
        (650, 105),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.7,
        (255, 255, 255),
        5,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(frame_path), frame)

    selected = source_caption_module.select_upper_replacement_caption_region(
        [{"time": time_s, "path": frame_path} for time_s in (1.0, 1.5, 2.0)],
        width=1920,
        height=1080,
    )

    assert selected is not None
    assert selected["track_name"] == "upper_overlay_caption"
    assert selected["region"]["top_y"] < 160


def test_visual_replacement_caption_groups_split_when_glyphs_change():
    bbox = {"left_x": 620, "top_y": 916, "right_x": 1180, "bottom_y": 1034}
    unchanged = "0" * 576
    changed = "1" * 576
    detections = [
        {"time": 10.0, "bbox": bbox, "glyph_signature": unchanged},
        {"time": 10.5, "bbox": bbox, "glyph_signature": unchanged},
        {"time": 11.0, "bbox": bbox, "glyph_signature": changed},
    ]

    groups = source_caption_module._visual_replacement_caption_groups(
        detections,
        sample_seconds=0.5,
        width=1920,
        height=1080,
    )

    assert [len(group) for group in groups] == [2, 1]


def test_representative_ocr_builds_intervals_from_pixel_visible_caption_bbox():
    frames = [
        {
            "time": 22.0,
            "path": Path("unused.jpg"),
            "origin_x": 560,
            "origin_y": 880,
            "visual_group_start_time": 21.75,
            "visual_group_end_time": 22.75,
            "visual_group_bbox": {"left_x": 624, "top_y": 916, "right_x": 1178, "bottom_y": 1034},
            "visual_group_sample_count": 3,
        }
    ]
    candidates = [
        {
            "time": 22.0,
            "text": "\u6211\u4eec\u662f\u6ca1\u6709\u673a",
            "confidence": 0.96,
            "bbox": {"left_x": 640, "top_y": 920, "right_x": 1160, "bottom_y": 1028},
            "center_x": 900,
            "center_y": 974,
            "height": 109,
            "unresolved_solid_glyph": False,
        }
    ]

    intervals = source_caption_module.build_visual_caption_intervals_from_representative_ocr(
        frames,
        candidates,
        sample_seconds=0.5,
        duration_seconds=30.0,
        width=1920,
        height=1080,
    )

    assert len(intervals) == 1
    assert intervals[0]["source_text"] == "\u6211\u4eec\u662f\u6ca1\u6709\u673a"
    assert intervals[0]["source_bbox"]["top_y"] == 916
    assert intervals[0]["source_bbox"]["bottom_y"] == 1034
    assert intervals[0]["ocr_gate_pass"] is True


def test_render_plan_keeps_upper_overlay_cues_in_upper_source_zone(monkeypatch):
    monkeypatch.setattr(
        source_caption_module,
        "media_summary",
        lambda _: {"duration_seconds": 30.0, "video": {"width": 1920, "height": 1080}},
    )

    class FakeCapture:
        def isOpened(self):
            return True

        def release(self):
            pass

    def fail_if_bottom_visual_refresh_is_used(*_args, **_kwargs):
        raise AssertionError("upper overlay cues must not be remapped to the lower caption detector")

    monkeypatch.setattr(source_caption_module.cv2, "VideoCapture", lambda *_args, **_kwargs: FakeCapture())
    monkeypatch.setattr(source_caption_module, "read_frame", fail_if_bottom_visual_refresh_is_used)

    plan = source_caption_module.build_source_caption_render_plan(
        Path("source.mp4"),
        [
            {
                "cue_id": "OCR_0001",
                "start_ms": 21000,
                "end_ms": 23000,
                "text": "I'm here.",
                "source_bbox": {"left_x": 880, "top_y": 64, "right_x": 1038, "bottom_y": 119},
                "source_interval": {"start_time": 21.0, "end_time": 23.0},
                "line_count": 1,
            }
        ],
        font_path=Path(r"C:\Windows\Fonts\arial.ttf"),
    )

    assert plan["layouts"][0]["anchor_y"] < 180
    assert plan["intervals"][0]["replacement_role"] == "stored_source_zone"


def test_render_plan_refreshes_prevalidated_cached_caption_geometry(monkeypatch):
    monkeypatch.setattr(
        source_caption_module,
        "media_summary",
        lambda _: {"duration_seconds": 30.0, "video": {"width": 1920, "height": 1080}},
    )

    class FakeCapture:
        def isOpened(self):
            return True

        def release(self):
            pass

    visual_reads = []

    def fake_read_frame(*_args, **_kwargs):
        visual_reads.append(True)
        return np.zeros((1080, 1920, 3), dtype=np.uint8)

    monkeypatch.setattr(source_caption_module.cv2, "VideoCapture", lambda *_args, **_kwargs: FakeCapture())
    monkeypatch.setattr(source_caption_module, "read_frame", fake_read_frame)
    monkeypatch.setattr(
        source_caption_module,
        "detect_source_subtitle_bbox",
        lambda *_args, **_kwargs: (
            {"left_x": 620, "top_y": 915, "right_x": 1180, "bottom_y": 1034},
            {"component_count": 8, "pixel_count": 512},
        ),
    )

    plan = source_caption_module.build_source_caption_render_plan(
        Path("source.mp4"),
        [
            {
                "cue_id": "OCR_0001",
                "start_ms": 21000,
                "end_ms": 23000,
                "text": "I'm here.",
                "source_bbox": {"left_x": 770, "top_y": 67, "right_x": 1138, "bottom_y": 115},
                "source_interval": {"start_time": 21.0, "end_time": 23.0},
                "line_count": 1,
                "needs_pixel_refresh": True,
                "geometry_source": "prevalidated_evidence",
            }
        ],
        font_path=Path(r"C:\Windows\Fonts\arial.ttf"),
    )

    assert visual_reads
    assert len(plan["intervals"]) == 1
    assert plan["intervals"][0]["geometry_refresh"] is True
    assert plan["intervals"][0]["source_bbox"]["top_y"] == 915
    assert plan["layouts"][0]["geometry_source"] == "pixel_visible_embedded_caption_zone"


def test_render_plan_fails_closed_for_uncovered_source_caption_pixel_window(monkeypatch):
    monkeypatch.setattr(
        source_caption_module,
        "media_summary",
        lambda _: {"duration_seconds": 30.0, "video": {"width": 1920, "height": 1080}},
    )

    class FakeCapture:
        def isOpened(self):
            return True

        def release(self):
            pass

    monkeypatch.setattr(source_caption_module.cv2, "VideoCapture", lambda *_args, **_kwargs: FakeCapture())
    monkeypatch.setattr(
        source_caption_module,
        "_source_caption_pixel_coverage_windows",
        lambda *_args, **_kwargs: [
            {
                "window_id": "caption_active_0001",
                "start_time": 10.0,
                "end_time": 11.0,
                "representative_times": [10.0, 10.5, 11.0],
                "source_bbox": {"left_x": 620, "top_y": 915, "right_x": 1180, "bottom_y": 1034},
                "pixel_confidence": 1.0,
                "sample_count": 3,
                "schema_version": "source_caption_pixel_coverage_v1",
            },
            {
                "window_id": "caption_active_0002",
                "start_time": 21.0,
                "end_time": 23.0,
                "representative_times": [21.0, 22.0, 23.0],
                "source_bbox": {"left_x": 620, "top_y": 915, "right_x": 1180, "bottom_y": 1034},
                "pixel_confidence": 1.0,
                "sample_count": 3,
                "schema_version": "source_caption_pixel_coverage_v1",
            },
        ],
    )

    with pytest.raises(SourceCaptionUnavailableError):
        source_caption_module.build_source_caption_render_plan(
            Path("source.mp4"),
            [
                {
                    "cue_id": "OCR_0001",
                    "start_ms": 21000,
                    "end_ms": 23000,
                    "text": "I'm here.",
                    "source_bbox": {"left_x": 770, "top_y": 67, "right_x": 1138, "bottom_y": 115},
                    "source_interval": {"start_time": 21.0, "end_time": 23.0},
                    "line_count": 1,
                    "needs_pixel_refresh": True,
                    "geometry_source": "prevalidated_evidence",
                }
            ],
            font_path=Path(r"C:\Windows\Fonts\arial.ttf"),
        )


def test_render_plan_extends_caption_timing_to_pixel_coverage_window(monkeypatch):
    monkeypatch.setattr(
        source_caption_module,
        "media_summary",
        lambda _: {"duration_seconds": 30.0, "video": {"width": 1920, "height": 1080}},
    )

    class FakeCapture:
        def isOpened(self):
            return True

        def release(self):
            pass

    bbox = {"left_x": 620, "top_y": 915, "right_x": 1180, "bottom_y": 1034}
    monkeypatch.setattr(source_caption_module.cv2, "VideoCapture", lambda *_args, **_kwargs: FakeCapture())
    monkeypatch.setattr(
        source_caption_module,
        "_source_caption_pixel_coverage_windows",
        lambda *_args, **_kwargs: [
            {
                "window_id": "caption_active_0001",
                "start_time": 20.6,
                "end_time": 23.2,
                "representative_times": [20.6, 21.9, 23.2],
                "source_bbox": bbox,
                "pixel_confidence": 1.0,
                "sample_count": 5,
                "schema_version": "source_caption_pixel_coverage_v1",
            }
        ],
    )
    monkeypatch.setattr(source_caption_module, "_visual_replacement_bbox_for_cue", lambda *_args, **_kwargs: bbox)

    plan = source_caption_module.build_source_caption_render_plan(
        Path("source.mp4"),
        [
            {
                "cue_id": "OCR_0001",
                "start_ms": 21000,
                "end_ms": 23000,
                "text": "I'm here.",
                "source_bbox": {"left_x": 770, "top_y": 67, "right_x": 1138, "bottom_y": 115},
                "source_interval": {"start_time": 21.0, "end_time": 23.0},
                "line_count": 1,
                "needs_pixel_refresh": True,
                "geometry_source": "prevalidated_evidence",
            }
        ],
        font_path=Path(r"C:\Windows\Fonts\arial.ttf"),
    )

    assert plan["source_caption_coverage"][0]["coverage_verdict"] == "PASS_MATCHED_SOURCE_CUE"
    assert plan["intervals"][0]["start_time"] <= 19.85
    assert plan["intervals"][0]["end_time"] >= 23.5
    assert plan["layouts"][0]["start_time"] <= 20.35
    assert plan["layouts"][0]["end_time"] == 23.2


def test_only_stale_caption_override_identity_mismatch_is_ignored():
    assert source_caption_module._is_stale_caption_override_error(
        CaptionOverrideValidationError("Caption override start time mismatch.")
    )
    assert not source_caption_module._is_stale_caption_override_error(
        CaptionOverrideValidationError("Caption override record hash mismatch.")
    )
