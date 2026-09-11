from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ComputeProviderCapabilities:
    provider: str
    automated: bool
    commercial_use_allowed: bool
    staged_artifacts: bool
    resumable: bool
    no_paid_overage_verified: bool
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class ComputeShard:
    shard_id: str
    workload: str
    input_sha256: tuple[str, ...]
    model: str
    model_revision: str
    runtime_fingerprint: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ComputeArtifact:
    path: Path
    sha256: str


@dataclass(frozen=True)
class ComputeShardResult:
    shard_id: str
    provider: str
    status: str
    output_sha256: tuple[str, ...]
    output_artifacts: tuple[ComputeArtifact, ...]
    cache_hit: bool
    timings: dict[str, float]
    provenance: dict[str, Any]


class ComputeProvider(Protocol):
    def capabilities(self) -> ComputeProviderCapabilities:
        ...

    def execute(
        self,
        shard: ComputeShard,
        artifacts: tuple[ComputeArtifact, ...],
        output_dir: Path,
    ) -> ComputeShardResult:
        ...


def bind_compute_artifacts(
    shard: ComputeShard,
    paths: list[Path] | tuple[Path, ...],
) -> tuple[ComputeArtifact, ...]:
    from app.core.hashing import sha256_file

    resolved = tuple(Path(path).resolve() for path in paths)
    if len(resolved) != len(shard.input_sha256):
        raise ValueError("compute artifact count does not match shard identity")
    artifacts: list[ComputeArtifact] = []
    for path, expected in zip(resolved, shard.input_sha256):
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256_file(path).lower()
        if actual != expected.lower():
            raise ValueError("compute artifact hash does not match shard identity")
        artifacts.append(ComputeArtifact(path=path, sha256=actual))
    return tuple(artifacts)


def build_shard_identity(
    *,
    workload: str,
    input_sha256: list[str] | tuple[str, ...],
    model: str,
    model_revision: str,
    runtime_fingerprint: str,
    parameters: dict[str, Any] | None = None,
) -> ComputeShard:
    inputs = tuple(str(item).strip().lower() for item in input_sha256)
    if not workload.strip():
        raise ValueError("workload is required")
    if not inputs or any(len(item) != 64 for item in inputs):
        raise ValueError("each input_sha256 must be a 64-character SHA-256 digest")
    if not model.strip() or not model_revision.strip() or not runtime_fingerprint.strip():
        raise ValueError("model, model_revision and runtime_fingerprint are required")
    payload = {
        "workload": workload.strip(),
        "input_sha256": list(inputs),
        "model": model.strip(),
        "model_revision": model_revision.strip(),
        "runtime_fingerprint": runtime_fingerprint.strip(),
        "parameters": parameters or {},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    shard_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ComputeShard(
        shard_id=shard_id,
        workload=payload["workload"],
        input_sha256=inputs,
        model=payload["model"],
        model_revision=payload["model_revision"],
        runtime_fingerprint=payload["runtime_fingerprint"],
        parameters=dict(payload["parameters"]),
    )
