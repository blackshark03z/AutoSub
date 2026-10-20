"""Owner-selected Hongguo local-file intake for M3 Phase B-1.

This is a narrow local-file handoff, not URL ingestion. It verifies the exact
pre-existing episode file and binds provider/series/episode/source-hash identity
before the accepted M2 OCR -> en-US -> mask/render pipeline runs.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from app.core.media import media_summary
from app.services.ingestion_contract import (
    IngestionHandoffRecord,
    attach_and_validate_local_media,
)

PROVIDER = "HONGGUO_LOCAL_FILE"
INPUT_MODE = "LOCAL_INPUT"
SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}
IDENTITY_FIELDS = ("provider", "series_id", "episode", "source_sha256", "duration_ms")
IDENTITY_FILE = "local_input_identity.json"


class LocalSourceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_local_media(path_value: str | Path) -> Path:
    source = Path(path_value).expanduser()
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError:
        raise LocalSourceError("SOURCE_MISSING", f"local media does not exist: {source}") from None
    if not source.is_file():
        raise LocalSourceError("SOURCE_NOT_FILE", f"local media is not a regular file: {source}")
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise LocalSourceError("UNSUPPORTED_EXTENSION", f"unsupported media extension: {source.suffix}")
    if source.stat().st_size <= 0:
        raise LocalSourceError("EMPTY_SOURCE", f"local media is empty: {source}")
    return source


def probe_local_media(path: Path) -> dict[str, Any]:
    try:
        summary = media_summary(path)
    except Exception as exc:
        raise LocalSourceError("PROBE_FAILURE", f"ffprobe failed: {type(exc).__name__}") from exc
    video = summary.get("video") or {}
    audio = summary.get("audio") or {}
    if not video.get("width") or not video.get("height"):
        raise LocalSourceError("INVALID_CONTAINER", "no decodable video stream")
    if not audio.get("codec"):
        raise LocalSourceError("NO_AUDIO_STREAM", "no audio stream; source audio must be preserved")
    duration = float(summary.get("duration_seconds") or 0.0)
    if duration <= 0.0:
        raise LocalSourceError("INVALID_DURATION", "media duration is not positive")
    _assert_fully_decodable(path, duration)
    return summary


def _assert_fully_decodable(path: Path, duration_seconds: float) -> None:
    """Reject truncated media that still exposes valid container metadata."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
         "-progress", "pipe:1", "-nostats"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
    )
    if proc.returncode != 0:
        raise LocalSourceError("CORRUPT_MEDIA", "media decode failed")
    times = [int(value) for value in re.findall(r"out_time_ms=(\d+)", proc.stdout or "")]
    decoded_seconds = (max(times) / 1_000_000.0) if times else 0.0
    if decoded_seconds < duration_seconds - 1.0:
        raise LocalSourceError(
            "TRUNCATED_MEDIA",
            f"decoded {decoded_seconds:.3f}s of {duration_seconds:.3f}s",
        )


def create_hongguo_local_handoff(
    source_path: Path,
    *,
    series_id: str,
    episode: str,
    title: str,
    expected_sha256: str | None = None,
    summary: dict[str, Any] | None = None,
) -> IngestionHandoffRecord:
    path = resolve_local_media(source_path)
    actual_sha = sha256_file(path)
    if expected_sha256 and expected_sha256.lower() != actual_sha.lower():
        raise LocalSourceError(
            "SOURCE_HASH_MISMATCH",
            f"expected {expected_sha256.lower()} but file is {actual_sha}",
        )
    probe = summary or probe_local_media(path)
    episode_number = int(str(episode).lstrip("0") or "0")
    if episode_number <= 0:
        raise LocalSourceError("INVALID_EPISODE", f"episode must be a positive number, got {episode!r}")

    handoff = IngestionHandoffRecord(
        source_id=f"hongguo:{series_id}:{episode}",
        source_url_redacted="",
        canonical_id=str(series_id),
        platform=PROVIDER.lower(),
        series_id=str(series_id),
        part_or_episode_id=str(episode),
        selected_item_index=episode_number,
        discovery_status=INPUT_MODE,
        access_status="LOCAL_FILE",
    )
    handoff = attach_and_validate_local_media(handoff, path, is_live_media=False)
    if handoff.ingestion_status != "LOCAL_FIXTURE_VALIDATED":
        raise LocalSourceError(handoff.error_code or "LOCAL_MEDIA_INVALID",
                               handoff.error_message or "local media validation failed")
    handoff.ingestion_status = "LOCAL_SOURCE_VALIDATED"
    handoff.parts_or_chapters = [{
        "episode": str(episode),
        "type": "full_episode_local_file",
        "title": title,
        "duration_seconds": float(probe.get("duration_seconds") or 0.0),
        "source_sha256": actual_sha,
        "url_ingest": False,
    }]
    return handoff


def bind_local_input_identity(
    job_dir: Path,
    identity: dict[str, Any],
    *,
    allow_create: bool,
) -> dict[str, Any]:
    path = job_dir / IDENTITY_FILE
    if path.exists():
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise LocalSourceError("LOCAL_INPUT_MISMATCH", "unreadable local input identity") from None
        for field in IDENTITY_FIELDS:
            if recorded.get(field) != identity.get(field):
                raise LocalSourceError(
                    "LOCAL_INPUT_MISMATCH",
                    f"{field} {recorded.get(field)!r} != {identity.get(field)!r}",
                )
        return recorded
    if not allow_create:
        raise LocalSourceError("LOCAL_INPUT_MISMATCH", "existing job has no recorded local input identity")
    path.write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    return identity
