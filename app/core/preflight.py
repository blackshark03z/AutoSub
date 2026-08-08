import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.core.config import get_settings


GIB = 1024**3
RUN_ONLY_MIN_FREE_BYTES = 1 * GIB
MEDIA_PROCESSING_MIN_FREE_BYTES = 2 * GIB
PACKAGE_BUILD_MIN_FREE_BYTES = 4 * GIB
PACKAGE_SAFETY_RESERVE_BYTES = 1 * GIB

STORAGE_OPERATION_THRESHOLDS = {
    "run": RUN_ONLY_MIN_FREE_BYTES,
    "media": MEDIA_PROCESSING_MIN_FREE_BYTES,
    "package": PACKAGE_BUILD_MIN_FREE_BYTES,
}


def storage_preflight(
    operation: str,
    target_path: str | Path | None = None,
    *,
    projected_workspace_bytes: int | None = None,
    safety_reserve_bytes: int = PACKAGE_SAFETY_RESERVE_BYTES,
    free_space_getter: Callable[[Path], int] | None = None,
) -> dict:
    operation = operation.strip().lower()
    if operation not in STORAGE_OPERATION_THRESHOLDS:
        raise ValueError(f"Unsupported storage preflight operation: {operation}")

    required = STORAGE_OPERATION_THRESHOLDS[operation]
    if operation == "package" and projected_workspace_bytes is not None:
        if projected_workspace_bytes < 0 or safety_reserve_bytes < 0:
            raise ValueError("Projected workspace and safety reserve must be non-negative.")
        required = max(required, projected_workspace_bytes + safety_reserve_bytes)

    measured_at = datetime.now(timezone.utc).isoformat()
    try:
        settings = get_settings()
        checked = Path(target_path) if target_path is not None else settings.root
        checked = checked.expanduser().resolve()
        usage_path = checked if checked.exists() else checked.parent
        if not usage_path.exists():
            raise FileNotFoundError(str(checked))
        free = free_space_getter(usage_path) if free_space_getter else shutil.disk_usage(usage_path).free
        margin = free - required
        passed = free >= required
        recommendation = (
            f"{operation} operation may proceed; recheck immediately before execution."
            if passed
            else f"Free at least {-margin} more bytes before {operation} operation."
        )
        return {
            "operation": operation,
            "drive_path_checked": str(usage_path),
            "current_free_bytes": int(free),
            "required_minimum_bytes": int(required),
            "passed": bool(passed),
            "margin_bytes": int(margin),
            "measured_at": measured_at,
            "recommendation": recommendation,
        }
    except Exception as exc:
        try:
            fallback_path = str(target_path or get_settings().root)
        except Exception:
            fallback_path = str(target_path or "")
        return {
            "operation": operation,
            "drive_path_checked": fallback_path,
            "current_free_bytes": None,
            "required_minimum_bytes": int(required),
            "passed": False,
            "margin_bytes": None,
            "measured_at": measured_at,
            "recommendation": f"Storage measurement failed closed: {type(exc).__name__}.",
        }


def run_preflight() -> dict:
    settings = get_settings()
    disk = shutil.disk_usage(settings.root)
    storage = storage_preflight("run", settings.root)
    return {
        "python": True,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
        "font_path": str(settings.font_path),
        "font_exists": Path(settings.font_path).exists(),
        "source_exists": settings.source_path.exists(),
        "free_disk_gb": round(disk.free / (1024**3), 2),
        "storage": storage,
        "data_dir_exists": settings.data_dir.exists(),
    }
