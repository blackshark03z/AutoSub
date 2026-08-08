from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ASRSegment:
    start: float
    end: float
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


class ASRProvider:
    def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
        task: str = "transcribe",
    ) -> list[ASRSegment]:
        raise NotImplementedError
