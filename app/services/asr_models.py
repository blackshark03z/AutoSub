from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.core.config import get_settings


ASR_MODEL_CANDIDATES = ("tiny", "base", "small")
ASR_MODEL_REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
ASR_HF_NAMESPACE = "Systran"
ASR_HF_PREFIX = "faster-whisper-"
SIMPLE_UI_MODEL_NAME = "small"
SIMPLE_UI_MODEL_ID = f"{ASR_HF_NAMESPACE}/{ASR_HF_PREFIX}{SIMPLE_UI_MODEL_NAME}"
SIMPLE_UI_MODEL_DIRECTORY = f"{ASR_HF_PREFIX}{SIMPLE_UI_MODEL_NAME}"
SIMPLE_UI_MODEL_SNAPSHOT = "536b0662742c02347bc0e980a01041f333bce120"
SIMPLE_UI_MODEL_POLICY = "simple_ui_quality_model"
SIMPLE_UI_MODEL_SOURCE = "bundled"

logger = logging.getLogger(__name__)


def normalize_model_name(model_name: str | None, *, default: str = "tiny") -> str:
    value = str(model_name or "").strip().lower()
    if not value:
        return default
    if value.startswith(f"{ASR_HF_PREFIX}"):
        value = value[len(ASR_HF_PREFIX) :]
    if value.startswith(f"{ASR_HF_NAMESPACE.lower()}/"):
        value = value.split("/", 1)[1]
    if value not in ASR_MODEL_CANDIDATES:
        return default
    return value


def model_family_name(model_name: str | None) -> str:
    return f"{ASR_HF_NAMESPACE}/{ASR_HF_PREFIX}{normalize_model_name(model_name)}"


def model_directory_name(model_name: str | None) -> str:
    return f"{ASR_HF_PREFIX}{normalize_model_name(model_name)}"


def read_run_config() -> dict[str, Any]:
    try:
        return json.loads(get_settings().run_config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def selected_model_name(default: str = "tiny") -> str:
    env = os.environ.get("TOOL_AUTO_SUB_ASR_MODEL_NAME")
    if env:
        return normalize_model_name(env, default=default)
    config = read_run_config()
    hardware = config.get("hardware") if isinstance(config.get("hardware"), dict) else {}
    configured = hardware.get("whisper_model") or hardware.get("asr_model")
    return normalize_model_name(configured, default=default)


def selected_model_family(default: str = "tiny") -> str:
    return model_family_name(selected_model_name(default=default))


def resolve_local_model_path(model_name: str | None = None) -> Path:
    selected = normalize_model_name(model_name or selected_model_name())
    settings = get_settings()
    candidates: list[Path] = []

    configured_dir = os.environ.get("TOOL_AUTO_SUB_ASR_MODEL_DIR")
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser())

    candidates.append(settings.root / "models" / model_directory_name(selected))

    cache_root = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / f"models--{ASR_HF_NAMESPACE}--{ASR_HF_PREFIX}{selected}"
        / "snapshots"
    )
    if cache_root.is_dir():
        candidates.extend(sorted((path for path in cache_root.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True))

    for candidate in candidates:
        resolved = candidate.resolve()
        if all((resolved / name).is_file() for name in ASR_MODEL_REQUIRED_FILES):
            return resolved
    raise FileNotFoundError(f"Offline ASR model unavailable: {model_family_name(selected)}")


def simple_ui_model_path() -> Path:
    return (get_settings().root / "models" / SIMPLE_UI_MODEL_DIRECTORY).resolve()


def normalize_simple_ui_model_name(model_name: str | None) -> str:
    value = str(model_name or "").strip()
    if value and normalize_model_name(value, default=SIMPLE_UI_MODEL_NAME) != SIMPLE_UI_MODEL_NAME:
        logger.info(
            "Normalized legacy Simple UI ASR model setting old=%s normalized=%s policy=%s",
            value,
            SIMPLE_UI_MODEL_NAME,
            SIMPLE_UI_MODEL_POLICY,
        )
    return SIMPLE_UI_MODEL_NAME


def normalize_simple_ui_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(settings or {})
    legacy_values: list[str] = []
    for key in ("asr_model", "whisper_model", "model_name"):
        if key in normalized:
            legacy_values.append(str(normalized.pop(key) or ""))
    asr = normalized.get("asr")
    if isinstance(asr, dict):
        asr_copy = dict(asr)
        for key in ("model", "model_name", "whisper_model"):
            if key in asr_copy:
                legacy_values.append(str(asr_copy.pop(key) or ""))
        normalized["asr"] = asr_copy
    for value in legacy_values:
        normalize_simple_ui_model_name(value)
    normalized.update(
        {
            "asr_provider": "faster_whisper",
            "asr_model": SIMPLE_UI_MODEL_NAME,
            "asr_model_path": str(simple_ui_model_path()),
            "asr_model_source": SIMPLE_UI_MODEL_SOURCE,
            "asr_model_policy": SIMPLE_UI_MODEL_POLICY,
        }
    )
    return normalized


def resolve_simple_ui_model_path() -> Path:
    candidate = simple_ui_model_path()
    missing = [name for name in (*ASR_MODEL_REQUIRED_FILES, "MODEL_METADATA.json") if not (candidate / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Bundled Simple UI ASR model is incomplete at {candidate}: missing {', '.join(missing)}"
        )
    try:
        metadata = json.loads((candidate / "MODEL_METADATA.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FileNotFoundError(f"Bundled Simple UI ASR model metadata is unreadable at {candidate}") from exc
    if (
        metadata.get("model_name") != SIMPLE_UI_MODEL_NAME
        or metadata.get("model_id") != SIMPLE_UI_MODEL_ID
        or metadata.get("snapshot") != SIMPLE_UI_MODEL_SNAPSHOT
        or metadata.get("local_files_only") is not True
    ):
        raise FileNotFoundError(f"Bundled Simple UI ASR model identity mismatch at {candidate}")
    return candidate
