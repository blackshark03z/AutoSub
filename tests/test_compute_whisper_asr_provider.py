from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.hashing import sha256_file
from app.providers.asr.compute_whisper_provider import (
    ComputeWhisperASRProvider,
    ComputeWhisperContractError,
    ComputeWhisperUnavailableError,
    WHISPER_TURBO_MODEL,
    WHISPER_TURBO_REVISION,
)
from app.providers.compute.base import ComputeArtifact, ComputeProviderCapabilities, ComputeShardResult


class FakeComputeProvider:
    def __init__(self, payload: dict, *, no_overage: bool = True):
        self.payload = payload
        self.no_overage = no_overage
        self.calls = []

    def capabilities(self):
        return ComputeProviderCapabilities(
            provider="modal",
            automated=True,
            commercial_use_allowed=True,
            staged_artifacts=True,
            resumable=True,
            no_paid_overage_verified=self.no_overage,
            available=True,
        )

    def execute(self, shard, artifacts, output_dir):
        self.calls.append((shard, artifacts, Path(output_dir)))
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output = Path(output_dir) / "asr.json"
        output.write_text(json.dumps(self.payload), encoding="utf-8")
        digest = sha256_file(output)
        return ComputeShardResult(
            shard_id=shard.shard_id,
            provider="modal",
            status="ready",
            output_sha256=(digest,),
            output_artifacts=(ComputeArtifact(path=output, sha256=digest),),
            cache_hit=False,
            timings={"inference_seconds": 1.25},
            provenance={"worker_version": "test"},
        )


def test_compute_whisper_binds_audio_model_revision_and_parses_timestamped_output(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav-fixture")
    provider = FakeComputeProvider({
        "model": WHISPER_TURBO_MODEL,
        "model_revision": WHISPER_TURBO_REVISION,
        "language": "zh",
        "chunks": [
            {"text": "你好", "timestamp": [0.1, 0.8]},
            {"text": "世界", "timestamp": [0.9, 1.5]},
        ],
    })
    asr = ComputeWhisperASRProvider(provider, tmp_path / "out")
    segments = asr.transcribe(audio, language="zh")
    assert [(item.start, item.end, item.text) for item in segments] == [(0.1, 0.8, "你好"), (0.9, 1.5, "世界")]
    shard = provider.calls[0][0]
    assert shard.model == WHISPER_TURBO_MODEL
    assert shard.model_revision == WHISPER_TURBO_REVISION
    assert shard.input_sha256 == (sha256_file(audio),)
    assert shard.parameters["language"] == "zh"
    assert asr.last_metadata["compute_provider"] == "modal"


def test_compute_whisper_refuses_unverified_paid_overage_boundary(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav")
    provider = FakeComputeProvider({}, no_overage=False)
    asr = ComputeWhisperASRProvider(provider, tmp_path / "out")
    with pytest.raises(ComputeWhisperUnavailableError):
        asr.transcribe(audio)
    assert provider.calls == []


def test_compute_whisper_rejects_unqualified_model_revision(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pinned revision"):
        ComputeWhisperASRProvider(
            FakeComputeProvider({}),
            tmp_path,
            model_revision="deadbeef",
        )


def test_compute_whisper_rejects_non_monotonic_provider_timestamps(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav")
    provider = FakeComputeProvider({
        "model": WHISPER_TURBO_MODEL,
        "model_revision": WHISPER_TURBO_REVISION,
        "chunks": [
            {"text": "one", "timestamp": [1.0, 2.0]},
            {"text": "two", "timestamp": [1.2, 1.8]},
        ],
    })
    asr = ComputeWhisperASRProvider(provider, tmp_path / "out")
    with pytest.raises(ComputeWhisperContractError, match="non-monotonic"):
        asr.transcribe(audio)


def test_modal_worker_passes_pinned_revision_to_model_and_processor() -> None:
    text = Path("tools/modal_compute_worker.py").read_text(encoding="utf-8")
    assert "revision=model_revision" in text
    assert "WHISPER_MODEL_REVISIONS" in text
