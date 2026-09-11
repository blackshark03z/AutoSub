import json
from pathlib import Path

import pytest

import app.services.visual_ocr_runner as runner
from app.core.hashing import sha256_file
from app.providers.compute.base import ComputeArtifact, ComputeProviderCapabilities, ComputeShardResult
from app.providers.ocr.compute_ppocrv6_provider import PPOCRV6_SMALL_MODEL, PPOCRV6_SMALL_REVISION
from app.services.ocr_runtime import OCRRuntimeError
from app.services.visual_ocr_runner import RoutedWholeFrameOCRRunner, normalize_local_whole_frame_ocr_payload


def test_local_ocr_payload_is_normalized_to_whole_frame_evidence_shape(tmp_path: Path) -> None:
    first = tmp_path / "frame_00001.jpg"
    second = tmp_path / "frame_00002.jpg"
    payload = {
        "runtime": "paddleocr_local",
        "model_version": "2.10",
        "frames": [
            {"items": [{"text": "中文", "confidence": 0.98, "box": [[1, 2], [11, 2], [11, 12], [1, 12]]}]},
            {"items": [{"text": "2500", "confidence": 0.91, "box": {"left_x": 20, "top_y": 30, "right_x": 50, "bottom_y": 45}}]},
        ],
    }
    result = normalize_local_whole_frame_ocr_payload([first, second], payload)
    assert [frame["name"] for frame in result["frames"]] == [first.name, second.name]
    assert result["frames"][0]["texts"][0]["text"] == "中文"
    assert result["frames"][1]["texts"][0]["poly"] == [[20.0, 30.0], [50.0, 30.0], [50.0, 45.0], [20.0, 45.0]]


def test_local_ocr_payload_rejects_frame_count_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="frame count"):
        normalize_local_whole_frame_ocr_payload([tmp_path / "one.jpg"], {"frames": []})


def _fake_local_ocr_payload(chunk):
    return {
        "runtime": "paddleocr",
        "model_version": "paddleocr-2.10.0",
        "frames": [
            {"items": [{"text": "中文", "confidence": 0.99, "box": [[1, 1], [10, 1], [10, 10], [1, 10]]}]}
            for _ in chunk
        ],
    }


def test_local_whole_frame_ocr_keeps_model_alive_for_bounded_large_batch(tmp_path: Path, monkeypatch) -> None:
    paths = [tmp_path / f"frame_{index:05d}.jpg" for index in range(90)]
    calls: list[list[str]] = []

    def fake_batch(chunk, config_path=None):
        assert config_path is None
        calls.append([Path(path).name for path in chunk])
        return _fake_local_ocr_payload(chunk)

    monkeypatch.setattr(runner, "run_ocr_on_images", fake_batch)
    result = runner.run_local_whole_frame_ocr(paths)
    assert [len(call) for call in calls] == [90]
    assert [frame["name"] for frame in result["frames"]] == [path.name for path in paths]
    assert result["runtime"] == "paddleocr"
    assert result["model_version"] == "paddleocr-2.10.0"
    assert result["batch_size"] == 90
    assert result["batch_count"] == 1
    assert result["batch_sizes"] == [90]
    assert result["fallback_split_count"] == 0


def test_local_whole_frame_ocr_bisects_failed_large_batch_to_stable_floor(tmp_path: Path, monkeypatch) -> None:
    paths = [tmp_path / f"frame_{index:05d}.jpg" for index in range(7)]
    calls: list[int] = []

    def fake_batch(chunk, config_path=None):
        assert config_path is None
        calls.append(len(chunk))
        if len(chunk) > 3:
            raise OCRRuntimeError("simulated primitive pressure")
        return _fake_local_ocr_payload(chunk)

    monkeypatch.setattr(runner, "run_ocr_on_images", fake_batch)
    result = runner.run_local_whole_frame_ocr(paths)
    assert calls == [7, 3, 4, 2, 2]
    assert [frame["name"] for frame in result["frames"]] == [path.name for path in paths]
    assert result["batch_size"] == 90
    assert result["batch_count"] == 3
    assert result["batch_sizes"] == [3, 2, 2]
    assert result["fallback_split_count"] == 2


def test_dead_persistent_ocr_session_falls_back_to_cold_recursive_recovery(tmp_path: Path, monkeypatch) -> None:
    paths = [tmp_path / f"frame_{index:05d}.jpg" for index in range(7)]
    calls: list[tuple[str, int]] = []

    class DeadSession:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    session = DeadSession()

    def fake_batch(chunk, config_path=None, session=None):
        assert config_path is None
        mode = "warm" if session is not None else "cold"
        calls.append((mode, len(chunk)))
        if session is not None:
            raise OCRRuntimeError("persistent worker poisoned")
        if len(chunk) > 3:
            raise OCRRuntimeError("simulated primitive pressure")
        return _fake_local_ocr_payload(chunk)

    monkeypatch.setattr(runner, "run_ocr_on_images", fake_batch)
    result = runner.run_local_whole_frame_ocr(paths, session=session)
    assert calls == [("warm", 7), ("cold", 7), ("cold", 3), ("cold", 4), ("cold", 2), ("cold", 2)]
    assert session.close_count == 1
    assert [frame["name"] for frame in result["frames"]] == [path.name for path in paths]
    assert result["batch_sizes"] == [3, 2, 2]
    assert result["fallback_split_count"] == 2


class _FakeModal:
    def __init__(self, *, no_overage: bool):
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
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "ocr.json"
        payload = {
            "runtime": "modal_ppocrv6",
            "model_version": PPOCRV6_SMALL_MODEL,
            "model_revision": PPOCRV6_SMALL_REVISION,
            "frames": [
                {"name": artifact.path.name, "texts": [{"text": "管理员", "score": 0.99, "poly": [[1, 1], [20, 1], [20, 10], [1, 10]]}]}
                for artifact in artifacts
            ],
        }
        output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        digest = sha256_file(output)
        return ComputeShardResult(
            shard_id=shard.shard_id,
            provider="modal",
            status="ready",
            output_sha256=(digest,),
            output_artifacts=(ComputeArtifact(path=output, sha256=digest),),
            cache_hit=False,
            timings={"inference_seconds": 1.0},
            provenance={},
        )


def _routing_frames(tmp_path: Path, count: int) -> list[Path]:
    result = []
    for index in range(count):
        path = tmp_path / f"route_{index:05d}.jpg"
        path.write_bytes(f"frame-{index}".encode())
        result.append(path)
    return result


def test_routed_visual_ocr_uses_cloud_only_when_materially_faster_and_no_overage_verified(tmp_path: Path) -> None:
    modal = _FakeModal(no_overage=True)
    local_calls = []
    routed = RoutedWholeFrameOCRRunner(
        root=tmp_path,
        run_directory=tmp_path / "run",
        compute_policy="auto",
        modal_provider=modal,
        local_runner=lambda paths: local_calls.append(paths) or {"provider": "local_paddleocr", "runtime": "local", "model_version": "2.10", "frames": []},
    )
    paths = _routing_frames(tmp_path, 40)
    payload = routed(paths)
    assert payload["provider"] == "modal"
    assert modal.calls and local_calls == []
    route = json.loads((tmp_path / "run" / "logs" / "visual_ocr_compute_route.json").read_text(encoding="utf-8"))
    assert route["calls"][-1]["route"] == "cloud"
    assert route["calls"][-1]["no_paid_overage_verified"] is True


def test_routed_visual_ocr_splits_large_cloud_workload_into_bounded_resumable_shards(tmp_path: Path) -> None:
    modal = _FakeModal(no_overage=True)
    local_calls = []
    routed = RoutedWholeFrameOCRRunner(
        root=tmp_path,
        run_directory=tmp_path / "run",
        compute_policy="auto",
        modal_provider=modal,
        local_runner=lambda paths: local_calls.append(paths) or {"provider": "local_paddleocr", "runtime": "local", "model_version": "2.10", "frames": []},
    )
    paths = _routing_frames(tmp_path, 130)
    payload = routed(paths)

    assert payload["provider"] == "modal"
    assert len(payload["frames"]) == 130
    assert [len(call[1]) for call in modal.calls] == [64, 64, 2]
    assert local_calls == []
    route = json.loads((tmp_path / "run" / "logs" / "visual_ocr_compute_route.json").read_text(encoding="utf-8"))
    latest = route["calls"][-1]
    assert latest["route"] == "cloud"
    assert latest["cloud_batch_size"] == 64
    assert latest["compute_shard_count"] == 3
    assert len(latest["compute_shard_ids"]) == 3


def test_routed_visual_ocr_stays_local_when_paid_overage_not_verified(tmp_path: Path) -> None:
    modal = _FakeModal(no_overage=False)
    paths = _routing_frames(tmp_path, 12)
    routed = RoutedWholeFrameOCRRunner(
        root=tmp_path,
        run_directory=tmp_path / "run",
        compute_policy="auto",
        modal_provider=modal,
        local_runner=lambda values: {"provider": "local_paddleocr", "runtime": "local", "model_version": "2.10", "frames": [{"name": p.name, "texts": []} for p in values]},
    )
    payload = routed(paths)
    assert payload["provider"] == "local_paddleocr"
    assert modal.calls == []
    route = json.loads((tmp_path / "run" / "logs" / "visual_ocr_compute_route.json").read_text(encoding="utf-8"))
    assert route["calls"][-1]["reason"] == "no_qualified_cloud_provider"


def test_routed_visual_ocr_keeps_micro_workload_local_even_when_cloud_is_safe(tmp_path: Path) -> None:
    modal = _FakeModal(no_overage=True)
    local_calls = []
    paths = _routing_frames(tmp_path, 2)
    routed = RoutedWholeFrameOCRRunner(
        root=tmp_path,
        run_directory=tmp_path / "run",
        compute_policy="auto",
        modal_provider=modal,
        local_runner=lambda values: local_calls.append(values) or {"provider": "local_paddleocr", "runtime": "local", "model_version": "2.10", "frames": [{"name": p.name, "texts": []} for p in values]},
    )
    payload = routed(paths)
    assert payload["provider"] == "local_paddleocr"
    assert local_calls and modal.calls == []


def test_default_routed_local_ocr_reuses_and_closes_persistent_session(tmp_path: Path, monkeypatch) -> None:
    class FakeSession:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    session = FakeSession()
    monkeypatch.setattr(runner, "OCRPersistentWorkerSession", lambda: session)
    calls = []

    def custom_runner(paths):
        calls.append(list(paths))
        return {
            "provider": "local_paddleocr",
            "runtime": "local",
            "model_version": "2.10",
            "frames": [{"name": Path(path).name, "texts": []} for path in paths],
        }

    routed = RoutedWholeFrameOCRRunner(
        root=tmp_path,
        run_directory=tmp_path / "run",
        compute_policy="local_only",
        modal_provider=_FakeModal(no_overage=False),
        local_runner=custom_runner,
    )
    paths = _routing_frames(tmp_path, 2)
    routed(paths)
    assert calls
    assert routed._persistent_local_session is None
    routed.close()


def test_routed_default_runner_owns_one_session_across_calls_and_closes_it(tmp_path: Path, monkeypatch) -> None:
    class FakeSession:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    original_default = runner.run_local_whole_frame_ocr
    fake_session = FakeSession()
    monkeypatch.setattr(runner, "OCRPersistentWorkerSession", lambda: fake_session)
    routed = RoutedWholeFrameOCRRunner(
        root=tmp_path,
        run_directory=tmp_path / "run",
        compute_policy="local_only",
        modal_provider=_FakeModal(no_overage=False),
        local_runner=original_default,
    )
    assert routed._persistent_local_session is fake_session
    routed.close()
    assert fake_session.closed is True
