"""
AutoSub Cloud V2 — Libtorrent Discovery Adapter
Leverages libtorrent (arvidn/libtorrent) Python bindings for BEP 9 metadata-only exchange when available.

SAFETY POLICY (P0-01):
- Reading local .torrent files via libtorrent.torrent_info is strictly offline and performs no network I/O.
- Libtorrent network metadata probe is DISABLED by default (network_probe_enabled = False)
  to prevent unverified swarm joining, unexpected socket allocation, or disk writes across heterogeneous bindings.
- 'download_tested=False' denotes that download was not invoked; zero-download is enforced by omitting network socket addition.
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
    TorrentFormat,
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
    Capable of reading .torrent files offline and (if qualified sandbox exists) BEP 9 metadata probes.
    """

    def __init__(self, network_probe_enabled: bool = False):
        # Network probe is disabled by default until verified in a qualified sandbox
        self.network_probe_enabled = network_probe_enabled

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

        try:
            source_str = (request.source or "").strip()

            # 1. Handle local .torrent file (STRICTLY OFFLINE, no network session created)
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
                
                # Check v1 and v2 identity
                v1_hex = str(info.info_hash()).lower()
                has_v2 = hasattr(info, "v2") and info.v2()
                v2_hex = str(info.info_hash_v2()).lower() if hasattr(info, "info_hash_v2") else None

                if hasattr(info, "is_hybrid") and info.is_hybrid():
                    torrent_format = TorrentFormat.HYBRID.value
                    primary_hash = v1_hex
                elif hasattr(info, "v2_only") and info.v2_only():
                    torrent_format = TorrentFormat.V2.value
                    primary_hash = v2_hex or v1_hex
                    v1_hex = None  # v2-only has no v1 hash
                else:
                    torrent_format = TorrentFormat.V1.value
                    primary_hash = v1_hex
                
                return TorrentDiscoveryResult(
                    source_type="torrent",
                    status=DiscoveryStatus.METADATA_AVAILABLE.value,
                    infohash=primary_hash,
                    infohash_v1=v1_hex,
                    infohash_v2=v2_hex,
                    torrent_format=torrent_format,
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

            # 2. Handle Magnet URI
            if source_str.startswith("magnet:?"):
                params = lt.parse_magnet_uri(source_str)
                infohash = str(params.info_hash).lower()

                # Offline mode or probe not authorized by operator
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

                # Safety Check (P0-01): Network mode is disabled by default
                allow_network = self.network_probe_enabled or getattr(request, "allow_libtorrent_network", False)
                if not allow_network:
                    return TorrentDiscoveryResult(
                        source_type="torrent",
                        status=DiscoveryStatus.PROBE_NOT_ALLOWED.value,
                        infohash=infohash,
                        torrent_name=params.name if hasattr(params, "name") else None,
                        files=[],
                        total_size_bytes=0,
                        video_candidates=[],
                        seeders=None,
                        metadata_complete=False,
                        download_tested=False,
                        recommended_engine=RecommendedEngine.LIBTORRENT.value,
                        error_message=(
                            "Libtorrent network metadata probe is disabled by default for safety pending "
                            "qualified zero-payload sandbox certification."
                        ),
                        probe_duration_ms=(time.monotonic() - start_time) * 1000.0,
                    )

                # Qualified Network Probe with Guaranteed Cleanup & Safety Flags
                session = None
                handle = None
                try:
                    session = lt.session()
                    
                    # Safety: set upload_mode and pause flags if available in binding
                    if hasattr(lt, "torrent_flags"):
                        if hasattr(lt.torrent_flags, "upload_mode"):
                            params.flags |= lt.torrent_flags.upload_mode
                        if hasattr(lt.torrent_flags, "stop_when_ready"):
                            params.flags |= lt.torrent_flags.stop_when_ready
                        if hasattr(lt.torrent_flags, "default_dont_download"):
                            params.flags |= lt.torrent_flags.default_dont_download

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
                finally:
                    # Strict Session Cleanup (P0-01)
                    if session is not None and handle is not None:
                        try:
                            session.remove_torrent(handle)
                        except Exception:
                            pass
                    if session is not None:
                        del session

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
