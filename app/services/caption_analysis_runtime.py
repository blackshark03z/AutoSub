from __future__ import annotations

import json
import multiprocessing
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - optional diagnostic enrichment
    psutil = None


STALL_ERROR_CODE = "EMBEDDED_CAPTION_ANALYSIS_STALLED"
WORKER_ERROR_CODE = "EMBEDDED_CAPTION_ANALYSIS_FAILED"
DEFAULT_STALL_SECONDS = 120.0
DEFAULT_HEARTBEAT_SECONDS = 10.0


class CaptionAnalysisError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_message(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")[:500]
    text = re.sub(r"(?i)(api[_-]?key|xi-api-key|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)
    return text


@dataclass
class CaptionAnalysisProgress:
    run_directory: Path
    persist_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        self.path = self.run_directory / "diagnostics" / "embedded_caption_progress.json"
        self.log_path = self.run_directory / "logs" / "embedded_caption_analyzer.log"
        self._lock = threading.Lock()
        self._last_persist = 0.0
        self._state: dict[str, Any] = {
            "analysis_stage": "initializing",
            "sampled_frames_total": 0,
            "sampled_frames_completed": 0,
            "dense_crops_total": 0,
            "dense_crops_completed": 0,
            "ocr_batches_total": 0,
            "ocr_batches_completed": 0,
            "current_item_id": None,
            "last_progress_at": _utc_now(),
            "last_heartbeat_at": _utc_now(),
            "worker_state": "starting",
            "worker_pid_or_thread": os.getpid(),
            "retry_count": 0,
            "error_code": None,
            "error_message": None,
            "stage_started_at": _utc_now(),
            "stage_history": [],
        }
        loaded_existing = False
        if self.path.is_file():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(existing, dict):
                    self._state.update(existing)
                    loaded_existing = True
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
        if not loaded_existing:
            self._state["worker_pid_or_thread"] = os.getpid()
        self._stage_started_monotonic = time.monotonic()
        self.persist(force=True)

    def update(self, *, force: bool = False, heartbeat: bool = True, **changes: Any) -> None:
        with self._lock:
            changed_progress = False
            next_stage = changes.get("analysis_stage")
            stage_changed = bool(next_stage and next_stage != self._state.get("analysis_stage"))
            if stage_changed:
                history = list(self._state.get("stage_history") or [])
                history.append(
                    {
                        "stage": self._state.get("analysis_stage"),
                        "started_at": self._state.get("stage_started_at"),
                        "completed_at": _utc_now(),
                        "duration_seconds": round(time.monotonic() - self._stage_started_monotonic, 3),
                    }
                )
                changes["stage_history"] = history
                changes["stage_started_at"] = _utc_now()
                self._stage_started_monotonic = time.monotonic()
            for key in ("sampled_frames_completed", "dense_crops_completed", "ocr_batches_completed", "retry_count"):
                if key in changes:
                    value = int(changes[key])
                    if value < int(self._state.get(key) or 0):
                        raise CaptionAnalysisError("CAPTION_PROGRESS_REGRESSION", f"Progress field {key} regressed")
                    changed_progress = changed_progress or value > int(self._state.get(key) or 0)
                    changes[key] = value
            for completed, total in (
                ("sampled_frames_completed", "sampled_frames_total"),
                ("dense_crops_completed", "dense_crops_total"),
                ("ocr_batches_completed", "ocr_batches_total"),
            ):
                completed_value = int(changes.get(completed, self._state.get(completed) or 0))
                total_value = int(changes.get(total, self._state.get(total) or 0))
                if total_value and completed_value > total_value:
                    raise CaptionAnalysisError("CAPTION_PROGRESS_INVALID", f"Progress field {completed} exceeds {total}")
            self._state.update(changes)
            if changed_progress:
                self._state["last_progress_at"] = _utc_now()
            if heartbeat:
                self._state["last_heartbeat_at"] = _utc_now()
            self.persist(force=force)
            if stage_changed:
                self.log(f"STAGE stage={next_stage} item={self._state.get('current_item_id')}")

    def heartbeat(self, current_item_id: str | None = None, *, force: bool = False) -> None:
        changes = {"current_item_id": current_item_id} if current_item_id is not None else {}
        self.update(force=force, heartbeat=True, **changes)

    def fail(self, code: str, exc: object, *, current_item_id: str | None = None) -> None:
        self.update(
            force=True,
            worker_state="failed",
            error_code=code,
            error_message=_safe_message(exc),
            current_item_id=current_item_id or self._state.get("current_item_id"),
        )
        self.log(f"ERROR code={code} stage={self._state['analysis_stage']} item={self._state.get('current_item_id')} message={_safe_message(exc)}")

    def complete(self) -> None:
        self.update(force=True, worker_state="completed", analysis_stage="completed", current_item_id=None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def persist(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_persist < self.persist_interval_seconds:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        last_error: OSError | None = None
        for attempt in range(5):
            try:
                temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.path)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
            except OSError as exc:
                raise CaptionAnalysisError(
                    "CAPTION_PROGRESS_PERSIST_FAILED",
                    f"Unable to persist caption analysis progress: {exc}",
                ) from exc
        if last_error is not None:
            raise CaptionAnalysisError(
                "CAPTION_PROGRESS_PERSIST_FAILED",
                f"Unable to persist caption analysis progress after 5 retries: {last_error}",
            ) from last_error
        self._last_persist = now

    def log(self, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{_utc_now()} {_safe_message(message)}\n")


def load_caption_analysis_progress(run_directory: Path) -> dict[str, Any] | None:
    path = run_directory / "diagnostics" / "embedded_caption_progress.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"worker_state": "progress_unavailable", "error_code": "CAPTION_PROGRESS_READ_FAILED"}
    return payload if isinstance(payload, dict) else None


def _analysis_worker_entry(result_queue: Any, source_path: str, run_directory: str, analysis_only: bool, ocr_config_path: str | None) -> None:
    from app.services.source_caption_translation import create_source_caption_translation

    progress = CaptionAnalysisProgress(Path(run_directory))
    progress.update(force=True, worker_state="active", worker_pid_or_thread=os.getpid())
    try:
        result = create_source_caption_translation(
            Path(source_path),
            Path(run_directory),
            progress=progress,
            analysis_only=analysis_only,
            ocr_config_path=Path(ocr_config_path) if ocr_config_path else None,
        )
        progress.complete()
        result_queue.put({"status": "ok", "result": result})
    except BaseException as exc:
        code = getattr(exc, "code", WORKER_ERROR_CODE)
        
        # Diagnostic persistence
        try:
            import traceback
            import re
            
            diag_path = Path(run_directory) / "diagnostics" / "worker_exception_diag.json"
            diag_path.parent.mkdir(parents=True, exist_ok=True)
            
            tb_str = traceback.format_exc()
            msg_str = str(exc)
            
            def sanitize(text: str) -> str:
                if not text: return text
                text = re.sub(r'(?i)(authorization)\s*:\s*(bearer\s+)?[\w\-\.]+', r'\1: ***', text)
                text = re.sub(r'(?i)(key|token|secret|credential|password)[-_a-z0-9]*\s*[:=]\s*[\'"]?[a-zA-Z0-9_\-\.]+[\'"]?', r'\1=***', text)
                return text
                
            state = getattr(progress, "_state", {})
            diag_data = {
                "schema_version": 1,
                "failure_stage": state.get("analysis_stage"),
                "exception_type": type(exc).__name__,
                "exception_message": sanitize(msg_str),
                "traceback_sanitized": sanitize(tb_str),
                "analysis_stage": state.get("analysis_stage"),
                "current_item_id": state.get("current_item_id"),
                "sampled_frames_total": state.get("sampled_frames_total"),
                "sampled_frames_completed": state.get("sampled_frames_completed"),
                "dense_crops_total": state.get("dense_crops_total"),
                "dense_crops_completed": state.get("dense_crops_completed"),
                "ocr_batches_total": state.get("ocr_batches_total"),
                "ocr_batches_completed": state.get("ocr_batches_completed"),
                "last_progress_at": state.get("last_progress_at"),
                "last_heartbeat_at": state.get("last_heartbeat_at"),
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            
            temp_path = diag_path.with_suffix('.tmp')
            temp_path.write_text(json.dumps(diag_data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(diag_path)
        except BaseException:
            pass

        progress.fail(str(code), exc)
        result_queue.put({"status": "error", "code": str(code), "message": _safe_message(exc)})


def _terminate_process_tree(process: Any, *, timeout_seconds: float = 10.0) -> None:
    descendants: list[Any] = []
    if psutil is not None and process.pid:
        try:
            descendants = psutil.Process(process.pid).children(recursive=True)
            for child in descendants:
                try:
                    child.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if process.is_alive():
        process.terminate()
        process.join(timeout=timeout_seconds)
    if descendants and psutil is not None:
        _, alive = psutil.wait_procs(descendants, timeout=timeout_seconds)
        for child in alive:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if alive:
            psutil.wait_procs(alive, timeout=timeout_seconds)
    if process.is_alive():
        process.kill()
        process.join(timeout=timeout_seconds)


def run_caption_analysis_worker(
    source_path: Path,
    run_directory: Path,
    *,
    analysis_only: bool = False,
    stall_seconds: float = DEFAULT_STALL_SECONDS,
    poll_seconds: float = 2.0,
    ocr_config_path: Path | None = None,
    _worker_target: Any = _analysis_worker_entry,
) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_worker_target,
        args=(result_queue, str(source_path), str(run_directory), analysis_only, str(ocr_config_path) if ocr_config_path else None),
        name="embedded-caption-analyzer",
    )
    process.start()
    observed_process = psutil.Process(process.pid) if psutil is not None else None
    if observed_process is not None:
        observed_process.cpu_percent(None)
    cpu_samples: list[float] = []
    peak_memory = 0
    last_signature: tuple[Any, ...] | None = None
    last_change = time.monotonic()
    message: dict[str, Any] | None = None
    try:
        while process.is_alive():
            process.join(timeout=poll_seconds)
            try:
                message = result_queue.get_nowait()
            except queue.Empty:
                message = None
            if message is not None:
                process.join(timeout=10)
                break
            if observed_process is not None:
                try:
                    cpu_samples.append(float(observed_process.cpu_percent(None)))
                    peak_memory = max(peak_memory, int(observed_process.memory_info().rss))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            progress = load_caption_analysis_progress(run_directory) or {}
            signature = (
                progress.get("analysis_stage"),
                progress.get("sampled_frames_completed"),
                progress.get("dense_crops_completed"),
                progress.get("ocr_batches_completed"),
                progress.get("current_item_id"),
                progress.get("last_heartbeat_at"),
            )
            if signature != last_signature:
                last_signature = signature
                last_change = time.monotonic()
            elif time.monotonic() - last_change >= stall_seconds:
                _terminate_process_tree(process)
                progress_writer = CaptionAnalysisProgress(run_directory)
                progress_writer.fail(STALL_ERROR_CODE, "Analyzer made no measurable progress", current_item_id=str(progress.get("current_item_id") or ""))
                raise CaptionAnalysisError(STALL_ERROR_CODE, "Embedded caption analyzer stalled")
        if message is None:
            try:
                message = result_queue.get(timeout=5)
            except queue.Empty as exc:
                raise CaptionAnalysisError("EMBEDDED_CAPTION_WORKER_EXITED_WITHOUT_RESULT", f"Analyzer exited with code {process.exitcode}") from exc
        if message.get("status") != "ok":
            raise CaptionAnalysisError(str(message.get("code") or WORKER_ERROR_CODE), str(message.get("message") or "Analyzer failed"))
        _merge_progress_metadata(
            run_directory,
            {
                "supervisor_metrics": {
                    "peak_memory_bytes": peak_memory,
                    "average_cpu_percent": round(sum(cpu_samples) / len(cpu_samples), 3) if cpu_samples else 0.0,
                    "sample_count": len(cpu_samples),
                }
            },
        )
        return message["result"]
    finally:
        if process.is_alive():
            _terminate_process_tree(process)
        result_queue.close()


def _merge_progress_metadata(run_directory: Path, values: dict[str, Any]) -> None:
    path = run_directory / "diagnostics" / "embedded_caption_progress.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        payload.update(values)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptionAnalysisError("CAPTION_PROGRESS_PERSIST_FAILED", "Unable to persist supervisor metrics") from exc
