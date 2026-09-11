from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.hashing import sha256_file
from app.providers.asr.base import ASRProvider, ASRSegment
from app.providers.compute.base import ComputeProvider, bind_compute_artifacts, build_shard_identity

WHISPER_TURBO_MODEL = "openai/whisper-large-v3-turbo"
WHISPER_TURBO_REVISION = "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"
WHISPER_LARGE_V3_MODEL = "openai/whisper-large-v3"
WHISPER_LARGE_V3_REVISION = "06f233fe06e710322aca913c1bc4249a0d71fce1"
WHISPER_REVISIONS = {
    WHISPER_TURBO_MODEL: WHISPER_TURBO_REVISION,
    WHISPER_LARGE_V3_MODEL: WHISPER_LARGE_V3_REVISION,
}
DEFAULT_RUNTIME_FINGERPRINT = "modal:t4:autosub-modal-worker-v1"


class ComputeWhisperUnavailableError(RuntimeError):
    pass


class ComputeWhisperContractError(RuntimeError):
    pass


class ComputeWhisperASRProvider(ASRProvider):
    """Hash-bound Whisper ASR over a qualified ComputeProvider.

    This adapter intentionally refuses cloud execution unless provider-side
    no-paid-overage protection is verified. Routing policy may decide not to use
    this provider before construction; this is a second fail-closed boundary.
    """

    def __init__(
        self,
        compute_provider: ComputeProvider,
        output_dir: Path,
        *,
        model: str = WHISPER_TURBO_MODEL,
        model_revision: str | None = None,
        runtime_fingerprint: str = DEFAULT_RUNTIME_FINGERPRINT,
    ) -> None:
        if model not in WHISPER_REVISIONS:
            raise ValueError(f"unsupported qualified Whisper model: {model}")
        expected_revision = WHISPER_REVISIONS[model]
        revision = str(model_revision or expected_revision).strip()
        if revision != expected_revision:
            raise ValueError("Whisper model revision must match the qualified pinned revision")
        self.compute_provider = compute_provider
        self.output_dir = Path(output_dir).resolve()
        self.model = model
        self.model_revision = revision
        self.runtime_fingerprint = str(runtime_fingerprint).strip()
        if not self.runtime_fingerprint:
            raise ValueError("runtime_fingerprint is required")
        self.last_metadata: dict[str, Any] = {}

    def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
        task: str = "transcribe",
    ) -> list[ASRSegment]:
        if task != "transcribe":
            raise ValueError("Compute Whisper source ASR only supports task=transcribe")
        source = Path(audio_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        capabilities = self.compute_provider.capabilities()
        if not (
            capabilities.available
            and capabilities.automated
            and capabilities.commercial_use_allowed
            and capabilities.staged_artifacts
            and capabilities.resumable
            and capabilities.no_paid_overage_verified
        ):
            raise ComputeWhisperUnavailableError(capabilities.reason or "cloud_compute_not_safely_qualified")

        normalized_language = None if not language or str(language).strip().lower() == "auto" else str(language).strip()
        source_sha = sha256_file(source).lower()
        shard = build_shard_identity(
            workload="asr",
            input_sha256=[source_sha],
            model=self.model,
            model_revision=self.model_revision,
            runtime_fingerprint=self.runtime_fingerprint,
            parameters={"language": normalized_language, "task": "transcribe", "timestamps": True},
        )
        artifacts = bind_compute_artifacts(shard, [source])
        result = self.compute_provider.execute(shard, artifacts, self.output_dir / shard.shard_id)
        if result.status != "ready" or len(result.output_artifacts) != 1:
            raise ComputeWhisperContractError("compute provider did not return one ready ASR artifact")
        artifact = result.output_artifacts[0]
        if sha256_file(artifact.path).lower() != artifact.sha256.lower():
            raise ComputeWhisperContractError("accepted ASR artifact hash changed after provider verification")
        try:
            payload = json.loads(Path(artifact.path).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ComputeWhisperContractError("ASR output artifact is unreadable") from exc
        if (
            not isinstance(payload, dict)
            or str(payload.get("model") or "") != self.model
            or str(payload.get("model_revision") or "") != self.model_revision
        ):
            raise ComputeWhisperContractError("ASR output model identity mismatch")

        segments: list[ASRSegment] = []
        last_end = 0.0
        for item in list(payload.get("chunks") or []):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            timestamp = item.get("timestamp") or item.get("timestamps")
            if not text or not isinstance(timestamp, (list, tuple)) or len(timestamp) != 2:
                continue
            try:
                start = float(timestamp[0])
                end = float(timestamp[1])
            except (TypeError, ValueError):
                continue
            if start < 0 or end <= start or start + 0.05 < last_end:
                raise ComputeWhisperContractError("ASR output contains invalid or non-monotonic timestamps")
            segments.append(ASRSegment(start=start, end=end, text=text))
            last_end = end
        if not segments:
            raise ComputeWhisperContractError("ASR output contains no usable timestamped speech segments")

        self.last_metadata = {
            "language": str(payload.get("language") or normalized_language or "unknown"),
            "asr_provider": "whisper_compute",
            "asr_engine": "Whisper",
            "engine_version": "transformers",
            "model": self.model,
            "model_revision": self.model_revision,
            "compute_provider": result.provider,
            "compute_shard_id": result.shard_id,
            "cache_hit": bool(result.cache_hit),
            "timings": dict(result.timings),
            "runtime_fingerprint": self.runtime_fingerprint,
            "output_sha256": artifact.sha256,
        }
        return segments
