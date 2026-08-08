from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TTSRequest:
    project_id: str
    segment_id: str
    text: str
    voice_id: str
    model: str
    previous_request_ids: list[str]
    previous_text: str | None = None
    next_text: str | None = None
    target_locale: str = "en-US"
    output_format: str = "mp3_44100_128"
    voice_settings: dict[str, float] = field(
        default_factory=lambda: {"stability": 0.55, "similarity_boost": 0.75}
    )
    pronunciation_data: list[dict] = field(default_factory=list)
    provider_request_version: str = "tts-v2"


@dataclass(frozen=True)
class TTSResult:
    provider: str
    model: str
    voice_id: str
    request_hash: str
    cache_status: str
    audio_path: Path
    request_id: str | None
    credential_ref: str | None
    character_count: int
    uncertain: bool = False


class TTSProvider(Protocol):
    provider_name: str
    model: str

    def validate_credentials(self) -> bool:
        ...

    def list_voices(self) -> list[dict]:
        ...

    def list_models(self) -> list[dict]:
        ...

    def estimate_usage(self, request: TTSRequest) -> int:
        ...

    def synthesize(self, request: TTSRequest, output_path: Path) -> TTSResult:
        ...


class TTSRateLimitError(RuntimeError):
    pass


class TTSUncertainError(RuntimeError):
    pass


class TTSAuthenticationError(RuntimeError):
    pass


class TTSPaymentRequiredError(RuntimeError):
    pass


class TTSAuthorizationError(RuntimeError):
    pass
