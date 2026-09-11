from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from app.providers.compute.modal_provider import ModalComputeProvider, discover_modal_compute_config
from app.providers.ocr.compute_ppocrv6_provider import (
    ComputePPocrV6ContractError,
    ComputePPocrV6Runner,
    ComputePPocrV6UnavailableError,
)
from app.services.compute_router import choose_compute_route
from app.services.ocr_runtime import run_ocr_on_images


def _polygon_from_box(value: Any) -> list[list[float]] | None:
    if isinstance(value, dict):
        try:
            left = float(value["left_x"])
            top = float(value["top_y"])
            right = float(value["right_x"])
            bottom = float(value["bottom_y"])
        except (KeyError, TypeError, ValueError):
            return None
        return [[left, top], [right, top], [right, bottom], [left, bottom]]
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) >= 4 and all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in value):
        try:
            return [[float(point[0]), float(point[1])] for point in value]
        except (TypeError, ValueError):
            return None
    if len(value) == 4:
        try:
            left, top, right, bottom = (float(item) for item in value)
        except (TypeError, ValueError):
            return None
        return [[left, top], [right, top], [right, bottom], [left, bottom]]
    return None


def normalize_local_whole_frame_ocr_payload(
    image_paths: list[Path],
    payload: dict[str, Any],
) -> dict[str, Any]:
    frames = list(payload.get("frames") or []) if isinstance(payload, dict) else []
    if len(frames) != len(image_paths):
        raise ValueError("local whole-frame OCR returned a mismatched frame count")
    normalized = []
    for image_path, frame in zip(image_paths, frames):
        if not isinstance(frame, dict):
            raise ValueError("local whole-frame OCR returned an invalid frame")
        texts = []
        for item in list(frame.get("items") or []):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                continue
            polygon = _polygon_from_box(item.get("box") or item.get("poly"))
            if polygon is None:
                continue
            texts.append({"text": text, "score": confidence, "poly": polygon})
        normalized.append({"name": Path(image_path).name, "texts": texts})
    return {
        "frames": normalized,
        "runtime": str(payload.get("runtime") or "local_ocr"),
        "model_version": str(payload.get("model_version") or "unknown"),
    }


LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE = 3
CLOUD_WHOLE_FRAME_OCR_BATCH_SIZE = 64


def run_local_whole_frame_ocr(
    image_paths: list[Path],
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    paths = [Path(path).resolve() for path in image_paths]
    if not paths:
        return {"frames": [], "runtime": "local_ocr", "model_version": "unknown", "batch_size": LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE}

    # The pinned Windows PaddleOCR 2.10 runtime was verified on the real fixture
    # to be stable for three frames per process but can fail with
    # `could not create a primitive` on larger repeated-inference batches.
    # Chunking here keeps the model/provider contract unchanged and makes the
    # local fallback reliable; qualified cloud OCR remains the throughput path.
    frames: list[dict[str, Any]] = []
    runtimes: list[str] = []
    models: list[str] = []
    for offset in range(0, len(paths), LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE):
        chunk = paths[offset : offset + LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE]
        payload = run_ocr_on_images(chunk, config_path=config_path)
        normalized = normalize_local_whole_frame_ocr_payload(chunk, payload)
        frames.extend(list(normalized.get("frames") or []))
        runtimes.append(str(normalized.get("runtime") or "local_ocr"))
        models.append(str(normalized.get("model_version") or "unknown"))
    return {
        "frames": frames,
        "provider": "local_paddleocr",
        "runtime": runtimes[0] if runtimes and len(set(runtimes)) == 1 else "local_ocr_chunked",
        "model_version": models[0] if models and len(set(models)) == 1 else "+".join(sorted(set(models))),
        "batch_size": LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE,
        "batch_count": (len(paths) + LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE - 1) // LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE,
    }


class RoutedWholeFrameOCRRunner:
    """Route expensive whole-frame OCR without weakening the local fallback contract."""

    def __init__(
        self,
        *,
        root: Path,
        run_directory: Path,
        compute_policy: str,
        modal_provider: ModalComputeProvider | Any | None = None,
        local_runner: Callable[[list[Path]], dict[str, Any]] = run_local_whole_frame_ocr,
    ) -> None:
        self.root = Path(root).resolve()
        self.run_directory = Path(run_directory).resolve()
        self.compute_policy = str(compute_policy or "auto")
        self.modal_provider = modal_provider or ModalComputeProvider(discover_modal_compute_config(self.root))
        self.local_runner = local_runner
        self.call_count = 0

    def __call__(self, image_paths: list[Path]) -> dict[str, Any]:
        paths = [Path(path).resolve() for path in image_paths]
        self.call_count += 1
        capabilities = self.modal_provider.capabilities()
        frame_count = len(paths)
        # Real Windows fallback evidence: 9 frames / 3 fresh Paddle workers took
        # ~26 s. Real Modal PP-OCRv6 evidence has material fixed cold/model load
        # plus sub-second-per-frame warm inference. Keep estimates conservative
        # and compare total wall time, not GPU inference alone.
        local_estimated = max(0.0, frame_count * 2.9)
        cloud_estimated = 20.0 + frame_count * 0.30
        decision = choose_compute_route(
            policy=self.compute_policy,
            workload="ocr",
            local_estimated_seconds=local_estimated,
            cloud_estimated_seconds=cloud_estimated,
            provider_capabilities=[capabilities],
            minimum_relative_speedup=0.15,
            minimum_absolute_seconds_saved=5.0,
        )
        entry: dict[str, Any] = {
            "call_index": self.call_count,
            "frame_count": frame_count,
            "policy": self.compute_policy,
            "route": decision.route,
            "provider": decision.provider,
            "reason": decision.reason,
            "local_estimated_seconds": decision.local_estimated_seconds,
            "cloud_estimated_seconds": decision.cloud_estimated_seconds,
            "no_paid_overage_verified": bool(capabilities.no_paid_overage_verified),
            "fallback": False,
        }
        if decision.route == "cloud":
            try:
                merged_frames: list[dict[str, Any]] = []
                shard_ids: list[str] = []
                cache_hits: list[bool] = []
                timing_totals: dict[str, float] = {}
                runtime = "modal_ppocrv6"
                model_version = "PP-OCRv6_small"
                for offset in range(0, len(paths), CLOUD_WHOLE_FRAME_OCR_BATCH_SIZE):
                    chunk = paths[offset : offset + CLOUD_WHOLE_FRAME_OCR_BATCH_SIZE]
                    cloud = ComputePPocrV6Runner(
                        self.modal_provider,
                        self.run_directory / "work" / "compute" / "ocr",
                    )
                    chunk_payload = cloud(chunk)
                    chunk_frames = list(chunk_payload.get("frames") or [])
                    if len(chunk_frames) != len(chunk):
                        raise ComputePPocrV6ContractError("cloud OCR shard returned a mismatched frame count")
                    merged_frames.extend(chunk_frames)
                    shard_id = str(cloud.last_metadata.get("compute_shard_id") or "")
                    if shard_id:
                        shard_ids.append(shard_id)
                    cache_hits.append(bool(cloud.last_metadata.get("cache_hit")))
                    for key, value in dict(cloud.last_metadata.get("timings") or {}).items():
                        try:
                            timing_totals[str(key)] = timing_totals.get(str(key), 0.0) + float(value)
                        except (TypeError, ValueError):
                            continue
                    runtime = str(chunk_payload.get("runtime") or runtime)
                    model_version = str(chunk_payload.get("model_version") or model_version)
                payload = {
                    "frames": merged_frames,
                    "provider": "modal",
                    "runtime": runtime,
                    "model_version": model_version,
                }
                entry.update(
                    actual_provider="modal",
                    compute_shard_count=len(shard_ids),
                    compute_shard_ids=shard_ids,
                    cache_hit=bool(cache_hits) and all(cache_hits),
                    cache_hit_count=sum(cache_hits),
                    timings={key: round(value, 6) for key, value in timing_totals.items()},
                    cloud_batch_size=CLOUD_WHOLE_FRAME_OCR_BATCH_SIZE,
                )
                self._write_route_entry(entry)
                return payload
            except (ComputePPocrV6UnavailableError, ComputePPocrV6ContractError, OSError, ValueError) as exc:
                entry.update(
                    route="local",
                    actual_provider="local_paddleocr",
                    fallback=True,
                    fallback_reason=type(exc).__name__,
                )
        payload = self.local_runner(paths)
        entry.setdefault("actual_provider", "local_paddleocr")
        self._write_route_entry(entry)
        return payload

    def _write_route_entry(self, entry: dict[str, Any]) -> None:
        path = self.run_directory / "logs" / "visual_ocr_compute_route.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        history: list[dict[str, Any]] = []
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(existing, dict) and isinstance(existing.get("calls"), list):
                    history = [item for item in existing["calls"] if isinstance(item, dict)]
            except (OSError, UnicodeError, json.JSONDecodeError):
                history = []
        history.append(entry)
        path.write_text(
            json.dumps({"schema_version": 1, "workload": "ocr", "calls": history}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def build_routed_whole_frame_ocr_runner(
    *,
    root: Path,
    run_directory: Path,
    compute_policy: str,
) -> RoutedWholeFrameOCRRunner:
    return RoutedWholeFrameOCRRunner(
        root=root,
        run_directory=run_directory,
        compute_policy=compute_policy,
    )
