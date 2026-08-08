from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.hashing import sha256_file


OVERRIDE_SCHEMA_VERSION = 1
HUMAN_REVIEWED_CAPTION_PROVENANCE = "human_verified_caption_override"
OWNER_APPROVED = "OWNER_APPROVED"
PROVIDER_SEMANTIC_CONTRACT_FAILURE = "PROVIDER_SEMANTIC_CONTRACT_FAILURE"
OVERRIDE_ROOT_ENV = "TOOL_AUTO_SUB_REVIEW_OVERRIDE_DIR"
_INTERVAL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class CaptionOverrideValidationError(ValueError):
    pass


def default_override_root() -> Path:
    configured = os.environ.get(OVERRIDE_ROOT_ENV)
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise CaptionOverrideValidationError("LOCALAPPDATA is unavailable.")
    return Path(local_app_data) / "ToolAutoSubBeta" / "review_overrides"


def caption_region_fingerprint(
    *,
    source_video_sha256: str,
    interval: dict[str, Any],
) -> str:
    """Bind an override to one exact caption interval without storing pixels."""
    payload = {
        "source_video_sha256": _validate_sha256(source_video_sha256, "source_video_sha256"),
        "interval_id": _interval_id(interval),
        "start_ms": _time_ms(interval, "start_time"),
        "end_ms": _time_ms(interval, "end_time"),
        "ocr_source_text": _required_text(interval.get("source_text"), "source_text"),
    }
    return _sha256_json(payload)


def create_reviewed_caption_override(
    *,
    source_path: Path,
    interval: dict[str, Any],
    corrected_chinese: str,
    approved_english: str,
    original_ocr_candidates: list[dict[str, Any]] | None = None,
    reviewer_authority: str = OWNER_APPROVED,
    reason: str = PROVIDER_SEMANTIC_CONTRACT_FAILURE,
    override_root: Path | None = None,
    created_at_utc: str | None = None,
) -> tuple[dict[str, Any], Path]:
    source_path = Path(source_path)
    source_hash = sha256_file(source_path)
    interval_id = _interval_id(interval)
    if reviewer_authority != OWNER_APPROVED:
        raise CaptionOverrideValidationError("Unsupported reviewer authority.")
    if reason != PROVIDER_SEMANTIC_CONTRACT_FAILURE:
        raise CaptionOverrideValidationError("Unsupported override reason.")
    candidates = original_ocr_candidates
    if candidates is None:
        candidates = interval.get("ocr_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise CaptionOverrideValidationError("Original OCR candidates are required.")
    record: dict[str, Any] = {
        "schema_version": OVERRIDE_SCHEMA_VERSION,
        "interval_id": interval_id,
        "source_video_sha256": source_hash,
        "interval_start_ms": _time_ms(interval, "start_time"),
        "interval_end_ms": _time_ms(interval, "end_time"),
        "caption_region_fingerprint": caption_region_fingerprint(
            source_video_sha256=source_hash,
            interval=interval,
        ),
        "original_ocr_candidates": _canonical_candidates(candidates),
        "corrected_chinese": _required_text(corrected_chinese, "corrected_chinese"),
        "approved_english": _required_text(approved_english, "approved_english"),
        "reviewer_authority": reviewer_authority,
        "reason": reason,
        "created_at_utc": created_at_utc or datetime.now(timezone.utc).isoformat(),
        "provenance": HUMAN_REVIEWED_CAPTION_PROVENANCE,
    }
    record["record_sha256"] = _sha256_json(record)
    validate_reviewed_caption_override(
        record,
        source_video_sha256=source_hash,
        interval=interval,
    )
    root = Path(override_root) if override_root is not None else default_override_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / _override_filename(source_hash, interval_id)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return record, target


def load_matching_caption_override(
    *,
    source_path: Path,
    interval: dict[str, Any],
    source_video_sha256: str | None = None,
    override_root: Path | None = None,
) -> dict[str, Any] | None:
    source_hash = source_video_sha256 or sha256_file(Path(source_path))
    root = Path(override_root) if override_root is not None else default_override_root()
    path = root / _override_filename(source_hash, _interval_id(interval))
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptionOverrideValidationError("Caption override record is unreadable.") from exc
    validate_reviewed_caption_override(
        record,
        source_video_sha256=source_hash,
        interval=interval,
    )
    return {**record, "record_path": str(path)}


def validate_reviewed_caption_override(
    record: dict[str, Any],
    *,
    source_video_sha256: str,
    interval: dict[str, Any],
) -> None:
    if not isinstance(record, dict):
        raise CaptionOverrideValidationError("Caption override record must be an object.")
    if record.get("schema_version") != OVERRIDE_SCHEMA_VERSION:
        raise CaptionOverrideValidationError("Unsupported caption override schema.")
    expected_hash = _sha256_json({key: value for key, value in record.items() if key != "record_sha256"})
    if record.get("record_sha256") != expected_hash:
        raise CaptionOverrideValidationError("Caption override record hash mismatch.")
    if record.get("source_video_sha256") != _validate_sha256(source_video_sha256, "source_video_sha256"):
        raise CaptionOverrideValidationError("Caption override source video mismatch.")
    if record.get("interval_id") != _interval_id(interval):
        raise CaptionOverrideValidationError("Caption override interval ID mismatch.")
    if record.get("interval_start_ms") != _time_ms(interval, "start_time"):
        raise CaptionOverrideValidationError("Caption override start time mismatch.")
    if record.get("interval_end_ms") != _time_ms(interval, "end_time"):
        raise CaptionOverrideValidationError("Caption override end time mismatch.")
    expected_fingerprint = caption_region_fingerprint(
        source_video_sha256=source_video_sha256,
        interval=interval,
    )
    if record.get("caption_region_fingerprint") != expected_fingerprint:
        raise CaptionOverrideValidationError("Caption override region fingerprint mismatch.")
    if record.get("reviewer_authority") != OWNER_APPROVED:
        raise CaptionOverrideValidationError("Caption override authority is invalid.")
    if record.get("reason") != PROVIDER_SEMANTIC_CONTRACT_FAILURE:
        raise CaptionOverrideValidationError("Caption override reason is invalid.")
    if record.get("provenance") != HUMAN_REVIEWED_CAPTION_PROVENANCE:
        raise CaptionOverrideValidationError("Caption override provenance is invalid.")
    _required_text(record.get("corrected_chinese"), "corrected_chinese")
    _required_text(record.get("approved_english"), "approved_english")
    if not isinstance(record.get("original_ocr_candidates"), list) or not record["original_ocr_candidates"]:
        raise CaptionOverrideValidationError("Caption override OCR candidates are invalid.")


def _override_filename(source_hash: str, interval_id: str) -> str:
    return f"{_validate_sha256(source_hash, 'source_video_sha256')}_{interval_id}.json"


def _interval_id(interval: dict[str, Any]) -> str:
    interval_id = str(interval.get("id") or "").strip()
    if not _INTERVAL_ID_RE.fullmatch(interval_id):
        raise CaptionOverrideValidationError("Caption interval ID is invalid.")
    return interval_id


def _time_ms(interval: dict[str, Any], key: str) -> int:
    try:
        value = round(float(interval[key]) * 1000)
    except (KeyError, TypeError, ValueError) as exc:
        raise CaptionOverrideValidationError(f"Caption interval {key} is invalid.") from exc
    if value < 0:
        raise CaptionOverrideValidationError(f"Caption interval {key} is invalid.")
    return value


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CaptionOverrideValidationError(f"Caption override {field} is empty.")
    return text


def _canonical_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise CaptionOverrideValidationError("Caption override OCR candidate is invalid.")
        normalized.append(
            {
                "text": _required_text(candidate.get("text"), "original_ocr_candidates.text"),
                "count": max(1, int(candidate.get("count") or 1)),
                "median_confidence": round(float(candidate.get("median_confidence") or 0), 6),
            }
        )
    return normalized


def _validate_sha256(value: str, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise CaptionOverrideValidationError(f"{field} is not a SHA-256 value.")
    return normalized


def _sha256_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
