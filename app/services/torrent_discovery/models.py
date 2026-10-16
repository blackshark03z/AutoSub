"""
AutoSub Cloud V2 — Torrent Discovery Data Models (WP-DL-001B)
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Dict, Any


class DiscoveryStatus(str, Enum):
    """Discovery execution status states."""
    METADATA_AVAILABLE = "METADATA_AVAILABLE"
    INFOHASH_ONLY = "INFOHASH_ONLY"
    METADATA_PENDING = "METADATA_PENDING"
    METADATA_FETCH_FAILED = "METADATA_FETCH_FAILED"
    INVALID_SOURCE = "INVALID_SOURCE"
    PROBE_NOT_ALLOWED = "PROBE_NOT_ALLOWED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class DiscoveryMode(str, Enum):
    """Discovery execution mode."""
    OFFLINE = "offline"
    NETWORK_PROBE = "network_probe"


class RecommendedEngine(str, Enum):
    """Recommended download engine for downstream staging."""
    LIBTORRENT = "libtorrent"
    QBITTORRENT = "qbittorrent"
    BUILTIN = "builtin"


@dataclass
class TorrentFileItem:
    """Individual file metadata inside a torrent inventory."""
    path: str
    size_bytes: int
    extension: str
    is_video: bool
    index: int = 0
    pieces_root: Optional[str] = None  # BitTorrent v2 merkle root

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "extension": self.extension,
            "is_video": self.is_video,
            "index": self.index,
            **({"pieces_root": self.pieces_root} if self.pieces_root else {}),
        }


@dataclass
class VideoCandidate:
    """Classified video file candidate for localization."""
    path: str
    size_bytes: int
    extension: str
    file_index: int
    episode_hint: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "extension": self.extension,
            "file_index": self.file_index,
        }
        if self.episode_hint:
            data["episode_hint"] = self.episode_hint
        if self.season_number is not None:
            data["season_number"] = self.season_number
        if self.episode_number is not None:
            data["episode_number"] = self.episode_number
        return data


@dataclass
class TorrentDiscoveryRequest:
    """Input payload for Torrent Discovery."""
    source: str  # Magnet URI string or filesystem path to .torrent file
    raw_torrent_bytes: Optional[bytes] = None  # Optional in-memory .torrent bytes
    mode: DiscoveryMode = DiscoveryMode.OFFLINE
    timeout_seconds: float = 10.0
    probe_allowed: bool = False  # Explicit operator authorization for network probing
    preferred_engine: Optional[str] = None


@dataclass
class TorrentDiscoveryResult:
    """
    Standardized discovery result schema complying with WP-DL-001B contract.
    """
    source_type: str = "torrent"
    status: str = DiscoveryStatus.METADATA_AVAILABLE.value
    infohash: str = ""
    torrent_name: Optional[str] = None
    files: List[Dict[str, Any]] = field(default_factory=list)
    total_size_bytes: int = 0
    video_candidates: List[Dict[str, Any]] = field(default_factory=list)
    seeders: Optional[int] = None
    metadata_complete: bool = False
    download_tested: bool = False  # In discovery mode, NEVER test download payload
    recommended_engine: str = RecommendedEngine.LIBTORRENT.value

    # Extended telemetry fields
    infohash_v1: Optional[str] = None
    infohash_v2: Optional[str] = None
    is_multi_file: bool = False
    is_multi_episode: bool = False
    piece_length: Optional[int] = None
    num_pieces: Optional[int] = None
    trackers: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    probe_duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary matching contract."""
        data = {
            "source_type": self.source_type,
            "status": self.status,
            "infohash": self.infohash,
            "torrent_name": self.torrent_name,
            "files": self.files,
            "total_size_bytes": self.total_size_bytes,
            "video_candidates": self.video_candidates,
            "seeders": self.seeders,
            "metadata_complete": self.metadata_complete,
            "download_tested": self.download_tested,
            "recommended_engine": self.recommended_engine,
            # Extended metadata
            "is_multi_file": self.is_multi_file,
            "is_multi_episode": self.is_multi_episode,
            "trackers": self.trackers,
        }
        if self.infohash_v1:
            data["infohash_v1"] = self.infohash_v1
        if self.infohash_v2:
            data["infohash_v2"] = self.infohash_v2
        if self.piece_length is not None:
            data["piece_length"] = self.piece_length
        if self.num_pieces is not None:
            data["num_pieces"] = self.num_pieces
        if self.error_message:
            data["error_message"] = self.error_message
        if self.probe_duration_ms > 0:
            data["probe_duration_ms"] = round(self.probe_duration_ms, 2)
        return data
