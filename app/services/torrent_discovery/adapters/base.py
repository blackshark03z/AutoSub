"""
AutoSub Cloud V2 — Torrent Discovery Adapter Base Interface (WP-DL-001B)
"""

from abc import ABC, abstractmethod
from app.services.torrent_discovery.models import TorrentDiscoveryRequest, TorrentDiscoveryResult


class BaseTorrentDiscoveryAdapter(ABC):
    """Abstract contract for Torrent Discovery Adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter unique name."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the adapter runtime and dependencies are available on the current host."""
        pass

    @abstractmethod
    def discover(self, request: TorrentDiscoveryRequest) -> TorrentDiscoveryResult:
        """
        Execute discovery for a Magnet URI or .torrent input.
        Must NOT download any payload media or initiate torrent seeding.
        """
        pass
