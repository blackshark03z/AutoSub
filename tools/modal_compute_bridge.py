from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


_FUNCTIONS_BY_WORKLOAD = {
    "asr": "asr_shard",
    "ocr": "ocr_shard",
    "inpainting": "inpainting_shard",
}


class ModalBridgeContractError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_request(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("provider") != "modal":
        raise ModalBridgeContractError("invalid_request")
    shard = payload.get("shard")
    modal_config = payload.get("modal")
    artifacts = payload.get("artifacts")
    if not isinstance(shard, dict) or not isinstance(modal_config, dict) or not isinstance(artifacts, list):
        raise ModalBridgeContractError("invalid_request_shape")
    shard_id = str(shard.get("shard_id") or "")
    workload = str(shard.get("workload") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", shard_id) or workload not in _FUNCTIONS_BY_WORKLOAD:
        raise ModalBridgeContractError("invalid_shard_identity")
    return payload


def _safe_filename(value: Any, index: int) -> str:
    name = Path(str(value or "")).name
    if not name or name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]{1,180}", name):
        return f"modal_output_{index:04d}.bin"
    return name


def _modal_handle_kwargs(environment_name: Any) -> dict[str, str]:
    value = str(environment_name or "").strip()
    return {"environment_name": value} if value else {}


def _remote_result_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ModalBridgeContractError("remote_result_not_object")
    return value


def execute_bridge(
    request: dict[str, Any],
    output_dir: Path,
    *,
    modal_module: Any | None = None,
) -> dict[str, Any]:
    if modal_module is None:
        import modal as modal_module  # type: ignore[import-not-found]

    shard = request["shard"]
    modal_config = request["modal"]
    artifacts = request["artifacts"]
    shard_id = str(shard["shard_id"])
    workload = str(shard["workload"])
    function_name = _FUNCTIONS_BY_WORKLOAD[workload]
    app_name = str(modal_config.get("app_name") or "autosub-compute")
    volume_name = str(modal_config.get("volume_name") or "autosub-compute")
    environment_name = modal_config.get("environment_name")
    handle_kwargs = _modal_handle_kwargs(environment_name)
    volume = modal_module.Volume.from_name(volume_name, create_if_missing=False, **handle_kwargs)

    verified_remote_artifacts: list[dict[str, Any]] = []
    with volume.batch_upload(force=True) as batch:
        for index, item in enumerate(artifacts, start=1):
            if not isinstance(item, dict):
                raise ModalBridgeContractError("artifact_not_object")
            local_path = Path(str(item.get("local_path") or "")).resolve()
            declared = str(item.get("sha256") or "").lower()
            remote_path = str(item.get("remote_path") or "").replace("\\", "/").lstrip("/")
            if not local_path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", declared):
                raise ModalBridgeContractError("invalid_local_artifact")
            if _sha256_file(local_path) != declared:
                raise ModalBridgeContractError("local_artifact_hash_mismatch")
            expected_prefix = f"shards/{shard_id}/input/"
            if not remote_path.startswith(expected_prefix) or ".." in Path(remote_path).parts:
                raise ModalBridgeContractError("invalid_remote_input_path")
            batch.put_file(str(local_path), remote_path)
            verified_remote_artifacts.append(
                {
                    "index": int(item.get("index") or index),
                    "logical_name": _safe_filename(item.get("logical_name") or local_path.name, index),
                    "sha256": declared,
                    "remote_path": remote_path,
                }
            )

    function = modal_module.Function.from_name(app_name, function_name, **handle_kwargs)
    remote_request = {
        "schema_version": 1,
        "provider": "modal",
        "shard": shard,
        "volume_name": volume_name,
        "artifacts": verified_remote_artifacts,
    }
    remote = _remote_result_object(function.remote(remote_request))
    if remote.get("provider") != "modal" or remote.get("shard_id") != shard_id:
        raise ModalBridgeContractError("remote_identity_mismatch")
    if remote.get("status") != "ready":
        return {
            "provider": "modal",
            "shard_id": shard_id,
            "status": "failed",
            "reason": str(remote.get("reason") or "remote_shard_not_ready"),
            "outputs": [],
            "timings": remote.get("timings") if isinstance(remote.get("timings"), dict) else {},
            "provenance": {
                "app_name": app_name,
                "function_name": function_name,
                "volume_name": volume_name,
                "environment_name": environment_name,
                "cleanup_status": "deferred_after_failure",
            },
        }

    remote_outputs = remote.get("outputs")
    if not isinstance(remote_outputs, list) or not remote_outputs:
        raise ModalBridgeContractError("remote_outputs_missing")
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict[str, Any]] = []
    for index, item in enumerate(remote_outputs, start=1):
        if not isinstance(item, dict):
            raise ModalBridgeContractError("remote_output_not_object")
        remote_path = str(item.get("remote_path") or "").replace("\\", "/").lstrip("/")
        declared = str(item.get("sha256") or "").lower()
        if not remote_path.startswith(f"shards/{shard_id}/output/") or ".." in Path(remote_path).parts:
            raise ModalBridgeContractError("invalid_remote_output_path")
        if not re.fullmatch(r"[0-9a-f]{64}", declared):
            raise ModalBridgeContractError("invalid_remote_output_hash")
        payload = b"".join(volume.read_file(remote_path))
        if _sha256_bytes(payload) != declared:
            raise ModalBridgeContractError("remote_download_hash_mismatch")
        filename = _safe_filename(item.get("filename") or Path(remote_path).name, index)
        local_path = (output_root / filename).resolve()
        try:
            local_path.relative_to(output_root)
        except ValueError as exc:
            raise ModalBridgeContractError("local_output_escape") from exc
        temporary = local_path.with_name(f".{local_path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(payload)
        if _sha256_file(temporary) != declared:
            temporary.unlink(missing_ok=True)
            raise ModalBridgeContractError("local_output_hash_mismatch")
        os.replace(temporary, local_path)
        downloaded.append(
            {
                "local_path": str(local_path),
                "sha256": declared,
                "remote_path": remote_path,
                "filename": filename,
            }
        )

    cleanup_status = "deleted"
    try:
        volume.remove_file(f"shards/{shard_id}", recursive=True)
    except Exception:
        cleanup_status = "cleanup_failed"

    remote_provenance = remote.get("provenance") if isinstance(remote.get("provenance"), dict) else {}
    return {
        "provider": "modal",
        "shard_id": shard_id,
        "status": "ready",
        "cache_hit": bool(remote.get("cache_hit")),
        "outputs": downloaded,
        "timings": remote.get("timings") if isinstance(remote.get("timings"), dict) else {},
        "provenance": {
            "app_name": app_name,
            "function_name": function_name,
            "volume_name": volume_name,
            "environment_name": environment_name,
            "runtime_fingerprint": shard.get("runtime_fingerprint"),
            "model_revision": shard.get("model_revision"),
            "worker_version": remote_provenance.get("worker_version"),
            "call_id": remote_provenance.get("call_id"),
            "cleanup_status": cleanup_status,
        },
    }


def _write_response(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    response_path = Path(args.response).resolve()
    request: dict[str, Any] | None = None
    try:
        request = _load_request(Path(args.request).resolve())
        response = execute_bridge(request, Path(args.output_dir).resolve())
    except Exception as exc:
        shard = request.get("shard") if isinstance(request, dict) and isinstance(request.get("shard"), dict) else {}
        response = {
            "provider": "modal",
            "shard_id": shard.get("shard_id"),
            "status": "failed",
            "reason": type(exc).__name__,
            "outputs": [],
            "timings": {},
            "provenance": {"cleanup_status": "unknown"},
        }
    _write_response(response_path, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
