import asyncio
import json
from pathlib import Path

import pytest

from app.services.subtitle_tracks import SubtitleContentUnavailableError, USER_CONTENT_ERROR, validate_resolved_subtitle_content
from tests.test_cp10b_simple_workflow import _make_tiny_video, _with_client, configure_test_root


def _payload(texts, provenance="user_import"):
    return {
        "subtitle_provenance": provenance,
        "cues": [
            {"cue_id": f"CUE_{index:04d}", "start_ms": index * 300, "end_ms": (index + 1) * 300, "resolved_text": text}
            for index, text in enumerate(texts, start=1)
        ],
    }


@pytest.mark.parametrize(
    "text",
    [
        "Translation line 1.",
        " translation   LINE 2 ",
        "Original line 1.",
        "Subtitle line 7.",
        "Sample subtitle",
        "Placeholder.",
    ],
)
def test_task34_placeholder_patterns_are_rejected(text):
    result = validate_resolved_subtitle_content(_payload([text]), duration_ms=10_000)
    assert result["status"] == "FAIL"
    assert result["reason_code"] == "subtitle_content_placeholder"


def test_task34_fixture_is_allowed_only_in_explicit_test_context():
    payload = _payload(["Translation line 1."], provenance="test_fixture")
    assert validate_resolved_subtitle_content(payload, duration_ms=10_000, allow_test_fixture=True)["status"] == "PASS"
    blocked = validate_resolved_subtitle_content(payload, duration_ms=10_000, allow_test_fixture=False)
    assert blocked["status"] == "FAIL"
    assert blocked["reason_code"] == "subtitle_provenance_invalid"


def test_task34_real_imported_content_is_accepted():
    result = validate_resolved_subtitle_content(
        _payload(["A real imported cue.", "Another real cue."]),
        duration_ms=10_000,
    )
    assert result["status"] == "PASS"
    assert result["provenance"] == "user_import"


def test_task34_user_run_blocks_without_real_subtitle_source(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.delenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", raising=False)
    monkeypatch.setattr(
        "app.services.simple_workflow.ensure_local_transcription_track",
        lambda *args, **kwargs: (_ for _ in ()).throw(SubtitleContentUnavailableError(USER_CONTENT_ERROR)),
    )
    source = tmp_path / "user-video.mp4"
    _make_tiny_video(source)

    async def run(client):
        created = await client.post(
            "/api/simple/runs",
            json={
                "source_path": str(source),
                "settings": {
                    "TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES": True,
                    "caption_mode": "local_audio_transcription",
                },
            },
        )
        assert created.status_code == 200
        run_payload = created.json()["run"]
        started = await client.post(f"/api/simple/runs/{run_payload['run_id']}/start")
        assert started.status_code == 200
        assert started.json()["run"]["internal_state"] == "processing"
        status = (await client.get(f"/api/simple/runs/{run_payload['run_id']}")).json()["run"]
        assert status["internal_state"] == "blocked"
        assert status["failure_category"] == "real_subtitle_content_unavailable"
        assert status["output"]["path"] is None
        run_dir = Path(status["run_directory"])
        assert not (run_dir / "subtitles" / "dialogue_subtitles_en.ass").exists()
        assert not (run_dir / "output" / "final_video.mp4").exists()
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "blocked"
        assert manifest["provider_calls"] == {"gemini": 0, "elevenlabs": 0, "youtube": 0}

    asyncio.run(_with_client(run))
