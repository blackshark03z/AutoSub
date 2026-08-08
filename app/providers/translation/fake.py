from app.core.provider_cache import build_request_hash, read_cached_response, write_cached_response
from app.providers.translation.base import TranslationBlockRequest, TranslationBlockResult


class FakeTranslationProvider:
    provider_name = "fake_translation"

    def __init__(self, model: str = "fake-transform-v1") -> None:
        self.model = model
        self.calls = 0

    def transform_block(self, request: TranslationBlockRequest) -> TranslationBlockResult:
        payload = _request_payload(self.provider_name, self.model, request)
        request_hash = build_request_hash(payload)
        cached = read_cached_response(self.provider_name, request_hash)
        if cached is not None:
            return TranslationBlockResult(self.provider_name, self.model, request_hash, "hit", cached)

        self.calls += 1
        response = {
            "schema_version": 1,
            "market_profile_id": request.market_profile_id,
            "segments": [
                {
                    "id": segment["id"],
                    "translated_text": f"Translation: {segment['source_text']}",
                    "spoken_text": f"Natural narration for {segment['id']}.",
                    "subtitle_text": f"Subtitle for {segment['id']}",
                    "status": "draft",
                    "issues": [],
                    "duration_budget_ms": segment["duration_budget_ms"],
                    "change_summary": "fake deterministic transform",
                }
                for segment in request.segments
            ],
            "transformation_log": [
                "fake provider preserved segment IDs and generated four-field content"
            ],
        }
        write_cached_response(self.provider_name, request_hash, response)
        return TranslationBlockResult(self.provider_name, self.model, request_hash, "miss", response)


def _request_payload(provider: str, model: str, request: TranslationBlockRequest) -> dict:
    return {
        "provider": provider,
        "model": model,
        "project_id": request.project_id,
        "market_profile_id": request.market_profile_id,
        "transformation_mode": request.transformation_mode,
        "target_locale": request.target_locale,
        "duration_budget_ms": request.duration_budget_ms,
        "segments": request.segments,
    }
