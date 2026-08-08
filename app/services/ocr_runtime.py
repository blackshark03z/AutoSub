from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.config import get_settings


class OCRRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRRuntimeConfig:
    runtime_root: Path
    python_path: Path
    model_root: Path
    temp_root: Path
    log_root: Path
    timeout_seconds: int = 30


def discover_ocr_runtime_config_path(config_path: Path | None = None) -> Path | None:
    settings = get_settings()
    candidates: list[Path] = []
    if config_path is not None:
      candidates.append(Path(config_path))
    candidates.extend(
        [
            settings.root / "operator" / "ocr_runtime_config.local.json",
            settings.root / "addons" / "ocr_runtime" / "operator" / "ocr_runtime_config.local.json",
        ]
    )
    env_config = os.environ.get("TOOL_AUTO_SUB_OCR_RUNTIME_CONFIG", "").strip()
    if env_config:
        candidates.append(Path(env_config))
    env_root = os.environ.get("TOOL_AUTO_SUB_OCR_RUNTIME_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root) / "operator" / "ocr_runtime_config.local.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def load_ocr_runtime_config(config_path: Path | None = None) -> OCRRuntimeConfig:
    path = discover_ocr_runtime_config_path(config_path)
    if path is None:
        raise OCRRuntimeError("OCR runtime config is missing")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    payload = {**payload, "_config_path": str(path)}
    runtime_root = _required_path(payload, "runtime_root")
    config = OCRRuntimeConfig(
        runtime_root=runtime_root,
        python_path=_required_path(payload, "python_path"),
        model_root=_required_path(payload, "model_root"),
        temp_root=_required_path(payload, "temp_root"),
        log_root=_required_path(payload, "log_root"),
        timeout_seconds=int(payload.get("timeout_seconds", 30)),
    )
    _validate_runtime_config(config)
    return config


def get_ocr_runtime_status(config_path: Path | None = None) -> dict[str, Any]:
    settings = get_settings()
    resolved_path = discover_ocr_runtime_config_path(config_path)
    if resolved_path is None:
        return {
            "available": False,
            "status": "missing",
            "configured_path": None,
            "runtime_path": None,
            "runtime_version": None,
            "model_availability": {"ch_det": False, "ch_rec": False, "ch_cls": False},
            "import_result": "missing",
            "smoke_result": "not_run",
            "last_verification_time": None,
            "actionable_fix_message": "Install the CP11C OCR add-on or provide TOOL_AUTO_SUB_OCR_RUNTIME_CONFIG.",
        }
    try:
        config = load_ocr_runtime_config(resolved_path)
    except OCRRuntimeError as exc:
        return {
            "available": False,
            "status": "invalid",
            "configured_path": str(resolved_path),
            "runtime_path": None,
            "runtime_version": None,
            "model_availability": {"ch_det": False, "ch_rec": False, "ch_cls": False},
            "import_result": "fail",
            "smoke_result": "not_run",
            "last_verification_time": None,
            "actionable_fix_message": str(exc),
        }
    verification = _read_verification_summary(config.runtime_root.parent)
    model_availability = {
        "ch_det": (config.model_root / "ch_det").exists(),
        "ch_rec": (config.model_root / "ch_rec").exists(),
        "ch_cls": (config.model_root / "ch_cls").exists(),
    }
    available = config.python_path.exists() and all(model_availability.values())
    smoke_result = verification.get("smoke_result", "not_run")
    import_result = verification.get("import_result", "unknown")
    version = verification.get("runtime_version") or _runtime_version(config.python_path)
    actionable = verification.get("actionable_fix_message") or (
        "OCR add-on is ready." if available else "Verify the installed OCR add-on and model files."
    )
    return {
        "available": available,
        "status": "available" if available else "missing",
        "configured_path": str(resolved_path),
        "runtime_path": str(config.runtime_root),
        "runtime_version": version,
        "model_availability": model_availability,
        "import_result": import_result,
        "smoke_result": smoke_result,
        "last_verification_time": verification.get("verified_at"),
        "actionable_fix_message": actionable,
    }


def run_ocr_on_image(image_path: Path, config_path: Path | None = None) -> dict[str, Any]:
    payload = run_ocr_on_images([image_path], config_path=config_path)
    frame = payload["frames"][0]
    result = {
        "ok": payload.get("ok", True),
        "items": frame.get("items", []),
    }
    for key in ("contains_cjk", "runtime", "model_version"):
        if key in payload:
            result[key] = payload[key]
    return result


def run_ocr_on_images(
    image_paths: list[Path],
    config_path: Path | None = None,
    *,
    manifest_path: Path | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Run one OCR worker for a frame sequence so model load is paid once."""
    config = load_ocr_runtime_config(config_path)
    if not image_paths:
        raise OCRRuntimeError("OCR image sequence is empty")
    images = [_resolve_allowed_image(image, config.runtime_root) for image in image_paths]
    _validate_model_files(config.model_root)
    worker = get_settings().root / "tools" / "ocr_runtime_worker.py"
    config_file = discover_ocr_runtime_config_path(config_path)
    if config_file is None:
        raise OCRRuntimeError("OCR runtime config is missing")
    owned_manifest = manifest_path is None
    config.temp_root.mkdir(parents=True, exist_ok=True)
    if manifest_path is None:
        descriptor, filename = tempfile.mkstemp(prefix="ocr_frames_", suffix=".json", dir=config.temp_root)
        os.close(descriptor)
        manifest = Path(filename)
    else:
        manifest = manifest_path
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"images": [str(image) for image in images]}, ensure_ascii=False),
        encoding="utf-8",
    )
    command = [
        str(config.python_path),
        str(worker),
        "--config",
        str(config_file),
        "--manifest",
        str(manifest),
    ]
    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        if heartbeat_callback is None:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
                encoding="utf-8",
                env=env,
                cwd=str(config.runtime_root),
            )
        else:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=str(config.runtime_root),
            )
            deadline = time.monotonic() + config.timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.communicate()
                    raise subprocess.TimeoutExpired(command, config.timeout_seconds)
                try:
                    stdout, stderr = process.communicate(timeout=min(5.0, remaining))
                    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                    break
                except subprocess.TimeoutExpired:
                    try:
                        heartbeat_callback()
                    except BaseException:
                        process.kill()
                        process.communicate()
                        raise
    except subprocess.TimeoutExpired as exc:
        raise OCRRuntimeError("OCR worker timed out") from exc
    finally:
        if owned_manifest:
            manifest.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise OCRRuntimeError("OCR worker failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OCRRuntimeError("OCR worker returned malformed JSON") from exc
    if isinstance(payload, dict) and "frames" not in payload and isinstance(payload.get("items"), list):
        payload["frames"] = [{"index": 0, "items": payload["items"]}]
    if not isinstance(payload, dict) or not isinstance(payload.get("frames"), list):
        raise OCRRuntimeError("OCR worker returned invalid payload")
    return _redact_paths(payload)


def is_cjk_text(text: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in text
    )


def _required_path(payload: dict[str, Any], key: str) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OCRRuntimeError(f"OCR runtime config field {key} is required")
    path = Path(value)
    if not path.is_absolute():
        config_path = payload.get("_config_path")
        base = Path(config_path).parent.parent if config_path else Path.cwd()
        path = base / path
    return path.resolve()


def _validate_runtime_config(config: OCRRuntimeConfig) -> None:
    if not str(config.python_path).lower().startswith(str(config.runtime_root).lower()):
        raise OCRRuntimeError("OCR python must be inside the runtime root")
    if not config.python_path.exists():
        raise OCRRuntimeError("OCR python does not exist")
    for path in (config.model_root, config.temp_root, config.log_root):
        if not str(path).lower().startswith(str(config.runtime_root).lower()):
            raise OCRRuntimeError("OCR runtime paths must stay inside runtime root")


def _validate_model_files(model_root: Path) -> None:
    required = (
        model_root / "ch_det" / "inference.pdmodel",
        model_root / "ch_det" / "inference.pdiparams",
        model_root / "ch_rec" / "inference.pdmodel",
        model_root / "ch_rec" / "inference.pdiparams",
        model_root / "ch_cls" / "inference.pdmodel",
        model_root / "ch_cls" / "inference.pdiparams",
    )
    if not all(path.is_file() for path in required):
        raise OCRRuntimeError("OCR model files are missing; refusing implicit download")


def _resolve_allowed_image(image_path: Path, runtime_root: Path) -> Path:
    candidate = image_path.resolve()
    settings = get_settings()
    allowed_roots = [settings.root.resolve(), settings.data_dir.resolve(), runtime_root.resolve()]
    if not candidate.exists() or not candidate.is_file():
        raise OCRRuntimeError("OCR image input does not exist")
    if not any(_is_relative_to(candidate, root) for root in allowed_roots):
        raise OCRRuntimeError("OCR image input is outside approved roots")
    return candidate


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _redact_paths(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    redacted.pop("image_path", None)
    redacted.pop("model_root", None)
    return redacted


def _read_verification_summary(addon_root: Path) -> dict[str, Any]:
    path = addon_root / "verification.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_version(python_path: Path) -> str | None:
    try:
        completed = subprocess.run([str(python_path), "-c", "import sys; print(sys.version.split()[0])"], capture_output=True, text=True, check=False, timeout=10)
    except Exception:
        return None
    version = (completed.stdout or completed.stderr).strip().splitlines()
    return version[0] if version else None
