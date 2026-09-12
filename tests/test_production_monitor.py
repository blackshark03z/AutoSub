import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.simple_workflow as workflow


def test_production_monitor_surface_and_truthful_evidence_contract():
    html = Path("app/static/simple/index.html").read_text(encoding="utf-8")
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")
    css = Path("app/static/simple/styles.css").read_text(encoding="utf-8")
    workflow = Path("app/services/simple_workflow.py").read_text(encoding="utf-8")

    for required in (
        'id="monitorCaptionList"',
        'id="monitorMetrics"',
        'id="monitorEvidenceGrid"',
        'id="monitorWarning"',
        'id="resultQcSummary"',
        'role="log"',
        'aria-live="polite"',
    ):
        assert required in html

    for required in (
        "renderCaptionMonitor(run)",
        "renderMonitorEvidence(run)",
        "renderResultQc(run)",
        "provider.prevalidated_evidence_reused",
        "analysis.ocr_batches_completed",
        "analysis.translations_completed",
        "render.processed_seconds",
        "visual_ocr_progress",
        '"Visual OCR"',
        "Đang OCR ${Number(visualOcrProgress.completed_frames || 0)}/${Number(visualOcrProgress.frame_count || 0)} frame",
    ):
        assert required in js

    assert ".production-monitor-grid" in css
    assert "grid-template-columns: 1fr" in css
    assert ".activity-bar.determinate span" in css
    assert '"monitor": _monitor_summary(row, validation)' in workflow
    assert '"render_progress": render' in workflow
    assert "_run_ffmpeg_with_progress(" in workflow
    assert '"-progress"' in workflow
    assert '"pipe:1"' in workflow


def test_monitor_does_not_fake_total_percentage():
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")
    assert 'if (max > 0)' in js
    assert '"Chưa có counter định lượng"' in js
    assert 'Number.isFinite(renderPercent)' in js


def test_monitor_treats_recent_visual_ocr_batch_progress_as_live_activity(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "logs").mkdir(parents=True)
    now = datetime.now(timezone.utc)
    (run_dir / "logs" / "visual_ocr_progress.json").write_text(json.dumps({
        "schema_version": 1,
        "workload": "ocr",
        "state": "running",
        "frame_count": 914,
        "completed_frames": 180,
        "percentage": 19.7,
        "batch_index": 3,
        "batch_count": 11,
        "current_batch_size": 90,
        "route": "local",
        "provider": "local_paddleocr",
        "updated_at": now.isoformat(),
    }), encoding="utf-8")
    old = (now - timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(workflow, "load_caption_analysis_progress", lambda _path: {
        "last_progress_at": old,
        "last_heartbeat_at": old,
        "worker_state": "active",
    })
    row = SimpleNamespace(
        run_directory=str(run_dir),
        requested_settings_json=json.dumps({
            "localization_mode": "full_visual_localization",
            "caption_mode": "external_audio_transcription",
        }),
        started_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=5),
        internal_state="processing",
        current_phase="Localize visual text",
    )

    monitor = workflow._monitor_summary(row, {"status": "PENDING", "eligible": False})

    assert monitor["stalled"] is False
    assert monitor["activity_age_seconds"] < 5
    assert monitor["activity_stall_after_seconds"] == 192.0
    progress = monitor["v2"]["compute"]["visual_ocr_progress"]
    assert progress["completed_frames"] == 180
    assert progress["frame_count"] == 914


def test_monitor_marks_visual_ocr_stalled_after_batch_aware_grace(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "logs").mkdir(parents=True)
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=205)
    (run_dir / "logs" / "visual_ocr_progress.json").write_text(json.dumps({
        "schema_version": 1,
        "workload": "ocr",
        "state": "running",
        "frame_count": 914,
        "completed_frames": 180,
        "percentage": 19.7,
        "batch_index": 3,
        "batch_count": 11,
        "current_batch_size": 90,
        "route": "local",
        "provider": "local_paddleocr",
        "updated_at": stale.isoformat(),
    }), encoding="utf-8")
    monkeypatch.setattr(workflow, "load_caption_analysis_progress", lambda _path: {
        "last_progress_at": stale.isoformat(),
        "last_heartbeat_at": stale.isoformat(),
        "worker_state": "active",
    })
    row = SimpleNamespace(
        run_directory=str(run_dir),
        requested_settings_json=json.dumps({
            "localization_mode": "full_visual_localization",
            "caption_mode": "external_audio_transcription",
        }),
        started_at=now - timedelta(minutes=10),
        updated_at=stale,
        internal_state="processing",
        current_phase="Localize visual text",
    )

    monitor = workflow._monitor_summary(row, {"status": "PENDING", "eligible": False})

    assert monitor["activity_stall_after_seconds"] == 192.0
    assert monitor["activity_age_seconds"] >= 200
    assert monitor["stalled"] is True


def test_render_progress_transient_windows_replace_lock_does_not_fail_render(tmp_path, monkeypatch):
    real_replace = workflow.os.replace
    calls = 0

    def flaky_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(5, "sharing violation")
        return real_replace(source, destination)

    monkeypatch.setattr(workflow.os, "replace", flaky_replace)
    monkeypatch.setattr(workflow.time, "sleep", lambda _seconds: None)

    assert workflow._write_render_progress(
        tmp_path,
        {"state": "rendering", "processed_seconds": 12.5, "duration_seconds": 20.0},
    ) is True
    assert calls == 3
    assert workflow._load_render_progress(tmp_path)["processed_seconds"] == 12.5
    assert not list((tmp_path / "diagnostics").glob("*.tmp"))


def test_render_progress_persistent_windows_lock_is_best_effort(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow.os, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError(5, "locked")))
    monkeypatch.setattr(workflow.time, "sleep", lambda _seconds: None)

    assert workflow._write_render_progress(tmp_path, {"state": "rendering"}) is False
    assert not list((tmp_path / "diagnostics").glob("*.tmp"))


def test_render_process_is_reaped_if_unexpected_monitor_exception_occurs(tmp_path, monkeypatch):
    class FakePipe:
        def __init__(self, lines=()):
            self.lines = list(lines)
            self.closed = False

        def __iter__(self):
            return iter(self.lines)

        def read(self):
            return ""

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self):
            self.stdout = FakePipe(["progress=continue\n"])
            self.stderr = FakePipe()
            self.return_code = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.return_code

        def terminate(self):
            self.terminated = True
            self.return_code = -15

        def kill(self):
            self.killed = True
            self.return_code = -9

        def wait(self, timeout=None):
            return 0 if self.return_code is None else self.return_code

    process = FakeProcess()
    writes = 0

    def write_progress(*_args, **_kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("unexpected monitor failure")
        return True

    monkeypatch.setattr(workflow.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(workflow, "_write_render_progress", write_progress)

    with pytest.raises(RuntimeError, match="unexpected monitor failure"):
        workflow._run_ffmpeg_with_progress(
            ["ffmpeg", "output.mp4"],
            run_dir=tmp_path,
            duration_seconds=10.0,
        )

    assert process.terminated is True
    assert process.killed is False
    assert process.stdout.closed is True
    assert process.stderr.closed is True
