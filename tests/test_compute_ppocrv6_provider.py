from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.hashing import sha256_file
from app.providers.compute.base import ComputeArtifact, ComputeProviderCapabilities, ComputeShardResult
from app.providers.ocr.compute_ppocrv6_provider import (
    ComputePPocrV6ContractError,
    ComputePPocrV6Runner,
    ComputePPocrV6UnavailableError,
    PPOCRV6_SMALL_DET_REVISION,
    PPOCRV6_SMALL_MODEL,
    PPOCRV6_SMALL_REC_REVISION,
    PPOCRV6_SMALL_REVISION,
)


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
        output = Path(output_dir) / "ocr.json"
        output.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        digest = sha256_file(output)
        return ComputeShardResult(
            shard_id=shard.shard_id,
            provider="modal",
            status="ready",
            output_sha256=(digest,),
            output_artifacts=(ComputeArtifact(path=output, sha256=digest),),
            cache_hit=False,
            timings={"inference_seconds": 0.5},
            provenance={"worker_version": "test"},
        )


def _payload(names: list[str]) -> dict:
    return {
        "runtime": "modal_ppocrv6",
        "model_version": PPOCRV6_SMALL_MODEL,
        "model_revision": PPOCRV6_SMALL_REVISION,
        "frames": [
            {"name": name, "texts": [{"text": "管理员", "score": 0.98, "poly": [[1, 2], [11, 2], [11, 12], [1, 12]]}]}
            for name in names
        ],
    }


def test_compute_ppocrv6_binds_all_frame_hashes_order_and_pinned_revision(tmp_path: Path) -> None:
    first = tmp_path / "frame_00001.jpg"
    second = tmp_path / "frame_00002.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    provider = FakeComputeProvider(_payload([first.name, second.name]))
    runner = ComputePPocrV6Runner(provider, tmp_path / "out")
    result = runner([first, second])
    assert [frame["name"] for frame in result["frames"]] == [first.name, second.name]
    assert result["frames"][0]["texts"][0]["text"] == "管理员"
    shard = provider.calls[0][0]
    assert shard.model == PPOCRV6_SMALL_MODEL
    assert shard.model_revision == PPOCRV6_SMALL_REVISION
    assert shard.input_sha256 == (sha256_file(first), sha256_file(second))
    assert shard.parameters["frame_names"] == [first.name, second.name]
    assert runner.last_metadata["compute_provider"] == "modal"


def test_compute_ppocrv6_refuses_unverified_paid_overage_boundary(tmp_path: Path) -> None:
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    provider = FakeComputeProvider(_payload([frame.name]), no_overage=False)
    runner = ComputePPocrV6Runner(provider, tmp_path / "out")
    with pytest.raises(ComputePPocrV6UnavailableError):
        runner([frame])
    assert provider.calls == []


def test_compute_ppocrv6_rejects_unqualified_revision(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pinned revision"):
        ComputePPocrV6Runner(FakeComputeProvider({}), tmp_path, model_revision="mutable-main")


def test_compute_ppocrv6_rejects_frame_identity_mismatch(tmp_path: Path) -> None:
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    provider = FakeComputeProvider(_payload(["wrong.jpg"]))
    runner = ComputePPocrV6Runner(provider, tmp_path / "out")
    with pytest.raises(ComputePPocrV6ContractError, match="frame identity"):
        runner([frame])


def test_modal_ocr_worker_uses_exact_weight_snapshots() -> None:
    text = Path("tools/modal_compute_worker.py").read_text(encoding="utf-8")
    assert PPOCRV6_SMALL_DET_REVISION in text
    assert PPOCRV6_SMALL_REC_REVISION in text
    assert "text_detection_model_dir=str(det_dir)" in text
    assert "text_recognition_model_dir=str(rec_dir)" in text
    assert "unqualified OCR model revision" in text
