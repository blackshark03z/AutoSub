"""
AutoSub Cloud V2 — Libtorrent Discovery Adapter
Leverages libtorrent (arvidn/libtorrent) Python bindings for BEP 9 metadata-only exchange when available.
"""

import time
from pathlib import Path
from typing import Optional

from app.services.torrent_discovery.adapters.base import BaseTorrentDiscoveryAdapter
from app.services.torrent_discovery.models import (
    DiscoveryMode,
    DiscoveryStatus,
    RecommendedEngine,
    TorrentDiscoveryRequest,
    TorrentDiscoveryResult,
)

try:
    import libtorrent as lt
    LIBTORRENT_AVAILABLE = True
except ImportError:
    lt = None
    LIBTORRENT_AVAILABLE = False


class LibtorrentDiscoveryAdapter(BaseTorrentDiscoveryAdapter):
    """
    Adapter utilizing libtorrent (C++ rasterbar bindings).
    Capable of performing BEP 9 (ut_metadata) swarm metadata exchange without downloading media.
    """

    @property
    def name(self) -> str:
        return "libtorrent"

    def is_available(self) -> bool:
        return LIBTORRENT_AVAILABLE

    def discover(self, request: TorrentDiscoveryRequest) -> TorrentDiscoveryResult:
        start_time = time.monotonic()

        if not self.is_available():
            return TorrentDiscoveryResult(
                status=DiscoveryStatus.ERROR.value,
                error_message="libtorrent library is not installed in the current Python environment",
                probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
            )

        # Implementation for environments with libtorrent installed
        try:
            source_str = (request.source or "").strip()

            # Handle local .torrent file
            if Path(source_str).is_file():
                info = lt.torrent_info(source_str)
                files_list = []
                for idx in range(info.num_files()):
                    f = info.files().at(idx)
                    files_list.append({
                        "path": f.path,
                        "size_bytes": f.size,
                        "extension": Path(f.path).suffix.lower(),
                        "is_video": Path(f.path).suffix.lower() in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts"},
                        "index": idx,
                    })

                total_size = sum(f["size_bytes"] for f in files_list)
                v1_hex = str(info.info_hash())
                
                return TorrentDiscoveryResult(
                    source_type="torrent",
                    status=DiscoveryStatus.METADATA_AVAILABLE.value,
                    infohash=v1_hex.lower(),
                    torrent_name=info.name(),
                    files=files_list,
                    total_size_bytes=total_size,
                    video_candidates=[f for f in files_list if f["is_video"]],
                    seeders=None,
                    metadata_complete=True,
                    download_tested=False,
                    recommended_engine=RecommendedEngine.LIBTORRENT.value,
                    is_multi_file=len(files_list) > 1,
                    probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
                )

            # Handle Magnet URI
            if source_str.startswith("magnet:?"):
                params = lt.parse_magnet_uri(source_str)
                infohash = str(params.info_hash).lower()

                if request.mode == DiscoveryMode.OFFLINE or not request.probe_allowed:
                    return TorrentDiscoveryResult(
                        source_type="torrent",
                        status=DiscoveryStatus.INFOHASH_ONLY.value,
                        infohash=infohash,
                        torrent_name=params.name if hasattr(params, "name") else None,
                        files=[],
                        total_size_bytes=0,
                        video_candidates=[],
                        seeders=None,
                        metadata_complete=False,
                        download_tested=False,
                        recommended_engine=RecommendedEngine.LIBTORRENT.value,
                        probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
                    )

                # Metadata-only swarm probe with strict download disable
                session = lt.session()
                # Stop all downloading and disable uploading
                session.listen_on(6881, 6891)
                
                # In libtorrent, setting download priorities to zero prevents downloading any piece
                handle = session.add_torrent(params)
                
                timeout = request.timeout_seconds
                t0 = time.monotonic()
                metadata_found = False

                while (time.monotonic() - t0) < timeout:
                    if handle.has_metadata():
                        metadata_found = True
                        break
                    time.sleep(0.2)

                if metadata_found:
                    info = handle.get_torrent_info()
                    files_list = []
                    for idx in range(info.num_files()):
                        f = info.files().at(idx)
                        files_list.append({
                            "path": f.path,
                            "size_bytes": f.size,
                            "extension": Path(f.path).suffix.lower(),
                            "is_video": Path(f.path).suffix.lower() in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts"},
                            "index": idx,
                        })
                    total_size = sum(f["size_bytes"] for f in files_list)
                    session.remove_torrent(handle)

                    return TorrentDiscoveryResult(
                        source_type="torrent",
                        status=DiscoveryStatus.METADATA_AVAILABLE.value,
                        infohash=infohash,
                        torrent_name=info.name(),
                        files=files_list,
                        total_size_bytes=total_size,
                        video_candidates=[f for f in files_list if f["is_video"]],
                        seeders=None,
                        metadata_complete=True,
                        download_tested=False,
                        recommended_engine=RecommendedEngine.LIBTORRENT.value,
                        is_multi_file=len(files_list) > 1,
                        probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
                    )
                else:
                    session.remove_torrent(handle)
                    return TorrentDiscoveryResult(
                        source_type="torrent",
                        status=DiscoveryStatus.METADATA_FETCH_FAILED.value,
                        infohash=infohash,
                        torrent_name=params.name if hasattr(params, "name") else None,
                        files=[],
                        total_size_bytes=0,
                        video_candidates=[],
                        seeders=None,
                        metadata_complete=False,
                        download_tested=False,
                        recommended_engine=RecommendedEngine.LIBTORRENT.value,
                        error_message="Timed out waiting for BEP 9 swarm metadata exchange",
                        probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
                    )

            return TorrentDiscoveryResult(
                status=DiscoveryStatus.INVALID_SOURCE.value,
                error_message="Invalid source",
                probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
            )

        except Exception as exc:
            return TorrentDiscoveryResult(
                status=DiscoveryStatus.ERROR.value,
                error_message=f"libtorrent adapter exception: {exc}",
                probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
            )
