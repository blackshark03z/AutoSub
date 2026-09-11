from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from app.providers.compute.base import bind_compute_artifacts, build_shard_identity
from app.providers.compute.modal_provider import (
    ModalComputeConfig,
    ModalComputeContractError,
    ModalComputeProvider,
    discover_modal_compute_config,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config(tmp_path: Path, bridge: Path) -> ModalComputeConfig:
    return ModalComputeConfig(
        python_executable=Path(sys.executable).resolve(),
        bridge_script=bridge.resolve(),
        app_name="autosub-compute-test",
        volume_name="autosub-compute-test",
        timeout_seconds=30,
        no_paid_overage_verified=False,
    )


def _write_bridge(path: Path, *, corrupt_hash: bool = False) -> None:
    path.write_text(
        """
import argparse, hashlib, json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--request'); p.add_argument('--response'); p.add_argument('--output-dir'); a=p.parse_args()
request=json.loads(Path(a.request).read_text(encoding='utf-8'))
out=Path(a.output_dir); out.mkdir(parents=True, exist_ok=True)
count=out/'bridge_invocations.txt'; count.write_text(str(int(count.read_text() if count.exists() else '0')+1), encoding='utf-8')
artifact=out/'result.bin'; artifact.write_bytes(('result:'+request['shard']['shard_id']).encode('utf-8'))
sha=hashlib.sha256(artifact.read_bytes()).hexdigest()
response={
  'provider':'modal', 'shard_id':request['shard']['shard_id'], 'status':'ready',
  'cache_hit':False,
  'outputs':[{'local_path':str(artifact), 'sha256':('0'*64 if CORRUPT else sha)}],
  'timings':{'upload_seconds':0.1,'remote_seconds':0.2,'download_seconds':0.1},
  'provenance':{'app_name':'autosub-compute-test','function_name':'ocr_shard','volume_name':'autosub-compute-test','worker_version':'test-v1','secret':'must-not-escape'}
}
Path(a.response).write_text(json.dumps(response), encoding='utf-8')
""".replace("CORRUPT", "True" if corrupt_hash else "False"),
        encoding="utf-8",
    )


def _shard_and_artifacts(tmp_path: Path):
    source = tmp_path / "frame.jpg"
    source.write_bytes(b"jpeg-fixture")
    shard = build_shard_identity(
        workload="ocr",
        input_sha256=[_sha(source)],
        model="PP-OCRv6_small",
        model_revision="rev-1",
        runtime_fingerprint="modal:t4:test-image",
        parameters={"language": "zh"},
    )
    return shard, bind_compute_artifacts(shard, [source])


def test_discovery_prefers_managed_modal_runtime_when_present(tmp_path: Path, monkeypatch) -> None:
    managed = tmp_path / "runtime" / "modal" / ".venv" / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(b"placeholder")
    monkeypatch.delenv("AUTOSUB_MODAL_PYTHON", raising=False)
    config = discover_modal_compute_config(tmp_path)
    assert config.python_executable == managed.resolve()


def test_capabilities_fail_closed_when_runtime_is_not_ready(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.py"
    bridge.write_text("print('unused')", encoding="utf-8")
    provider = ModalComputeProvider(_config(tmp_path, bridge), runtime_probe=lambda: False)
    capabilities = provider.capabilities()
    assert capabilities.available is False
    assert capabilities.reason == "modal_runtime_unavailable"
    assert capabilities.no_paid_overage_verified is False


def test_execute_verifies_outputs_and_reuses_local_verified_result(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.py"
    _write_bridge(bridge)
    provider = ModalComputeProvider(_config(tmp_path, bridge), runtime_probe=lambda: True)
    shard, artifacts = _shard_and_artifacts(tmp_path)
    output_dir = tmp_path / "outputs"

    first = provider.execute(shard, artifacts, output_dir)
    assert first.status == "ready"
    assert first.cache_hit is False
    assert len(first.output_sha256) == 1
    assert len(first.output_artifacts) == 1
    assert first.output_artifacts[0].path.read_bytes().startswith(b"result:")
    assert first.provenance.get("secret") is None
    assert (output_dir / "bridge_invocations.txt").read_text(encoding="utf-8") == "1"

    second = provider.execute(shard, artifacts, output_dir)
    assert second.status == "ready"
    assert second.cache_hit is True
    assert second.output_sha256 == first.output_sha256
    assert second.output_artifacts[0].path == first.output_artifacts[0].path
    assert second.provenance["local_result_cache"] is True
    assert (output_dir / "bridge_invocations.txt").read_text(encoding="utf-8") == "1"


def test_execute_rejects_downloaded_output_hash_mismatch(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.py"
    _write_bridge(bridge, corrupt_hash=True)
    provider = ModalComputeProvider(_config(tmp_path, bridge), runtime_probe=lambda: True)
    shard, artifacts = _shard_and_artifacts(tmp_path)
    with pytest.raises(ModalComputeContractError, match="hash mismatch"):
        provider.execute(shard, artifacts, tmp_path / "outputs")


def test_execute_rechecks_bound_input_hash_before_staging(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.py"
    _write_bridge(bridge)
    provider = ModalComputeProvider(_config(tmp_path, bridge), runtime_probe=lambda: True)
    shard, artifacts = _shard_and_artifacts(tmp_path)
    artifacts[0].path.write_bytes(b"mutated-after-bind")
    with pytest.raises(ModalComputeContractError, match="hash mismatch"):
        provider.execute(shard, artifacts, tmp_path / "outputs")
