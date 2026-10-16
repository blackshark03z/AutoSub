"""
AutoSub Cloud V2 — Torrent Source Discovery Test Suite (WP-DL-001B)
Verifies Magnet parsing, v1/v2 infohashes, bencode security, episode detection,
passkey redaction, timeout handling, and zero-payload invariants.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.torrent_discovery import (
    TorrentDiscoveryService,
    TorrentDiscoveryRequest,
    TorrentDiscoveryResult,
    DiscoveryMode,
    DiscoveryStatus,
    parse_magnet_uri,
    is_magnet_uri,
    InvalidMagnetUriError,
    parse_torrent_bytes,
    parse_torrent_file,
    InvalidTorrentFileError,
    bencode,
    bdecode,
    bdecode_with_info_slice,
    BencodeError,
    BencodeDecodeError,
    BencodeDepthError,
    BencodeSizeError,
    sanitize_tracker_url,
    sanitize_tracker_urls,
    sanitize_error_message,
    is_private_tracker_url,
    validate_tracker_url,
    resolve_and_validate_hostname,
    SSRFBlockedError,
    SafeRedirectHandler,
    PinnedIPHTTPConnection,
    PinnedIPHTTPSConnection,
    create_safe_tracker_opener,
    TorrentFormat,
)
from app.services.torrent_discovery.probe import scrape_http_tracker
from app.services.torrent_discovery.adapters.libtorrent_adapter import LibtorrentDiscoveryAdapter
from app.services.torrent_discovery.episode_detector import (
    analyze_video_inventory,
    parse_episode_info,
)
from app.services.torrent_discovery.models import TorrentFileItem


# ============================================================================
# 1. Magnet URI Parsing Tests
# ============================================================================

class TestMagnetUriParsing:
    """Test BitTorrent Magnet URI parsing across v1, v2, and edge cases."""

    def test_valid_v1_hex_magnet(self):
        infohash_hex = "4a8c9b1234567890abcdef1234567890abcdef12"
        uri = f"magnet:?xt=urn:btih:{infohash_hex}&dn=Big+Buck+Bunny&tr=http://tracker.example.com/announce"
        parsed = parse_magnet_uri(uri)

        assert parsed.infohash == infohash_hex.lower()
        assert parsed.infohash_v1 == infohash_hex.lower()
        assert parsed.infohash_v2 is None
        assert parsed.display_name == "Big Buck Bunny"
        assert parsed.trackers == ["http://tracker.example.com/announce"]
        assert not parsed.is_hybrid

    def test_valid_v1_base32_magnet(self):
        # 20 raw bytes -> base32 encoded (32 chars)
        raw_bytes = b"\x01" * 20
        b32_str = base64.b32encode(raw_bytes).decode("ascii")
        expected_hex = raw_bytes.hex().lower()

        uri = f"magnet:?xt=urn:btih:{b32_str}&dn=Base32+Sample"
        parsed = parse_magnet_uri(uri)

        assert parsed.infohash == expected_hex
        assert parsed.infohash_v1 == expected_hex
        assert parsed.display_name == "Base32 Sample"

    def test_valid_v2_btmh_magnet(self):
        # BitTorrent v2 multihash (1220 prefix + 64 hex characters)
        v2_hash = "f" * 64
        uri = f"magnet:?xt=urn:btmh:1220{v2_hash}&dn=V2+Video"
        parsed = parse_magnet_uri(uri)

        assert parsed.infohash == v2_hash
        assert parsed.infohash_v2 == v2_hash
        assert parsed.infohash_v1 is None
        assert parsed.display_name == "V2 Video"

    def test_hybrid_magnet(self):
        v1_hash = "1" * 40
        v2_hash = "2" * 64
        uri = f"magnet:?xt=urn:btih:{v1_hash}&xt=urn:btmh:1220{v2_hash}&dn=Hybrid"
        parsed = parse_magnet_uri(uri)

        assert parsed.is_hybrid
        assert parsed.infohash == v1_hash
        assert parsed.infohash_v1 == v1_hash
        assert parsed.infohash_v2 == v2_hash

    def test_invalid_scheme(self):
        with pytest.raises(InvalidMagnetUriError, match="does not start with 'magnet:\\?'"):
            parse_magnet_uri("http://tracker.example.com/file.torrent")

    def test_missing_xt_parameter(self):
        with pytest.raises(InvalidMagnetUriError, match="missing required 'xt'"):
            parse_magnet_uri("magnet:?dn=No+Topic+Here")

    def test_malformed_infohash(self):
        with pytest.raises(InvalidMagnetUriError, match="Invalid btih infohash format"):
            parse_magnet_uri("magnet:?xt=urn:btih:tooshort")


# ============================================================================
# 2. Bencode Security & DoS Defense Tests
# ============================================================================

class TestBencodeSecurity:
    """Test safe bencode parsing, depth guards, and malicious input defense."""

    def test_primitive_roundtrip(self):
        data = {
            "int": 42,
            "neg": -100,
            "str": "hello world",
            "bytes": b"\x00\xff",
            "list": [1, "two", 3],
            "dict": {"a": 1, "b": "val"},
        }
        encoded = bencode(data)
        decoded = bdecode(encoded)

        assert decoded[b"int"] == 42
        assert decoded[b"neg"] == -100
        assert decoded[b"str"] == b"hello world"
        assert decoded[b"bytes"] == b"\x00\xff"
        assert decoded[b"list"] == [1, b"two", 3]
        assert decoded[b"dict"] == {b"a": 1, b"b": b"val"}

    def test_canonical_dictionary_key_sorting(self):
        # BitTorrent spec requires dict keys to be sorted lexicographically
        data = {"z": 1, "a": 2, "m": 3}
        encoded = bencode(data)
        assert encoded == b"d1:ai2e1:mi3e1:zi1ee"

    def test_invalid_integers_rejected(self):
        # Leading zeros forbidden: i03e
        with pytest.raises(BencodeDecodeError, match="Leading zeros are prohibited"):
            bdecode(b"i03e")

        # Negative zero forbidden: i-0e
        with pytest.raises(BencodeDecodeError, match="Negative zero is invalid"):
            bdecode(b"i-0e")

        # Empty integer: ie
        with pytest.raises(BencodeDecodeError, match="Empty integer token"):
            bdecode(b"ie")

    def test_unterminated_tokens(self):
        with pytest.raises(BencodeDecodeError, match="Unterminated list"):
            bdecode(b"li1ei2e")

        with pytest.raises(BencodeDecodeError, match="Unterminated dictionary"):
            bdecode(b"d1:a1:b")

        with pytest.raises(BencodeDecodeError, match="Unterminated integer"):
            bdecode(b"i123")

    def test_recursion_depth_limit_defense(self):
        # Nesting deeper than 32 levels must raise BencodeDepthError
        bomb = b"l" * 35 + b"e" * 35
        with pytest.raises(BencodeDepthError, match="recursion depth limit"):
            bdecode(bomb, max_depth=32)

    def test_max_file_size_limit_defense(self):
        # Huge payload over size limit must be rejected before parsing
        huge_bytes = b"0" * 1000
        with pytest.raises(BencodeSizeError, match="exceeds maximum allowed limit"):
            bdecode(huge_bytes, max_size=500)

    def test_raw_info_slice_accuracy(self):
        # Verify the raw info slice matches the exact bytes in the torrent
        info_dict = {"name": "TestFile", "length": 1024}
        raw_torrent = bencode({"announce": "http://tracker", "info": info_dict})

        _, raw_info = bdecode_with_info_slice(raw_torrent)
        assert raw_info is not None
        assert raw_info == bencode(info_dict)


# ============================================================================
# 3. .torrent Parsing & File Inventory Tests
# ============================================================================

class TestTorrentParsing:
    """Test single-file, multi-file, and BitTorrent v2 torrent decoding."""

    def test_single_file_torrent(self):
        info_dict = {
            "name": "SingleMovie.mp4",
            "length": 500000000,
            "piece length": 262144,
            "pieces": b"p" * 40,
        }
        torrent = {
            "announce": "http://tracker.example.com/announce",
            "info": info_dict,
        }
        raw = bencode(torrent)
        res, files, videos = parse_torrent_bytes(raw)

        assert res.status == DiscoveryStatus.METADATA_AVAILABLE.value
        assert res.metadata_complete is True
        assert res.total_size_bytes == 500000000
        assert res.torrent_name == "SingleMovie.mp4"
        assert res.is_multi_file is False
        assert res.is_multi_episode is False
        assert len(res.files) == 1
        assert res.files[0]["is_video"] is True
        assert res.files[0]["extension"] == ".mp4"
        assert len(res.video_candidates) == 1
        assert res.download_tested is False

    def test_multi_file_torrent_with_episodes(self):
        info_dict = {
            "name": "Attack on Titan Season 4",
            "piece length": 524288,
            "pieces": b"p" * 60,
            "files": [
                {"length": 300000000, "path": ["Season 4", "AoT S04E01.mkv"]},
                {"length": 310000000, "path": ["Season 4", "AoT S04E02.mkv"]},
                {"length": 5000, "path": ["Season 4", "release.nfo"]},
                {"length": 10000, "path": ["Season 4", "cover.jpg"]},
            ],
        }
        torrent = {
            "announce": "http://tracker.example.com/announce",
            "info": info_dict,
        }
        res, files, videos = parse_torrent_bytes(bencode(torrent))

        assert res.status == DiscoveryStatus.METADATA_AVAILABLE.value
        assert res.is_multi_file is True
        assert res.is_multi_episode is True
        assert res.total_size_bytes == 610015000
        assert len(res.files) == 4
        # Non-video files filtered from candidates
        assert len(res.video_candidates) == 2
        assert res.video_candidates[0]["episode_hint"] == "S04E01"
        assert res.video_candidates[0]["season_number"] == 4
        assert res.video_candidates[0]["episode_number"] == 1
        assert res.video_candidates[1]["episode_hint"] == "S04E02"

    def test_bittorrent_v2_file_tree_parsing(self):
        info_dict = {
            "meta version": 2,
            "name": "V2 Series",
            "piece length": 16384,
            "file tree": {
                "Episodes": {
                    "EP01.mp4": {"": {"length": 15000000, "pieces root": b"1" * 32}},
                    "EP02.mp4": {"": {"length": 16000000, "pieces root": b"2" * 32}},
                }
            },
        }
        torrent = {
            "announce": "http://tracker.example.com/announce",
            "info": info_dict,
        }
        res, files, videos = parse_torrent_bytes(bencode(torrent))

        assert res.status == DiscoveryStatus.METADATA_AVAILABLE.value
        assert res.infohash_v2 is not None
        assert len(res.infohash_v2) == 64
        assert res.total_size_bytes == 31000000
        assert len(res.files) == 2
        assert res.files[0]["path"] == "Episodes/EP01.mp4"
        assert res.files[0]["pieces_root"] == (b"1" * 32).hex()

    def test_corrupted_torrent_missing_info(self):
        with pytest.raises(InvalidTorrentFileError, match="Missing or invalid 'info'"):
            parse_torrent_bytes(bencode({"announce": "http://tracker"}))


# ============================================================================
# 4. Episode Detection & Classification Tests
# ============================================================================

class TestEpisodeClassification:
    """Test scene episode pattern matching."""

    @pytest.mark.parametrize(
        "filename,expected_hint,expected_s,expected_e",
        [
            ("Game.of.Thrones.S01E05.1080p.mkv", "S01E05", 1, 5),
            ("show.s2e10.mp4", "S02E10", 2, 10),
            ("Anime Title [03] 1080p.mkv", "EP03", 1, 3),
            ("Drama Series EP08 720p.mp4", "EP08", 1, 8),
            ("Random Movie (2024).mp4", None, None, None),
        ],
    )
    def test_episode_pattern_matching(self, filename, expected_hint, expected_s, expected_e):
        hint, s, e = parse_episode_info(filename)
        assert hint == expected_hint
        assert s == expected_s
        assert e == expected_e


# ============================================================================
# 5. Secret & Passkey Redaction Tests
# ============================================================================

class TestSecretRedaction:
    """Ensure private tracker passkeys and tokens never leak."""

    def test_query_param_passkey_redaction(self):
        url = "http://tracker.private.org/announce?passkey=abcdef1234567890abcdef"
        sanitized = sanitize_tracker_url(url)
        assert "passkey=%5BREDACTED%5D" in sanitized or "passkey=[REDACTED]" in sanitized
        assert "abcdef1234567890abcdef" not in sanitized

    def test_query_auth_token_redaction(self):
        url = "http://tracker.private.org/announce?auth=secret_token&user=john"
        sanitized = sanitize_tracker_url(url)
        assert "auth=%5BREDACTED%5D" in sanitized or "auth=[REDACTED]" in sanitized
        assert "user=john" in sanitized

    def test_basic_auth_password_redaction(self):
        url = "http://user:secretpassword@tracker.example.com/announce"
        sanitized = sanitize_tracker_url(url)
        assert "secretpassword" not in sanitized
        assert "user:[REDACTED]@tracker.example.com" in sanitized

    def test_path_token_redaction(self):
        token = "0123456789abcdef0123456789abcdef"
        url = f"http://tracker.private.org/announce/{token}"
        sanitized = sanitize_tracker_url(url)
        assert token not in sanitized
        assert "[REDACTED]" in sanitized


# ============================================================================
# 6. Service, Timeout & Offline Safety Invariants
# ============================================================================

class TestServiceAndSafetyInvariants:
    """Verify service behavior, timeout handling, and media non-download."""

    def test_offline_magnet_discovery_never_calls_network(self):
        service = TorrentDiscoveryService()
        uri = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=OfflineTest"

        # Explicitly offline
        res = service.discover_magnet(uri, mode=DiscoveryMode.OFFLINE, probe_allowed=False)

        assert res.source_type == "torrent"
        assert res.status == DiscoveryStatus.INFOHASH_ONLY.value
        assert res.metadata_complete is False
        assert res.seeders is None
        assert res.files == []
        assert res.download_tested is False
        assert res.torrent_name == "OfflineTest"

    def test_network_probe_unauthorized_fails_safe(self):
        service = TorrentDiscoveryService()
        uri = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=SafetyTest"

        # Requested network probe, but probe_allowed=False (operator gate)
        res = service.discover_magnet(uri, mode=DiscoveryMode.NETWORK_PROBE, probe_allowed=False)

        assert res.status == DiscoveryStatus.INFOHASH_ONLY.value
        assert res.metadata_complete is False
        assert res.seeders is None

    def test_network_probe_timeout_handling(self):
        service = TorrentDiscoveryService()
        uri = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&tr=http://192.0.2.1/announce"

        # 192.0.2.1 is TEST-NET-1 (non-routable), will time out
        req = TorrentDiscoveryRequest(
            source=uri,
            mode=DiscoveryMode.NETWORK_PROBE,
            timeout_seconds=0.5,
            probe_allowed=True,
        )
        res = service.discover(req)

        assert res.status == DiscoveryStatus.METADATA_FETCH_FAILED.value
        assert res.seeders is None
        assert res.metadata_complete is False
        assert res.infohash == "da39a3ee5e6b4b0d3255bfef95601890afd80709"

    def test_mocked_tracker_scrape_contract_verification(self):
        """
        Verifies the BEP 48 bencode scrape response parsing contract using simulated responses.
        NOTE: This is a contract verification test using simulated fixtures;
        it does NOT claim live swarm verification against a real external network.
        """
        service = TorrentDiscoveryService()
        infohash = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        raw_hash = bytes.fromhex(infohash)
        uri = f"magnet:?xt=urn:btih:{infohash}&tr=http://tracker.example.com/announce"

        # Simulated scrape HTTP response body
        fake_scrape_response = bencode({
            "files": {
                raw_hash: {"complete": 42, "incomplete": 5, "downloaded": 100}
            }
        })

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_scrape_response
        mock_resp.__enter__.return_value = mock_resp

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        # Patch both create_safe_tracker_opener and validate_tracker_url to allow simulated host
        with patch("app.services.torrent_discovery.probe.create_safe_tracker_opener", return_value=(mock_opener, "93.184.216.34")), \
             patch("app.services.torrent_discovery.probe.validate_tracker_url", return_value="93.184.216.34"):
            res = service.discover_magnet(
                uri, mode=DiscoveryMode.NETWORK_PROBE, timeout=2.0, probe_allowed=True
            )

            assert res.seeders == 42
            assert res.seeders_provenance == "live_tracker_scrape"
            assert res.status == DiscoveryStatus.METADATA_PENDING.value
            assert res.download_tested is False

    def test_ssrf_tracker_probe_blocked_safely(self):
        """Ensure network probe against cloud metadata or private IP fails safe without exception."""
        service = TorrentDiscoveryService()
        infohash = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        # 169.254.169.254 is AWS/GCP/Azure cloud metadata endpoint
        uri = f"magnet:?xt=urn:btih:{infohash}&tr=http://169.254.169.254/announce"

        res = service.discover_magnet(
            uri, mode=DiscoveryMode.NETWORK_PROBE, timeout=1.0, probe_allowed=True
        )

        assert res.status == DiscoveryStatus.METADATA_FETCH_FAILED.value
        assert res.seeders is None
        assert res.seeders_provenance == "unverified"
        assert "SSRF blocked" in (res.error_message or "")

    def test_cli_execution_smoke(self, tmp_path: Path):
        # Create a real small .torrent file on disk
        torrent_file = tmp_path / "sample.torrent"
        torrent_data = {
            "announce": "http://tracker.example.com/announce",
            "info": {
                "name": "CliTest.mp4",
                "length": 1024000,
                "piece length": 32768,
                "pieces": b"x" * 20,
            },
        }
        torrent_file.write_bytes(bencode(torrent_data))

        # Invoke CLI runner via subprocess
        cli_script = Path(__file__).parent.parent / "tools" / "torrent_discovery_cli.py"
        cmd = [sys.executable, str(cli_script), str(torrent_file), "--pretty"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)

        payload = json.loads(proc.stdout)
        assert payload["source_type"] == "torrent"
        assert payload["status"] == "METADATA_AVAILABLE"
        assert payload["torrent_name"] == "CliTest.mp4"
        assert payload["total_size_bytes"] == 1024000
        assert payload["metadata_complete"] is True
        assert payload["download_tested"] is False
        assert payload["recommended_engine"] == "libtorrent"

    def test_utf8_cjk_filenames_in_torrent(self):
        cjk_title = "鬼灭之刃 锻刀村篇 第01话.mp4"
        torrent_data = {
            "announce": "http://tracker.example.com/announce",
            "info": {
                "name.utf-8": cjk_title,
                "length": 524288000,
                "piece length": 65536,
                "pieces": b"x" * 20,
            },
        }
        res, files, videos = parse_torrent_bytes(bencode(torrent_data))
        assert res.torrent_name == cjk_title
        assert len(res.files) == 1
        assert res.files[0]["path"] == cjk_title
        assert res.files[0]["is_video"] is True

    def test_nonexistent_file_handling(self):
        service = TorrentDiscoveryService()
        res = service.discover_file("C:/does/not/exist/fake.torrent")
        assert res.status == DiscoveryStatus.INVALID_SOURCE.value
        assert "neither an existing file nor a magnet URI" in (res.error_message or "")

    def test_truncated_string_length_rejected(self):
        # String header says 50 bytes, but only 5 provided
        with pytest.raises(BencodeDecodeError, match="exceeds available payload size"):
            bdecode(b"50:short")


# ============================================================================
# 7. SSRF Protection Tests (Private IPs, Loopback, Cloud Metadata, Redirects)
# ============================================================================

class TestTrackerSSRFDefense:
    """Ensure tracker probe refuses to contact internal or cloud metadata infrastructure."""

    @pytest.mark.parametrize(
        "prohibited_url,expected_reason",
        [
            ("http://127.0.0.1:8080/announce", "Loopback address prohibited"),
            ("http://127.0.0.2:80/announce", "Loopback address prohibited"),
            ("http://[::1]:8080/announce", "Loopback address prohibited"),
            ("http://localhost:6969/announce", "Prohibited hostname: localhost"),
            ("http://10.0.0.1:80/announce", "Private RFC 1918/4193 address prohibited"),
            ("http://10.254.254.254/announce", "Private RFC 1918/4193 address prohibited"),
            ("http://172.16.0.1:80/announce", "Private RFC 1918/4193 address prohibited"),
            ("http://172.31.255.255/announce", "Private RFC 1918/4193 address prohibited"),
            ("http://192.168.1.1:80/announce", "Private RFC 1918/4193 address prohibited"),
            ("http://169.254.169.254/latest/meta-data/", "Prohibited cloud metadata IP: 169.254.169.254"),
            ("http://100.100.100.200/announce", "Prohibited cloud metadata IP: 100.100.100.200"),
            ("http://metadata.google.internal/announce", "Prohibited hostname: metadata.google.internal"),
            ("http://0.0.0.0:80/announce", "Unspecified.*address prohibited"),
            ("http://[::]:80/announce", "Unspecified.*address prohibited"),
        ],
    )
    def test_direct_ssrf_urls_blocked(self, prohibited_url, expected_reason):
        with pytest.raises(SSRFBlockedError, match=expected_reason):
            validate_tracker_url(prohibited_url)

    @pytest.mark.parametrize("dangerous_port", [21, 22, 23, 25, 445, 2375, 6379, 10250, 11211, 27017])
    def test_dangerous_service_ports_blocked(self, dangerous_port):
        url = f"http://tracker.example.com:{dangerous_port}/announce"
        with pytest.raises(SSRFBlockedError, match=f"Port {dangerous_port} in tracker URL is blocked"):
            validate_tracker_url(url)

    def test_redirect_to_cloud_metadata_blocked(self):
        """Verify SafeRedirectHandler intercepts 302 redirect to cloud metadata and blocks it."""
        handler = SafeRedirectHandler(max_redirects=3)
        req = MagicMock()
        headers = {}
        
        # Simulating HTTP 302 redirect to AWS/GCP metadata IP
        redirect_target = "http://169.254.169.254/latest/meta-data/"
        
        with pytest.raises(SSRFBlockedError, match="Prohibited cloud metadata IP"):
            handler.redirect_request(req, fp=None, code=302, msg="Found", headers=headers, newurl=redirect_target)

    def test_redirect_to_loopback_blocked(self):
        """Verify SafeRedirectHandler intercepts 301 redirect to localhost and blocks it."""
        handler = SafeRedirectHandler(max_redirects=3)
        req = MagicMock()
        headers = {}
        
        redirect_target = "http://127.0.0.1:8080/scrape"
        with pytest.raises(SSRFBlockedError, match="Loopback address prohibited"):
            handler.redirect_request(req, fp=None, code=301, msg="Moved", headers=headers, newurl=redirect_target)

    def test_safe_public_ip_passes_validation(self):
        """Legitimate public IP address passes SSRF validation."""
        # 93.184.216.34 is example.com public IP
        validate_tracker_url("http://93.184.216.34:6969/announce")


# ============================================================================
# 8. P0-01: Libtorrent Metadata-Only Safety Tests
# ============================================================================

class TestLibtorrentSafety:
    """Verifies that libtorrent network mode is disabled by default, prevents media payload I/O, and enforces session cleanup."""

    def test_libtorrent_network_mode_disabled_by_default(self):
        """Adapter has network_probe_enabled=False by default and returns PROBE_NOT_ALLOWED without opening sessions."""
        adapter = LibtorrentDiscoveryAdapter()
        assert adapter.network_probe_enabled is False

        uri = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=Test"
        req = TorrentDiscoveryRequest(
            source=uri,
            mode=DiscoveryMode.NETWORK_PROBE,
            probe_allowed=True,
            allow_libtorrent_network=False,
        )

        mock_lt = MagicMock()
        mock_params = MagicMock()
        mock_params.info_hash = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        mock_params.name = "Test"
        mock_lt.parse_magnet_uri.return_value = mock_params

        with patch("app.services.torrent_discovery.adapters.libtorrent_adapter.LIBTORRENT_AVAILABLE", True), \
             patch("app.services.torrent_discovery.adapters.libtorrent_adapter.lt", mock_lt):
            res = adapter.discover(req)
            assert res.status == DiscoveryStatus.PROBE_NOT_ALLOWED.value
            assert "disabled by default" in res.error_message
            # Critical invariant: session.add_torrent was NEVER called
            mock_lt.session.assert_not_called()

    def test_libtorrent_adapter_no_arbitrary_listen_port(self):
        """When network mode is enabled under explicit authorization, verify no arbitrary listen ports are opened."""
        adapter = LibtorrentDiscoveryAdapter(network_probe_enabled=True)
        uri = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=Test"
        req = TorrentDiscoveryRequest(
            source=uri,
            mode=DiscoveryMode.NETWORK_PROBE,
            probe_allowed=True,
            timeout_seconds=0.1,
            allow_libtorrent_network=True,
        )

        mock_lt = MagicMock()
        mock_params = MagicMock()
        mock_params.flags = 0
        mock_params.info_hash = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        mock_params.name = "Test"
        mock_lt.parse_magnet_uri.return_value = mock_params

        mock_session = MagicMock()
        mock_handle = MagicMock()
        mock_handle.has_metadata.return_value = False
        mock_session.add_torrent.return_value = mock_handle
        mock_lt.session.return_value = mock_session

        with patch("app.services.torrent_discovery.adapters.libtorrent_adapter.LIBTORRENT_AVAILABLE", True), \
             patch("app.services.torrent_discovery.adapters.libtorrent_adapter.lt", mock_lt):
            res = adapter.discover(req)
            assert res.status == DiscoveryStatus.METADATA_FETCH_FAILED.value
            # Verify listen_on was NEVER called
            mock_session.listen_on.assert_not_called()
            # Verify session cleanup called remove_torrent
            mock_session.remove_torrent.assert_called_once_with(mock_handle)

    def test_libtorrent_session_cleanup_on_exception(self):
        """Verifies session.remove_torrent is called in finally block even if an exception occurs during loop."""
        adapter = LibtorrentDiscoveryAdapter(network_probe_enabled=True)
        uri = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709"
        req = TorrentDiscoveryRequest(
            source=uri,
            mode=DiscoveryMode.NETWORK_PROBE,
            probe_allowed=True,
            allow_libtorrent_network=True,
        )

        mock_lt = MagicMock()
        mock_params = MagicMock()
        mock_params.flags = 0
        mock_params.info_hash = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        mock_params.name = "Test"
        mock_lt.parse_magnet_uri.return_value = mock_params

        mock_session = MagicMock()
        mock_handle = MagicMock()
        mock_handle.has_metadata.side_effect = RuntimeError("Fatal swarm glitch")
        mock_session.add_torrent.return_value = mock_handle
        mock_lt.session.return_value = mock_session

        with patch("app.services.torrent_discovery.adapters.libtorrent_adapter.LIBTORRENT_AVAILABLE", True), \
             patch("app.services.torrent_discovery.adapters.libtorrent_adapter.lt", mock_lt):
            res = adapter.discover(req)
            assert res.status == DiscoveryStatus.ERROR.value
            mock_session.remove_torrent.assert_called_once_with(mock_handle)

    def test_download_tested_is_operational_indicator_not_cryptographic_proof(self):
        """Field download_tested=False is an operational indicator, not a cryptographic assertion of zero download."""
        res = TorrentDiscoveryResult(
            source_type="torrent",
            status=DiscoveryStatus.METADATA_AVAILABLE.value,
            infohash="da39a3ee5e6b4b0d3255bfef95601890afd80709",
            download_tested=False,
        )
        assert res.download_tested is False
        assert "download_tested" in res.to_dict()


# ============================================================================
# 9. P0-02: SSRF Hardening & DNS Rebinding Tests
# ============================================================================

class TestSSRFDnsRebindingAndAbnormalUrls:
    """Verifies DNS rebinding TOCTOU defense, pinned-IP socket connection, and abnormal URL validation."""

    def test_pinned_ip_http_connection_uses_pinned_ip(self):
        """PinnedIPHTTPConnection must connect socket directly to pinned_ip, preventing DNS rebinding."""
        conn = PinnedIPHTTPConnection(host="tracker.example.com", port=80, pinned_ip="93.184.216.34", timeout=5.0)
        with patch("socket.create_connection") as mock_create_conn:
            mock_sock = MagicMock()
            mock_create_conn.return_value = mock_sock
            conn.connect()
            assert mock_create_conn.call_args[0][0] == ("93.184.216.34", 80)
            assert mock_create_conn.call_args[0][1] == 5.0
            assert conn.sock == mock_sock

    def test_pinned_ip_https_connection_uses_pinned_ip_and_sni(self):
        """PinnedIPHTTPSConnection must connect socket to pinned_ip and use original hostname for SNI/cert validation."""
        mock_ssl_inst = MagicMock()
        mock_wrapped_sock = MagicMock()
        mock_ssl_inst.wrap_socket.return_value = mock_wrapped_sock

        conn = PinnedIPHTTPSConnection(
            host="tracker.example.com", port=443, pinned_ip="93.184.216.34", timeout=5.0, context=mock_ssl_inst
        )
        with patch("socket.create_connection") as mock_create_conn:
            mock_sock = MagicMock()
            mock_create_conn.return_value = mock_sock

            conn.connect()
            assert mock_create_conn.call_args[0][0] == ("93.184.216.34", 443)
            assert mock_create_conn.call_args[0][1] == 5.0
            mock_ssl_inst.wrap_socket.assert_called_once_with(mock_sock, server_hostname="tracker.example.com")
            assert conn.sock == mock_wrapped_sock

    @pytest.mark.parametrize(
        "abnormal_url,expected_pattern",
        [
            ("http://2130706433/announce", "Decimal IP .*prohibited"),
            ("http://0x7f000001/announce", "Hex IP .*prohibited"),
            ("http://[::ffff:127.0.0.1]/announce", "Private RFC 1918/4193 address prohibited|Loopback address prohibited"),
            ("http://[::ffff:169.254.169.254]/announce", "Prohibited cloud metadata IP"),
            ("http://[2002:7f00:0001::]/announce", "Private RFC 1918/4193 address prohibited|Loopback address prohibited|6to4"),
            ("http://admin:secret@tracker.example.com/announce", "Userinfo credentials embedded in tracker URL are prohibited"),
        ],
    )
    def test_abnormal_urls_and_encodings_blocked(self, abnormal_url, expected_pattern):
        """Abnormal IPv4 encodings, IPv4-mapped IPv6, 6to4, and embedded user credentials must be blocked."""
        with pytest.raises(SSRFBlockedError, match=expected_pattern):
            validate_tracker_url(abnormal_url)

    def test_redirect_chain_limit_exceeded(self):
        """Redirecting more than max_redirects hops must raise SSRFBlockedError."""
        handler = SafeRedirectHandler(max_redirects=3)
        req = MagicMock()
        req.get_method.return_value = "GET"
        req.full_url = "http://tracker1.example.com/announce"
        headers = {}
        with patch("app.services.torrent_discovery.ssrf.validate_tracker_url", return_value="93.184.216.34"):
            # Hop 1, 2, 3 ok
            handler.redirect_request(req, None, 302, "Found", headers, "http://tracker1.example.com/announce")
            handler.redirect_request(req, None, 302, "Found", headers, "http://tracker2.example.com/announce")
            handler.redirect_request(req, None, 302, "Found", headers, "http://tracker3.example.com/announce")
            # Hop 4 exceeds limit
            with pytest.raises(SSRFBlockedError, match="Too many redirects"):
                handler.redirect_request(req, None, 302, "Found", headers, "http://tracker4.example.com/announce")

    def test_redirect_updates_pinned_ip_map(self):
        """Every redirect hop re-validates target and records its pinned IP for subsequent requests."""
        pinned_map = {}
        handler = SafeRedirectHandler(max_redirects=3, pinned_ip_map=pinned_map)
        req = MagicMock()
        req.get_method.return_value = "GET"
        req.full_url = "http://tracker-target.example.com/announce"
        headers = {}

        with patch("app.services.torrent_discovery.ssrf.validate_tracker_url", return_value="93.184.216.34"):
            handler.redirect_request(req, None, 302, "Found", headers, "http://tracker-target.example.com/announce")
            assert pinned_map["tracker-target.example.com"] == "93.184.216.34"


# ============================================================================
# 10. P1-01: Private Tracker Credential Handling Tests
# ============================================================================

class TestPrivateTrackerCredentialPolicy:
    """Verifies that private tracker passkeys are segregated in memory and never exposed in output/logs."""

    def test_is_private_tracker_url_detection(self):
        """Accurately detects passkeys/tokens across query parameters and URL path segments."""
        assert is_private_tracker_url("https://tracker.example.com/announce?passkey=abcdef1234567890abcdef") is True
        assert is_private_tracker_url("https://tracker.example.com/announce?authkey=abcdef1234567890") is True
        assert is_private_tracker_url("https://tracker.example.com/announce?torrent_pass=abcdef1234567890") is True
        assert is_private_tracker_url("https://tracker.example.com/announce?key=abcdef1234567890") is True
        assert is_private_tracker_url("https://tracker.example.com:443/0123456789abcdef0123456789abcdef/announce") is True
        # Public trackers do not trigger private detection
        assert is_private_tracker_url("http://tracker.opentrackr.org:1337/announce") is False
        assert is_private_tracker_url("udp://tracker.openbittorrent.com:6969/announce") is False

    def test_private_tracker_probe_blocked_by_default(self):
        """Scraping a private tracker URL is blocked by default to protect ratio and passkey."""
        url = "https://tracker.example.com/announce?passkey=mysecretpasskey123"
        seeders, prov, err = scrape_http_tracker(
            url,
            infohash_hex="da39a3ee5e6b4b0d3255bfef95601890afd80709",
            allow_private_tracker_probe=False,
        )
        assert seeders is None
        assert prov == "unverified"
        assert "Private tracker probe is disabled by default" in (err or "")
        # Critical: Passkey must NOT be exposed in the error message
        assert "mysecretpasskey123" not in (err or "")

    def test_raw_trackers_hidden_from_result_serialization(self):
        """_raw_trackers is kept in memory (repr=False) and NEVER exposed in to_dict() or repr()."""
        raw_url = "https://tracker.example.com/announce?passkey=secret12345"
        sanitized_url = sanitize_tracker_url(raw_url)

        res = TorrentDiscoveryResult(
            source_type="torrent",
            status=DiscoveryStatus.METADATA_AVAILABLE.value,
            infohash="da39a3ee5e6b4b0d3255bfef95601890afd80709",
            trackers=[sanitized_url],
            _raw_trackers=[raw_url],
        )

        # In memory access
        assert res._raw_trackers == [raw_url]
        # Serialized output
        serialized = res.to_dict()
        assert "_raw_trackers" not in serialized
        assert serialized["trackers"] == ["https://tracker.example.com/announce?passkey=[REDACTED]"]
        assert "secret12345" not in repr(res)
        assert "secret12345" not in str(res)

    def test_sanitize_error_message_scrubs_passkeys(self):
        """All error messages scrub passkeys from query params."""
        raw_msg = "Failed connecting to https://tracker.org/announce?passkey=topsecretpassword123456&mode=scrape"
        scrubbed = sanitize_error_message(raw_msg)
        assert "topsecretpassword123456" not in scrubbed
        assert "passkey=[REDACTED]" in scrubbed


# ============================================================================
# 11. P1-02: Torrent v1/v2 Identity Correctness Tests
# ============================================================================

class TestTorrentV1V2IdentityCorrectness:
    """Verifies strict adherence to BitTorrent v1, v2-only, and hybrid identity protocols."""

    def test_v1_only_torrent_identification(self):
        """Standard v1 torrent with 'pieces' has 40-char infohash_v1 and infohash_v2 is None."""
        v1_info = {b"name": b"v1_sample.mp4", b"piece length": 16384, b"pieces": b"A" * 20, b"length": 5000}
        data = bencode({b"info": v1_info, b"announce": b"http://tracker.example.com/announce"})

        res, files, videos = parse_torrent_bytes(data)
        assert res.torrent_format == TorrentFormat.V1.value
        assert res.infohash_v1 is not None
        assert len(res.infohash_v1) == 40
        assert res.infohash_v2 is None
        assert res.infohash == res.infohash_v1

    def test_v2_only_torrent_identification_no_fake_sha1(self):
        """v2-only torrent (BEP 52) has 64-char infohash_v2; infohash_v1 is strictly None (no fake SHA-1)."""
        v2_info = {
            b"meta version": 2,
            b"name": b"v2_sample.mp4",
            b"piece length": 16384,
            b"file tree": {
                b"v2_sample.mp4": {
                    b"": {b"length": 8000, b"pieces root": b"B" * 32}
                }
            },
        }
        data = bencode({b"info": v2_info, b"announce": b"http://tracker.example.com/announce"})

        res, files, videos = parse_torrent_bytes(data)
        assert res.torrent_format == TorrentFormat.V2.value
        assert res.infohash_v2 is not None
        assert len(res.infohash_v2) == 64
        # P1-02 Core Requirement: v2-only torrent MUST NOT have an invented SHA-1 infohash_v1
        assert res.infohash_v1 is None
        assert res.infohash == res.infohash_v2

    def test_hybrid_torrent_identification(self):
        """Hybrid torrent contains both valid v1 pieces and v2 file tree; provides both infohashes."""
        hybrid_info = {
            b"meta version": 2,
            b"name": b"hybrid_sample.mp4",
            b"piece length": 16384,
            b"pieces": b"C" * 20,
            b"file tree": {
                b"hybrid_sample.mp4": {
                    b"": {b"length": 9000, b"pieces root": b"D" * 32}
                }
            },
        }
        data = bencode({b"info": hybrid_info, b"announce": b"http://tracker.example.com/announce"})

        res, files, videos = parse_torrent_bytes(data)
        assert res.torrent_format == TorrentFormat.HYBRID.value
        assert res.infohash_v1 is not None
        assert len(res.infohash_v1) == 40
        assert res.infohash_v2 is not None
        assert len(res.infohash_v2) == 64
        assert res.infohash == res.infohash_v1

    def test_magnet_v1_v2_hybrid_format_classification(self):
        """Magnet URIs correctly identify v1, v2, or hybrid format based on exact xt urn types."""
        v1_mag = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=V1Only"
        p_v1 = parse_magnet_uri(v1_mag)
        assert p_v1.torrent_format == "v1"
        assert p_v1.infohash_v1 == "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        assert p_v1.infohash_v2 is None

        v2_mag = "magnet:?xt=urn:btmh:1220" + "00" * 32 + "&dn=V2Only"
        p_v2 = parse_magnet_uri(v2_mag)
        assert p_v2.torrent_format == "v2"
        assert p_v2.infohash_v2 == "00" * 32
        assert p_v2.infohash_v1 is None

        hybrid_mag = f"magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&xt=urn:btmh:1220{'00' * 32}&dn=Hybrid"
        p_hyb = parse_magnet_uri(hybrid_mag)
        assert p_hyb.torrent_format == "hybrid"
        assert p_hyb.infohash_v1 == "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        assert p_hyb.infohash_v2 == "00" * 32

    def test_bep48_scrape_for_v2_torrent_does_not_use_fake_sha1(self):
        """BEP 48 tracker scrape logic receives 32-byte hash (or truncated 20-byte), never a fake SHA-1."""
        infohash_v2 = "11" * 32
        raw_hash_v2 = bytes.fromhex(infohash_v2)

        fake_scrape_response = bencode({
            "files": {
                raw_hash_v2: {"complete": 17, "incomplete": 2, "downloaded": 50}
            }
        })

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_scrape_response
        mock_resp.__enter__.return_value = mock_resp

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp

        with patch("app.services.torrent_discovery.probe.create_safe_tracker_opener", return_value=(mock_opener, "93.184.216.34")), \
             patch("app.services.torrent_discovery.probe.validate_tracker_url", return_value="93.184.216.34"):
            seeders, prov, err = scrape_http_tracker(
                "http://tracker.example.com/announce",
                infohash_hex=infohash_v2,
            )
            assert seeders == 17
            assert prov == "live_tracker_scrape"
            assert err is None



