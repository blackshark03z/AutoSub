from pathlib import Path

from app.providers.asr.base import ASRProvider, ASRSegment


class FakeASRProvider(ASRProvider):
    def __init__(self, music_only: bool = False) -> None:
        self.music_only = music_only

    def transcribe(
        self,
        audio_path: Path,
        language: str | None = "zh",
        task: str = "transcribe",
    ) -> list[ASRSegment]:
        if self.music_only:
            return [ASRSegment(start=0.0, end=10.0, text="", avg_logprob=-3.0, no_speech_prob=0.98)]
        return [
            ASRSegment(start=0.5, end=4.0, text="这里有一个新的 Roblox 挑战。", avg_logprob=-0.2, no_speech_prob=0.05),
            ASRSegment(start=4.4, end=8.2, text="玩家需要快速找到隐藏道具。", avg_logprob=-0.25, no_speech_prob=0.04),
            ASRSegment(start=8.8, end=13.0, text="最后的路线非常危险。", avg_logprob=-0.3, no_speech_prob=0.06),
        ]
