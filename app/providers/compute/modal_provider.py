from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.hashing import sha256_file
from app.providers.compute.base import (
    ComputeArtifact,
    ComputeProviderCapabilities,
    ComputeShard,
    ComputeShardResult,
)


class ModalComputeUnavailableError(RuntimeError):
    pass


class ModalComputeContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModalComputeConfig:
    python_executable: Path
    bridge_script: Path
    app_name: str = "autosub-compute"
    volume_name: str = "autosub-compute"
    environment_name: str | None = None
    timeout_seconds: int = 1800
    no_paid_overage_verified: bool = False


def discover_modal_compute_config(root: Path) -> ModalComputeConfig:
    root = Path(root).resolve()
    python_value = os.environ.get("AUTOSUB_MODAL_PYTHON", "").strip()
    managed_python = root / "runtime" / "modal" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python_value:
        python_executable = Path(python_value).expanduser().resolve()
    elif managed_python.is_file():
        python_executable = managed_python.resolve()
    else:
        python_executable = Path(sys.executable).resolve()
    bridge_value = os.environ.get("AUTOSUB_MODAL_BRIDGE", "").strip()
    bridge_script = Path(bridge_value).expanduser().resolve() if bridge_value else (root / "tools" / "modal_compute_bridge.py").resolve()
    timeout_value = os.environ.get("AUTOSUB_MODAL_TIMEOUT_SECONDS", "1800").strip()
    try:
        timeout_seconds = max(30, min(int(timeout_value), 7200))
    except ValueError:
        timeout_seconds = 1800
    environment_name = os.environ.get("AUTOSUB_MODAL_ENVIRONMENT", "").strip() or None
    return ModalComputeConfig(
        python_executable=python_executable,
        bridge_script=bridge_script,
        app_name=os.environ.get("AUTOSUB_MODAL_APP", "autosub-compute").strip() or "autosub-compute",
        volume_name=os.environ.get("AUTOSUB_MODAL_VOLUME", "autosub-compute").strip() or "autosub-compute",
        environment_name=environment_name,
        timeout_seconds=timeout_seconds,
        no_paid_overage_verified=os.environ.get("AUTOSUB_MODAL_NO_PAID_OVERAGE_VERIFIED", "").strip() == "1",
    )


class ModalComputeProvider:
    provider_name = "modal"

    def __init__(
        self,
        config: ModalComputeConfig,
        *,
        runtime_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self._runtime_probe = runtime_probe

    def capabilities(self) -> ComputeProviderCapabilities:
        reason = None
        available = True
        if not self.config.python_executable.is_file():
            available = False
            reason = "modal_python_missing"
        elif not self.config.bridge_script.is_file():
            available = False
            reason = "modal_bridge_missing"
        elif not self._modal_runtime_ready():
            available = False
            reason = "modal_runtime_unavailable"
        return ComputeProviderCapabilities(
            provider=self.provider_name,
            automated=True,
            commercial_use_allowed=True,
            staged_artifacts=True,
            resumable=True,
            no_paid_overage_verified=bool(self.config.no_paid_overage_verified),
            available=available,
            reason=reason,
        )

    def execute(
        self,
        shard: ComputeShard,
        artifacts: tuple[ComputeArtifact, ...],
        output_dir: Path,
    ) -> ComputeShardResult:
        capabilities = self.capabilities()
        if not capabilities.available:
            raise ModalComputeUnavailableError(capabilities.reason or "modal_unavailable")
        verified_artifacts = self._verify_artifacts(shard, artifacts)
        output_root = Path(output_dir).resolve()
        output_root.mkdir(parents=True, exist_ok=True)

        cached = self._load_verified_local_manifest(shard, output_root)
        if cached is not None:
            return cached

        with tempfile.TemporaryDirectory(prefix="autosub-modal-control-", dir=str(output_root)) as temp_value:
            control_dir = Path(temp_value)
            request_path = control_dir / "request.json"
            response_path = control_dir / "response.json"
            request_path.write_text(
                json.dumps(
                    self._request_payload(shard, verified_artifacts),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            command = [
                str(self.config.python_executable),
                str(self.config.bridge_script),
                "--request",
                str(request_path),
                "--response",
                str(response_path),
                "--output-dir",
                str(output_root),
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.config.timeout_seconds,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ModalComputeUnavailableError("modal_bridge_start_failed") from exc
            if completed.returncode != 0:
                raise ModalComputeUnavailableError(
                    f"modal_bridge_failed:{_safe_process_detail(completed.stderr or completed.stdout)}"
                )
            if not response_path.is_file():
                raise ModalComputeContractError("modal bridge did not write a response manifest")
            try:
                response = json.loads(response_path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ModalComputeContractError("modal bridge response is unreadable") from exc

        result, local_manifest = self._validate_response(shard, output_root, response)
        self._manifest_path(shard, output_root).write_text(
            json.dumps(local_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return result

    def _modal_runtime_ready(self) -> bool:
        if self._runtime_probe is not None:
            try:
                return bool(self._runtime_probe())
            except Exception:
                return False
        try:
            completed = subprocess.run(
                [str(self.config.python_executable), "-c", "import modal; print('ready')"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0 and "ready" in completed.stdout

    def _verify_artifacts(
        self,
        shard: ComputeShard,
        artifacts: tuple[ComputeArtifact, ...],
    ) -> tuple[ComputeArtifact, ...]:
        if len(artifacts) != len(shard.input_sha256):
            raise ModalComputeContractError("artifact count does not match shard identity")
        verified: list[ComputeArtifact] = []
        for artifact, expected in zip(artifacts, shard.input_sha256):
            path = Path(artifact.path).resolve()
            if not path.is_file():
                raise ModalComputeContractError("bound compute artifact is missing")
            actual = sha256_file(path).lower()
            if actual != str(artifact.sha256).lower() or actual != str(expected).lower():
                raise ModalComputeContractError("bound compute artifact hash mismatch")
            verified.append(ComputeArtifact(path=path, sha256=actual))
        return tuple(verified)

    def _request_payload(
        self,
        shard: ComputeShard,
        artifacts: tuple[ComputeArtifact, ...],
    ) -> dict[str, Any]:
        remote_inputs = []
        for index, artifact in enumerate(artifacts, start=1):
            suffix = artifact.path.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                suffix = ".bin"
            remote_inputs.append(
                {
                    "index": index,
                    "local_path": str(artifact.path),
                    "logical_name": artifact.path.name,
                    "sha256": artifact.sha256,
                    "remote_path": f"shards/{shard.shard_id}/input/{index:04d}-{artifact.sha256}{suffix}",
                }
            )
        return {
            "schema_version": 1,
            "provider": self.provider_name,
            "shard": {
                "shard_id": shard.shard_id,
                "workload": shard.workload,
                "input_sha256": list(shard.input_sha256),
                "model": shard.model,
                "model_revision": shard.model_revision,
                "runtime_fingerprint": shard.runtime_fingerprint,
                "parameters": shard.parameters,
            },
            "modal": {
                "app_name": self.config.app_name,
                "volume_name": self.config.volume_name,
                "environment_name": self.config.environment_name,
            },
            "artifacts": remote_inputs,
        }

    def _validate_response(
        self,
        shard: ComputeShard,
        output_root: Path,
        response: Any,
    ) -> tuple[ComputeShardResult, dict[str, Any]]:
        if not isinstance(response, dict):
            raise ModalComputeContractError("modal bridge response must be an object")
        if response.get("provider") != self.provider_name or response.get("shard_id") != shard.shard_id:
            raise ModalComputeContractError("modal bridge response identity mismatch")
        if response.get("status") != "ready":
            raise ModalComputeUnavailableError(str(response.get("reason") or "modal_shard_not_ready"))
        outputs = response.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise ModalComputeContractError("modal bridge returned no output artifacts")

        verified_outputs: list[dict[str, str]] = []
        output_hashes: list[str] = []
        output_artifacts: list[ComputeArtifact] = []
        for index, item in enumerate(outputs, start=1):
            if not isinstance(item, dict):
                raise ModalComputeContractError("modal output entry must be an object")
            declared = str(item.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", declared):
                raise ModalComputeContractError("modal output hash is invalid")
            path = Path(str(item.get("local_path") or "")).resolve()
            try:
                path.relative_to(output_root)
            except ValueError as exc:
                raise ModalComputeContractError("modal output escaped the requested output directory") from exc
            if not path.is_file():
                raise ModalComputeContractError("modal output artifact is missing after download")
            actual = sha256_file(path).lower()
            if actual != declared:
                raise ModalComputeContractError("modal output artifact hash mismatch")
            verified_outputs.append({"filename": path.name, "sha256": actual})
            output_hashes.append(actual)
            output_artifacts.append(ComputeArtifact(path=path, sha256=actual))

        timings = _numeric_timings(response.get("timings"))
        safe_provenance = _safe_provenance(response.get("provenance"))
        remote_cache_hit = bool(response.get("cache_hit"))
        safe_provenance["remote_cache_hit"] = remote_cache_hit
        local_manifest = {
            "schema_version": 1,
            "provider": self.provider_name,
            "shard_id": shard.shard_id,
            "workload": shard.workload,
            "input_sha256": list(shard.input_sha256),
            "status": "ready",
            "outputs": verified_outputs,
            "timings": timings,
            "provenance": safe_provenance,
        }
        return (
            ComputeShardResult(
                shard_id=shard.shard_id,
                provider=self.provider_name,
                status="ready",
                output_sha256=tuple(output_hashes),
                output_artifacts=tuple(output_artifacts),
                cache_hit=remote_cache_hit,
                timings=timings,
                provenance=safe_provenance,
            ),
            local_manifest,
        )

    def _load_verified_local_manifest(
        self,
        shard: ComputeShard,
        output_root: Path,
    ) -> ComputeShardResult | None:
        path = self._manifest_path(shard, output_root)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("provider") != self.provider_name
            or payload.get("shard_id") != shard.shard_id
            or payload.get("workload") != shard.workload
            or list(payload.get("input_sha256") or []) != list(shard.input_sha256)
            or payload.get("status") != "ready"
        ):
            return None
        outputs = payload.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            return None
        hashes: list[str] = []
        output_artifacts: list[ComputeArtifact] = []
        for item in outputs:
            if not isinstance(item, dict):
                return None
            filename = str(item.get("filename") or "")
            declared = str(item.get("sha256") or "").lower()
            if not filename or Path(filename).name != filename or not re.fullmatch(r"[0-9a-f]{64}", declared):
                return None
            artifact_path = (output_root / filename).resolve()
            try:
                artifact_path.relative_to(output_root)
            except ValueError:
                return None
            if not artifact_path.is_file() or sha256_file(artifact_path).lower() != declared:
                return None
            hashes.append(declared)
            output_artifacts.append(ComputeArtifact(path=artifact_path, sha256=declared))
        provenance = _safe_provenance(payload.get("provenance"))
        provenance["local_result_cache"] = True
        return ComputeShardResult(
            shard_id=shard.shard_id,
            provider=self.provider_name,
            status="ready",
            output_sha256=tuple(hashes),
            output_artifacts=tuple(output_artifacts),
            cache_hit=True,
            timings=_numeric_timings(payload.get("timings")),
            provenance=provenance,
        )

    @staticmethod
    def _manifest_path(shard: ComputeShard, output_root: Path) -> Path:
        return output_root / f"{shard.shard_id}.modal-result.json"


def _numeric_timings(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or isinstance(item, bool):
            continue
        try:
            parsed = float(item)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            result[key] = parsed
    return result


def _safe_provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "app_name",
        "function_name",
        "volume_name",
        "environment_name",
        "runtime_fingerprint",
        "model_revision",
        "cleanup_status",
        "call_id",
        "worker_version",
    }
    safe: dict[str, Any] = {}
    for key in allowed:
        item = value.get(key)
        if item is None or isinstance(item, (str, int, float, bool)):
            safe[key] = item
    return safe


def _safe_process_detail(value: str) -> str:
    detail = " ".join(str(value or "").split())
    detail = re.sub(r"(?i)(token|secret|key|password)=[^\s]+", r"\1=[redacted]", detail)
    return detail[:240] if detail else "no diagnostic detail"
