import json
from pathlib import Path

from app.core.hashing import sha256_file
from app.core.provider_cache import build_request_hash
from app.providers.tts.base import TTSRequest
from app.providers.tts.kokoro_local import KokoroLocalTTSProvider
from app.providers.tts.request import tts_request_payload
from app.services.tts_generation import _alignment_metadata_for_audio


def test_kokoro_provider_binds_request_voice_speed_and_text(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_synthesize(voice_id, speed, text, output_path):
        captured.update(voice_id=voice_id, speed=speed, text=text, output_path=output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFF-kokoro-test")
        return {"audio_path": str(output_path)}

    monkeypatch.setattr("app.providers.tts.kokoro_local.synthesize_kokoro_to_file", fake_synthesize)
    provider = KokoroLocalTTSProvider()
    request = TTSRequest(
        project_id="project",
        segment_id="unit",
        text="The selected voice must be immutable.",
        voice_id="bf_emma",
        model=provider.model,
        previous_request_ids=[],
        output_format=provider.output_format,
        speed=1.1,
        provider_request_version=provider.provider_request_version,
    )
    output = tmp_path / "narration.wav"
    result = provider.synthesize(request, output)
    assert captured == {
        "voice_id": "bf_emma",
        "speed": 1.1,
        "text": request.text,
        "output_path": output,
    }
    assert result.voice_id == "bf_emma"
    assert result.request_hash == build_request_hash(tts_request_payload("kokoro_local", request))
    assert result.audio_path == output


def test_alignment_sidecar_is_accepted_only_when_bound_to_audio_hash(tmp_path) -> None:
    audio = tmp_path / "unit.wav"
    audio.write_bytes(b"RIFF-audio")
    digest = sha256_file(audio)
    sidecar = audio.with_suffix(".wav.alignment.json")
    sidecar.write_text(json.dumps({
        "audio_sha256": digest,
        "identity_sha256": "a" * 64,
        "duration_seconds": 1.25,
        "sample_rate": 24000,
        "tokens": [{"text": "Hello", "start": 0.1, "end": 0.5}],
    }), encoding="utf-8")
    accepted = _alignment_metadata_for_audio(audio, digest)
    assert accepted is not None
    assert accepted["sample_rate"] == 24000
    assert accepted["tokens"][0]["text"] == "Hello"

    sidecar.write_text(json.dumps({"audio_sha256": "b" * 64, "tokens": []}), encoding="utf-8")
    assert _alignment_metadata_for_audio(audio, digest) is None


def test_kokoro_provider_routes_synthesis_through_configured_persistent_session(monkeypatch, tmp_path) -> None:
    captured = {}
    session = object()

    def fake_synthesize(voice_id, speed, text, output_path, *, session=None):
        captured.update(voice_id=voice_id, speed=speed, text=text, output_path=output_path, session=session)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFF-kokoro-session")
        return {"audio_path": str(output_path)}

    monkeypatch.setattr("app.providers.tts.kokoro_local.synthesize_kokoro_to_file", fake_synthesize)
    provider = KokoroLocalTTSProvider(session=session)
    request = TTSRequest(
        project_id="project",
        segment_id="unit",
        text="Keep the same model warm across narration units.",
        voice_id="bm_george",
        model=provider.model,
        previous_request_ids=[],
        output_format=provider.output_format,
        speed=1.0,
        provider_request_version=provider.provider_request_version,
    )
    output = tmp_path / "narration.wav"
    provider.synthesize(request, output)
    assert captured["session"] is session
    assert captured["voice_id"] == "bm_george"
    assert captured["text"] == request.text
