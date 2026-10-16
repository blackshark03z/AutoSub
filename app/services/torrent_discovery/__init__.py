"""
AutoSub Cloud V2 — Torrent Discovery Module (WP-DL-001B)
"""

from app.services.torrent_discovery.bencode import (
    bdecode,
    bdecode_with_info_slice,
    bencode,
    BencodeError,
    BencodeDecodeError,
    BencodeDepthError,
    BencodeSizeError,
)
from app.services.torrent_discovery.magnet import (
    parse_magnet_uri,
    is_magnet_uri,
    InvalidMagnetUriError,
    ParsedMagnet,
)
from app.services.torrent_discovery.models import (
    DiscoveryMode,
    DiscoveryStatus,
    RecommendedEngine,
    TorrentDiscoveryRequest,
    TorrentDiscoveryResult,
    TorrentFileItem,
    VideoCandidate,
)
from app.services.torrent_discovery.redaction import sanitize_tracker_url, sanitize_tracker_urls
from app.services.torrent_discovery.service import (
    TorrentDiscoveryService,
    default_torrent_discovery_service,
)
from app.services.torrent_discovery.torrent_parser import (
    parse_torrent_bytes,
    parse_torrent_file,
    InvalidTorrentFileError,
)

__all__ = [
    "TorrentDiscoveryService",
    "default_torrent_discovery_service",
    "TorrentDiscoveryRequest",
    "TorrentDiscoveryResult",
    "TorrentFileItem",
    "VideoCandidate",
    "DiscoveryMode",
    "DiscoveryStatus",
    "RecommendedEngine",
    "parse_magnet_uri",
    "is_magnet_uri",
    "InvalidMagnetUriError",
    "ParsedMagnet",
    "parse_torrent_file",
    "parse_torrent_bytes",
    "InvalidTorrentFileError",
    "bdecode",
    "bdecode_with_info_slice",
    "bencode",
    "BencodeError",
    "BencodeDecodeError",
    "BencodeDepthError",
    "BencodeSizeError",
    "sanitize_tracker_url",
    "sanitize_tracker_urls",
]
