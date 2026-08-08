from pathlib import Path
from typing import Any

from app.providers.asr.base import ASRProvider, ASRSegment


class FasterWhisperASRProvider(ASRProvider):
    def __init__(
        self,
        model_name: str | Path = "tiny",
        device: str = "cpu",
        compute_type: str = "int8",
        local_files_only: bool = True,
    ) -> None:
        from faster_whisper import WhisperModel

        self.model_name = str(model_name)
        self.device = device
        self.compute_type = compute_type
        self.local_files_only = local_files_only
        self.last_metadata: dict[str, Any] = {}
        self.model = WhisperModel(
            str(model_name),
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
        )

    def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
        task: str = "transcribe",
    ) -> list[ASRSegment]:
        if task not in {"transcribe", "translate"}:
            raise ValueError("Unsupported local ASR task.")
        segments, info = self.model.transcribe(
            str(audio_path),
            language=language,
            task=task,
            vad_filter=True,
            beam_size=5,
            word_timestamps=False,
        )
        result = [
            ASRSegment(
                start=float(segment.start),
                end=float(segment.end),
                text=segment.text.strip(),
                avg_logprob=getattr(segment, "avg_logprob", None),
                no_speech_prob=getattr(segment, "no_speech_prob", None),
            )
            for segment in segments
        ]
        self.last_metadata = {
            "language": getattr(info, "language", language),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
            "duration_after_vad": getattr(info, "duration_after_vad", None),
            "task": task,
            "model": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "local_files_only": self.local_files_only,
        }
        return result
