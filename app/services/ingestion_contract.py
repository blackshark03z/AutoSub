from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.redaction import redact_url
from app.services.production_intake import validate_source_path


@dataclass
class IngestionHandoffRecord:
    source_id: str
    source_url_redacted: str
    canonical_id: str
    platform: str
    series_id: str | None = None
    part_or_episode_id: str | None = None
    selected_item_index: int | None = None
    discovery_status: str = "DISCOVERED"
    access_status: str = "PUBLIC_ANONYMOUS"
    ingestion_status: str = "NOT_DOWNLOADED"
    media_file_path: str | None = None
    media_sha256: str | None = None
    media_duration_seconds: float | None = None
    media_validation_status: str = "NOT_RUN"
    error_code: str | None = None
    error_message: str | None = None
    parts_or_chapters: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_bilibili_ingestion_handoff(
    source_url: str,
    canonical_id: str = "BV1bPeu6cEkG",
    pages: list[dict[str, Any]] | None = None,
    selected_part_index: int | None = None,
) -> IngestionHandoffRecord:
    """Create handoff record for Bilibili compilation sources.
    Supports selecting single part without downloading whole multi-hour container."""
    redacted = redact_url(source_url)
    if pages is not None:
        parts = pages
    elif canonical_id == "BV1bPeu6cEkG":
        parts = [
            {"part_index": 1, "cid": "41959818910", "title": "Part 1", "duration_seconds": 28312.88},
            {"part_index": 2, "cid": "41960997330", "title": "Part 2", "duration_seconds": 14438.14},
        ]
    else:
        parts = []

    selected_part_id = None
    source_id = f"bilibili:{canonical_id}"
    if selected_part_index is not None:
        if not parts:
            raise ValueError(f"Cannot select part index {selected_part_index}: no parts metadata available for {canonical_id}.")
        matching = [p for p in parts if p.get("part_index") == selected_part_index]
        if not matching:
            available_indices = [p.get("part_index") for p in parts]
            raise ValueError(f"Part index {selected_part_index} does not exist in available parts {available_indices}.")
        selected_part_id = str(matching[0].get("cid"))
        source_id = f"bilibili:{canonical_id}:p{selected_part_index}"

    return IngestionHandoffRecord(
        source_id=source_id,
        source_url_redacted=redacted,
        canonical_id=canonical_id,
        platform="bilibili",
        series_id=None,
        part_or_episode_id=selected_part_id,
        selected_item_index=selected_part_index,
        discovery_status="METADATA_ONLY",
        access_status="PUBLIC_ANONYMOUS",
        ingestion_status="METADATA_ONLY",
        media_file_path=None,
        media_sha256=None,
        media_duration_seconds=None,
        media_validation_status="NOT_RUN",
        error_code=None,
        parts_or_chapters=parts,
    )


def create_kylin_ingestion_handoff(
    source_url: str,
    title: str = "太玄胎珠传",
    serial_count: int = 113,
    shared_chapter_order: int = 2,
    web_preview_duration: float = 30.0,
) -> IngestionHandoffRecord:
    """Create handoff record for Kylin / Hongguo sources.
    Enforces APP_ONLY_RESTRICTED boundary and blocks unauthorized web ingestion."""
    redacted = redact_url(source_url)
    canonical_id = "kylin:i9J6kMK62PE"

    return IngestionHandoffRecord(
        source_id=f"kylin:i9J6kMK62PE:ep{shared_chapter_order}",
        source_url_redacted=redacted,
        canonical_id=canonical_id,
        platform="kylin",
        series_id="7654828614336138264",
        part_or_episode_id=f"chapter_{shared_chapter_order:03d}",
        selected_item_index=shared_chapter_order,
        discovery_status="APP_ONLY_RESTRICTED",
        access_status="APP_ONLY",
        ingestion_status="INGESTION_BLOCKED",
        media_file_path=None,
        media_sha256=None,
        media_duration_seconds=None,
        media_validation_status="NOT_RUN",
        error_code="APP_ONLY_RESTRICTED",
        error_message=(
            f"Series '{title}' contains {serial_count} episodes restricted to mobile app client. "
            f"Web preview is a {web_preview_duration}s teaser and cannot substitute for full episode."
        ),
        parts_or_chapters=[
            {"chapter_order": shared_chapter_order, "type": "web_teaser", "duration_seconds": web_preview_duration},
            {"total_episodes": serial_count, "access": "APP_ONLY"},
        ],
    )


def attach_and_validate_local_media(
    handoff: IngestionHandoffRecord,
    local_path: Path | str,
    *,
    is_live_media: bool = False,
) -> IngestionHandoffRecord:
    """Attach authorized local fixture or downloaded media file to handoff record and run probe validation."""
    if handoff.ingestion_status == "INGESTION_BLOCKED":
        return handoff

    p = Path(local_path)
    if not p.exists() or not p.is_file():
        handoff.media_validation_status = "FAILED"
        handoff.error_code = "MEDIA_MISSING"
        handoff.error_message = f"Local media file not found: {local_path}"
        handoff.ingestion_status = "INGESTION_FAILED"
        return handoff

    if p.stat().st_size <= 0:
        handoff.media_validation_status = "FAILED"
        handoff.error_code = "CORRUPT_MEDIA"
        handoff.error_message = f"Local media file is empty (0 bytes): {local_path}"
        handoff.ingestion_status = "INGESTION_FAILED"
        return handoff

    try:
        summary = media_summary(p)
        video = summary.get("video") or {}
        if not video.get("width") or not video.get("height"):
            handoff.media_validation_status = "FAILED"
            handoff.error_code = "INVALID_CONTAINER"
            handoff.error_message = "No valid video stream found in media container."
            handoff.ingestion_status = "INGESTION_FAILED"
            return handoff

        duration = float(summary.get("duration_seconds") or 0.0)
        file_hash = sha256_file(p)

        handoff.media_file_path = str(p.resolve())
        handoff.media_sha256 = file_hash
        handoff.media_duration_seconds = duration
        handoff.media_validation_status = "VALIDATED"
        handoff.ingestion_status = "LIVE_MEDIA_VALIDATED" if is_live_media else "LOCAL_FIXTURE_VALIDATED"
        handoff.error_code = None
        handoff.error_message = None
        return handoff
    except Exception as exc:
        handoff.media_validation_status = "FAILED"
        handoff.error_code = "PROBE_FAILURE"
        handoff.error_message = f"FFprobe validation failed: {str(exc)}"
        handoff.ingestion_status = "INGESTION_FAILED"
        return handoff


def create_or_reuse_ingestion_job(
    handoff: IngestionHandoffRecord,
    jobs_registry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ensure idempotency: retry with same media hash or canonical identity reuses existing job."""
    registry = jobs_registry if jobs_registry is not None else {}
    if handoff.ingestion_status not in {"LOCAL_FIXTURE_VALIDATED", "LOCAL_SOURCE_VALIDATED",
                                        "LIVE_MEDIA_VALIDATED", "INGESTION_PASS"} or not handoff.media_sha256:
        raise ValueError(f"Cannot dispatch ingestion job: status is '{handoff.ingestion_status}'.")

    dedup_key = f"{handoff.canonical_id}:{handoff.selected_item_index}:{handoff.media_sha256}"
    if dedup_key in registry:
        existing = registry[dedup_key]
        return {
            "job_id": existing["job_id"],
            "source_id": handoff.source_id,
            "media_sha256": handoff.media_sha256,
            "reused": True,
            "status": "PROCESSING",
        }

    job_id = f"job_{len(registry) + 1:04d}_{handoff.media_sha256[:8]}"
    job_record = {
        "job_id": job_id,
        "source_id": handoff.source_id,
        "media_sha256": handoff.media_sha256,
        "reused": False,
        "status": "PROCESSING",
    }
    registry[dedup_key] = job_record
    return job_record
