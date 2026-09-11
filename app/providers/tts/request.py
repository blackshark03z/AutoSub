from __future__ import annotations

from app.providers.tts.base import TTSRequest


def tts_request_payload(provider: str, request: TTSRequest) -> dict:
    payload = {
        "provider": provider,
        "text": request.text,
        "voice_id": request.voice_id,
        "model": request.model,
        "voice_settings": request.voice_settings,
        "output_format": request.output_format,
        "speed": request.speed,
        "pronunciation_data": request.pronunciation_data,
        "previous_request_ids": request.previous_request_ids[-3:],
        "next_text": request.next_text,
        "target_locale": request.target_locale,
        "provider_request_version": request.provider_request_version,
    }
    if request.previous_text is not None:
        payload["previous_text"] = request.previous_text
    return payload
