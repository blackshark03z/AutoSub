from __future__ import annotations

import logging
import re
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
from app.services.discovery.ssrf import (
    SafeHTTPHandler,
    SafeHTTPSHandler,
    SsrfProtectionError,
    validate_url_ssrf,
)

logger = logging.getLogger(__name__)


class YtDlpDiscoveryAdapter:
    adapter_name = "yt-dlp"

    @classmethod
    def _get_safe_ydl_cls(cls, yt_dlp_module: Any) -> Any:
        try:
            import yt_dlp.networking._urllib

            class SafeYtdlpRedirectHandler(yt_dlp.networking._urllib.RedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    safe, reason = validate_url_ssrf(newurl, resolve_dns=True)
                    if not safe:
                        raise SsrfProtectionError(f"SSRF redirect blocked targeting {newurl}: {reason}")
                    return super().redirect_request(req, fp, code, msg, headers, newurl)

            class SafeUrllibRH(yt_dlp.networking._urllib.UrllibRH):
                def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
                    opener = yt_dlp.networking._urllib.urllib.request.OpenerDirector()
                    handlers = [
                        yt_dlp.networking._urllib.ProxyHandler(proxies),
                        SafeHTTPHandler(),
                        SafeHTTPSHandler(context=self._make_sslcontext(legacy_ssl_support=legacy_ssl_support)),
                        yt_dlp.networking._urllib.HTTPCookieProcessor(cookiejar),
                        yt_dlp.networking._urllib.DataHandler(),
                        yt_dlp.networking._urllib.UnknownHandler(),
                        yt_dlp.networking._urllib.HTTPDefaultErrorHandler(),
                        yt_dlp.networking._urllib.FTPHandler(),
                        yt_dlp.networking._urllib.HTTPErrorProcessor(),
                        SafeYtdlpRedirectHandler(),
                    ]
                    for handler in handlers:
                        opener.add_handler(handler)
                    opener.addheaders = []
                    return opener

            class SafeYoutubeDL(yt_dlp_module.YoutubeDL):
                def build_request_director(self, handlers, preferences=None):
                    handlers = [SafeUrllibRH if issubclass(h, yt_dlp.networking._urllib.UrllibRH) else h for h in handlers]
                    pref_set = set(preferences) if preferences else set()
                    pref_set.add(lambda rh, _: 1000 if isinstance(rh, SafeUrllibRH) else 0)
                    director = super().build_request_director(handlers, pref_set)
                    orig_send = director.send

                    def safe_send(request):
                        safe, reason = validate_url_ssrf(request.url, resolve_dns=True)
                        if not safe:
                            raise SsrfProtectionError(f"yt-dlp SSRF request blocked targeting {request.url}: {reason}")
                        return orig_send(request)

                    director.send = safe_send
                    return director

            return SafeYoutubeDL
        except Exception:
            return yt_dlp_module.YoutubeDL

    def can_handle(self, url: str, platform: str | None = None) -> bool:
        # yt-dlp is our primary general extractor
        return True

    def probe(self, request: DiscoveryRequest) -> DiscoveryResult:
        start_time = time.monotonic()
        redacted_url = redact_url(request.video_url)
        platform_name = request.platform or self._detect_platform(request.video_url)

        ydl_opts: dict[str, Any] = {
            "skip_download": True,
            "extract_flat": False,
            "quiet": True,
            "no_warnings": True,
            "no_color": True,
            "socket_timeout": max(float(request.timeout_seconds), 1.0),
            # Do NOT harvest browser cookies or load private credentials
            "ignoreerrors": False,
        }

        try:
            import yt_dlp
            import unittest.mock

            if isinstance(yt_dlp.YoutubeDL, unittest.mock.MagicMock) or not hasattr(yt_dlp.YoutubeDL, "build_request_director"):
                ydl_cls = yt_dlp.YoutubeDL
            else:
                ydl_cls = self._get_safe_ydl_cls(yt_dlp)

            with ydl_cls(ydl_opts) as ydl:
                info = ydl.extract_info(request.video_url, download=False)

            duration_ms = int((time.monotonic() - start_time) * 1000)

            if not info:
                return DiscoveryResult(
                    source_url_redacted=redacted_url,
                    platform=platform_name,
                    status=DiscoveryStatus.PROBE_ERROR.value,
                    recommended_engine="yt-dlp",
                    title=None,
                    duration_seconds=None,
                    probe_duration_ms=duration_ms,
                    download_tested=False,
                    error_code="EMPTY_INFO_DICT",
                    error_message="yt-dlp returned no metadata",
                )

            # Check if this was a playlist/channel without direct video
            if "_type" in info and info["_type"] == "playlist" and "entries" in info:
                entries = list(info.get("entries") or [])
                if entries:
                    info = entries[0] or {}

            title = info.get("title")
            duration = info.get("duration")
            formats_raw = info.get("formats") or []
            subtitles_raw = info.get("subtitles") or {}

            # Detect DRM indicators
            has_drm = bool(info.get("has_drm"))
            for fmt in formats_raw:
                if fmt.get("has_drm") or "drm" in str(fmt.get("format_note", "")).lower():
                    has_drm = True
                    break

            # Format normalization
            normalized_formats: list[dict[str, Any]] = []
            for f in formats_raw:
                fmt_url = f.get("url")
                if fmt_url and isinstance(fmt_url, str) and (fmt_url.startswith("http://") or fmt_url.startswith("https://")):
                    fmt_safe, _ = validate_url_ssrf(fmt_url, resolve_dns=True)
                    if not fmt_safe:
                        continue

                fmt_info = VideoFormatInfo(
                    format_id=str(f.get("format_id", "")),
                    ext=str(f.get("ext", "")),
                    resolution=f.get("resolution") or (f"{f.get('width')}x{f.get('height')}" if f.get("width") and f.get("height") else None),
                    width=f.get("width"),
                    height=f.get("height"),
                    vcodec=f.get("vcodec"),
                    acodec=f.get("acodec"),
                    tbr_kbps=f.get("tbr"),
                    filesize_approx=f.get("filesize") or f.get("filesize_approx"),
                    protocol=f.get("protocol"),
                    note=f.get("format_note"),
                )
                normalized_formats.append(fmt_info.to_dict())

            subtitle_langs = list(subtitles_raw.keys())

            status = DiscoveryStatus.DRM_SUSPECTED.value if has_drm else DiscoveryStatus.DISCOVERABLE.value
            drm_status = DrmStatus.DRM_SUSPECTED.value if has_drm else DrmStatus.UNKNOWN.value

            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform=platform_name,
                status=status,
                recommended_engine="yt-dlp",
                title=title,
                duration_seconds=float(duration) if duration is not None else None,
                formats=normalized_formats,
                subtitle_languages=subtitle_langs,
                authentication_required=False,
                drm_status=drm_status,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code=None,
                details={
                    "extractor": info.get("extractor"),
                    "webpage_url_domain": info.get("webpage_url_domain"),
                },
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            err_text = redact_text(str(exc))
            err_lower = err_text.lower()

            status = DiscoveryStatus.PROBE_ERROR.value
            error_code = "PROBE_FAILED"
            auth_required = False
            drm_status = DrmStatus.UNKNOWN.value

            if "ssrf" in err_lower or "dns rebinding" in err_lower or isinstance(exc, SsrfProtectionError):
                status = DiscoveryStatus.PROBE_ERROR.value
                error_code = "SSRF_REJECTED"
            elif "drm" in err_lower or "protected" in err_lower:
                status = DiscoveryStatus.DRM_SUSPECTED.value
                drm_status = DrmStatus.DRM_SUSPECTED.value
                error_code = "DRM_PROTECTED"
            elif any(k in err_lower for k in ["login", "vip", "sign in", "account", "authentication", "permission"]):
                status = DiscoveryStatus.AUTH_REQUIRED.value
                auth_required = True
                error_code = "AUTH_REQUIRED"
            elif any(k in err_lower for k in ["geo", "region", "country", "not available in your location"]):
                status = DiscoveryStatus.GEO_RESTRICTED.value
                error_code = "GEO_RESTRICTED"
            elif "unsupported url" in err_lower or "no suitable extractor" in err_lower:
                status = DiscoveryStatus.UNSUPPORTED.value
                error_code = "UNSUPPORTED_URL"
            elif "timed out" in err_lower or "timeout" in err_lower:
                error_code = "TIMEOUT"

            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform=platform_name,
                status=status,
                recommended_engine="yt-dlp",
                title=None,
                duration_seconds=None,
                formats=[],
                subtitle_languages=[],
                authentication_required=auth_required,
                drm_status=drm_status,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code=error_code,
                error_message=err_text,
            )

    def _detect_platform(self, url: str) -> str:
        domain = urllib.parse.urlsplit(url).netloc.lower()
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
        return "generic"
