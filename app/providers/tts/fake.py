import math
import os
import wave
from pathlib import Path

from app.core.hashing import sha256_file
from app.core.config import get_settings
from app.core.provider_cache import build_request_hash, read_cached_response, write_cached_response
from app.providers.tts.base import TTSRequest, TTSResult


class FakeTTSProvider:
    provider_name = "fake_tts"
    output_format = "mp3_44100_128"
    provider_request_version = "tts-v2"

    def __init__(self, model: str = "fake-tts-v1") -> None:
        self.model = model
        self.calls = 0

    def validate_credentials(self) -> bool:
        return True

    def list_voices(self) -> list[dict]:
        return [{"voice_id": "fake_voice", "name": "Fake Voice"}]

    def list_models(self) -> list[dict]:
        return [{"model_id": self.model, "can_do_text_to_speech": True}]

    def estimate_usage(self, request: TTSRequest) -> int:
        return len(request.text)

    def synthesize(self, request: TTSRequest, output_path: Path) -> TTSResult:
        payload = tts_request_payload(self.provider_name, request)
        request_hash = build_request_hash(payload)
        cached = read_cached_response(self.provider_name, request_hash)
        if cached is not None and _cache_artifact_valid(cached):
            return TTSResult(
                self.provider_name,
                request.model,
                request.voice_id,
                request_hash,
                "hit",
                Path(cached["audio_path"]),
                cached.get("request_id"),
                None,
                len(request.text),
            )
        self.calls += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_tone(output_path, max(0.35, min(2.0, len(request.text) / 18)))
        request_id = f"fake_req_{request_hash[:12]}"
        digest = sha256_file(output_path)
        write_cached_response(
            self.provider_name,
            request_hash,
            {
                "audio_path": str(output_path),
                "request_id": request_id,
                "character_count": len(request.text),
                "sha256": digest,
                "model": request.model,
                "voice_id": request.voice_id,
            },
        )
        return TTSResult(
            self.provider_name,
            request.model,
            request.voice_id,
            request_hash,
            "miss",
            output_path,
            request_id,
            None,
            len(request.text),
        )


def tts_request_payload(provider: str, request: TTSRequest) -> dict:
    payload = {
        "provider": provider,
        "text": request.text,
        "voice_id": request.voice_id,
        "model": request.model,
        "voice_settings": request.voice_settings,
        "output_format": request.output_format,
        "pronunciation_data": request.pronunciation_data,
        "previous_request_ids": request.previous_request_ids[-3:],
        "next_text": request.next_text,
        "target_locale": request.target_locale,
        "provider_request_version": request.provider_request_version,
    }
    if request.previous_text is not None:
        payload["previous_text"] = request.previous_text
    return payload


def _write_tone(path: Path, duration_seconds: float) -> None:
    sample_rate = 48000
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with wave.open(str(temp_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        total = int(sample_rate * duration_seconds)
        for index in range(total):
            value = int(12000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            wav.writeframesraw(value.to_bytes(2, "little", signed=True))
    os.replace(temp_path, path)


def _cache_artifact_valid(cached: dict) -> bool:
    path = Path(cached.get("audio_path", "")).resolve()
    if not path.is_relative_to(get_settings().data_dir.resolve()):
        return False
    expected = cached.get("sha256")
    return path.exists() and expected is not None and sha256_file(path) == expected
