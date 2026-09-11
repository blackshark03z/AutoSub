from pathlib import Path

from PIL import Image

from app.services.visual_recognition_refinement import refine_uncertain_visual_text_evidence


def _evidence(text: str = "重力", confidence: float = 0.70):
    return {
        "schema_version": 1,
        "track_count": 1,
        "localizable_track_count": 1,
        "tracks": [
            {
                "track_id": "vtt_000001",
                "canonical_text": text,
                "confidence": confidence,
                "start_ms": 0,
                "end_ms": 0,
                "observation_count": 1,
                "classification": {"action": "localize", "reason": "source_cjk_semantic_candidate"},
                "observations": [
                    {
                        "observation_id": "frame_00001.jpg:0001",
                        "time_ms": 0,
                        "text": text,
                        "confidence": confidence,
                        "bbox": [10, 10, 60, 35],
                        "provider": "fake",
                        "model": "fake",
                        "frame_id": "frame_00001.jpg",
                    }
                ],
            }
        ],
    }


def _frame(tmp_path: Path) -> Path:
    path = tmp_path / "frame_00001.jpg"
    Image.new("RGB", (100, 60), "white").save(path)
    return path


def _runner_for(texts):
    def runner(paths):
        assert len(paths) == 3
        return {
            "frames": [
                {"name": path.name, "texts": items}
                for path, items in zip(paths, texts)
            ]
        }
    return runner


def test_crop_consensus_can_verify_low_confidence_source_text(tmp_path: Path) -> None:
    frame = _frame(tmp_path)
    runner = _runner_for([
        [{"text": "乞弓脸", "score": 0.66}],
        [{"text": "乞弓脸", "score": 0.75}],
        [{"text": "乞弓脸", "score": 0.76}],
    ])
    result = refine_uncertain_visual_text_evidence(
        _evidence("乞弯脸", 0.72),
        frame_paths={frame.name: frame},
        ocr_runner=runner,
        work_dir=tmp_path / "refine",
    )
    track = result["tracks"][0]
    assert track["classification"]["action"] == "localize"
    assert track["canonical_text"] == "乞弓脸"
    assert track["recognition"]["status"] == "verified"
    assert track["recognition"]["method"] == "crop_transform_consensus"
    assert result["unresolved_recognition_track_count"] == 0
    assert not (tmp_path / "refine").exists()


def test_crop_high_confidence_non_source_refutes_false_cjk_hit(tmp_path: Path) -> None:
    frame = _frame(tmp_path)
    runner = _runner_for([
        [{"text": "[Chad]", "score": 0.99}],
        [{"text": "[chad]", "score": 0.93}],
        [{"text": "[chad]", "score": 0.95}],
    ])
    result = refine_uncertain_visual_text_evidence(
        _evidence("重启", 0.82),
        frame_paths={frame.name: frame},
        ocr_runner=runner,
        work_dir=tmp_path / "refine",
    )
    track = result["tracks"][0]
    assert track["classification"]["action"] == "preserve"
    assert track["canonical_text"].casefold() == "[chad]"
    assert track["recognition"]["status"] == "verified"
    assert result["localizable_track_count"] == 0


def test_single_frame_source_disagreement_becomes_attention_not_blocker(tmp_path: Path) -> None:
    frame = _frame(tmp_path)
    runner = _runner_for([
        [{"text": "蛋白格", "score": 0.73}],
        [{"text": "蛋白核", "score": 0.75}],
        [{"text": "蛋白楼", "score": 0.72}],
    ])
    result = refine_uncertain_visual_text_evidence(
        _evidence("蛋白棉", 0.75),
        frame_paths={frame.name: frame},
        ocr_runner=runner,
        work_dir=tmp_path / "refine",
    )
    track = result["tracks"][0]
    assert track["classification"] == {"action": "ignore", "reason": "transient_single_observation_unverified"}
    assert track["recognition"]["status"] == "attention"
    assert track["recognition"]["method"] == "transient_single_observation_disagreement"
    assert result["unresolved_recognition_track_count"] == 0
    assert result["recognition_attention_track_count"] == 1


def test_repeated_source_disagreement_still_fails_closed(tmp_path: Path) -> None:
    frame = _frame(tmp_path)
    evidence = _evidence("蛋白棉", 0.75)
    track = evidence["tracks"][0]
    track["observation_count"] = 2
    track["observations"].append({**track["observations"][0], "observation_id": "frame_00002.jpg:0001", "time_ms": 500})
    runner = _runner_for([
        [{"text": "蛋白格", "score": 0.73}],
        [{"text": "蛋白核", "score": 0.75}],
        [{"text": "蛋白楼", "score": 0.72}],
    ])
    result = refine_uncertain_visual_text_evidence(
        evidence,
        frame_paths={frame.name: frame},
        ocr_runner=runner,
        work_dir=tmp_path / "refine",
    )
    track = result["tracks"][0]
    assert track["classification"]["action"] == "unresolved"
    assert track["recognition"]["status"] == "unresolved"
    assert result["unresolved_recognition_track_count"] == 1
    assert result["recognition_attention_track_count"] == 0


def test_unreproduced_source_script_is_treated_as_noise_candidate(tmp_path: Path) -> None:
    frame = _frame(tmp_path)
    runner = _runner_for([
        [{"text": "Jra", "score": 0.52}],
        [],
        [],
    ])
    result = refine_uncertain_visual_text_evidence(
        _evidence("重动", 0.70),
        frame_paths={frame.name: frame},
        ocr_runner=runner,
        work_dir=tmp_path / "refine",
    )
    track = result["tracks"][0]
    assert track["classification"]["action"] == "ignore"
    assert track["recognition"]["status"] == "refuted"
    assert result["unresolved_recognition_track_count"] == 0
