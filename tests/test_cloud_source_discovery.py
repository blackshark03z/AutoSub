from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.discovery.adapters.direct import DirectFileDiscoveryAdapter
from app.services.discovery.adapters.manifest import ManifestDiscoveryAdapter
from app.services.discovery.adapters.ytdlp import YtDlpDiscoveryAdapter
from app.services.discovery.cli import main as cli_main
from app.services.discovery.models import (
    DiscoveryRequest,
    DiscoveryResult,
    DiscoveryStatus,
    DrmStatus,
)
from app.services.discovery.redaction import redact_text, redact_url
from app.services.discovery.ssrf import (
    SsrfProtectionError,
    safe_http_fetch,
    validate_url_ssrf,
)
from app.services.discovery.worker import DiscoveryWorker


# ---------------------------------------------------------------------------
# 1. Redaction Tests
# ---------------------------------------------------------------------------

def test_redact_url_preserves_public_params_and_hides_secrets():
    raw_url = "https://example.com/play?v=BV12345&token=SECRET_abc123&sessdata=xyz987&key=AIzaSyD-fakeKey&page=2"
    sanitized = redact_url(raw_url)
    assert "token=%5BREDACTED%5D" in sanitized or "token=[REDACTED]" in sanitized
    assert "sessdata=%5BREDACTED%5D" in sanitized or "sessdata=[REDACTED]" in sanitized
    assert "key=%5BREDACTED%5D" in sanitized or "key=[REDACTED]" in sanitized
    assert "v=BV12345" in sanitized
    assert "page=2" in sanitized
    assert "SECRET_abc123" not in sanitized
    assert "xyz987" not in sanitized


def test_redact_url_userinfo():
    raw_url = "https://admin:supersecret@media.example.com/video.mp4"
    sanitized = redact_url(raw_url)
    assert "supersecret" not in sanitized
    assert "admin:[REDACTED]@media.example.com" in sanitized


def test_redact_text_sanitizes_known_tokens():
    raw_text = "Failed with key AIzaSyD1234567890abcdef12345 and SESSDATA=abcde12345"
    sanitized = redact_text(raw_text)
    assert "AIzaSyD1234567890abcdef12345" not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized
    assert "SESSDATA=[REDACTED]" in sanitized


# ---------------------------------------------------------------------------
# 2. SSRF Protection Tests
# ---------------------------------------------------------------------------

def test_validate_url_ssrf_rejects_loopback_and_private_ips():
    # Loopback
    safe, reason = validate_url_ssrf("http://127.0.0.1:8080/video.mp4", resolve_dns=False)
    assert not safe
    assert "BLOCKED_IP_LITERAL" in reason

    # Localhost
    safe, reason = validate_url_ssrf("http://localhost/test.m3u8", resolve_dns=False)
    assert not safe
    assert "BLOCKED_HOST" in reason

    # Private IP
    safe, reason = validate_url_ssrf("http://192.168.1.100/video.mp4", resolve_dns=False)
    assert not safe
    assert "BLOCKED_IP_LITERAL" in reason

    safe, reason = validate_url_ssrf("http://10.0.0.5:8000/stream.mpd", resolve_dns=False)
    assert not safe
    assert "BLOCKED_IP_LITERAL" in reason

    # Cloud metadata
    safe, reason = validate_url_ssrf("http://169.254.169.254/latest/meta-data/", resolve_dns=False)
    assert not safe

    safe, reason = validate_url_ssrf("http://metadata.google.internal/computeMetadata/v1/", resolve_dns=False)
    assert not safe
    assert "BLOCKED_HOST" in reason


def test_validate_url_ssrf_rejects_non_http_schemes():
    safe, reason = validate_url_ssrf("file:///etc/passwd", resolve_dns=False)
    assert not safe
    assert "UNSUPPORTED_SCHEME" in reason

    safe, reason = validate_url_ssrf("gopher://127.0.0.1:70/", resolve_dns=False)
    assert not safe


def test_safe_http_fetch_blocks_ssrf_redirect_to_metadata():
    # Simulates an initial request that redirects (HTTP 302) to AWS metadata
    redirect_error = urllib.error.HTTPError(
        url="http://public.example.com/redirect",
        code=302,
        msg="Found",
        hdrs={"Location": "http://169.254.169.254/latest/meta-data/"},
        fp=None,
    )

    with patch("urllib.request.build_opener") as mock_opener_cls:
        mock_opener = MagicMock()
        mock_opener.open.side_effect = redirect_error
        mock_opener_cls.return_value = mock_opener

        # Disable resolve_dns for this mocked public domain
        with patch("app.services.discovery.ssrf.validate_url_ssrf", side_effect=[(True, None), (False, "BLOCKED_IP_LITERAL: 169.254.169.254")]):
            with pytest.raises(SsrfProtectionError) as exc_info:
                safe_http_fetch("http://public.example.com/redirect")
            assert "SSRF violation" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 3. Platform Detection Tests
# ---------------------------------------------------------------------------

def test_discovery_worker_platform_detection():
    worker = DiscoveryWorker(enforce_dns_validation=False)

    assert worker.detect_platform("https://www.bilibili.com/video/BV1xx411c7mD") == "bilibili"
    assert worker.detect_platform("https://b23.tv/BV1xx411c7mD") == "bilibili"
    assert worker.detect_platform("https://www.iqiyi.com/v_19rr7phwvw.html") == "iqiyi"
    assert worker.detect_platform("https://www.iq.com/play/my-drama") == "iqiyi"
    assert worker.detect_platform("https://v.youku.com/v_show/id_XMTI4.html") == "youku"
    assert worker.detect_platform("https://v.qq.com/x/cover/m4410086.html") == "tencent"
    assert worker.detect_platform("https://wetv.vip/play/12345") == "tencent"
    assert worker.detect_platform("https://www.douyin.com/video/7123456789") == "douyin"
    assert worker.detect_platform("https://www.ixigua.com/1234567") == "xigua"
    assert worker.detect_platform("https://cdn.example.com/video.m3u8?token=123") == "manifest_hls"
    assert worker.detect_platform("https://cdn.example.com/dash/stream.mpd") == "manifest_dash"
    assert worker.detect_platform("https://cdn.example.com/media/clip.mp4") == "direct_file"
    assert worker.detect_platform("https://other-site.com/watch") == "generic"


# ---------------------------------------------------------------------------
# 4. Operator Authorization Gate Tests
# ---------------------------------------------------------------------------

def test_operator_authorization_required():
    worker = DiscoveryWorker(enforce_dns_validation=False)
    result = worker.probe_url(
        video_url="https://www.bilibili.com/video/BV1xx411c7mD",
        authorized_access_confirmed=False,
    )
    assert result.status == DiscoveryStatus.AUTH_REQUIRED.value
    assert result.error_code == "OPERATOR_AUTHORIZATION_REQUIRED"
    assert result.authentication_required is True
    assert result.download_tested is False


# ---------------------------------------------------------------------------
# 5. Manifest Discovery Adapter Tests (HLS & DASH)
# ---------------------------------------------------------------------------

HLS_CLEAR_FIXTURE = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",URI="sub_en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"
720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3500000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
1080p.m3u8
"""

HLS_DRM_FIXTURE = """#EXTM3U
#EXT-X-VERSION:5
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://fps.key",KEYFORMAT="com.apple.streamingkeydelivery"
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080
enc_1080p.m3u8
"""

DASH_CLEAR_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" minBufferTime="PT1.5S" type="static">
  <Period>
    <AdaptationSet mimeType="video/mp4" contentType="video">
      <Representation id="v1" width="1920" height="1080" bandwidth="4500000" codecs="avc1.640028"/>
      <Representation id="v2" width="1280" height="720" bandwidth="2200000" codecs="avc1.4d401f"/>
    </AdaptationSet>
  </Period>
</MPD>
"""

DASH_WIDEVINE_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:cenc="urn:mpeg:cenc:2013">
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed">
        <cenc:pssh>AAAANHBzc2gAAAAA7e+LqXnWSs6jyCfc1R0h7QAAABQIARIQ1234567890abcdef</cenc:pssh>
      </ContentProtection>
      <Representation id="v1080" width="1920" height="1080" bandwidth="5000000"/>
    </AdaptationSet>
  </Period>
</MPD>
"""

def test_manifest_adapter_hls_clear():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://cdn.example.com/clear_stream.m3u8",
        authorized_access_confirmed=True,
    )

    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/x-mpegURL"}, HLS_CLEAR_FIXTURE.encode("utf-8"))
        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.DISCOVERABLE.value
        assert result.drm_status == DrmStatus.NO_DRM_APPARENT.value
        assert result.recommended_engine == "n_m3u8dl_re"
        assert len(result.formats) == 2
        assert result.formats[0]["resolution"] == "1280x720"
        assert result.formats[1]["resolution"] == "1920x1080"
        assert result.subtitle_languages == ["en"]
        assert result.download_tested is False


def test_manifest_adapter_hls_drm_suspected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://cdn.example.com/drm_stream.m3u8",
        authorized_access_confirmed=True,
    )

    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/x-mpegURL"}, HLS_DRM_FIXTURE.encode("utf-8"))
        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.DRM_SUSPECTED.value
        assert result.drm_status == DrmStatus.DRM_SUSPECTED.value
        assert result.download_tested is False


def test_manifest_adapter_dash_clear():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://cdn.example.com/stream.mpd",
        authorized_access_confirmed=True,
    )

    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/dash+xml"}, DASH_CLEAR_FIXTURE.encode("utf-8"))
        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.DISCOVERABLE.value
        assert result.drm_status == DrmStatus.NO_DRM_APPARENT.value
        assert len(result.formats) == 2
        assert result.formats[0]["resolution"] == "1920x1080"


def test_manifest_adapter_dash_widevine_suspected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://cdn.example.com/stream.mpd",
        authorized_access_confirmed=True,
    )

    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/dash+xml"}, DASH_WIDEVINE_FIXTURE.encode("utf-8"))
        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.DRM_SUSPECTED.value
        assert result.drm_status == DrmStatus.DRM_SUSPECTED.value
        assert result.details.get("drm_scheme") == "Widevine"


# ---------------------------------------------------------------------------
# 6. Direct File Discovery Adapter Tests
# ---------------------------------------------------------------------------

def test_direct_file_adapter_clear_mp4():
    adapter = DirectFileDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://cdn.example.com/video.mp4",
        authorized_access_confirmed=True,
    )

    mock_body = b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    headers = {
        "content-type": "video/mp4",
        "content-range": "bytes 0-1023/10485760",
        "accept-ranges": "bytes",
    }

    with patch("app.services.discovery.adapters.direct.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (206, headers, mock_body)
        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.DISCOVERABLE.value
        assert result.drm_status == DrmStatus.NO_DRM_APPARENT.value
        assert result.recommended_engine == "direct_http"
        assert len(result.formats) == 1
        assert result.formats[0]["filesize_approx"] == 10485760
        assert result.download_tested is False


def test_direct_file_adapter_cenc_drm_suspected():
    adapter = DirectFileDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://cdn.example.com/encrypted.mp4",
        authorized_access_confirmed=True,
    )

    # Contains ISO-CENC protection atom
    mock_body = b"\x00\x00\x00 ftypisom\x00\x00\x00\x10sinf\x00\x00\x00\x0cschm\x00\x00\x00\x00cenc"
    headers = {"content-type": "video/mp4", "content-length": "5000000"}

    with patch("app.services.discovery.adapters.direct.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, headers, mock_body)
        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.DRM_SUSPECTED.value
        assert result.drm_status == DrmStatus.DRM_SUSPECTED.value


def test_direct_file_adapter_auth_required():
    adapter = DirectFileDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://cdn.example.com/protected.mp4",
        authorized_access_confirmed=True,
    )

    with patch("app.services.discovery.adapters.direct.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (403, {"content-type": "text/html"}, b"Access Denied")
        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.AUTH_REQUIRED.value
        assert result.authentication_required is True
        assert result.error_code == "HTTP_403"


# ---------------------------------------------------------------------------
# 7. yt-dlp Discovery Adapter Tests
# ---------------------------------------------------------------------------

def test_ytdlp_adapter_successful_probe():
    adapter = YtDlpDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://www.bilibili.com/video/BV1xx411c7mD",
        authorized_access_confirmed=True,
    )

    fake_info = {
        "id": "BV1xx411c7mD",
        "title": "Bilibili Sample Episode",
        "duration": 1845,
        "extractor": "BiliBili",
        "webpage_url_domain": "bilibili.com",
        "formats": [
            {
                "format_id": "16",
                "ext": "mp4",
                "width": 640,
                "height": 360,
                "resolution": "640x360",
                "vcodec": "avc1.64001e",
                "acodec": "mp4a.40.2",
                "tbr": 450.5,
            },
            {
                "format_id": "80",
                "ext": "mp4",
                "width": 1920,
                "height": 1080,
                "resolution": "1920x1080",
                "vcodec": "avc1.640028",
                "acodec": "mp4a.40.2",
                "tbr": 2800.0,
            },
        ],
        "subtitles": {"zh-Hans": [{"ext": "json"}], "en": [{"ext": "srt"}]},
    }

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = fake_info
        mock_ydl_cls.return_value = mock_ydl

        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.DISCOVERABLE.value
        assert result.title == "Bilibili Sample Episode"
        assert result.duration_seconds == 1845.0
        assert len(result.formats) == 2
        assert "zh-Hans" in result.subtitle_languages
        assert "en" in result.subtitle_languages
        assert result.download_tested is False


def test_ytdlp_adapter_classifies_drm_error():
    adapter = YtDlpDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://v.qq.com/x/cover/m4410086.html",
        authorized_access_confirmed=True,
    )

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = Exception("This video is DRM protected and cannot be extracted")
        mock_ydl_cls.return_value = mock_ydl

        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.DRM_SUSPECTED.value
        assert result.drm_status == DrmStatus.DRM_SUSPECTED.value
        assert result.error_code == "DRM_PROTECTED"


def test_ytdlp_adapter_classifies_login_required():
    adapter = YtDlpDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://www.iqiyi.com/v_vip_movie.html",
        authorized_access_confirmed=True,
    )

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = Exception("VIP account login required to access this stream")
        mock_ydl_cls.return_value = mock_ydl

        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.AUTH_REQUIRED.value
        assert result.authentication_required is True
        assert result.error_code == "AUTH_REQUIRED"


def test_ytdlp_adapter_classifies_geo_restricted():
    adapter = YtDlpDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://v.youku.com/v_show/id_geo.html",
        authorized_access_confirmed=True,
    )

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = Exception("This video is not available in your region due to copyright")
        mock_ydl_cls.return_value = mock_ydl

        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.GEO_RESTRICTED.value
        assert result.error_code == "GEO_RESTRICTED"


# ---------------------------------------------------------------------------
# 8. Worker Fallback & Invariant Tests
# ---------------------------------------------------------------------------

def test_worker_fallback_from_manifest_to_ytdlp():
    # If manifest adapter raises an error, fallback to yt-dlp
    worker = DiscoveryWorker(enforce_dns_validation=False)

    fake_info = {
        "id": "sample_stream",
        "title": "Fallback Video Success",
        "duration": 60,
        "formats": [{"format_id": "best", "ext": "mp4"}],
    }

    with patch("app.services.discovery.adapters.manifest.safe_http_fetch", side_effect=ConnectionError("Manifest CDN down")):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = fake_info
            mock_ydl_cls.return_value = mock_ydl

            result = worker.probe_url(
                video_url="https://cdn.example.com/playlist.m3u8",
                authorized_access_confirmed=True,
            )

            assert result.status == DiscoveryStatus.DISCOVERABLE.value
            assert result.title == "Fallback Video Success"
            assert result.download_tested is False


def test_worker_timeout_handling():
    worker = DiscoveryWorker(enforce_dns_validation=False)

    with patch("app.services.discovery.adapters.direct.safe_http_fetch", side_effect=TimeoutError("Connection timed out")):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.side_effect = TimeoutError("yt-dlp socket timeout")
            mock_ydl_cls.return_value = mock_ydl

            result = worker.probe_url(
                video_url="https://cdn.example.com/slow_video.mp4",
                timeout_seconds=2.0,
                authorized_access_confirmed=True,
            )

            assert result.status == DiscoveryStatus.PROBE_ERROR.value
            assert result.error_code in {"TIMEOUT", "ADAPTER_EXCEPTION", "DIRECT_PROBE_ERROR"}
            assert result.download_tested is False


def test_worker_malformed_metadata_handling():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://cdn.example.com/corrupt.mpd",
        authorized_access_confirmed=True,
    )

    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/dash+xml"}, b"<MPD><unclosed_tag>")
        result = adapter.probe(req)

        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "INVALID_DASH_MANIFEST"


def test_invariant_download_tested_never_true():
    # Absolute invariant test: regardless of input, download_tested MUST be False
    worker = DiscoveryWorker(enforce_dns_validation=False)
    result = worker.probe_url("https://www.bilibili.com/video/BV1test", authorized_access_confirmed=False)
    assert result.download_tested is False

    dict_repr = result.to_dict()
    assert dict_repr["download_tested"] is False


# ---------------------------------------------------------------------------
# 9. CLI Execution Tests
# ---------------------------------------------------------------------------

def test_cli_execution_with_ssrf_blocking(capsys):
    ret = cli_main(["http://127.0.0.1:8080/secret.mp4", "--confirm-authorized"])
    assert ret == 1
    out, _ = capsys.readouterr()
    payload = json.loads(out)
    assert payload["status"] == "PROBE_ERROR"
    assert payload["error_code"] == "SSRF_REJECTED"
    assert payload["download_tested"] is False


def test_cli_execution_without_auth_confirmation(capsys):
    ret = cli_main(["https://example.com/video.mp4"])
    assert ret == 0  # Expected discovery response
    out, _ = capsys.readouterr()
    payload = json.loads(out)
    assert payload["status"] == "AUTH_REQUIRED"
    assert payload["error_code"] == "OPERATOR_AUTHORIZATION_REQUIRED"


# ---------------------------------------------------------------------------
# 10. SSRF Hardening & DNS Rebinding Tests
# ---------------------------------------------------------------------------

def test_ssrf_dns_rebinding_socket_peer_blocked():
    from app.services.discovery.ssrf import SafeHTTPConnection, SsrfProtectionError
    import socket

    conn = SafeHTTPConnection("example.com", 80)
    # Simulate socket connected to private loopback IP (as in DNS rebinding)
    mock_sock = MagicMock()
    mock_sock.getpeername.return_value = ("127.0.0.1", 80)
    conn._create_connection = MagicMock(return_value=mock_sock)

    with pytest.raises(SsrfProtectionError) as exc_info:
        conn.connect()
    assert "DNS rebinding detected" in str(exc_info.value)
    mock_sock.close.assert_called_once()


def test_manifest_adapter_hls_variant_ssrf_poisoning_rejected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://legit-cdn.com/stream.m3u8",
        authorized_access_confirmed=True,
    )
    poisoned_hls = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1000000\n"
        "http://169.254.169.254/computeMetadata/v1/poisoned.m3u8\n"
    )
    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/x-mpegURL"}, poisoned_hls.encode("utf-8"))
        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SSRF_MANIFEST_POISONING"
        assert len(result.formats) == 0


def test_manifest_adapter_hls_key_ssrf_poisoning_rejected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://legit-cdn.com/stream.m3u8",
        authorized_access_confirmed=True,
    )
    poisoned_hls = (
        "#EXTM3U\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="http://127.0.0.1:8000/internal_secrets"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=1000000\n"
        "video.m3u8\n"
    )
    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/x-mpegURL"}, poisoned_hls.encode("utf-8"))
        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SSRF_MANIFEST_POISONING"


def test_manifest_adapter_dash_baseurl_ssrf_poisoning_rejected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://legit-cdn.com/stream.mpd",
        authorized_access_confirmed=True,
    )
    poisoned_dash = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">\n'
        '  <BaseURL>http://169.254.169.254/latest/meta-data/</BaseURL>\n'
        '  <Period>\n'
        '    <AdaptationSet mimeType="video/mp4">\n'
        '      <Representation id="1" bandwidth="1000000"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>'
    )
    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/dash+xml"}, poisoned_dash.encode("utf-8"))
        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SSRF_MANIFEST_POISONING"


def test_ytdlp_adapter_redirect_ssrf_protection():
    adapter = YtDlpDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://example.com/redirect_test",
        authorized_access_confirmed=True,
    )
    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_instance = MagicMock()
        mock_instance.extract_info.side_effect = SsrfProtectionError("SSRF redirect blocked targeting http://169.254.169.254")
        mock_ydl_cls.return_value.__enter__.return_value = mock_instance

        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SSRF_REJECTED"


# ---------------------------------------------------------------------------
# 11. Security Hardening & Fail-Closed Transport Tests
# ---------------------------------------------------------------------------

def test_ytdlp_fail_closed_when_secure_transport_fails():
    from app.services.discovery.adapters.ytdlp import SecureTransportUnavailableError

    adapter = YtDlpDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://www.bilibili.com/video/BV1test",
        authorized_access_confirmed=True,
    )

    with patch.object(adapter, "_get_safe_ydl_cls", side_effect=SecureTransportUnavailableError("Secure transport failed")):
        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SECURE_TRANSPORT_UNAVAILABLE"
        assert result.download_tested is False
        assert "Secure transport failed" in result.error_message


def test_ytdlp_build_request_director_drops_insecure_handlers():
    from app.services.discovery.adapters.ytdlp import _get_safe_ydl_cls

    safe_ydl_cls = _get_safe_ydl_cls()
    ydl = safe_ydl_cls({"quiet": True})
    SafeUrllibRH = safe_ydl_cls.SafeUrllibRH

    class DummyRequestsRH:
        RH_NAME = "requests"

    class DummyCurlRH:
        RH_NAME = "curl_cffi"

    class DummyWebsocketsRH:
        RH_NAME = "websockets"

    all_handlers = [SafeUrllibRH, DummyRequestsRH, DummyCurlRH, DummyWebsocketsRH]

    director = ydl.build_request_director(all_handlers, {})
    handler_classes = [h.__class__ for h in director.handlers.values()]
    assert SafeUrllibRH in handler_classes
    assert DummyRequestsRH not in handler_classes
    assert DummyCurlRH not in handler_classes
    assert DummyWebsocketsRH not in handler_classes


def test_ytdlp_rejects_unsafe_proxy():
    from app.services.discovery.adapters.ytdlp import _get_safe_ydl_cls

    safe_ydl_cls = _get_safe_ydl_cls()
    # 1. By default, untrusted discovery rejects any proxy
    with pytest.raises(SsrfProtectionError) as exc_info:
        safe_ydl_cls({"proxy": "http://127.0.0.1:8080"})
    assert "Proxy disabled for untrusted discovery" in str(exc_info.value)

    # 2. When proxy is authorized via operator flag, loopback IP is still rejected fail-closed
    with patch.dict("os.environ", {"AUTOSUB_ALLOW_DISCOVERY_PROXY": "1"}):
        with pytest.raises(SsrfProtectionError) as exc_info:
            safe_ydl_cls({"proxy": "http://127.0.0.1:8080"})
        assert "BLOCKED_IP_LITERAL" in str(exc_info.value)


def test_ssrf_rejects_ipv4_mapped_ipv6():
    # Loopback mapped IPv6
    safe, reason = validate_url_ssrf("http://[::ffff:127.0.0.1]/video.mp4", resolve_dns=False)
    assert not safe
    assert "BLOCKED_IP_LITERAL" in reason

    # Cloud metadata mapped IPv6
    safe, reason = validate_url_ssrf("http://[::ffff:169.254.169.254]/meta-data", resolve_dns=False)
    assert not safe
    assert "BLOCKED_IP_LITERAL" in reason

    # Private IP mapped IPv6
    safe, reason = validate_url_ssrf("http://[::ffff:10.0.0.1]/stream.m3u8", resolve_dns=False)
    assert not safe
    assert "BLOCKED_IP_LITERAL" in reason

    safe, reason = validate_url_ssrf("http://[::ffff:192.168.1.1]/media.mp4", resolve_dns=False)
    assert not safe
    assert "BLOCKED_IP_LITERAL" in reason


def test_validate_proxy_url():
    from app.services.discovery.ssrf import validate_proxy_url

    # Loopback proxy rejected
    safe, reason = validate_proxy_url("http://127.0.0.1:8080", resolve_dns=False)
    assert not safe
    assert "BLOCKED_IP_LITERAL" in reason

    # Cloud metadata proxy rejected
    safe, reason = validate_proxy_url("http://169.254.169.254:3128", resolve_dns=False)
    assert not safe

    # Non-HTTP proxy rejected
    safe, reason = validate_proxy_url("socks5://127.0.0.1:1080", resolve_dns=False)
    assert not safe


def test_safe_http_fetch_injects_empty_proxy_handler_by_default():
    with patch("urllib.request.build_opener") as mock_build_opener:
        mock_opener = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.read.return_value = b"OK"
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        with patch("app.services.discovery.ssrf.validate_url_ssrf", return_value=(True, None)):
            safe_http_fetch("https://example.com/test")

        args, _ = mock_build_opener.call_args
        handlers = args
        handler_types = [type(h) for h in handlers]
        assert urllib.request.ProxyHandler in handler_types
        for h in handlers:
            if isinstance(h, urllib.request.ProxyHandler):
                assert h.proxies == {}


def test_safe_http_connection_rejects_ipv4_mapped_ipv6_peer():
    from app.services.discovery.ssrf import SafeHTTPConnection, SsrfProtectionError

    conn = SafeHTTPConnection("example.com", 80)
    mock_sock = MagicMock()
    mock_sock.getpeername.return_value = ("::ffff:127.0.0.1", 80)
    conn._create_connection = MagicMock(return_value=mock_sock)

    with pytest.raises(SsrfProtectionError) as exc_info:
        conn.connect()
    assert "DNS rebinding detected" in str(exc_info.value)
    mock_sock.close.assert_called_once()


def test_manifest_adapter_hls_subtitle_ssrf_poisoning_rejected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://legit-cdn.com/stream.m3u8",
        authorized_access_confirmed=True,
    )
    poisoned_hls = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="En",URI="http://169.254.169.254/subs.vtt"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=1000000\n"
        "video.m3u8\n"
    )
    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/x-mpegURL"}, poisoned_hls.encode("utf-8"))
        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SSRF_MANIFEST_POISONING"


def test_manifest_adapter_hls_segment_ssrf_poisoning_rejected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://legit-cdn.com/media.m3u8",
        authorized_access_confirmed=True,
    )
    poisoned_media_playlist = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:10.0,\n"
        "http://127.0.0.1:8000/internal_chunk.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/x-mpegURL"}, poisoned_media_playlist.encode("utf-8"))
        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SSRF_MANIFEST_POISONING"


def test_manifest_adapter_hls_init_map_ssrf_poisoning_rejected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://legit-cdn.com/media.m3u8",
        authorized_access_confirmed=True,
    )
    poisoned_init = (
        "#EXTM3U\n"
        '#EXT-X-MAP:URI="http://169.254.169.254/init.mp4"\n'
        "#EXTINF:10.0,\n"
        "segment1.ts\n"
    )
    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/x-mpegURL"}, poisoned_init.encode("utf-8"))
        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SSRF_MANIFEST_POISONING"


def test_manifest_adapter_dash_segment_template_ssrf_poisoning_rejected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://legit-cdn.com/stream.mpd",
        authorized_access_confirmed=True,
    )
    poisoned_dash = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">\n'
        '  <Period>\n'
        '    <AdaptationSet mimeType="video/mp4">\n'
        '      <SegmentTemplate media="http://169.254.169.254/$Number$.m4s" initialization="http://127.0.0.1/init.mp4"/>\n'
        '      <Representation id="1" bandwidth="1000000"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>'
    )
    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/dash+xml"}, poisoned_dash.encode("utf-8"))
        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SSRF_MANIFEST_POISONING"


def test_manifest_adapter_dash_segment_url_ssrf_poisoning_rejected():
    adapter = ManifestDiscoveryAdapter()
    req = DiscoveryRequest(
        video_url="https://legit-cdn.com/stream.mpd",
        authorized_access_confirmed=True,
    )
    poisoned_dash = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">\n'
        '  <Period>\n'
        '    <AdaptationSet mimeType="video/mp4">\n'
        '      <SegmentList>\n'
        '        <Initialization sourceURL="http://10.0.0.1/init.mp4"/>\n'
        '        <SegmentURL media="http://172.16.0.1/seg1.m4s"/>\n'
        '      </SegmentList>\n'
        '      <Representation id="1" bandwidth="1000000"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>'
    )
    with patch("app.services.discovery.adapters.manifest.safe_http_fetch") as mock_fetch:
        mock_fetch.return_value = (200, {"content-type": "application/dash+xml"}, poisoned_dash.encode("utf-8"))
        result = adapter.probe(req)
        assert result.status == DiscoveryStatus.PROBE_ERROR.value
        assert result.error_code == "SSRF_MANIFEST_POISONING"


def test_worker_no_downgrade_after_secure_transport_failure():
    """Verify that when an adapter fails due to SECURE_TRANSPORT_UNAVAILABLE, the worker aborts immediately and never falls back."""
    mock_adapter1 = MagicMock()
    mock_adapter1.adapter_name = "yt-dlp"
    mock_adapter1.can_handle.return_value = True
    mock_adapter1.probe.return_value = DiscoveryResult(
        source_url_redacted="https://example.com/video",
        platform="generic",
        status=DiscoveryStatus.PROBE_ERROR.value,
        recommended_engine="yt-dlp",
        title=None,
        duration_seconds=None,
        error_code="SECURE_TRANSPORT_UNAVAILABLE",
        error_message="Secure transport unavailable",
        download_tested=False,
    )

    mock_adapter2 = MagicMock()
    mock_adapter2.adapter_name = "lux"
    mock_adapter2.can_handle.return_value = True

    worker = DiscoveryWorker(adapters=[mock_adapter1, mock_adapter2], enforce_dns_validation=False)
    result = worker.probe_url("https://example.com/video", authorized_access_confirmed=True)

    assert result.status == DiscoveryStatus.PROBE_ERROR.value
    assert result.error_code == "SECURE_TRANSPORT_UNAVAILABLE"
    mock_adapter1.probe.assert_called_once()
    mock_adapter2.probe.assert_not_called()


def test_worker_no_downgrade_after_ssrf_rejected():
    """Verify that when an adapter returns SSRF_REJECTED, worker aborts immediately without trying other adapters."""
    mock_adapter1 = MagicMock()
    mock_adapter1.adapter_name = "manifest"
    mock_adapter1.can_handle.return_value = True
    mock_adapter1.probe.return_value = DiscoveryResult(
        source_url_redacted="https://example.com/stream.m3u8",
        platform="manifest_hls",
        status=DiscoveryStatus.PROBE_ERROR.value,
        recommended_engine="n_m3u8dl_re",
        title=None,
        duration_seconds=None,
        error_code="SSRF_MANIFEST_POISONING",
        error_message="Variant targets internal address",
        download_tested=False,
    )

    mock_adapter2 = MagicMock()
    mock_adapter2.adapter_name = "yt-dlp"
    mock_adapter2.can_handle.return_value = True

    worker = DiscoveryWorker(adapters=[mock_adapter1, mock_adapter2], enforce_dns_validation=False)
    result = worker.probe_url("https://example.com/stream.m3u8", authorized_access_confirmed=True)

    assert result.status == DiscoveryStatus.PROBE_ERROR.value
    assert result.error_code == "SSRF_MANIFEST_POISONING"
    mock_adapter1.probe.assert_called_once()
    mock_adapter2.probe.assert_not_called()


def test_lux_disabled_by_default():
    """Verify that Lux is disabled by default due to unhardened subprocess network transport."""
    from app.services.discovery.adapters.lux import LuxDiscoveryAdapter

    lux = LuxDiscoveryAdapter()
    assert not lux.enabled
    assert not lux.is_available()
    assert not lux.can_handle("https://www.bilibili.com/video/BV1test")

    req = DiscoveryRequest(video_url="https://www.bilibili.com/video/BV1test", authorized_access_confirmed=True)
    res = lux.probe(req)
    assert res.status == DiscoveryStatus.UNSUPPORTED.value
    assert res.error_code == "LUX_DISABLED_UNHARDENED"


def test_ytdlp_proxy_disabled_by_default_even_with_public_proxy():
    """Verify that proxies are disabled by default for untrusted URL discovery to prevent proxy pivoting into internal networks."""
    from app.services.discovery.adapters.ytdlp import _get_safe_ydl_cls

    safe_ydl_cls = _get_safe_ydl_cls()
    with patch.dict("os.environ", {"AUTOSUB_ALLOW_DISCOVERY_PROXY": "0"}):
        with pytest.raises(SsrfProtectionError) as exc_info:
            safe_ydl_cls({"proxy": "http://198.51.100.1:8080"})
        assert "Proxy disabled for untrusted discovery" in str(exc_info.value)



