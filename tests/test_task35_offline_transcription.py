import asyncio
import json
from pathlib import Path

import pytest

from app.providers.asr.base import ASRSegment
from app.services import local_transcription
from app.services.subtitle_tracks import SubtitleContentUnavailableError
from tests.test_cp10b_simple_workflow import _make_tiny_video, _with_client, configure_test_root


class FakeLocalASRProvider:
    def __init__(self, segments=None, error=None):
        self.segments = segments or []
        self.error = error
        self.last_metadata = {
            "language": "zh",
            "language_probability": 0.99,
            "task": "translate",
        }
        self.calls = []

    def transcribe(self, audio_path, language=None, task="transcribe"):
        self.calls.append({"audio_path": str(audio_path), "language": language, "task": task})
        if self.error:
            raise self.error
        return self.segments


def _fake_segments():
    return [
        ASRSegment(start=0.05, end=0.28, text="A real opening line."),
        ASRSegment(start=0.31, end=0.62, text="The player keeps moving."),
        ASRSegment(start=0.68, end=0.94, text="The final route is clear."),
    ]


def test_task35_simple_workflow_calls_local_asr_and_persists_provenance(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", raising=False)
    monkeypatch.setenv("TOOL_AUTO_SUB_ASR_MODEL_NAME", "tiny")
    model_path = tmp_path / "model"
    model_path.mkdir()
    provider = FakeLocalASRProvider(_fake_segments())
    monkeypatch.setattr(local_transcription, "resolve_local_model_path", lambda: model_path)

    def use_fake_provider(*args, **kwargs):
        return local_transcription.ensure_local_transcription_track(
            *args,
            **kwargs,
            provider_factory=lambda _model_path: provider,
        )

    monkeypatch.setattr("app.services.simple_workflow.ensure_local_transcription_track", use_fake_provider)
    source = tmp_path / "real-content.mp4"
    _make_tiny_video(source)

    async def run(client):
        created = (await client.post("/api/simple/runs", json={"source_path": str(source)})).json()["run"]
        started = await client.post(f"/api/simple/runs/{created['run_id']}/start")
        assert started.status_code == 200
        assert started.json()["run"]["internal_state"] == "processing"
        completed = (await client.get(f"/api/simple/runs/{created['run_id']}")).json()["run"]
        assert completed["internal_state"] == "completed"
        assert completed["result_eligible"] is True
        resolved_path = Path(completed["run_directory"]) / "subtitles" / "resolved_active_track.json"
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        assert resolved["subtitle_provenance"] == "local_transcription"
        assert [cue["resolved_text"] for cue in resolved["cues"]] == [segment.text for segment in _fake_segments()]
        assert [(cue["start_ms"], cue["end_ms"]) for cue in resolved["cues"]] == [(50, 280), (310, 620), (680, 940)]
        metadata = resolved["active_track"]["metadata"]
        assert metadata["asr_provider"] == "faster_whisper"
        assert metadata["asr_model"] == local_transcription.MODEL_ID
        assert metadata["source_language"] == "zh"
        assert metadata["subtitle_language"] == "English"
        assert metadata["asr_device"] == "cpu"
        assert metadata["asr_compute_type"] == "int8"
        assert provider.calls == [
            {
                "audio_path": str(Path(completed["run_directory"]) / "work" / "source_asr_16khz_mono.wav"),
                "language": None,
                "task": "translate",
            }
        ]

    asyncio.run(_with_client(run))


def test_task35_user_import_skips_local_asr(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", raising=False)
    monkeypatch.setattr(
        "app.services.simple_workflow.ensure_local_transcription_track",
        lambda *args, **kwargs: pytest.fail("local ASR must not run for an active imported track"),
    )
    source = tmp_path / "imported.mp4"
    _make_tiny_video(source)

    async def run(client):
        created = (await client.post("/api/simple/runs", json={"source_path": str(source)})).json()["run"]
        run_id = created["run_id"]
        applied = (
            await client.post(
                f"/api/simple/runs/{run_id}/creative/import/apply",
                json={
                    "content": "First imported sentence.\nSecond imported sentence.\nThird imported sentence.",
                    "format": "txt",
                    "filename": "real-import.txt",
                    "mode": "line_by_line",
                    "track_type": "imported",
                    "fallback_policy": "block_render",
                },
            )
        ).json()
        await client.post(
            f"/api/simple/runs/{run_id}/tracks/active",
            json={"track_id": applied["track"]["track_id"], "fallback_policy": "block_render"},
        )
        started = await client.post(f"/api/simple/runs/{run_id}/start")
        assert started.status_code == 200
        resolved = json.loads(
            (Path(started.json()["run"]["run_directory"]) / "subtitles" / "resolved_active_track.json").read_text(encoding="utf-8")
        )
        assert resolved["subtitle_provenance"] == "user_import"

    asyncio.run(_with_client(run))


@pytest.mark.parametrize(
    ("provider", "message_fragment"),
    [
        (FakeLocalASRProvider([]), "Không phát hiện được lời nói"),
        (FakeLocalASRProvider(error=RuntimeError("load failed")), "Không thể nhận dạng lời nói"),
    ],
)
def test_task35_local_asr_failures_do_not_create_track(monkeypatch, tmp_path, provider, message_fragment):
    monkeypatch.setattr(local_transcription, "active_track_provenance", lambda _run_id: None)
    monkeypatch.setattr(local_transcription, "resolve_local_model_path", lambda: tmp_path / "model")
    monkeypatch.setattr(
        local_transcription,
        "extract_asr_audio",
        lambda _source, output, **kwargs: output.parent.mkdir(parents=True, exist_ok=True) or output.write_bytes(b"wav") or output,
    )
    with pytest.raises(SubtitleContentUnavailableError, match=message_fragment):
        local_transcription.ensure_local_transcription_track(
            "run_failure",
            source_path=tmp_path / "source.mp4",
            run_directory=tmp_path / "run",
            source_duration_seconds=10,
            target_language="English",
            provider_factory=lambda _model_path: provider,
        )


def test_task35_missing_model_is_blocked_before_provider_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(local_transcription, "active_track_provenance", lambda _run_id: None)
    monkeypatch.setattr(
        local_transcription,
        "extract_asr_audio",
        lambda _source, output, **kwargs: output.parent.mkdir(parents=True, exist_ok=True) or output.write_bytes(b"wav"),
    )
    monkeypatch.setattr(
        local_transcription,
        "resolve_local_model_path",
        lambda: (_ for _ in ()).throw(SubtitleContentUnavailableError(local_transcription.MODEL_MISSING_MESSAGE)),
    )

    with pytest.raises(SubtitleContentUnavailableError, match="mô hình phiên âm chất lượng"):
        local_transcription.ensure_local_transcription_track(
            "run_missing_model",
            source_path=tmp_path / "source.mp4",
            run_directory=tmp_path / "run",
            source_duration_seconds=10,
            target_language="English",
            provider_factory=lambda _model_path: pytest.fail("provider must not be created without a model"),
        )


def test_task35_render_failure_marks_run_failed(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", raising=False)
    model_path = tmp_path / "model"
    model_path.mkdir()
    provider = FakeLocalASRProvider(_fake_segments())
    monkeypatch.setattr(local_transcription, "resolve_local_model_path", lambda: model_path)
    monkeypatch.setattr(
        "app.services.simple_workflow.ensure_local_transcription_track",
        lambda *args, **kwargs: local_transcription.ensure_local_transcription_track(
            *args,
            **kwargs,
            provider_factory=lambda _model_path: provider,
        ),
    )
    monkeypatch.setattr(
        "app.services.simple_workflow._bounded_subtitle_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ffmpeg failed")),
    )
    source = tmp_path / "render-failure.mp4"
    _make_tiny_video(source)

    async def run(client):
        created = (await client.post("/api/simple/runs", json={"source_path": str(source)})).json()["run"]
        started = await client.post(f"/api/simple/runs/{created['run_id']}/start")
        assert started.status_code == 200
        assert started.json()["run"]["internal_state"] == "processing"
        failed = (await client.get(f"/api/simple/runs/{created['run_id']}")).json()["run"]
        assert failed["internal_state"] == "failed"
        assert failed["failure_category"] == "render_failed"
        assert failed["result_eligible"] is False
        assert failed["output"]["path"] is None
        log_path = Path(failed["run_directory"]) / "logs" / "simple_workflow_error.log"
        assert "RuntimeError: ffmpeg failed" in log_path.read_text(encoding="utf-8")

    asyncio.run(_with_client(run))


def test_task35_packaging_declares_runtime_model_and_license():
    requirements = Path("requirements-asr.lock.txt").read_text(encoding="utf-8")
    builder = Path("tools/build_cp13a1_complete_payload_hotfix.py").read_text(encoding="utf-8")
    license_text = Path("licenses/faster-whisper-small-MIT.txt").read_text(encoding="utf-8")

    assert "faster-whisper==1.2.1" in requirements
    assert "ctranslate2==4.8.1" in requirements
    assert "onnxruntime==1.27.0" in requirements
    assert "Pillow==12.3.0" in requirements
    assert "bundle_offline_asr(stage)" in builder
    assert "ASR_MODEL_NAME = SIMPLE_UI_MODEL_NAME" in builder
    assert "resolve_asr_model_path(ASR_MODEL_NAME)" in builder
    assert "model_directory_name(ASR_MODEL_NAME)" in builder
    assert '"offline_asr_package"' in builder
    assert '"offline_asr_runtime"' in builder
    assert '"subtitle_font_runtime"' in builder
    assert '"offline_asr_license"' in builder
    assert "MIT License" in license_text
    assert "TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES" not in builder
