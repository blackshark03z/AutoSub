from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.full_visual_pipeline as pipeline


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

    result = pipeline.execute_full_visual_localization(
        source,
        run_dir,
        output,
        ocr_runner=lambda _paths: {},
        translator=_Translator(),
        font_path=Path(r"C:\Windows\Fonts\arial.ttf"),
        render_runner=render,
    )
    assert calls and calls[0][1] == 2.0
    assert result["quality"]["status"] == "PASS"
    assert result["summary"]["replacement_count"] == 1
    assert output.read_bytes() == b"localized-candidate"
    assert not (run_dir / "work" / "full_visual_candidate.mp4").exists()


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
