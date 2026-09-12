from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

import app.services.full_visual_pipeline as pipeline


def test_tier_a_render_retries_one_transient_process_failure(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    candidate = tmp_path / "candidate.mp4"
    ass_path = tmp_path / "visual.ass"
    ass_path.write_text("[Events]\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "build_tier_a_filter", lambda *_args, **_kwargs: "null")
    calls: list[list[str]] = []

    def render(command, _duration):
        calls.append(list(command))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, command)
        Path(command[-1]).write_bytes(b"candidate")

    pipeline._render_tier_a_candidate(
        source,
        candidate,
        plan={"replacements": []},
        ass_path=ass_path,
        duration_seconds=2.0,
        render_runner=render,
    )
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert candidate.read_bytes() == b"candidate"


def test_tier_a_render_stops_after_one_retry(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    candidate = tmp_path / "candidate.mp4"
    ass_path = tmp_path / "visual.ass"
    ass_path.write_text("[Events]\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "build_tier_a_filter", lambda *_args, **_kwargs: "null")
    calls = 0

    def render(command, _duration):
        nonlocal calls
        calls += 1
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        pipeline._render_tier_a_candidate(
            source,
            candidate,
            plan={"replacements": []},
            ass_path=ass_path,
            duration_seconds=2.0,
            render_runner=render,
        )
    assert calls == 2
    assert not candidate.exists()


def test_tier_a_render_uses_filter_script_for_large_graph(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    candidate = tmp_path / "candidate.mp4"
    ass_path = tmp_path / "visual.ass"
    ass_path.write_text("[Events]\n", encoding="utf-8")
    filter_value = ",".join(["null"] * 12000)
    monkeypatch.setattr(pipeline, "build_tier_a_filter", lambda *_args, **_kwargs: filter_value)
    calls: list[list[str]] = []

    def render(command, _duration):
        calls.append(list(command))
        Path(command[-1]).write_bytes(b"candidate")

    pipeline._render_tier_a_candidate(
        source,
        candidate,
        plan={"replacements": []},
        ass_path=ass_path,
        duration_seconds=2.0,
        render_runner=render,
    )

    assert len(calls) == 1
    command = calls[0]
    assert "-filter_script:v" in command
    assert "-vf" not in command
    script_path = Path(command[command.index("-filter_script:v") + 1])
    assert script_path.read_text(encoding="utf-8") == filter_value
    assert filter_value not in command
    assert candidate.read_bytes() == b"candidate"


class _ReleasableOCRRunner:
    def __init__(self) -> None:
        self.release_count = 0

    def __call__(self, _paths):
        return {}

    def release_local_session(self) -> None:
        self.release_count += 1


class _Translator:
    provider_name = "fake_translation"
    model = "fake-model"

    def translate(self, captions, **_kwargs):
        return SimpleNamespace(
            translations=[{"id": item["id"], "english": "Settings"} for item in captions],
            model=self.model,
            request_count=1,
            retry_count=0,
            cache_hits=0,
            cache_misses=1,
            input_tokens=10,
            output_tokens=2,
            request_ids=[],
        )


def _source_evidence():
    return {
        "track_count": 1,
        "localizable_track_count": 1,
        "tracks": [{
            "track_id": "vtt_000001",
            "canonical_text": "设置",
            "confidence": 0.99,
            "start_ms": 0,
            "end_ms": 1000,
            "classification": {"action": "localize", "reason": "source_cjk_semantic_candidate"},
            "observations": [
                {"bbox": [100, 100, 220, 140], "time_ms": 0},
                {"bbox": [101, 100, 221, 140], "time_ms": 500},
            ],
        }],
    }


def test_full_visual_composition_only_publishes_after_final_ocr_qc(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "subtitle_stage.mp4"
    source.write_bytes(b"source-stage")
    run_dir = tmp_path / "run"
    output = run_dir / "output" / "final_video.mp4"
    calls = []

    monkeypatch.setattr(pipeline, "media_summary", lambda _path: {"duration_seconds": 2.0, "video": {"width": 1280, "height": 720}})
    monkeypatch.setattr(
        pipeline,
        "analyze_whole_frame_visual_text",
        lambda path, *_args, **_kwargs: _source_evidence() if Path(path).name == "subtitle_stage.mp4" else {"track_count": 0, "localizable_track_count": 0, "tracks": []},
    )

    def render(command, duration):
        calls.append((command, duration))
        Path(command[-1]).write_bytes(b"localized-candidate")

    ocr_runner = _ReleasableOCRRunner()
    result = pipeline.execute_full_visual_localization(
        source,
        run_dir,
        output,
        ocr_runner=ocr_runner,
        translator=_Translator(),
        font_path=Path(r"C:\Windows\Fonts\arial.ttf"),
        render_runner=render,
    )
    assert calls and calls[0][1] == 2.0
    assert ocr_runner.release_count == 1
    assert result["quality"]["status"] == "PASS"
    assert result["summary"]["replacement_count"] == 1
    assert output.read_bytes() == b"localized-candidate"
    assert not (run_dir / "work" / "full_visual_candidate.mp4").exists()


def test_resume_full_visual_candidate_qc_reuses_rendered_candidate(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "subtitle_stage.mp4"
    source.write_bytes(b"source-stage")
    run_dir = tmp_path / "run"
    visual_dir = run_dir / "visual"
    work_dir = run_dir / "work"
    output = run_dir / "output" / "final_video.mp4"
    visual_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    candidate = work_dir / "full_visual_candidate.mp4"
    candidate.write_bytes(b"already-rendered-candidate")
    evidence = _source_evidence()
    localized = {
        **evidence,
        "translated_track_count": 1,
        "tracks": [{**evidence["tracks"][0], "target_text": "Settings"}],
    }
    plan = {
        "replacement_count": 1,
        "escalation_count": 0,
        "replacements": [{"track_id": "vtt_000001"}],
        "escalations": [],
    }
    (visual_dir / "visual_text_source.json").write_text(__import__("json").dumps(evidence), encoding="utf-8")
    (visual_dir / "visual_text_localized.json").write_text(__import__("json").dumps(localized), encoding="utf-8")
    (visual_dir / "replacement_plan.json").write_text(__import__("json").dumps(plan), encoding="utf-8")
    (visual_dir / "visual_localization.ass").write_text("[Events]\n", encoding="utf-8")

    monkeypatch.setattr(
        pipeline,
        "media_summary",
        lambda path: {"duration_seconds": 2.0, "video": {"width": 1280, "height": 720}},
    )
    analyzed = []

    def analyze(path, *_args, **_kwargs):
        analyzed.append(Path(path).name)
        return {"track_count": 0, "localizable_track_count": 0, "tracks": []}

    monkeypatch.setattr(pipeline, "analyze_whole_frame_visual_text", analyze)

    def render(_command, _duration):
        raise AssertionError("resume path must not rerender an already-complete candidate")

    result = pipeline.resume_full_visual_candidate_qc(
        source,
        run_dir,
        output,
        ocr_runner=_ReleasableOCRRunner(),
        render_runner=render,
    )

    assert analyzed == ["full_visual_candidate.mp4"]
    assert result["quality"]["status"] == "PASS"
    assert output.read_bytes() == b"already-rendered-candidate"
    assert not candidate.exists()
    assert (visual_dir / "visual_quality_evidence.json").exists()
    assert (visual_dir / "localization_summary.json").exists()


def test_full_visual_residual_qc_runs_one_bounded_repair_pass(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "subtitle_stage.mp4"
    source.write_bytes(b"source-stage")
    run_dir = tmp_path / "run"
    output = run_dir / "output" / "final_video.mp4"
    renders = []
    candidate_reads = 0
    candidate_kwargs = []

    monkeypatch.setattr(pipeline, "media_summary", lambda _path: {"duration_seconds": 2.0, "video": {"width": 1280, "height": 720}})

    def analyze(path, *_args, **_kwargs):
        nonlocal candidate_reads
        if Path(path).name == "subtitle_stage.mp4":
            return _source_evidence()
        candidate_reads += 1
        candidate_kwargs.append(dict(_kwargs))
        if candidate_reads == 1:
            return {
                "track_count": 1,
                "localizable_track_count": 1,
                "tracks": [{
                    "track_id": "residual",
                    "canonical_text": "管理员",
                    "confidence": 0.99,
                    "start_ms": 500,
                    "end_ms": 500,
                    "classification": {"action": "localize"},
                    "observations": [{"bbox": [100, 100, 220, 140], "time_ms": 500}],
                }],
            }
        return {"track_count": 0, "localizable_track_count": 0, "tracks": []}

    monkeypatch.setattr(pipeline, "analyze_whole_frame_visual_text", analyze)

    def render(command, _duration):
        renders.append(command)
        Path(command[-1]).write_bytes(f"candidate-{len(renders)}".encode())

    ocr_runner = _ReleasableOCRRunner()
    result = pipeline.execute_full_visual_localization(
        source,
        run_dir,
        output,
        ocr_runner=ocr_runner,
        translator=_Translator(),
        font_path=Path(r"C:\\Windows\\Fonts\\arial.ttf"),
        render_runner=render,
    )
    assert len(renders) == 2
    assert ocr_runner.release_count == 2
    assert candidate_reads == 2
    assert "time_windows_ms" not in candidate_kwargs[0]
    assert candidate_kwargs[1]["time_windows_ms"] == [(0, 1500)]
    assert result["quality"]["status"] == "PASS"
    assert result["quality"]["expected_localized_track_count"] == 1
    assert result["quality"]["planned_replacement_count"] == 1
    assert result["quality"]["repair_verification_mode"] == "targeted_residual_windows"
    assert result["summary"]["residual_repair_performed"] is True
    assert result["summary"]["residual_repair_mask_count"] == 1
    assert result["residual_repair"]["repair_pass_count"] == 1
    assert result["residual_repair"]["verification_mode"] == "targeted_residual_windows"
    assert result["residual_repair"]["verification_windows_ms"] == [[0, 1500]]
    assert (run_dir / "visual" / "visual_quality_first_pass.json").exists()
    assert (run_dir / "visual" / "visual_residual_repair.json").exists()
    assert output.read_bytes() == b"candidate-2"


def test_full_visual_residual_repair_fails_closed_after_one_retry(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "subtitle_stage.mp4"
    source.write_bytes(b"source-stage")
    run_dir = tmp_path / "run"
    output = run_dir / "output" / "final_video.mp4"
    renders = []

    monkeypatch.setattr(pipeline, "media_summary", lambda _path: {"duration_seconds": 2.0, "video": {"width": 1280, "height": 720}})

    def analyze(path, *_args, **_kwargs):
        if Path(path).name == "subtitle_stage.mp4":
            return _source_evidence()
        return {
            "track_count": 1,
            "localizable_track_count": 1,
            "tracks": [{
                "track_id": "residual",
                "canonical_text": "管理员",
                "confidence": 0.99,
                "start_ms": 500,
                "end_ms": 500,
                "classification": {"action": "localize"},
                "observations": [{"bbox": [100, 100, 220, 140], "time_ms": 500}],
            }],
        }

    monkeypatch.setattr(pipeline, "analyze_whole_frame_visual_text", analyze)

    def render(command, _duration):
        renders.append(command)
        Path(command[-1]).write_bytes(b"candidate")

    with pytest.raises(pipeline.VisualLocalizationError) as exc:
        pipeline.execute_full_visual_localization(
            source,
            run_dir,
            output,
            ocr_runner=lambda _paths: {},
            translator=_Translator(),
            font_path=Path(r"C:\\Windows\\Fonts\\arial.ttf"),
            render_runner=render,
        )
    assert exc.value.code == "VISUAL_LOCALIZATION_QC_FAILED"
    assert len(renders) == 2
    assert not output.exists()


def test_full_visual_escalation_fails_before_publish(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "subtitle_stage.mp4"
    source.write_bytes(b"source-stage")
    run_dir = tmp_path / "run"
    output = run_dir / "output" / "final_video.mp4"
    monkeypatch.setattr(pipeline, "media_summary", lambda _path: {"duration_seconds": 2.0, "video": {"width": 1280, "height": 720}})
    monkeypatch.setattr(pipeline, "analyze_whole_frame_visual_text", lambda *_args, **_kwargs: _source_evidence())
    monkeypatch.setattr(
        pipeline,
        "build_tier_a_replacement_plan",
        lambda *_args, **_kwargs: {
            "replacement_count": 0,
            "escalation_count": 1,
            "replacements": [],
            "escalations": [{"track_id": "vtt_000001", "reason": "unstable_geometry"}],
        },
    )
    rendered = False

    def render(_command, _duration):
        nonlocal rendered
        rendered = True

    with pytest.raises(pipeline.VisualLocalizationError, match="Tier-B") as exc:
        pipeline.execute_full_visual_localization(
            source,
            run_dir,
            output,
            ocr_runner=lambda _paths: {},
            translator=_Translator(),
            font_path=Path(r"C:\Windows\Fonts\arial.ttf"),
            render_runner=render,
        )
    assert exc.value.code == "VISUAL_REPLACEMENT_ESCALATION_REQUIRED"
    assert rendered is False
    assert not output.exists()
