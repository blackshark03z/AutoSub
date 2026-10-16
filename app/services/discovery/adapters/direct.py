from __future__ import annotations

import time
import urllib.parse
from typing import Any

from app.services.discovery.models import (
    DiscoveryRequest,
    DiscoveryResult,
    DiscoveryStatus,
    DrmStatus,
    VideoFormatInfo,
)
from app.services.discovery.redaction import redact_url
from app.services.discovery.ssrf import safe_http_fetch

DIRECT_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".flv", ".ts"}
CENC_PROTECTION_ATOMS = [b"sinf", b"schm", b"encv", b"enca", b"pssh"]


class DirectFileDiscoveryAdapter:
    adapter_name = "direct_file"

    def can_handle(self, url: str, platform: str | None = None) -> bool:
        if platform in {"direct", "direct_file", "mp4", "webm"}:
            return True
        path = urllib.parse.urlsplit(url).path.lower()
        return any(path.endswith(ext) for ext in DIRECT_EXTENSIONS)

    def probe(self, request: DiscoveryRequest) -> DiscoveryResult:
        start_time = time.monotonic()
        redacted_url = redact_url(request.video_url)
        path = urllib.parse.urlsplit(request.video_url).path
        filename = path.split("/")[-1] if "/" in path else "direct_media"
        ext = filename.split(".")[-1].lower() if "." in filename else "mp4"

        try:
            # 1. Fetch only first 1024 bytes with Range header (metadata-only probe)
            headers = {"Range": "bytes=0-1023"}
            status_code, resp_headers, body = safe_http_fetch(
                request.video_url,
                method="GET",
                headers=headers,
                timeout_seconds=request.timeout_seconds,
                max_bytes=1024,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)

            if status_code in {401, 403}:
                return DiscoveryResult(
                    source_url_redacted=redacted_url,
                    platform="direct_file",
                    status=DiscoveryStatus.AUTH_REQUIRED.value,
                    recommended_engine="direct_http",
                    title=filename,
                    duration_seconds=None,
                    probe_duration_ms=duration_ms,
                    download_tested=False,
                    authentication_required=True,
                    error_code=f"HTTP_{status_code}",
                    error_message=f"Access denied with HTTP {status_code}",
                )

            if status_code not in {200, 206}:
                return DiscoveryResult(
                    source_url_redacted=redacted_url,
                    platform="direct_file",
                    status=DiscoveryStatus.PROBE_ERROR.value,
                    recommended_engine="direct_http",
                    title=filename,
                    duration_seconds=None,
                    probe_duration_ms=duration_ms,
                    download_tested=False,
                    error_code=f"HTTP_{status_code}",
                    error_message=f"Direct file probe returned HTTP {status_code}",
                )

            content_type = resp_headers.get("content-type", "")
            content_length = resp_headers.get("content-length")
            content_range = resp_headers.get("content-range")
            total_size = None
            if content_range and "/" in content_range:
                try:
                    total_size = int(content_range.split("/")[-1])
                except ValueError:
                    pass
            elif content_length and status_code == 200:
                try:
                    total_size = int(content_length)
                except ValueError:
                    pass

            # Inspect binary header (first 1024 bytes) for container and DRM atoms
            drm_suspected = any(atom in body for atom in CENC_PROTECTION_ATOMS)
            has_ftyp = b"ftyp" in body
            has_ebml = body.startswith(b"\x1a\x45\xdf\xa3")

            status = DiscoveryStatus.DRM_SUSPECTED.value if drm_suspected else DiscoveryStatus.DISCOVERABLE.value
            drm_status = DrmStatus.DRM_SUSPECTED.value if drm_suspected else (
                DrmStatus.NO_DRM_APPARENT.value if (has_ftyp or has_ebml) else DrmStatus.UNKNOWN.value
            )

            format_info = VideoFormatInfo(
                format_id="direct_0",
                ext=ext,
                filesize_approx=total_size,
                protocol="https" if request.video_url.startswith("https") else "http",
                note=f"Content-Type: {content_type}",
            )

            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform="direct_file",
                status=status,
                recommended_engine="direct_http",
                title=filename,
                duration_seconds=None,
                formats=[format_info.to_dict()],
                subtitle_languages=[],
                authentication_required=False,
                drm_status=drm_status,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code=None,
                details={
                    "content_type": content_type,
                    "content_length": total_size,
                    "accept_ranges": "bytes" in resp_headers.get("accept-ranges", "").lower(),
                },
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform="direct_file",
                status=DiscoveryStatus.PROBE_ERROR.value,
                recommended_engine="direct_http",
                title=filename,
                duration_seconds=None,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="DIRECT_PROBE_ERROR",
                error_message=str(exc),
            )
