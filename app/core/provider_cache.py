import json
import os
import re
from pathlib import Path
from typing import Any

from app.core.canonical import canonical_hash, canonical_json
from app.core.config import get_settings
from app.core.paths import ensure_dir


def provider_cache_path(provider: str, request_hash: str) -> Path:
    if re.fullmatch(r"[A-Za-z0-9_.-]+", provider) is None:
        raise ValueError("Invalid provider cache namespace")
    if re.fullmatch(r"[0-9a-f]{64}", request_hash) is None:
        raise ValueError("Invalid canonical request hash")
    settings = get_settings()
    return settings.data_dir / "provider_cache" / provider / f"{request_hash}.json"


def build_request_hash(payload: dict[str, Any]) -> str:
    return canonical_hash(payload)


def read_cached_response(provider: str, request_hash: str) -> dict[str, Any] | None:
    path = provider_cache_path(provider, request_hash)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_cached_response(provider: str, request_hash: str, payload: dict[str, Any]) -> Path:
    path = provider_cache_path(provider, request_hash)
    ensure_dir(path.parent)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(canonical_json(payload), encoding="utf-8")
    os.replace(temp_path, path)
    return path
