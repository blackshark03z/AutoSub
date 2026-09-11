import json

import pytest

from app.services.visual_ocr_evidence import (
    build_visual_text_evidence,
    classify_visual_track_text,
    observations_from_ocr_frames,
    write_visual_text_evidence,
)


def _frame(name: str, text: str, score: float, x: int = 100) -> dict:
    return {
        "name": name,
        "seconds": 0.123,
        "texts": [{
            "text": text,
            "score": score,
            "poly": [[x, 100], [x + 120, 100], [x + 120, 140], [x, 140]],
        }],
    }


def test_ocr_inference_seconds_are_not_used_as_video_timestamps() -> None:
    frames = [_frame("f1.jpg", "管理员", 0.99)]
    observations = observations_from_ocr_frames(
        frames,
        frame_times_ms={"f1.jpg": 4250},
        provider="modal",
        model="PP-OCRv6_small",
    )
    assert observations[0].time_ms == 4250
    assert observations[0].time_ms != 123


def test_missing_authoritative_frame_time_fails_closed() -> None:
    with pytest.raises(ValueError, match="Missing authoritative video timestamp"):
        observations_from_ocr_frames(
            [_frame("f1.jpg", "管理员", 0.99)],
            frame_times_ms={},
            provider="modal",
            model="PP-OCRv6_small",
        )


def test_repeated_source_text_becomes_one_localizable_track() -> None:
    payload = {
        "frames": [
            _frame("f1.jpg", "管理员", 0.98),
            _frame("f2.jpg", "管理员", 0.99, x=102),
            _frame("f3.jpg", "管理员", 0.97, x=103),
        ]
    }
    evidence = build_visual_text_evidence(
        payload,
        frame_times_ms={"f1.jpg": 1000, "f2.jpg": 1250, "f3.jpg": 1500},
        provider="modal",
        model="PP-OCRv6_small",
    )
    assert evidence["observation_count"] == 3
    assert evidence["track_count"] == 1
    assert evidence["localizable_track_count"] == 1
    track = evidence["tracks"][0]
    assert track["canonical_text"] == "管理员"
    assert track["observation_count"] == 3
    assert track["classification"]["action"] == "localize"


def test_transient_tiny_source_text_is_non_material_until_temporally_corroborated() -> None:
    payload = {"frames": [{
        "name": "f1.jpg",
        "texts": [{"text": "香蕉", "score": 0.99, "poly": [[100, 100], [134, 100], [134, 126], [100, 126]]}],
    }]}
    evidence = build_visual_text_evidence(
        payload,
        frame_times_ms={"f1.jpg": 1000},
        provider="modal",
        model="PP-OCRv6_small",
    )
    assert evidence["localizable_track_count"] == 0
    assert evidence["tracks"][0]["classification"] == {
        "action": "ignore",
        "reason": "transient_microtext_single_observation",
    }


def test_target_script_identifiers_and_numbers_are_preserved_by_default() -> None:
    assert classify_visual_track_text("2500")["action"] == "preserve"
    assert classify_visual_track_text("linlinqijieshuo")["action"] == "preserve"
    assert classify_visual_track_text("Stage 1/12 (8%)")["action"] == "preserve"
    assert classify_visual_track_text("口")["action"] == "preserve"
    assert classify_visual_track_text("口口口口口口")["action"] == "preserve"
    assert classify_visual_track_text("入口")["action"] == "localize"
    assert classify_visual_track_text("跳")["action"] == "localize"
    assert classify_visual_track_text("强化身体")["action"] == "localize"


def test_visual_text_evidence_is_persisted(tmp_path) -> None:
    path = tmp_path / "visual" / "tracks.json"
    evidence = write_visual_text_evidence(
        path,
        {"frames": [_frame("f1.jpg", "所有东西", 0.999)]},
        frame_times_ms={"f1.jpg": 2000},
        provider="modal",
        model="PP-OCRv6_small",
    )
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == evidence
    assert stored["localizable_track_count"] == 1
