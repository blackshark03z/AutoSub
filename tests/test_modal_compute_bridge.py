from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.modal_compute_bridge import ModalBridgeContractError, execute_bridge


class _Batch:
    def __init__(self, volume):
        self.volume = volume

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def put_file(self, local_path: str, remote_path: str) -> None:
        self.volume.files[remote_path] = Path(local_path).read_bytes()


class _Volume:
    def __init__(self):
        self.files = {}
        self.removed = []

    def batch_upload(self, force=False):
        assert force is True
        return _Batch(self)

    def read_file(self, path: str):
        yield self.files[path]

    def remove_file(self, path: str, recursive=False):
        assert recursive is True
        self.removed.append(path)


class _Function:
    def __init__(self, volume, *, corrupt=False):
        self.volume = volume
        self.corrupt = corrupt

    def remote(self, request):
        shard_id = request["shard"]["shard_id"]
        payload = b"remote-result"
        path = f"shards/{shard_id}/output/result.bin"
        self.volume.files[path] = payload
        digest = hashlib.sha256(payload).hexdigest()
        return {
            "provider": "modal",
            "shard_id": shard_id,
            "status": "ready",
            "cache_hit": False,
            "outputs": [{"remote_path": path, "filename": "result.bin", "sha256": ("0" * 64 if self.corrupt else digest)}],
            "timings": {"remote_seconds": 1.0},
            "provenance": {"worker_version": "test-v1", "secret": "must-not-return"},
        }


def _request(source: Path):
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    shard_id = "a" * 64
    return {
        "schema_version": 1,
        "provider": "modal",
        "shard": {
            "shard_id": shard_id,
            "workload": "ocr",
            "input_sha256": [source_sha],
            "model": "PP-OCRv6_small",
            "model_revision": "r1",
            "runtime_fingerprint": "modal:t4:test",
            "parameters": {},
        },
        "modal": {"app_name": "app", "volume_name": "vol", "environment_name": None},
        "artifacts": [{
            "index": 1,
            "local_path": str(source),
            "sha256": source_sha,
            "remote_path": f"shards/{shard_id}/input/0001-{source_sha}.jpg",
        }],
    }


def _fake_modal(volume: _Volume, *, corrupt=False):
    function = _Function(volume, corrupt=corrupt)
    return SimpleNamespace(
        Volume=SimpleNamespace(from_name=lambda *_args, **_kwargs: volume),
        Function=SimpleNamespace(from_name=lambda *_args, **_kwargs: function),
    )


def test_bridge_stages_invokes_verifies_download_and_cleans_remote(tmp_path: Path) -> None:
    source = tmp_path / "frame.jpg"
    source.write_bytes(b"frame")
    volume = _Volume()
    response = execute_bridge(_request(source), tmp_path / "out", modal_module=_fake_modal(volume))
    assert response["status"] == "ready"
    assert response["outputs"][0]["sha256"] == hashlib.sha256(b"remote-result").hexdigest()
    assert Path(response["outputs"][0]["local_path"]).read_bytes() == b"remote-result"
    assert volume.removed == ["shards/" + "a" * 64]
    assert response["provenance"].get("secret") is None
    assert response["provenance"]["cleanup_status"] == "deleted"


def test_bridge_rejects_remote_hash_mismatch_before_accepting_output(tmp_path: Path) -> None:
    source = tmp_path / "frame.jpg"
    source.write_bytes(b"frame")
    volume = _Volume()
    with pytest.raises(ModalBridgeContractError, match="remote_download_hash_mismatch"):
        execute_bridge(_request(source), tmp_path / "out", modal_module=_fake_modal(volume, corrupt=True))
    assert not (tmp_path / "out" / "result.bin").exists()


def test_bridge_rechecks_local_input_before_staging(tmp_path: Path) -> None:
    source = tmp_path / "frame.jpg"
    source.write_bytes(b"frame")
    request = _request(source)
    source.write_bytes(b"mutated")
    with pytest.raises(ModalBridgeContractError, match="local_artifact_hash_mismatch"):
        execute_bridge(request, tmp_path / "out", modal_module=_fake_modal(_Volume()))
