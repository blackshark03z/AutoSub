"""
AutoSub Cloud V2 — Built-in Pure Python Torrent Discovery Adapter
Requires zero external C/C++ libraries. Works universally on headless Linux CPU and Windows.
"""

import time
from pathlib import Path
from typing import Optional

from app.services.torrent_discovery.adapters.base import BaseTorrentDiscoveryAdapter
from app.services.torrent_discovery.bencode import BencodeError, BencodeSizeError
from app.services.torrent_discovery.magnet import (
    is_magnet_uri,
    parse_magnet_uri,
    InvalidMagnetUriError,
)
from app.services.torrent_discovery.models import (
    DiscoveryMode,
    DiscoveryStatus,
    RecommendedEngine,
    TorrentDiscoveryRequest,
    TorrentDiscoveryResult,
)
from app.services.torrent_discovery.probe import probe_swarm_metadata
from app.services.torrent_discovery.torrent_parser import (
    parse_torrent_bytes,
    parse_torrent_file,
    InvalidTorrentFileError,
)


class BuiltinTorrentDiscoveryAdapter(BaseTorrentDiscoveryAdapter):
    """
    Standard built-in pure-Python discovery adapter.
    Handles offline .torrent parsing, Magnet URI inspection, secret redaction,
    video candidate classification, and optional authorized BEP 48 tracker scrapes.
    """

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True

    def discover(self, request: TorrentDiscoveryRequest) -> TorrentDiscoveryResult:
        start_time = time.monotonic()

        try:
            # 1. Handle in-memory raw torrent bytes
            if request.raw_torrent_bytes:
                res, _, _ = parse_torrent_bytes(request.raw_torrent_bytes, source_name="memory.torrent")
                res.probe_duration_ms = (time.monotonic() - start_time) * 1000.0
                return res

            source_str = (request.source or "").strip()
            if not source_str:
                return TorrentDiscoveryResult(
                    status=DiscoveryStatus.INVALID_SOURCE.value,
                    error_message="Empty source provided",
                    probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
                )

            # 2. Handle Magnet URIs
            if is_magnet_uri(source_str):
                parsed = parse_magnet_uri(source_str)

                # Offline mode or probe not allowed
                if request.mode == DiscoveryMode.OFFLINE or not request.probe_allowed:
                    duration_ms = (time.monotonic() - start_time) * 1000.0
                    return TorrentDiscoveryResult(
                        source_type="torrent",
                        status=DiscoveryStatus.INFOHASH_ONLY.value,
                        infohash=parsed.infohash,
                        infohash_v1=parsed.infohash_v1,
                        infohash_v2=parsed.infohash_v2,
                        torrent_name=parsed.display_name,
                        files=[],
                        total_size_bytes=parsed.exact_length or 0,
                        video_candidates=[],
                        seeders=None,
                        metadata_complete=False,
                        download_tested=False,
                        recommended_engine=RecommendedEngine.LIBTORRENT.value,
                        is_multi_file=False,
                        is_multi_episode=False,
                        trackers=parsed.trackers,
                        probe_duration_ms=duration_ms,
                    )

                # Network probe authorized by operator
                status, verified_seeders, err = probe_swarm_metadata(
                    infohash_hex=parsed.infohash,
                    trackers=parsed.trackers,
                    timeout_seconds=request.timeout_seconds,
                    probe_allowed=request.probe_allowed,
                )
                duration_ms = (time.monotonic() - start_time) * 1000.0

                return TorrentDiscoveryResult(
                    source_type="torrent",
                    status=status,
                    infohash=parsed.infohash,
                    infohash_v1=parsed.infohash_v1,
                    infohash_v2=parsed.infohash_v2,
                    torrent_name=parsed.display_name,
                    files=[],
                    total_size_bytes=parsed.exact_length or 0,
                    video_candidates=[],
                    seeders=verified_seeders,
                    metadata_complete=False,
                    download_tested=False,
                    recommended_engine=RecommendedEngine.LIBTORRENT.value,
                    is_multi_file=False,
                    is_multi_episode=False,
                    trackers=parsed.trackers,
                    error_message=err,
                    probe_duration_ms=duration_ms,
                )

            # 3. Handle local .torrent file
            source_path = Path(source_str)
            if source_path.is_file():
                res, _, _ = parse_torrent_file(source_path)
                res.probe_duration_ms = (time.monotonic() - start_time) * 1000.0
                return res

            # 4. Unknown/Unsupported source
            return TorrentDiscoveryResult(
                status=DiscoveryStatus.INVALID_SOURCE.value,
                error_message=f"Source is neither an existing file nor a magnet URI: {source_str[:60]}",
                probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
            )

        except (InvalidMagnetUriError, InvalidTorrentFileError, BencodeSizeError) as exc:
            return TorrentDiscoveryResult(
                status=DiscoveryStatus.INVALID_SOURCE.value,
                error_message=str(exc),
                probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
            )
        except BencodeError as exc:
            return TorrentDiscoveryResult(
                status=DiscoveryStatus.INVALID_SOURCE.value,
                error_message=f"Bencode error: {exc}",
                probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
            )
        except Exception as exc:
            return TorrentDiscoveryResult(
                status=DiscoveryStatus.ERROR.value,
                error_message=f"Unexpected error: {exc}",
                probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
            )
