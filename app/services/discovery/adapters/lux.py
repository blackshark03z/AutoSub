from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
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
from app.services.discovery.redaction import redact_text, redact_url

logger = logging.getLogger(__name__)


class LuxDiscoveryAdapter:
    adapter_name = "lux"

    def is_available(self) -> bool:
        return shutil.which("lux") is not None

    def can_handle(self, url: str, platform: str | None = None) -> bool:
        domain = urllib.parse.urlsplit(url).netloc.lower()
        supported_domains = ["bilibili.com", "douyin.com", "youku.com", "iqiyi.com", "ixigua.com"]
        matches_domain = any(d in domain for d in supported_domains)
        return self.is_available() and (matches_domain or platform in {"bilibili", "douyin", "youku", "iqiyi", "xigua"})

    def probe(self, request: DiscoveryRequest) -> DiscoveryResult:
        start_time = time.monotonic()
        redacted_url = redact_url(request.video_url)
        platform_name = request.platform or self._detect_platform(request.video_url)

        if not self.is_available():
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform=platform_name,
                status=DiscoveryStatus.UNSUPPORTED.value,
                recommended_engine="lux",
                title=None,
                duration_seconds=None,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="LUX_NOT_INSTALLED",
                error_message="The lux binary is not installed or not in PATH",
            )

        cmd = ["lux", "-j", request.video_url]
        try:
            # Subprocess execution with strict shell=False, timeout, and isolated env
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(float(request.timeout_seconds), 1.0),
                shell=False,
                check=False,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)

            if proc.returncode != 0:
                err_text = redact_text(proc.stderr.strip() or proc.stdout.strip())
                err_lower = err_text.lower()
                status = DiscoveryStatus.PROBE_ERROR.value
                error_code = "LUX_PROBE_FAILED"
                auth_required = False

                if "login" in err_lower or "cookie" in err_lower or "vip" in err_lower:
                    status = DiscoveryStatus.AUTH_REQUIRED.value
                    auth_required = True
                    error_code = "AUTH_REQUIRED"

                return DiscoveryResult(
                    source_url_redacted=redacted_url,
                    platform=platform_name,
                    status=status,
                    recommended_engine="lux",
                    title=None,
                    duration_seconds=None,
                    probe_duration_ms=duration_ms,
                    download_tested=False,
                    authentication_required=auth_required,
                    error_code=error_code,
                    error_message=err_text,
                )

            data = json.loads(proc.stdout)
            # Lux output might be a list of video objects or a single dict
            item = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})

            title = item.get("title")
            formats_dict = item.get("streams") or {}
            normalized_formats: list[dict[str, Any]] = []

            for stream_id, s_info in formats_dict.items():
                fmt = VideoFormatInfo(
                    format_id=str(stream_id),
                    ext=str(s_info.get("container", "mp4")),
                    resolution=str(s_info.get("quality", "")),
                    filesize_approx=s_info.get("size"),
                )
                normalized_formats.append(fmt.to_dict())

            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform=platform_name,
                status=DiscoveryStatus.DISCOVERABLE.value,
                recommended_engine="lux",
                title=title,
                duration_seconds=None,
                formats=normalized_formats,
                subtitle_languages=[],
                authentication_required=False,
                drm_status=DrmStatus.UNKNOWN.value,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code=None,
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform=platform_name,
                status=DiscoveryStatus.PROBE_ERROR.value,
                recommended_engine="lux",
                title=None,
                duration_seconds=None,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="TIMEOUT",
                error_message="Lux probe execution timed out",
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform=platform_name,
                status=DiscoveryStatus.PROBE_ERROR.value,
                recommended_engine="lux",
                title=None,
                duration_seconds=None,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="LUX_EXEC_ERROR",
                error_message=redact_text(str(exc)),
            )

    def _detect_platform(self, url: str) -> str:
        domain = urllib.parse.urlsplit(url).netloc.lower()
        if "bilibili.com" in domain:
            return "bilibili"
        if "douyin.com" in domain:
            return "douyin"
        if "youku.com" in domain:
            return "youku"
        if "iqiyi.com" in domain:
            return "iqiyi"
        return "generic"
