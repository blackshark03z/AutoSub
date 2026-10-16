"""
AutoSub Cloud V2 — Torrent Discovery Service Orchestrator
Coordinates builtin and pluggable discovery adapters, ensuring strict schema compliance and security boundaries.
"""

from pathlib import Path
from typing import Dict, Optional, Union

from app.services.torrent_discovery.adapters.base import BaseTorrentDiscoveryAdapter
from app.services.torrent_discovery.adapters.builtin import BuiltinTorrentDiscoveryAdapter
from app.services.torrent_discovery.adapters.libtorrent_adapter import LibtorrentDiscoveryAdapter
from app.services.torrent_discovery.models import (
    DiscoveryMode,
    DiscoveryStatus,
    TorrentDiscoveryRequest,
    TorrentDiscoveryResult,
)


class TorrentDiscoveryService:
    """
    Main entry point for Torrent Discovery in AutoSub Cloud V2.
    """

    def __init__(self):
        self._adapters: Dict[str, BaseTorrentDiscoveryAdapter] = {
            "builtin": BuiltinTorrentDiscoveryAdapter(),
            "libtorrent": LibtorrentDiscoveryAdapter(),
        }

    def register_adapter(self, adapter: BaseTorrentDiscoveryAdapter) -> None:
        """Register a custom discovery adapter."""
        self._adapters[adapter.name] = adapter

    def get_adapter(self, preferred_engine: Optional[str] = None) -> BaseTorrentDiscoveryAdapter:
        """Get the preferred adapter if available, falling back to 'builtin'."""
        if preferred_engine and preferred_engine in self._adapters:
            candidate = self._adapters[preferred_engine]
            if candidate.is_available():
                return candidate

        return self._adapters["builtin"]

    def discover(self, request: TorrentDiscoveryRequest) -> TorrentDiscoveryResult:
        """
        Execute source discovery for BitTorrent sources.
        """
        adapter = self.get_adapter(request.preferred_engine)
        return adapter.discover(request)

    def discover_magnet(
        self,
        magnet_uri: str,
        mode: Union[str, DiscoveryMode] = DiscoveryMode.OFFLINE,
        timeout: float = 10.0,
        probe_allowed: bool = False,
    ) -> TorrentDiscoveryResult:
        """Convenience method to discover a Magnet URI."""
        disc_mode = DiscoveryMode(mode) if isinstance(mode, str) else mode
        req = TorrentDiscoveryRequest(
            source=magnet_uri,
            mode=disc_mode,
            timeout_seconds=timeout,
            probe_allowed=probe_allowed,
        )
        return self.discover(req)

    def discover_file(self, file_path: Union[str, Path]) -> TorrentDiscoveryResult:
        """Convenience method to discover a local .torrent file."""
        req = TorrentDiscoveryRequest(
            source=str(file_path),
            mode=DiscoveryMode.OFFLINE,
            probe_allowed=False,
        )
        return self.discover(req)

    def discover_bytes(
        self, raw_bytes: bytes, source_name: Optional[str] = None
    ) -> TorrentDiscoveryResult:
        """Convenience method to discover in-memory .torrent bytes."""
        req = TorrentDiscoveryRequest(
            source=source_name or "memory.torrent",
            raw_torrent_bytes=raw_bytes,
            mode=DiscoveryMode.OFFLINE,
            probe_allowed=False,
        )
        return self.discover(req)


# Default global service singleton
default_torrent_discovery_service = TorrentDiscoveryService()
