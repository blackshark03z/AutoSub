from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class OfflineTranslationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OfflineTranslationConfig:
    python_path: Path
    packages_root: Path
    model_id: str
    timeout_seconds: int = 60


def discover_translation_config_path(config_path: Path | None = None) -> Path | None:
    settings = get_settings()
    candidates = []
    if config_path is not None:
        candidates.append(Path(config_path))
    candidates.extend([
        settings.root / "operator" / "translation_runtime_config.local.json",
        settings.root / "addons" / "translation_runtime" / "operator" / "translation_runtime_config.local.json",
    ])
    env_path = os.environ.get("TOOL_AUTO_SUB_TRANSLATION_RUNTIME_CONFIG", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def load_translation_config(config_path: Path | None = None) -> OfflineTranslationConfig:
    path = discover_translation_config_path(config_path)
    if path is None:
        raise OfflineTranslationError("Offline translation runtime config is missing")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    base = path.parent.parent

    def resolve(value: Any, key: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise OfflineTranslationError(f"Offline translation config field {key} is required")
        candidate = Path(value)
        return (base / candidate if not candidate.is_absolute() else candidate).resolve()

    python_path = resolve(payload.get("python_path"), "python_path")
    packages_root = resolve(payload.get("packages_root"), "packages_root")
    if not python_path.is_file():
        raise OfflineTranslationError("Offline translation Python runtime is missing")
    package = packages_root / str(payload.get("model_id") or "")
    if not (package / "model" / "model.bin").is_file():
        raise OfflineTranslationError("Offline translation model is missing; refusing download")
    return OfflineTranslationConfig(
        python_path=python_path,
        packages_root=packages_root,
        model_id=str(payload.get("model_id") or "translate-zh_en-1_9"),
        timeout_seconds=max(5, int(payload.get("timeout_seconds", 60))),
    )


def translate_source_captions(
    source_texts: list[str],
    config_path: Path | None = None,
) -> list[dict[str, Any]]:
    if not source_texts or any(not str(text).strip() for text in source_texts):
        raise OfflineTranslationError("OCR source caption text is empty")
    config = load_translation_config(config_path)
    request_fd, request_name = tempfile.mkstemp(prefix="translation_", suffix=".json")
    os.close(request_fd)
    request = Path(request_name)
    request.write_text(json.dumps({"texts": source_texts}, ensure_ascii=False), encoding="utf-8")
    worker = get_settings().root / "tools" / "offline_translation_worker.py"
    command = [
        str(config.python_path),
        str(worker),
        "--packages",
        str(config.packages_root),
        "--model-id",
        config.model_id,
        "--request",
        str(request),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=config.timeout_seconds,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise OfflineTranslationError("Offline translation worker timed out") from exc
    finally:
        request.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise OfflineTranslationError("Offline translation worker failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OfflineTranslationError("Offline translation worker returned malformed JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("translations"), list):
        raise OfflineTranslationError("Offline translation worker returned invalid payload")
    if len(payload["translations"]) != len(source_texts):
        raise OfflineTranslationError("Offline translation count does not match OCR captions")
    return [
        {
            "source_text": str(source_texts[index]),
            "translated_text": str(item.get("text") or "").strip(),
            "runtime": payload.get("runtime", "argostranslate"),
            "model": payload.get("model", config.model_id),
            "confidence": item.get("confidence"),
        }
        for index, item in enumerate(payload["translations"])
    ]
