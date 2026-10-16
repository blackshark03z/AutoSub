from __future__ import annotations

import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

from app.services.discovery.models import (
    DiscoveryRequest,
    DiscoveryResult,
    DiscoveryStatus,
    DrmStatus,
    VideoFormatInfo,
)
from app.services.discovery.redaction import redact_url
from app.services.discovery.ssrf import safe_http_fetch, validate_url_ssrf

WIDEVINE_UUID = "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"
PLAYREADY_UUID = "9a04f079-9840-4286-ab92-e65be0885f95"
FAIRPLAY_UUID = "94ce86fb-07ff-4f43-adb8-93d2fa968ca2"


class ManifestDiscoveryAdapter:
    adapter_name = "manifest"

    def __init__(self, enforce_dns_validation: bool = False) -> None:
        self.enforce_dns_validation = enforce_dns_validation

    def can_handle(self, url: str, platform: str | None = None) -> bool:
        if platform in {"manifest_hls", "manifest_dash", "hls", "dash", "m3u8", "mpd"}:
            return True
        path = urllib.parse.urlsplit(url).path.lower()
        return path.endswith(".m3u8") or path.endswith(".mpd")

    def probe(self, request: DiscoveryRequest) -> DiscoveryResult:
        start_time = time.monotonic()
        redacted_url = redact_url(request.video_url)
        path = urllib.parse.urlsplit(request.video_url).path.lower()
        is_dash = path.endswith(".mpd") or (request.platform in {"dash", "manifest_dash"})

        try:
            status_code, headers, body = safe_http_fetch(
                request.video_url,
                method="GET",
                timeout_seconds=request.timeout_seconds,
                max_bytes=524288,  # 512 KiB max for manifest
                resolve_dns=self.enforce_dns_validation,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)

            if status_code >= 400:
                status = DiscoveryStatus.AUTH_REQUIRED.value if status_code in {401, 403} else DiscoveryStatus.PROBE_ERROR.value
                return DiscoveryResult(
                    source_url_redacted=redacted_url,
                    platform="manifest_dash" if is_dash else "manifest_hls",
                    status=status,
                    recommended_engine="n_m3u8dl_re",
                    title=None,
                    duration_seconds=None,
                    probe_duration_ms=duration_ms,
                    download_tested=False,
                    authentication_required=status_code in {401, 403},
                    error_code=f"HTTP_{status_code}",
                    error_message=f"Manifest request returned HTTP {status_code}",
                )

            content_text = body.decode("utf-8", errors="replace")

            if is_dash:
                return self._parse_dash(request, content_text, duration_ms, redacted_url)
            else:
                return self._parse_hls(request, content_text, duration_ms, redacted_url)

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform="manifest_dash" if is_dash else "manifest_hls",
                status=DiscoveryStatus.PROBE_ERROR.value,
                recommended_engine="n_m3u8dl_re",
                title=None,
                duration_seconds=None,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="MANIFEST_FETCH_ERROR",
                error_message=str(exc),
            )

    def _parse_hls(
        self, request: DiscoveryRequest, content: str, duration_ms: int, redacted_url: str
    ) -> DiscoveryResult:
        if not content.startswith("#EXTM3U"):
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform="manifest_hls",
                status=DiscoveryStatus.PROBE_ERROR.value,
                recommended_engine="n_m3u8dl_re",
                title=None,
                duration_seconds=None,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="INVALID_HLS_MANIFEST",
                error_message="Content does not start with #EXTM3U",
            )

        drm_suspected = False
        drm_method = None
        verified_urls: list[str] = []

        # Check for EXT-X-KEY encryption and SSRF in key URI
        key_matches = re.findall(r"#EXT-X-KEY:METHOD=([A-Za-z0-9_\-]+)(?:,([^,\r\n]+))?", content)
        for method, attrs in key_matches:
            method_upper = method.upper()
            if method_upper in {"SAMPLE-AES", "SAMPLE-AES-CTR", "SAMPLE-AES-CENC"}:
                drm_suspected = True
                drm_method = method_upper
            elif "com.apple.streamingkeydelivery" in (attrs or ""):
                drm_suspected = True
                drm_method = "FairPlay"
            elif method_upper == "AES-128":
                drm_method = "AES-128"

            if attrs and 'URI="' in attrs:
                key_uri_m = re.search(r'URI="([^"]+)"', attrs)
                if key_uri_m:
                    raw_key_uri = key_uri_m.group(1).strip()
                    key_url = urllib.parse.urljoin(request.video_url, raw_key_uri)
                    parsed_key = urllib.parse.urlsplit(key_url)
                    if parsed_key.scheme.lower() in {"http", "https"}:
                        k_safe, k_reason = validate_url_ssrf(key_url, resolve_dns=self.enforce_dns_validation)
                        if not k_safe:
                            return DiscoveryResult(
                                source_url_redacted=redacted_url,
                                platform="manifest_hls",
                                status=DiscoveryStatus.PROBE_ERROR.value,
                                recommended_engine="n_m3u8dl_re",
                                title=None,
                                duration_seconds=None,
                                probe_duration_ms=duration_ms,
                                download_tested=False,
                                error_code="SSRF_MANIFEST_POISONING",
                                error_message=f"HLS key URI targets blocked address: {k_reason}",
                            )
                        verified_urls.append(key_url)

        # Parse variants and validate variant URLs against SSRF (with strict DNS resolution)
        formats: list[dict[str, Any]] = []
        stream_matches = re.finditer(
            r"#EXT-X-STREAM-INF:([^\r\n]+)\r?\n([^\r\n]+)", content
        )
        for i, match in enumerate(stream_matches):
            attrs_str = match.group(1)
            uri = match.group(2).strip()

            variant_full_url = urllib.parse.urljoin(request.video_url, uri)
            v_safe, v_reason = validate_url_ssrf(variant_full_url, resolve_dns=self.enforce_dns_validation)
            if not v_safe:
                return DiscoveryResult(
                    source_url_redacted=redacted_url,
                    platform="manifest_hls",
                    status=DiscoveryStatus.PROBE_ERROR.value,
                    recommended_engine="n_m3u8dl_re",
                    title=None,
                    duration_seconds=None,
                    probe_duration_ms=duration_ms,
                    download_tested=False,
                    error_code="SSRF_MANIFEST_POISONING",
                    error_message=f"HLS variant URI targets blocked address: {v_reason}",
                )
            verified_urls.append(variant_full_url)
            
            bandwidth = None
            bw_m = re.search(r"BANDWIDTH=(\d+)", attrs_str)
            if bw_m:
                bandwidth = int(bw_m.group(1))

            resolution = None
            res_m = re.search(r"RESOLUTION=(\d+x\d+)", attrs_str)
            if res_m:
                resolution = res_m.group(1)

            codecs = None
            codecs_m = re.search(r'CODECS="([^"]+)"', attrs_str)
            if codecs_m:
                codecs = codecs_m.group(1)

            fmt = VideoFormatInfo(
                format_id=f"hls_{i}",
                ext="m3u8",
                resolution=resolution,
                tbr_kbps=round(bandwidth / 1000.0, 1) if bandwidth else None,
                protocol="hls",
                note=f"codecs: {codecs}" if codecs else None,
            )
            formats.append(fmt.to_dict())

        # Subtitle track detection & SSRF check
        subtitle_languages: list[str] = []
        for line in content.splitlines():
            line_str = line.strip()
            if line_str.startswith("#EXT-X-MEDIA:") and "TYPE=SUBTITLES" in line_str:
                lang_m = re.search(r'LANGUAGE="([^"]+)"', line_str)
                if lang_m:
                    lang = lang_m.group(1)
                    if lang not in subtitle_languages:
                        subtitle_languages.append(lang)
                uri_m = re.search(r'URI="([^"]+)"', line_str)
                if uri_m:
                    sub_url = urllib.parse.urljoin(request.video_url, uri_m.group(1).strip())
                    s_safe, s_reason = validate_url_ssrf(sub_url, resolve_dns=self.enforce_dns_validation)
                    if not s_safe:
                        return DiscoveryResult(
                            source_url_redacted=redacted_url,
                            platform="manifest_hls",
                            status=DiscoveryStatus.PROBE_ERROR.value,
                            recommended_engine="n_m3u8dl_re",
                            title=None,
                            duration_seconds=None,
                            probe_duration_ms=duration_ms,
                            download_tested=False,
                            error_code="SSRF_MANIFEST_POISONING",
                            error_message=f"HLS subtitle URI targets blocked address: {s_reason}",
                        )
                    verified_urls.append(sub_url)

        # Media playlist segment & init map references check (Metadata-only: never download segments)
        for line in content.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if line_str.startswith("#EXT-X-MAP:"):
                map_m = re.search(r'URI="([^"]+)"', line_str)
                if map_m:
                    map_url = urllib.parse.urljoin(request.video_url, map_m.group(1).strip())
                    m_safe, m_reason = validate_url_ssrf(map_url, resolve_dns=self.enforce_dns_validation)
                    if not m_safe:
                        return DiscoveryResult(
                            source_url_redacted=redacted_url,
                            platform="manifest_hls",
                            status=DiscoveryStatus.PROBE_ERROR.value,
                            recommended_engine="n_m3u8dl_re",
                            title=None,
                            duration_seconds=None,
                            probe_duration_ms=duration_ms,
                            download_tested=False,
                            error_code="SSRF_MANIFEST_POISONING",
                            error_message=f"HLS init map URI targets blocked address: {m_reason}",
                        )
                    verified_urls.append(map_url)
            elif not line_str.startswith("#"):
                # Non-comment line in a media playlist is a segment reference
                seg_url = urllib.parse.urljoin(request.video_url, line_str)
                seg_safe, seg_reason = validate_url_ssrf(seg_url, resolve_dns=self.enforce_dns_validation)
                if not seg_safe:
                    return DiscoveryResult(
                        source_url_redacted=redacted_url,
                        platform="manifest_hls",
                        status=DiscoveryStatus.PROBE_ERROR.value,
                        recommended_engine="n_m3u8dl_re",
                        title=None,
                        duration_seconds=None,
                        probe_duration_ms=duration_ms,
                        download_tested=False,
                        error_code="SSRF_MANIFEST_POISONING",
                        error_message=f"HLS segment reference targets blocked address: {seg_reason}",
                    )
                verified_urls.append(seg_url)

        status = DiscoveryStatus.DRM_SUSPECTED.value if drm_suspected else DiscoveryStatus.DISCOVERABLE.value
        drm_status = DrmStatus.DRM_SUSPECTED.value if drm_suspected else (
            DrmStatus.NO_DRM_APPARENT.value if not key_matches else DrmStatus.UNKNOWN.value
        )

        return DiscoveryResult(
            source_url_redacted=redacted_url,
            platform="manifest_hls",
            status=status,
            recommended_engine="n_m3u8dl_re",
            title=None,
            duration_seconds=None,
            formats=formats,
            subtitle_languages=subtitle_languages,
            authentication_required=False,
            drm_status=drm_status,
            probe_duration_ms=duration_ms,
            download_tested=False,
            error_code=None,
            details={
                "encryption_method": drm_method,
                "variant_count": len(formats),
                "verified_url_count": len(verified_urls),
            },
        )

    def _parse_dash(
        self, request: DiscoveryRequest, content: str, duration_ms: int, redacted_url: str
    ) -> DiscoveryResult:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            return DiscoveryResult(
                source_url_redacted=redacted_url,
                platform="manifest_dash",
                status=DiscoveryStatus.PROBE_ERROR.value,
                recommended_engine="n_m3u8dl_re",
                title=None,
                duration_seconds=None,
                probe_duration_ms=duration_ms,
                download_tested=False,
                error_code="INVALID_DASH_MANIFEST",
                error_message=f"XML parsing failed: {exc}",
            )

        drm_suspected = False
        drm_scheme = None
        formats: list[dict[str, Any]] = []

        # Find namespaces
        ns_match = re.match(r"\{([^}]+)\}", root.tag)
        ns_prefix = f"{{{ns_match.group(1)}}}" if ns_match else ""

        verified_urls: list[str] = []

        # Validate BaseURL tags against SSRF
        base_urls = root.findall(f".//{ns_prefix}BaseURL")
        for b in base_urls:
            b_text = (b.text or "").strip()
            if b_text and (b_text.startswith("http://") or b_text.startswith("https://") or "://" in b_text):
                full_b_url = urllib.parse.urljoin(request.video_url, b_text)
                b_safe, b_reason = validate_url_ssrf(full_b_url, resolve_dns=self.enforce_dns_validation)
                if not b_safe:
                    return DiscoveryResult(
                        source_url_redacted=redacted_url,
                        platform="manifest_dash",
                        status=DiscoveryStatus.PROBE_ERROR.value,
                        recommended_engine="n_m3u8dl_re",
                        title=None,
                        duration_seconds=None,
                        probe_duration_ms=duration_ms,
                        download_tested=False,
                        error_code="SSRF_MANIFEST_POISONING",
                        error_message=f"DASH BaseURL targets blocked address: {b_reason}",
                    )
                verified_urls.append(full_b_url)

        # Validate SegmentTemplate tags against SSRF
        seg_templates = root.findall(f".//{ns_prefix}SegmentTemplate")
        for st in seg_templates:
            for attr in ("media", "initialization"):
                val = st.get(attr)
                if val and ("http://" in val or "https://" in val or "://" in val):
                    full_st_url = urllib.parse.urljoin(request.video_url, val)
                    st_safe, st_reason = validate_url_ssrf(full_st_url, resolve_dns=self.enforce_dns_validation)
                    if not st_safe:
                        return DiscoveryResult(
                            source_url_redacted=redacted_url,
                            platform="manifest_dash",
                            status=DiscoveryStatus.PROBE_ERROR.value,
                            recommended_engine="n_m3u8dl_re",
                            title=None,
                            duration_seconds=None,
                            probe_duration_ms=duration_ms,
                            download_tested=False,
                            error_code="SSRF_MANIFEST_POISONING",
                            error_message=f"DASH SegmentTemplate {attr} targets blocked address: {st_reason}",
                        )
                    verified_urls.append(full_st_url)

        # Validate SegmentURL and Initialization tags against SSRF
        seg_nodes = root.findall(f".//{ns_prefix}SegmentURL") + root.findall(f".//{ns_prefix}Initialization")
        for sn in seg_nodes:
            for attr in ("media", "initialization", "sourceURL"):
                val = sn.get(attr)
                if val and ("http://" in val or "https://" in val or "://" in val):
                    full_sn_url = urllib.parse.urljoin(request.video_url, val)
                    sn_safe, sn_reason = validate_url_ssrf(full_sn_url, resolve_dns=self.enforce_dns_validation)
                    if not sn_safe:
                        return DiscoveryResult(
                            source_url_redacted=redacted_url,
                            platform="manifest_dash",
                            status=DiscoveryStatus.PROBE_ERROR.value,
                            recommended_engine="n_m3u8dl_re",
                            title=None,
                            duration_seconds=None,
                            probe_duration_ms=duration_ms,
                            download_tested=False,
                            error_code="SSRF_MANIFEST_POISONING",
                            error_message=f"DASH segment element {attr} targets blocked address: {sn_reason}",
                        )
                    verified_urls.append(full_sn_url)

        # Check for ContentProtection tags (Widevine, PlayReady, FairPlay)
        content_protections = root.findall(f".//{ns_prefix}ContentProtection")
        for cp in content_protections:
            scheme_id = (cp.get("schemeIdUri") or "").lower()
            if WIDEVINE_UUID in scheme_id:
                drm_suspected = True
                drm_scheme = "Widevine"
            elif PLAYREADY_UUID in scheme_id:
                drm_suspected = True
                drm_scheme = "PlayReady"
            elif FAIRPLAY_UUID in scheme_id:
                drm_suspected = True
                drm_scheme = "FairPlay"
            elif "cenc" in scheme_id or "pssh" in ET.tostring(cp, encoding="utf-8").decode("utf-8").lower():
                drm_suspected = True
                drm_scheme = "CENC_PSSH"

        # Check Representations
        representations = root.findall(f".//{ns_prefix}Representation")
        for i, rep in enumerate(representations):
            rep_id = rep.get("id") or f"dash_{i}"
            width = int(rep.get("width")) if rep.get("width") else None
            height = int(rep.get("height")) if rep.get("height") else None
            bw = int(rep.get("bandwidth")) if rep.get("bandwidth") else None
            codecs = rep.get("codecs")
            mime_type = rep.get("mimeType")

            res_str = f"{width}x{height}" if width and height else None
            fmt = VideoFormatInfo(
                format_id=rep_id,
                ext="mpd",
                resolution=res_str,
                width=width,
                height=height,
                tbr_kbps=round(bw / 1000.0, 1) if bw else None,
                protocol="dash",
                note=f"mime: {mime_type}, codecs: {codecs}" if (mime_type or codecs) else None,
            )
            formats.append(fmt.to_dict())

        status = DiscoveryStatus.DRM_SUSPECTED.value if drm_suspected else DiscoveryStatus.DISCOVERABLE.value
        drm_status = DrmStatus.DRM_SUSPECTED.value if drm_suspected else (
            DrmStatus.NO_DRM_APPARENT.value if not content_protections else DrmStatus.UNKNOWN.value
        )

        return DiscoveryResult(
            source_url_redacted=redacted_url,
            platform="manifest_dash",
            status=status,
            recommended_engine="n_m3u8dl_re",
            title=None,
            duration_seconds=None,
            formats=formats,
            subtitle_languages=[],
            authentication_required=False,
            drm_status=drm_status,
            probe_duration_ms=duration_ms,
            download_tested=False,
            error_code=None,
            details={
                "drm_scheme": drm_scheme,
                "representation_count": len(formats),
                "verified_url_count": len(verified_urls),
            },
        )
