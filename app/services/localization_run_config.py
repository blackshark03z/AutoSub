from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

SUBTITLE_LOCALIZATION = "subtitle_localization"
FULL_VISUAL_LOCALIZATION = "full_visual_localization"
FULL_LOCALIZATION_VOICE = "full_localization_voice"
LOCALIZATION_MODES = {SUBTITLE_LOCALIZATION, FULL_VISUAL_LOCALIZATION, FULL_LOCALIZATION_VOICE}
COMPUTE_AUTO = "auto"
COMPUTE_LOCAL_ONLY = "local_only"
COMPUTE_CLOUD_NO_OVERAGE = "cloud_allowed_no_paid_overage"
COMPUTE_POLICIES = {COMPUTE_AUTO, COMPUTE_LOCAL_ONLY, COMPUTE_CLOUD_NO_OVERAGE}
DEFAULT_KOKORO_VOICE_ID = "bm_george"
DEFAULT_KOKORO_SPEED = 1.0
MIN_KOKORO_SPEED = 0.85
MAX_KOKORO_SPEED = 1.20
OUTPUT_AFFECTING_SETTINGS = (
    "target_language", "caption_mode", "subtitle_mode", "localization_mode",
    "translation_policy", "subtitle_quality_profile", "compute_policy",
    "include_ass_sidecar", "subtitle_style", "tts_provider", "voice_id", "voice_speed",
)

class LocalizationRunConfigError(ValueError):
    pass

@dataclass(frozen=True)
class LocalizationRunSnapshot:
    settings: dict[str, Any]
    canonical_json: str
    sha256: str

def _bounded_float(value: Any, *, name: str, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise LocalizationRunConfigError(f"{name} must be a number") from exc
    if parsed < minimum or parsed > maximum:
        raise LocalizationRunConfigError(f"{name} must be between {minimum:.2f} and {maximum:.2f}")
    return round(parsed, 3)

def normalize_localization_v2_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(settings or {})
    mode = str(normalized.get("localization_mode") or SUBTITLE_LOCALIZATION).strip().lower()
    if mode not in LOCALIZATION_MODES:
        raise LocalizationRunConfigError(f"Unsupported localization_mode: {mode}")
    normalized["localization_mode"] = mode
    compute_policy = str(normalized.get("compute_policy") or COMPUTE_AUTO).strip().lower()
    if compute_policy not in COMPUTE_POLICIES:
        raise LocalizationRunConfigError(f"Unsupported compute_policy: {compute_policy}")
    normalized["compute_policy"] = compute_policy
    normalized["translation_policy"] = str(normalized.get("translation_policy") or "quality_auto").strip().lower()
    normalized["subtitle_quality_profile"] = str(normalized.get("subtitle_quality_profile") or "english_v2").strip().lower()
    if mode == FULL_LOCALIZATION_VOICE:
        normalized["tts_provider"] = str(normalized.get("tts_provider") or "kokoro_local").strip().lower()
        if normalized["tts_provider"] != "kokoro_local":
            raise LocalizationRunConfigError("V2 voice mode currently requires tts_provider=kokoro_local")
        voice_id = str(normalized.get("voice_id") or DEFAULT_KOKORO_VOICE_ID).strip()
        if not voice_id:
            raise LocalizationRunConfigError("voice_id is required when voice localization is enabled")
        normalized["voice_id"] = voice_id
        normalized["voice_speed"] = _bounded_float(normalized.get("voice_speed", DEFAULT_KOKORO_SPEED), name="voice_speed", minimum=MIN_KOKORO_SPEED, maximum=MAX_KOKORO_SPEED)
    else:
        normalized["tts_provider"] = None
        normalized["voice_id"] = None
        normalized["voice_speed"] = None
    return normalized

def build_localization_run_snapshot(settings: dict[str, Any] | None) -> LocalizationRunSnapshot:
    normalized = normalize_localization_v2_settings(settings)
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return LocalizationRunSnapshot(settings=normalized, canonical_json=canonical, sha256=digest)

def output_affecting_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_localization_v2_settings(settings)
    return {key: normalized.get(key) for key in OUTPUT_AFFECTING_SETTINGS}

def settings_compatible_for_output(existing: dict[str, Any] | None, requested: dict[str, Any] | None) -> bool:
    try:
        return output_affecting_settings(existing) == output_affecting_settings(requested)
    except LocalizationRunConfigError:
        return False
