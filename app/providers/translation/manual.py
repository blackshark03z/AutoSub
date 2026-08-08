from app.core.provider_cache import build_request_hash
from app.providers.translation.base import TranslationBlockRequest, TranslationBlockResult


class ManualImportTranslationProvider:
    provider_name = "manual_import"

    def __init__(self, response: dict, model: str = "manual-import") -> None:
        self.response = response
        self.model = model

    def transform_block(self, request: TranslationBlockRequest) -> TranslationBlockResult:
        request_hash = build_request_hash(
            {
                "provider": self.provider_name,
                "model": self.model,
                "project_id": request.project_id,
                "segments": request.segments,
                "response": self.response,
            }
        )
        return TranslationBlockResult(self.provider_name, self.model, request_hash, "manual", self.response)
