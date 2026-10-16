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
)
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

    def test_mocked_verified_tracker_scrape(self):
        service = TorrentDiscoveryService()
        infohash = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        raw_hash = bytes.fromhex(infohash)
        uri = f"magnet:?xt=urn:btih:{infohash}&tr=http://tracker.example.com/announce"

        # Mock scrape HTTP response body
        fake_scrape_response = bencode({
            "files": {
                raw_hash: {"complete": 42, "incomplete": 5, "downloaded": 100}
            }
        })

        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_scrape_response
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = service.discover_magnet(
                uri, mode=DiscoveryMode.NETWORK_PROBE, timeout=2.0, probe_allowed=True
            )

            assert res.seeders == 42
            assert res.status == DiscoveryStatus.METADATA_PENDING.value
            assert res.download_tested is False

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

