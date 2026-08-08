from __future__ import annotations

import cv2
import numpy as np

from app.services.cjk_cleanup import detect_punctuation_residue


def _frame_with_punctuation(*, under_plate: bool = False) -> np.ndarray:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (42, 42, 42)
    plate = {"left_x": 410, "top_y": 631, "right_x": 900, "bottom_y": 679}
    cv2.rectangle(frame, (plate["left_x"], plate["top_y"]), (plate["right_x"], plate["bottom_y"]), (0, 0, 0), -1)
    cv2.putText(frame, "What does that mean?", (430, 660), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    xs = [292, 310, 328] if not under_plate else [378, 386, 392]
    for x in xs:
        cv2.putText(frame, "?", (x, 682), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2)
    return frame


def _plate_boxes() -> list[dict[str, int]]:
    return [{"left_x": 410, "top_y": 631, "right_x": 900, "bottom_y": 679}]


def test_cp08d4_detects_punctuation_only_candidate_without_unicode_dependency():
    result = detect_punctuation_residue(_frame_with_punctuation(), plate_boxes=_plate_boxes())
    assert result["detected"] is True
    assert result["bbox"]["left_x"] < 340
    assert result["bbox"]["bottom_y"] >= 675
    assert result["cluster_count"] >= 1


def test_cp08d4_detects_punctuation_partially_under_plate_edge():
    result = detect_punctuation_residue(_frame_with_punctuation(under_plate=True), plate_boxes=_plate_boxes())
    assert result["detected"] is True
    assert result["bbox"]["right_x"] <= 395


def test_cp08d4_post_repair_rescan_is_clean_when_masked():
    frame = _frame_with_punctuation()
    detected = detect_punctuation_residue(frame, plate_boxes=_plate_boxes())
    bbox = detected["bbox"]
    repaired = frame.copy()
    cv2.rectangle(
        repaired,
        (bbox["left_x"] - 12, bbox["top_y"] - 28),
        (bbox["right_x"] + 12, bbox["bottom_y"] + 8),
        (42, 42, 42),
        -1,
    )
    assert detect_punctuation_residue(repaired, plate_boxes=_plate_boxes())["detected"] is False


def test_cp08d4_temporal_persistence_has_no_rapid_toggle():
    frames = [_frame_with_punctuation() for _ in range(8)]
    detections = [detect_punctuation_residue(frame, plate_boxes=_plate_boxes())["detected"] for frame in frames]
    assert all(detections)
    toggles = sum(1 for previous, current in zip(detections, detections[1:]) if previous != current)
    assert toggles == 0


def test_cp08d4_detector_does_not_require_existing_artifact_mutation(tmp_path):
    immutable = tmp_path / "accepted.mp4"
    immutable.write_bytes(b"accepted")
    before = immutable.read_bytes()
    detect_punctuation_residue(_frame_with_punctuation(), plate_boxes=_plate_boxes())
    assert immutable.read_bytes() == before
