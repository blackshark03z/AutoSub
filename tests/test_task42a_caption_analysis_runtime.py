from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.services.caption_analysis_runtime import (
    CaptionAnalysisError,
    CaptionAnalysisProgress,
    load_caption_analysis_progress,
    run_caption_analysis_worker,
)


def _successful_worker(result_queue, source_path: str, run_directory: str, analysis_only: bool, ocr_config_path: str | None) -> None:
    progress = CaptionAnalysisProgress(Path(run_directory), persist_interval_seconds=0)
    progress.update(worker_state="active", analysis_stage="visual_detection", sampled_frames_total=3)
    for index in range(1, 4):
        progress.update(sampled_frames_completed=index, current_item_id=f"frame_{index:05d}", force=True)
        time.sleep(0.05)
    result_queue.put({"status": "ok", "result": {"tracks": [{"id": "track"}]}})


def _error_worker(result_queue, source_path: str, run_directory: str, analysis_only: bool, ocr_config_path: str | None) -> None:
    progress = CaptionAnalysisProgress(Path(run_directory), persist_interval_seconds=0)
    progress.fail("TEST_WORKER_ERROR", "redacted failure")
    result_queue.put({"status": "error", "code": "TEST_WORKER_ERROR", "message": "redacted failure"})


def _exit_without_result_worker(result_queue, source_path: str, run_directory: str, analysis_only: bool, ocr_config_path: str | None) -> None:
    CaptionAnalysisProgress(Path(run_directory), persist_interval_seconds=0)


def _stalled_worker(result_queue, source_path: str, run_directory: str, analysis_only: bool, ocr_config_path: str | None) -> None:
    progress = CaptionAnalysisProgress(Path(run_directory), persist_interval_seconds=0)
    progress.update(worker_state="active", analysis_stage="ocr", current_item_id="ocr_batch_0001", force=True)
    time.sleep(10)


def _large_result_worker(result_queue, source_path: str, run_directory: str, analysis_only: bool, ocr_config_path: str | None) -> None:
    progress = CaptionAnalysisProgress(Path(run_directory), persist_interval_seconds=0)
    progress.update(worker_state="active", analysis_stage="track_creation", force=True)
    progress.complete()
    result_queue.put({"status": "ok", "result": {"tracks": [{"text": "x" * 4096} for _ in range(512)]}})


def _stalled_worker_with_child(result_queue, source_path: str, run_directory: str, analysis_only: bool, ocr_config_path: str | None) -> None:
    progress = CaptionAnalysisProgress(Path(run_directory), persist_interval_seconds=0)
    progress.update(worker_state="active", analysis_stage="ocr", current_item_id="ocr_batch_0001", force=True)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    (Path(run_directory) / "child.pid").write_text(str(child.pid), encoding="ascii")
    time.sleep(30)


def test_progress_is_monotonic_and_heartbeat_is_persisted(tmp_path: Path) -> None:
    progress = CaptionAnalysisProgress(tmp_path, persist_interval_seconds=0)
    progress.update(sampled_frames_total=2, sampled_frames_completed=1, current_item_id="frame_00001", force=True)
    first = load_caption_analysis_progress(tmp_path)
    progress.heartbeat("frame_00002", force=True)
    second = load_caption_analysis_progress(tmp_path)

    assert first is not None and second is not None
    assert second["last_heartbeat_at"] >= first["last_heartbeat_at"]
    with pytest.raises(CaptionAnalysisError, match="regressed"):
        progress.update(sampled_frames_completed=0, force=True)


def test_supervisor_does_not_false_positive_while_progress_increases(tmp_path: Path) -> None:
    result = run_caption_analysis_worker(
        tmp_path / "source.mp4",
        tmp_path,
        analysis_only=True,
        stall_seconds=5.0,
        poll_seconds=0.02,
        _worker_target=_successful_worker,
    )
    assert result["tracks"][0]["id"] == "track"


def test_worker_exception_propagates_with_exact_code(tmp_path: Path) -> None:
    with pytest.raises(CaptionAnalysisError) as caught:
        run_caption_analysis_worker(
            tmp_path / "source.mp4",
            tmp_path,
            analysis_only=True,
            stall_seconds=5.0,
            poll_seconds=0.02,
            _worker_target=_error_worker,
        )
    assert caught.value.code == "TEST_WORKER_ERROR"


def test_worker_exit_without_future_result_is_bounded(tmp_path: Path) -> None:
    with pytest.raises(CaptionAnalysisError) as caught:
        run_caption_analysis_worker(
            tmp_path / "source.mp4",
            tmp_path,
            analysis_only=True,
            stall_seconds=5.0,
            poll_seconds=0.02,
            _worker_target=_exit_without_result_worker,
        )
    assert caught.value.code == "EMBEDDED_CAPTION_WORKER_EXITED_WITHOUT_RESULT"


def test_watchdog_fails_stalled_worker_and_releases_process(tmp_path: Path) -> None:
    with pytest.raises(CaptionAnalysisError) as caught:
        run_caption_analysis_worker(
            tmp_path / "source.mp4",
            tmp_path,
            analysis_only=True,
            stall_seconds=3.0,
            poll_seconds=0.02,
            _worker_target=_stalled_worker,
        )
    assert caught.value.code == "EMBEDDED_CAPTION_ANALYSIS_STALLED"
    progress = load_caption_analysis_progress(tmp_path)
    assert progress is not None
    assert progress["worker_state"] == "failed"
    assert progress["error_code"] == "EMBEDDED_CAPTION_ANALYSIS_STALLED"


def test_large_queue_result_is_consumed_before_worker_exit(tmp_path: Path) -> None:
    result = run_caption_analysis_worker(
        tmp_path / "source.mp4",
        tmp_path,
        analysis_only=True,
        stall_seconds=5.0,
        poll_seconds=0.02,
        _worker_target=_large_result_worker,
    )
    assert len(result["tracks"]) == 512


def test_watchdog_terminates_owned_child_process_tree(tmp_path: Path) -> None:
    psutil = pytest.importorskip("psutil")
    with pytest.raises(CaptionAnalysisError) as caught:
        run_caption_analysis_worker(
            tmp_path / "source.mp4",
            tmp_path,
            analysis_only=True,
            stall_seconds=3.0,
            poll_seconds=0.02,
            _worker_target=_stalled_worker_with_child,
        )
    assert caught.value.code == "EMBEDDED_CAPTION_ANALYSIS_STALLED"
    child_pid = int((tmp_path / "child.pid").read_text(encoding="ascii"))
    deadline = time.monotonic() + 3
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(child_pid)


def test_required_progress_contract_fields_are_safe(tmp_path: Path) -> None:
    progress = CaptionAnalysisProgress(tmp_path, persist_interval_seconds=0)
    payload = progress.snapshot()
    assert {
        "analysis_stage",
        "sampled_frames_total",
        "sampled_frames_completed",
        "dense_crops_total",
        "dense_crops_completed",
        "ocr_batches_total",
        "ocr_batches_completed",
        "current_item_id",
        "last_progress_at",
        "last_heartbeat_at",
        "worker_state",
        "worker_pid_or_thread",
        "retry_count",
        "error_code",
    } <= payload.keys()
    serialized = json.dumps(payload)
    assert "api_key" not in serialized.lower()
    assert payload["worker_pid_or_thread"] == os.getpid()


def test_progress_atomic_replace_retries_windows_sharing_violation(tmp_path: Path, monkeypatch) -> None:
    progress = CaptionAnalysisProgress(tmp_path, persist_interval_seconds=0)
    original_replace = Path.replace
    calls = 0

    def flaky_replace(path: Path, target: Path):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(32, "sharing violation")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    progress.update(sampled_frames_total=1, sampled_frames_completed=1, force=True)
    assert calls == 3
    assert load_caption_analysis_progress(tmp_path)["sampled_frames_completed"] == 1


def test_supervisor_reopen_preserves_analyzer_worker_pid(tmp_path: Path) -> None:
    progress = CaptionAnalysisProgress(tmp_path, persist_interval_seconds=0)
    progress.update(worker_pid_or_thread=12345, force=True)

    reopened = CaptionAnalysisProgress(tmp_path, persist_interval_seconds=0)

    assert reopened.snapshot()["worker_pid_or_thread"] == 12345


def test_background_exception_is_persisted_instead_of_silently_stalling(monkeypatch) -> None:
    from app.api import routes

    persisted = []

    def fail_start(run_id: str, *, accepted: bool = False) -> None:
        raise RuntimeError("safe test failure")

    monkeypatch.setattr(routes, "start_processing", fail_start)
    monkeypatch.setattr(routes, "persist_unhandled_processing_failure", lambda run_id, exc: persisted.append((run_id, str(exc))))

    routes._run_simple_processing_background("run-test")

    assert persisted == [("run-test", "safe test failure")]
