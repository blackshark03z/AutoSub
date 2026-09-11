from pathlib import Path

import cv2
import numpy as np

from app.services import visual_localization_analysis as module


def test_whole_frame_analysis_binds_provider_results_to_media_time(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    run_dir = tmp_path / "run"
    frames_dir = run_dir / "work" / "visual_localization_frames"
    frame1 = frames_dir / "frame_00001.jpg"
    frame2 = frames_dir / "frame_00002.jpg"
    frames_dir.mkdir(parents=True)
    frame1.write_bytes(b"a")
    frame2.write_bytes(b"b")

    monkeypatch.setattr(module, "media_summary", lambda _: {"duration_seconds": 2.0})
    monkeypatch.setattr(module, "_sample_frames", lambda *args, **kwargs: [
        {"path": frame1, "time": 0.0},
        {"path": frame2, "time": 0.5},
    ])
    cleaned = []
    monkeypatch.setattr(module, "_cleanup_sampled_frames", lambda frames, directory: cleaned.append((frames, directory)))

    def ocr_runner(paths):
        assert paths == [frame1, frame2]
        return {
            "runtime": "modal_t4",
            "model_version": "PP-OCRv6_small",
            "frames": [
                {"name": frame1.name, "seconds": 9.9, "texts": [{"text": "管理员", "score": 0.99, "poly": [[10, 10], [110, 10], [110, 40], [10, 40]]}]},
                {"name": frame2.name, "seconds": 8.8, "texts": [{"text": "管理员", "score": 0.98, "poly": [[12, 10], [112, 10], [112, 40], [12, 40]]}]},
            ],
        }

    evidence = module.analyze_whole_frame_visual_text(
        source,
        run_dir,
        ocr_runner=ocr_runner,
        provider="modal",
        model="PP-OCRv6_small",
        sample_seconds=0.5,
    )
    assert evidence["sampled_frame_count"] == 2
    assert evidence["track_count"] == 1
    assert evidence["tracks"][0]["start_ms"] == 0
    assert evidence["tracks"][0]["end_ms"] == 500
    assert evidence["provider_runtime"] == "modal_t4"
    assert cleaned
    assert (run_dir / "visual" / "visual_text_evidence.json").is_file()


def test_whole_frame_analysis_reuses_stable_frames_without_losing_media_identity(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    run_dir = tmp_path / "run"
    frames_dir = run_dir / "work" / "visual_localization_frames"
    frames_dir.mkdir(parents=True)
    frame_paths = [frames_dir / f"frame_{index:05d}.jpg" for index in range(1, 5)]
    stable = np.zeros((180, 320, 3), dtype=np.uint8)
    cv2.putText(stable, "same", (30, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
    for path in frame_paths:
        assert cv2.imwrite(str(path), stable)

    monkeypatch.setattr(module, "media_summary", lambda _: {"duration_seconds": 2.0})
    monkeypatch.setattr(module, "_sample_frames", lambda *args, **kwargs: [
        {"path": path, "time": index * 0.5}
        for index, path in enumerate(frame_paths)
    ])
    monkeypatch.setattr(module, "_cleanup_sampled_frames", lambda *args, **kwargs: None)
    calls = []

    def ocr_runner(paths):
        calls.append(list(paths))
        return {
            "provider": "modal",
            "runtime": "modal_t4",
            "model_version": "PP-OCRv6_small",
            "frames": [
                {
                    "name": path.name,
                    "texts": [
                        {
                            "text": "管理员",
                            "score": 0.99,
                            "poly": [[10, 10], [110, 10], [110, 40], [10, 40]],
                        }
                    ],
                }
                for path in paths
            ],
        }

    evidence = module.analyze_whole_frame_visual_text(
        source,
        run_dir,
        ocr_runner=ocr_runner,
        provider="modal",
        model="PP-OCRv6_small",
        sample_seconds=0.5,
    )

    assert calls[0] == [frame_paths[0]]
    assert evidence["sampled_frame_count"] == 4
    assert evidence["ocr_frame_count"] == 1
    assert evidence["ocr_frame_reuse_count"] == 3
    assert evidence["ocr_frame_reuse_ratio"] == 0.75
    assert evidence["track_count"] == 1
    assert evidence["tracks"][0]["start_ms"] == 0
    assert evidence["tracks"][0]["end_ms"] == 1500


def test_whole_frame_analysis_can_limit_ocr_to_targeted_time_windows(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    run_dir = tmp_path / "run"
    frame_dir = run_dir / "work" / "visual_localization_frames"
    frame_dir.mkdir(parents=True)
    frame_paths = [frame_dir / f"frame_{index:05d}.jpg" for index in range(1, 6)]
    for path in frame_paths:
        path.write_bytes(b"frame")
    sampled = [
        {"path": path, "time": index * 0.5}
        for index, path in enumerate(frame_paths)
    ]
    monkeypatch.setattr(module, "media_summary", lambda _: {"duration_seconds": 2.5})
    monkeypatch.setattr(module, "_sample_frames", lambda *args, **kwargs: sampled)
    monkeypatch.setattr(
        module,
        "_select_adaptive_ocr_frames",
        lambda frames, *, sample_seconds: (list(frames), list(range(len(frames))), [1.0] * len(frames)),
    )
    cleaned = []
    monkeypatch.setattr(module, "_cleanup_sampled_frames", lambda frames, directory: cleaned.append((list(frames), directory)))
    calls = []

    def ocr_runner(paths):
        calls.append(list(paths))
        return {
            "provider": "local_paddleocr",
            "runtime": "local",
            "model_version": "2.10",
            "frames": [{"name": Path(path).name, "texts": []} for path in paths],
        }

    evidence = module.analyze_whole_frame_visual_text(
        source,
        run_dir,
        ocr_runner=ocr_runner,
        provider="local_paddleocr",
        model="2.10",
        sample_seconds=0.5,
        time_windows_ms=[(400, 1100)],
    )

    assert calls == [[frame_paths[1], frame_paths[2]]]
    assert evidence["sampled_frame_count"] == 2
    assert evidence["full_sampled_frame_count"] == 5
    assert evidence["targeted_time_windows_ms"] == [[400, 1100]]
    assert cleaned and cleaned[0][0] == sampled


def test_adaptive_ocr_selects_changed_text_frame(tmp_path: Path) -> None:
    frame1 = tmp_path / "frame1.jpg"
    frame2 = tmp_path / "frame2.jpg"
    first = np.zeros((180, 320, 3), dtype=np.uint8)
    second = first.copy()
    cv2.putText(first, "A", (80, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (255, 255, 255), 3)
    cv2.putText(second, "B", (80, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (255, 255, 255), 3)
    assert cv2.imwrite(str(frame1), first)
    assert cv2.imwrite(str(frame2), second)

    selected, representative_indexes, distances = module._select_adaptive_ocr_frames(
        [
            {"path": frame1, "time": 0.0},
            {"path": frame2, "time": 0.5},
        ],
        sample_seconds=0.5,
    )

    assert selected == [
        {"path": frame1, "time": 0.0},
        {"path": frame2, "time": 0.5},
    ]
    assert representative_indexes == [0, 1]
    assert distances[1] > module.ADAPTIVE_VISUAL_REUSE_THRESHOLD


def test_whole_frame_analysis_fails_closed_on_provider_frame_identity(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    run_dir = tmp_path / "run"
    frame_dir = run_dir / "work" / "visual_localization_frames"
    frame = frame_dir / "frame_00001.jpg"
    frame_dir.mkdir(parents=True)
    frame.write_bytes(b"x")
    monkeypatch.setattr(module, "media_summary", lambda _: {"duration_seconds": 1.0})
    monkeypatch.setattr(module, "_sample_frames", lambda *args, **kwargs: [{"path": frame, "time": 0.0}])
    monkeypatch.setattr(module, "_cleanup_sampled_frames", lambda *args, **kwargs: None)

    try:
        module.analyze_whole_frame_visual_text(
            source,
            run_dir,
            ocr_runner=lambda _: {"frames": [{"name": "other.jpg", "texts": []}]},
            provider="modal",
            model="PP-OCRv6_small",
        )
    except ValueError as exc:
        assert "frame identity mismatch" in str(exc)
    else:
        raise AssertionError("provider frame identity mismatch must fail closed")
