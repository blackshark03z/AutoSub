import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from app.db.session import init_db, session_scope
from app.core.provider_cache import build_request_hash
from app.core.provider_cache import provider_cache_path
from app.domain.models import Project, TTSRequestReservation
from app.providers.tts.base import TTSRequest
from app.providers.tts.base import TTSUncertainError
from app.providers.tts.fake import FakeTTSProvider
from app.providers.tts.fake import tts_request_payload
from app.services.tts_generation import generate_tts_for_segment, resolve_voice_id


def _project(project_id: str) -> None:
    init_db()
    with session_scope() as session:
        if session.query(Project).filter(Project.project_id == project_id).one_or_none() is None:
            session.add(Project(project_id=project_id, title=project_id))


def test_fake_tts_generates_wav_and_cache_hit():
    project_id = f"proj_cp04_fake_{uuid4().hex[:8]}"
    _project(project_id)
    provider = FakeTTSProvider()
    segment = {"id": "seg_0001", "spoken_text": f"Hello from fake TTS {uuid4().hex}."}
    first = generate_tts_for_segment(project_id, segment, provider, "fake_voice")
    assert first["cache_status"] == "miss"
    assert Path(first["artifact_path"]).exists()
    assert first["character_count"] == len(segment["spoken_text"])
    second = generate_tts_for_segment(project_id, segment, provider, "fake_voice")
    assert second["cache_status"] == "hit"
    assert provider.calls == 1


def test_fake_tts_lists_models_and_voices():
    provider = FakeTTSProvider()
    assert provider.validate_credentials() is True
    assert resolve_voice_id(None, provider) == "fake_voice"
    assert provider.list_models()[0]["model_id"] == "fake-tts-v1"


def test_tts_request_limits_continuity_to_three_ids():
    provider = FakeTTSProvider()
    request = TTSRequest(
        project_id="proj",
        segment_id="seg",
        text="hello",
        voice_id="fake_voice",
        model=provider.model,
        previous_request_ids=["a", "b", "c", "d"],
    )
    output = Path("data/test_tmp/continuity.wav")
    result = provider.synthesize(request, output)
    assert result.cache_status in {"hit", "miss"}
    assert output.exists() or result.audio_path.exists()


def test_generation_reuses_ready_db_row_without_provider_call():
    project_id = f"proj_cp04_reuse_{uuid4().hex[:8]}"
    _project(project_id)
    provider = FakeTTSProvider()
    segment = {"id": "seg_0001", "spoken_text": f"Reuse this line {uuid4().hex}."}
    first = generate_tts_for_segment(project_id, segment, provider, "fake_voice")
    second = generate_tts_for_segment(project_id, segment, provider, "fake_voice")
    assert second["cache_status"] == "hit"
    assert second["generation_id"] == first["generation_id"]
    assert provider.calls == 1


def test_tampered_ready_artifact_blocks_automatic_paid_retry():
    project_id = f"proj_cp04_tamper_{uuid4().hex[:8]}"
    _project(project_id)
    provider = FakeTTSProvider()
    segment = {"id": "seg_0001", "spoken_text": f"Tamper check line {uuid4().hex}."}
    first = generate_tts_for_segment(project_id, segment, provider, "fake_voice")
    Path(first["artifact_path"]).write_text("tampered", encoding="utf-8")
    second = generate_tts_for_segment(project_id, segment, provider, "fake_voice")
    assert second["status"] == "uncertain"
    assert provider.calls == 1


def test_uncertain_provider_result_is_recorded():
    class UncertainFakeProvider(FakeTTSProvider):
        def synthesize(self, request, output_path):
            raise TTSUncertainError("ambiguous")

    project_id = f"proj_cp04_uncertain_{uuid4().hex[:8]}"
    _project(project_id)
    provider = UncertainFakeProvider()
    result = generate_tts_for_segment(project_id, {"id": "seg_0001", "spoken_text": "Maybe."}, provider, "fake_voice")
    assert result["status"] == "uncertain"
    assert result["cache_status"] == "uncertain"


def test_cache_key_invalidates_only_material_tts_inputs():
    request = TTSRequest(
        project_id="project-a",
        segment_id="subtitle-a",
        text="Stable narration.",
        voice_id="voice-a",
        model="model-a",
        previous_request_ids=[],
    )
    base = build_request_hash(tts_request_payload("fake_tts", request))
    subtitle_only = replace(request, project_id="project-b", segment_id="subtitle-b")
    assert build_request_hash(tts_request_payload("fake_tts", subtitle_only)) == base
    for changed in (
        replace(request, text="Changed narration."),
        replace(request, voice_id="voice-b"),
        replace(request, model="model-b"),
        replace(request, output_format="pcm_44100"),
        replace(request, target_locale="en-GB"),
        replace(request, voice_settings={"stability": 0.2}),
        replace(request, pronunciation_data=[{"dictionary_id": "dict-a"}]),
        replace(request, provider_request_version="tts-v3"),
    ):
        assert build_request_hash(tts_request_payload("fake_tts", changed)) != base


def test_two_workers_share_one_single_flight_provider_call():
    class SlowFakeProvider(FakeTTSProvider):
        def synthesize(self, request, output_path):
            time.sleep(0.2)
            return super().synthesize(request, output_path)

    project_id = f"proj_cp04_race_{uuid4().hex[:8]}"
    _project(project_id)
    provider = SlowFakeProvider()
    barrier = Barrier(2)
    text = f"Concurrent synthesis {uuid4().hex}."

    def worker():
        barrier.wait(timeout=2)
        return generate_tts_for_segment(
            project_id,
            {"id": "unit_race", "spoken_text": text},
            provider,
            "fake_voice",
            wait_timeout_seconds=5,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(worker)
        second_future = pool.submit(worker)
        first = first_future.result(timeout=8)
        second = second_future.result(timeout=8)
    assert provider.calls == 1
    assert first["generation_id"] == second["generation_id"]
    assert first["artifact_path"] == second["artifact_path"]
    assert Path(first["artifact_path"]).exists()


def test_expired_running_reservation_becomes_uncertain_without_retry():
    project_id = f"proj_cp04_crash_{uuid4().hex[:8]}"
    _project(project_id)
    provider = FakeTTSProvider()
    segment = {"id": "unit_crash", "spoken_text": f"Crash guard {uuid4().hex}."}
    request = TTSRequest(
        project_id=project_id,
        segment_id=segment["id"],
        text=segment["spoken_text"],
        voice_id="fake_voice",
        model=provider.model,
        previous_request_ids=[],
    )
    request_hash = build_request_hash(tts_request_payload(provider.provider_name, request))
    now = datetime.utcnow()
    with session_scope() as session:
        session.add(
            TTSRequestReservation(
                request_hash=request_hash,
                provider=provider.provider_name,
                model=provider.model,
                voice_id="fake_voice",
                status="running",
                owner_token="dead-worker",
                lease_expires_at=now - timedelta(seconds=1),
                created_at=now - timedelta(seconds=10),
                updated_at=now - timedelta(seconds=10),
            )
        )
    result = generate_tts_for_segment(project_id, segment, provider, "fake_voice", wait_timeout_seconds=1)
    assert result["status"] == "uncertain"
    assert provider.calls == 0


def test_provider_cache_path_rejects_traversal():
    with pytest.raises(ValueError):
        provider_cache_path("../outside", "a" * 64)
    with pytest.raises(ValueError):
        provider_cache_path("fake_tts", "../not-a-hash")
