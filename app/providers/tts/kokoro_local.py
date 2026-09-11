from __future__ import annotations

from pathlib import Path

from app.core.provider_cache import build_request_hash
from app.providers.tts.base import TTSRequest, TTSResult
from app.providers.tts.request import tts_request_payload
from app.services.kokoro_local import (
    KOKORO_MODEL_REPO,
    KokoroLocalError,
    KokoroPersistentWorkerSession,
    kokoro_status,
    list_kokoro_voices,
    synthesize_kokoro_to_file,
)


class KokoroLocalTTSProvider:
    provider_name = "kokoro_local"
    model = KOKORO_MODEL_REPO
    output_format = "wav_24000_mono_s16"
    provider_request_version = "kokoro-local-v1"

    def __init__(self, *, session: KokoroPersistentWorkerSession | None = None) -> None:
        self._session = session

    def validate_credentials(self) -> bool:
        return bool(kokoro_status().get("ready"))

    def list_voices(self) -> list[dict]:
        return list_kokoro_voices()

    def list_models(self) -> list[dict]:
        status = kokoro_status()
        return [{
            "model_id": self.model,
            "model_snapshot": status.get("model_snapshot"),
            "can_do_text_to_speech": bool(status.get("ready")),
            "local": True,
        }]

    def estimate_usage(self, request: TTSRequest) -> int:
        return len(request.text)

    def synthesize(self, request: TTSRequest, output_path: Path) -> TTSResult:
        if request.model != self.model:
            raise ValueError("Kokoro request model identity does not match provider model")
        request_hash = build_request_hash(tts_request_payload(self.provider_name, request))
        try:
            if self._session is None:
                result = synthesize_kokoro_to_file(
                    request.voice_id,
                    request.speed,
                    request.text,
                    output_path,
                )
            else:
                result = synthesize_kokoro_to_file(
                    request.voice_id,
                    request.speed,
                    request.text,
                    output_path,
                    session=self._session,
                )
        except KokoroLocalError:
            raise
        return TTSResult(
            provider=self.provider_name,
            model=self.model,
            voice_id=request.voice_id,
            request_hash=request_hash,
            cache_status="miss",
            audio_path=Path(result["audio_path"]),
            request_id=None,
            credential_ref=None,
            character_count=len(request.text),
            uncertain=False,
        )


def kokoro_alignment_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(audio_path.suffix + ".alignment.json")
