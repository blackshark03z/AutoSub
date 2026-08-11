import asyncio
import json
from pathlib import Path

import pytest

from app.providers.asr.base import ASRSegment
from app.services import external_transcription
from app.services.runtime_readiness import RuntimeReadinessError
from app.services.subtitle_tracks import SubtitleContentUnavailableError, list_tracks
from tests.test_cp10b_simple_workflow import _make_tiny_video, _with_client, configure_test_root


@pytest.fixture(autouse=True)
def _ready_managed_runtime(monkeypatch):
    monkeypatch.setattr(
        "app.services.simple_workflow.ensure_product_runtime_ready",
        lambda *_args, **_kwargs: {"status": "ready"},
    )


class FakeAutoSubsProvider:
    last_metadata = {"language": "zh", "model": "small", "engine_version": "3.8.0"}

    def transcribe(self, _audio_path, language=None, task="transcribe"):
        assert language is None
        assert task == "transcribe"
        return [
            ASRSegment(start=0.05, end=0.30, text="中文来源，不应被翻译。"),
            ASRSegment(start=0.35, end=0.70, text="第二句保留原文。"),
        ]


def _use_fake_external(*args, **kwargs):
    return external_transcription.ensure_external_transcription_track(
        *args,
        **kwargs,
        provider_factory=FakeAutoSubsProvider,
    )


def test_one_click_chinese_to_english_preserves_source_and_creates_translation(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", raising=False)
    monkeypatch.setattr("app.services.simple_workflow.ensure_external_transcription_track", _use_fake_external)
    monkeypatch.setattr(
        "app.services.simple_workflow.translate_source_captions",
        lambda texts: [{"translated_text": f"English {index + 1}"} for index, _text in enumerate(texts)],
    )
    source = tmp_path / "chinese-source.mp4"
    _make_tiny_video(source)

    async def run(client):
        created = (await client.post(
            "/api/simple/runs",
            json={"source_path": str(source), "settings": {"caption_mode": "external_audio_transcription", "target_language": "English"}},
        )).json()["run"]
        await client.post(f"/api/simple/runs/{created['run_id']}/start")
        completed = (await client.get(f"/api/simple/runs/{created['run_id']}")).json()["run"]
        assert completed["internal_state"] == "completed"
        assert completed["result_eligible"] is True
        resolved = json.loads((Path(completed["run_directory"]) / "subtitles" / "resolved_active_track.json").read_text(encoding="utf-8"))
        assert resolved["active_track"]["track_type"] == "translation"
        assert [cue["source_text"] for cue in resolved["cues"]] == ["中文来源，不应被翻译。", "第二句保留原文。"]
        assert [cue["translation_text"] for cue in resolved["cues"]] == ["English 1", "English 2"]
        tracks = list_tracks(created["run_id"])["tracks"]
        source_track = next(track for track in tracks if track["track_type"] == "source")
        assert source_track["metadata"]["asr_provider"] == "autosubs"
        assert [cue["source_text"] for cue in source_track["metadata"]["cues"]] == ["中文来源，不应被翻译。", "第二句保留原文。"]
        assert Path(completed["output"]["path"]).is_file()

    asyncio.run(_with_client(run))


def test_one_click_same_language_keeps_source_and_skips_translation(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", raising=False)
    monkeypatch.setattr("app.services.simple_workflow.ensure_external_transcription_track", _use_fake_external)
    monkeypatch.setattr("app.services.simple_workflow.translate_source_captions", lambda _texts: pytest.fail("same language must not translate"))
    source = tmp_path / "same-language.mp4"
    _make_tiny_video(source)

    async def run(client):
        created = (await client.post(
            "/api/simple/runs",
            json={"source_path": str(source), "settings": {"caption_mode": "external_audio_transcription", "target_language": "zh"}},
        )).json()["run"]
        await client.post(f"/api/simple/runs/{created['run_id']}/start")
        completed = (await client.get(f"/api/simple/runs/{created['run_id']}")).json()["run"]
        resolved = json.loads((Path(completed["run_directory"]) / "subtitles" / "resolved_active_track.json").read_text(encoding="utf-8"))
        assert resolved["active_track"]["track_type"] == "source"
        assert [cue["resolved_text"] for cue in resolved["cues"]] == ["中文来源，不应被翻译。", "第二句保留原文。"]

    asyncio.run(_with_client(run))


def test_external_engine_failure_blocks_export_with_actionable_message(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", raising=False)
    monkeypatch.setattr(
        "app.services.simple_workflow.ensure_external_transcription_track",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SubtitleContentUnavailableError("AutoSubs approved local model 'small' is not cached.")),
    )
    source = tmp_path / "missing-model.mp4"
    _make_tiny_video(source)

    async def run(client):
        created = (await client.post(
            "/api/simple/runs",
            json={"source_path": str(source), "settings": {"caption_mode": "external_audio_transcription"}},
        )).json()["run"]
        await client.post(f"/api/simple/runs/{created['run_id']}/start")
        failed = (await client.get(f"/api/simple/runs/{created['run_id']}")).json()["run"]
        assert failed["internal_state"] == "blocked"
        assert failed["output"]["path"] is None
        assert failed["failure_category"] == "real_subtitle_content_unavailable"
        assert failed["failure_detail"] == {
            "code": "autosubs_preflight_failed",
            "message": "AutoSubs approved local model 'small' is not cached.",
        }
        assert failed["settings"]["asr_provider"] == "autosubs"
        assert failed["settings"]["asr_model_policy"] == "autosubs_cached_model_preflight"

    asyncio.run(_with_client(run))


def test_managed_runtime_failure_is_actionable_and_does_not_look_hung(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", raising=False)
    monkeypatch.setattr(
        "app.services.simple_workflow.ensure_product_runtime_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeReadinessError("AutoSubs download failed. Check the network connection and retry.")),
    )
    source = tmp_path / "runtime-missing.mp4"
    _make_tiny_video(source)

    async def run(client):
        created = (await client.post(
            "/api/simple/runs",
            json={"source_path": str(source), "settings": {"caption_mode": "external_audio_transcription"}},
        )).json()["run"]
        await client.post(f"/api/simple/runs/{created['run_id']}/start")
        failed = (await client.get(f"/api/simple/runs/{created['run_id']}")).json()["run"]
        assert failed["internal_state"] == "blocked"
        assert failed["failure_category"] == "runtime_readiness_failed"
        assert failed["phase"] == "Preparing local runtime"
        detail = json.loads((Path(failed["run_directory"]) / "logs" / "runtime_readiness.json").read_text(encoding="utf-8"))
        assert detail["code"] == "runtime_readiness_failed"
        assert "network connection" in detail["message"]

    asyncio.run(_with_client(run))
