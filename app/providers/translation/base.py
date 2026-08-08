from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class TranslationBlockRequest:
    project_id: str
    market_profile_id: str
    transformation_mode: str
    target_locale: str
    duration_budget_ms: int
    segments: list[dict[str, Any]]
    model: str


@dataclass(frozen=True)
class TranslationBlockResult:
    provider: str
    model: str
    request_hash: str
    cache_status: str
    response: dict[str, Any]
    credential_ref: str | None = None


class TranslationProvider(Protocol):
    provider_name: str
    model: str

    def transform_block(self, request: TranslationBlockRequest) -> TranslationBlockResult:
        ...
