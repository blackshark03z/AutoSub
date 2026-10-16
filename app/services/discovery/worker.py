from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any, Sequence

from app.services.discovery.adapters.base import DiscoveryAdapter
from app.services.discovery.adapters.direct import DirectFileDiscoveryAdapter
from app.services.discovery.adapters.lux import LuxDiscoveryAdapter
from app.services.discovery.adapters.manifest import ManifestDiscoveryAdapter
from app.services.discovery.adapters.ytdlp import YtDlpDiscoveryAdapter
from app.services.discovery.models import (
    DiscoveryRequest,
    DiscoveryResult,
    DiscoveryStatus,
    DrmStatus,
)
from app.services.discovery.redaction import redact_url
from app.services.discovery.ssrf import validate_url_ssrf

logger = logging.getLogger(__name__)


class DiscoveryWorker:
    """Orchestrates video source discovery adapters with SSRF protection,
    secret redaction, and controlled fallback.
    
    Strictly performs metadata probing; never downloads full video media.
    """

    def __init__(
        self,
        adapters: Sequence[DiscoveryAdapter] | None = None,
        enforce_dns_validation: bool = True,
    ) -> None:
        self.adapters: list[DiscoveryAdapter] = list(adapters) if adapters is not None else [
            ManifestDiscoveryAdapter(enforce_dns_validation=enforce_dns_validation),
            DirectFileDiscoveryAdapter(),
            YtDlpDiscoveryAdapter(),
            LuxDiscoveryAdapter(),
        ]
        self.enforce_dns_validation = enforce_dns_validation

    def probe_url(
        self,
        video_url: str,
        platform: str | None = None,
        timeout_seconds: float = 15.0,
        authorized_access_confirmed: bool = False,
    ) -> DiscoveryResult:
        request = DiscoveryRequest(
            video_url=video_url.strip(),
            platform=platform.strip() if platform else None,
            timeout_seconds=timeout_seconds,
            authorized_access_confirmed=authorized_access_confirmed,
        )
        return self.execute(request)

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult:
        start_time = time.monotonic()
        url = request.video_url
        redacted_url = redact_url(url)
        detected_platform = request.platform or self.detect_platform(url)

        # 1. Access Authorization Gate
        if not request.authorized_access_confirmed:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform=detected_platform,
                status=DiscoveryStatus.AUTH_REQUIRED.value,
                recommended_engine=None,
                title=None,
                duration_seconds=None,
                formats=[],
                subtitle_languages=[],
                authentication_required=True,
                drm_status=DrmStatus.UNKNOWN.value,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="OPERATOR_AUTHORIZATION_REQUIRED",
                error_message="Probe requires operator confirmation of authorized access.",
            )

        # 2. SSRF Validation Guard
        safe, ssrf_reason = validate_url_ssrf(url, resolve_dns=self.enforce_dns_validation)
        if not safe:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform=detected_platform,
                status=DiscoveryStatus.PROBE_ERROR.value,
                recommended_engine=None,
                title=None,
                duration_seconds=None,
                formats=[],
                subtitle_languages=[],
                authentication_required=False,
                drm_status=DrmStatus.UNKNOWN.value,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="SSRF_REJECTED",
                error_message=f"SSRF validation blocked request: {ssrf_reason}",
            )

        # 3. Select Candidates & Controlled Fallback
        candidate_adapters = [a for a in self.adapters if a.can_handle(url, detected_platform)]
        if not candidate_adapters:
            # Fall back to any adapter that can generally handle it (e.g. yt-dlp)
            candidate_adapters = [a for a in self.adapters if a.adapter_name == "yt-dlp"]

        if not candidate_adapters:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform=detected_platform,
                status=DiscoveryStatus.UNSUPPORTED.value,
                recommended_engine=None,
                title=None,
                duration_seconds=None,
                formats=[],
                subtitle_languages=[],
                authentication_required=False,
                drm_status=DrmStatus.UNKNOWN.value,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="NO_ADAPTER_AVAILABLE",
                error_message="No discovery adapter can handle this source URL",
            )

        SECURITY_FATAL_ERROR_CODES = {
            "SECURE_TRANSPORT_UNAVAILABLE",
            "SSRF_REJECTED",
            "SSRF_MANIFEST_POISONING",
            "OPERATOR_AUTHORIZATION_REQUIRED",
            "UNSAFE_PROXY_REJECTED",
            "PROXIES_DISABLED_FOR_UNTRUSTED",
        }

        last_result: DiscoveryResult | None = None
        for i, adapter in enumerate(candidate_adapters):
            try:
                result = adapter.probe(request)

                # 1. Fail-closed: Never fall back after a security or transport hardening violation
                if (
                    result.error_code in SECURITY_FATAL_ERROR_CODES
                    or (result.error_code and "SSRF" in result.error_code)
                ):
                    logger.warning(
                        "Adapter %s encountered security fatal error %s. Aborting discovery immediately without fallback.",
                        adapter.adapter_name,
                        result.error_code,
                    )
                    return self._enforce_invariants(result, start_time)

                # 2. Successful or definitive states don't need fallback
                definitive_statuses = {
                    DiscoveryStatus.DISCOVERABLE.value,
                    DiscoveryStatus.DRM_SUSPECTED.value,
                    DiscoveryStatus.AUTH_REQUIRED.value,
                    DiscoveryStatus.GEO_RESTRICTED.value,
                }
                if result.status in definitive_statuses:
                    return self._enforce_invariants(result, start_time)

                last_result = result
                logger.info(
                    "Adapter %s returned non-definitive status %s (%s). Attempting fallback if available.",
                    adapter.adapter_name,
                    result.status,
                    result.error_code,
                )
            except Exception as exc:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                exc_name = type(exc).__name__
                is_security_exc = "Ssrf" in exc_name or "SecureTransport" in exc_name or "Security" in exc_name
                error_code = "SECURE_TRANSPORT_UNAVAILABLE" if "SecureTransport" in exc_name else (
                    "SSRF_REJECTED" if "Ssrf" in exc_name else "ADAPTER_EXCEPTION"
                )
                sec_result = DiscoveryResult(
                    source_url_redacted=redacted_url,
                    platform=detected_platform,
                    status=DiscoveryStatus.PROBE_ERROR.value,
                    recommended_engine=adapter.adapter_name,
                    title=None,
                    duration_seconds=None,
                    formats=[],
                    subtitle_languages=[],
                    authentication_required=False,
                    drm_status=DrmStatus.UNKNOWN.value,
                    probe_duration_ms=duration_ms,
                    download_tested=False,
                    error_code=error_code,
                    error_message=str(exc),
                )
                if is_security_exc:
                    logger.warning(
                        "Security exception raised by %s: %s (%s). Aborting immediately without fallback.",
                        adapter.adapter_name,
                        exc,
                        error_code,
                    )
                    return self._enforce_invariants(sec_result, start_time)
                last_result = sec_result

        if last_result is not None:
            return self._enforce_invariants(last_result, start_time)

        duration_ms = int((time.monotonic() - start_time) * 1000)
        return DiscoveryResult(
            source_url_redacted=redacted_url,
            platform=detected_platform,
            status=DiscoveryStatus.UNKNOWN.value,
            recommended_engine=None,
            title=None,
            duration_seconds=None,
            formats=[],
            subtitle_languages=[],
            authentication_required=False,
            drm_status=DrmStatus.UNKNOWN.value,
            probe_duration_ms=duration_ms,
            download_tested=False,
            error_code="DISCOVERY_EXHAUSTED",
            error_message="All discovery adapters completed without definitive metadata",
        )

    def detect_platform(self, url: str) -> str:
        try:
            parts = urllib.parse.urlsplit(url)
            domain = parts.netloc.lower()
            path = parts.path.lower()
        except Exception:
            return "generic"

        if "bilibili.com" in domain or "b23.tv" in domain:
            return "bilibili"
        if "iqiyi.com" in domain or "iq.com" in domain:
            return "iqiyi"
        if "youku.com" in domain:
            return "youku"
        if "qq.com" in domain or "wetv.vip" in domain:
            return "tencent"
        if "douyin.com" in domain:
            return "douyin"
        if "ixigua.com" in domain:
            return "xigua"
        if path.endswith(".m3u8"):
            return "manifest_hls"
        if path.endswith(".mpd"):
            return "manifest_dash"
        if any(path.endswith(ext) for ext in [".mp4", ".mov", ".mkv", ".webm", ".m4v", ".flv"]):
            return "direct_file"
        return "generic"

    def _enforce_invariants(self, result: DiscoveryResult, start_time: float) -> DiscoveryResult:
        # Invariant 1: download_tested must NEVER be True in WP-DL-001
        # Invariant 2: total probe_duration_ms is measured accurately
        # Invariant 3: source_url_redacted must not contain sensitive tokens
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return DiscoveryResult(
            source_url_redacted=redact_url(result.source_url_redacted),
            platform=result.platform,
            status=result.status,
            recommended_engine=result.recommended_engine,
            title=result.title,
            duration_seconds=result.duration_seconds,
            formats=result.formats,
            subtitle_languages=result.subtitle_languages,
            authentication_required=result.authentication_required,
            drm_status=result.drm_status,
            probe_duration_ms=duration_ms,
            download_tested=False,
            error_code=result.error_code,
            error_message=result.error_message,
            details=result.details,
        )
