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
from app.services.ocr_runtime import OCRPersistentWorkerSession, OCRRuntimeError, run_ocr_on_images


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


LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE = 90
LOCAL_WHOLE_FRAME_OCR_STABLE_FLOOR = 3
CLOUD_WHOLE_FRAME_OCR_BATCH_SIZE = 64


def _run_local_ocr_batch_with_fallback(
    paths: list[Path],
    *,
    config_path: Path | None,
    session: OCRPersistentWorkerSession | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[int], int]:
    """Keep one Paddle model load for large batches, then bisect only on runtime failure."""
    try:
        if session is None:
            payload = run_ocr_on_images(paths, config_path=config_path)
        else:
            try:
                payload = run_ocr_on_images(paths, config_path=config_path, session=session)
            except OCRRuntimeError:
                # A persistent worker failure may poison that process/model state.
                # Close it immediately and prove the same batch once through the
                # established cold-worker path before considering recursive split.
                session.close()
                payload = run_ocr_on_images(paths, config_path=config_path)
        normalized = normalize_local_whole_frame_ocr_payload(paths, payload)
        return (
            list(normalized.get("frames") or []),
            [str(normalized.get("runtime") or "local_ocr")],
            [str(normalized.get("model_version") or "unknown")],
            [len(paths)],
            0,
        )
    except OCRRuntimeError:
        if len(paths) <= LOCAL_WHOLE_FRAME_OCR_STABLE_FLOOR:
            raise
        midpoint = len(paths) // 2
        # Recursive recovery deliberately uses the known-stable cold path. A
        # dead persistent session must not poison every descendant split.
        left = _run_local_ocr_batch_with_fallback(paths[:midpoint], config_path=config_path, session=None)
        right = _run_local_ocr_batch_with_fallback(paths[midpoint:], config_path=config_path, session=None)
        return (
            left[0] + right[0],
            left[1] + right[1],
            left[2] + right[2],
            left[3] + right[3],
            left[4] + right[4] + 1,
        )


def run_local_whole_frame_ocr(
    image_paths: list[Path],
    *,
    config_path: Path | None = None,
    session: OCRPersistentWorkerSession | None = None,
) -> dict[str, Any]:
    paths = [Path(path).resolve() for path in image_paths]
    if not paths:
        return {
            "frames": [],
            "runtime": "local_ocr",
            "model_version": "unknown",
            "batch_size": LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE,
            "batch_count": 0,
            "batch_sizes": [],
            "fallback_split_count": 0,
        }

    # Real golden-source evidence on this Windows runtime: one persistent worker
    # completed 90 full 1920x1080 frames without the historical primitive error.
    # Paying model startup once per large bounded batch materially improves local
    # throughput. If the runtime regresses under memory/primitive pressure, bisect
    # the failed batch recursively; <=3 frames remains the known-stable floor.
    frames: list[dict[str, Any]] = []
    runtimes: list[str] = []
    models: list[str] = []
    batch_sizes: list[int] = []
    fallback_split_count = 0
    for offset in range(0, len(paths), LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE):
        chunk = paths[offset : offset + LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE]
        chunk_frames, chunk_runtimes, chunk_models, chunk_sizes, chunk_splits = _run_local_ocr_batch_with_fallback(
            chunk,
            config_path=config_path,
            session=session,
        )
        frames.extend(chunk_frames)
        runtimes.extend(chunk_runtimes)
        models.extend(chunk_models)
        batch_sizes.extend(chunk_sizes)
        fallback_split_count += chunk_splits
    return {
        "frames": frames,
        "provider": "local_paddleocr",
        "runtime": runtimes[0] if runtimes and len(set(runtimes)) == 1 else "local_ocr_chunked",
        "model_version": models[0] if models and len(set(models)) == 1 else "+".join(sorted(set(models))),
        "batch_size": LOCAL_WHOLE_FRAME_OCR_BATCH_SIZE,
        "batch_count": len(batch_sizes),
        "batch_sizes": batch_sizes,
        "fallback_split_count": fallback_split_count,
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
        self._persistent_local_session = OCRPersistentWorkerSession() if local_runner is run_local_whole_frame_ocr else None
        self.call_count = 0

    def __call__(self, image_paths: list[Path]) -> dict[str, Any]:
        paths = [Path(path).resolve() for path in image_paths]
        self.call_count += 1
        capabilities = self.modal_provider.capabilities()
        frame_count = len(paths)
        # Current real Windows evidence after keeping the Paddle model alive:
        # 9 golden full frames took ~8.5 s and 90 frames took ~94 s in one
        # bounded worker. Keep a conservative startup allowance and compare
        # total wall time, not inference-only marketing numbers. Modal retains
        # a material cold/model-load fixed cost, so small workloads stay local.
        local_estimated = 3.0 + frame_count * 1.10
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
        if self._persistent_local_session is not None:
            payload = run_local_whole_frame_ocr(paths, session=self._persistent_local_session)
        else:
            payload = self.local_runner(paths)
        entry.setdefault("actual_provider", "local_paddleocr")
        self._write_route_entry(entry)
        return payload

    def close(self) -> None:
        if self._persistent_local_session is not None:
            self._persistent_local_session.close()

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
