from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.hashing import sha256_file
from app.providers.compute.base import ComputeProvider, bind_compute_artifacts, build_shard_identity

PPOCRV6_SMALL_MODEL = "PP-OCRv6_small"
# Weight-bearing Hugging Face revisions. Later README-only commits do not alter
# the qualified inference artifacts, so the compute identity binds these exact
# model snapshots rather than mutable `main`.
PPOCRV6_SMALL_DET_REVISION = "680833b36f64110aea38cc7b82d0df2f5ba4c028"
PPOCRV6_SMALL_REC_REVISION = "728113cb5889dee64b2a18d707f294a76a5d1899"
PPOCRV6_SMALL_REVISION = (
    f"det:{PPOCRV6_SMALL_DET_REVISION};rec:{PPOCRV6_SMALL_REC_REVISION};paddleocr:3.7.0"
)
DEFAULT_RUNTIME_FINGERPRINT = "modal:t4:autosub-modal-worker-v1:ppocrv6-pinned"


class ComputePPocrV6UnavailableError(RuntimeError):
    pass


class ComputePPocrV6ContractError(RuntimeError):
    pass


class ComputePPocrV6Runner:
    """Whole-frame PP-OCRv6 over a qualified hash-bound ComputeProvider."""

    provider_name = "ppocrv6_compute"

    def __init__(
        self,
        compute_provider: ComputeProvider,
        output_dir: Path,
        *,
        model: str = PPOCRV6_SMALL_MODEL,
        model_revision: str = PPOCRV6_SMALL_REVISION,
        runtime_fingerprint: str = DEFAULT_RUNTIME_FINGERPRINT,
    ) -> None:
        if model != PPOCRV6_SMALL_MODEL:
            raise ValueError(f"unsupported qualified PP-OCRv6 model: {model}")
        if model_revision != PPOCRV6_SMALL_REVISION:
            raise ValueError("PP-OCRv6 model revision must match the qualified pinned revision")
        self.compute_provider = compute_provider
        self.output_dir = Path(output_dir).resolve()
        self.model = model
        self.model_revision = model_revision
        self.runtime_fingerprint = str(runtime_fingerprint).strip()
        if not self.runtime_fingerprint:
            raise ValueError("runtime_fingerprint is required")
        self.last_metadata: dict[str, Any] = {}

    def __call__(self, image_paths: list[Path]) -> dict[str, Any]:
        paths = [Path(path).resolve() for path in image_paths]
        if not paths:
            return {
                "frames": [],
                "runtime": "modal_ppocrv6",
                "model_version": self.model,
                "model_revision": self.model_revision,
            }
        if any(not path.is_file() for path in paths):
            raise FileNotFoundError("one or more whole-frame OCR inputs are missing")

        capabilities = self.compute_provider.capabilities()
        if not (
            capabilities.available
            and capabilities.automated
            and capabilities.commercial_use_allowed
            and capabilities.staged_artifacts
            and capabilities.resumable
            and capabilities.no_paid_overage_verified
        ):
            raise ComputePPocrV6UnavailableError(
                capabilities.reason or "cloud_compute_not_safely_qualified"
            )

        hashes = [sha256_file(path).lower() for path in paths]
        shard = build_shard_identity(
            workload="ocr",
            input_sha256=hashes,
            model=self.model,
            model_revision=self.model_revision,
            runtime_fingerprint=self.runtime_fingerprint,
            parameters={
                "frame_names": [path.name for path in paths],
                "whole_frame": True,
                "engine": "transformers",
            },
        )
        artifacts = bind_compute_artifacts(shard, paths)
        result = self.compute_provider.execute(shard, artifacts, self.output_dir / shard.shard_id)
        if result.status != "ready" or len(result.output_artifacts) != 1:
            raise ComputePPocrV6ContractError(
                "compute provider did not return one ready OCR artifact"
            )
        artifact = result.output_artifacts[0]
        if sha256_file(artifact.path).lower() != artifact.sha256.lower():
            raise ComputePPocrV6ContractError(
                "accepted OCR artifact hash changed after provider verification"
            )
        try:
            payload = json.loads(Path(artifact.path).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ComputePPocrV6ContractError("OCR output artifact is unreadable") from exc
        if not isinstance(payload, dict):
            raise ComputePPocrV6ContractError("OCR output artifact must be an object")
        if str(payload.get("model_version") or "") != self.model:
            raise ComputePPocrV6ContractError("OCR output model identity mismatch")
        if str(payload.get("model_revision") or "") != self.model_revision:
            raise ComputePPocrV6ContractError("OCR output model revision mismatch")
        frames = payload.get("frames")
        if not isinstance(frames, list) or len(frames) != len(paths):
            raise ComputePPocrV6ContractError("OCR output frame count mismatch")
        normalized_frames: list[dict[str, Any]] = []
        for expected_path, frame in zip(paths, frames):
            if not isinstance(frame, dict) or Path(str(frame.get("name") or "")).name != expected_path.name:
                raise ComputePPocrV6ContractError("OCR output frame identity mismatch")
            texts = frame.get("texts")
            if not isinstance(texts, list):
                raise ComputePPocrV6ContractError("OCR output frame texts must be an array")
            normalized_texts: list[dict[str, Any]] = []
            for item in texts:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                poly = item.get("poly")
                try:
                    score = float(item.get("score"))
                except (TypeError, ValueError):
                    continue
                if not text or not 0.0 <= score <= 1.0 or not _valid_polygon(poly):
                    continue
                normalized_texts.append({"text": text, "score": score, "poly": poly})
            normalized_frames.append({"name": expected_path.name, "texts": normalized_texts})

        self.last_metadata = {
            "compute_provider": result.provider,
            "compute_shard_id": result.shard_id,
            "cache_hit": bool(result.cache_hit),
            "timings": dict(result.timings),
            "runtime_fingerprint": self.runtime_fingerprint,
            "model": self.model,
            "model_revision": self.model_revision,
            "output_sha256": artifact.sha256,
            "frame_count": len(paths),
        }
        return {
            "frames": normalized_frames,
            "provider": result.provider,
            "runtime": str(payload.get("runtime") or "modal_ppocrv6"),
            "model_version": self.model,
            "model_revision": self.model_revision,
            "compute": dict(self.last_metadata),
        }


def _valid_polygon(value: Any) -> bool:
    if not isinstance(value, list) or len(value) < 3:
        return False
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return False
        try:
            float(point[0])
            float(point[1])
        except (TypeError, ValueError):
            return False
    return True
