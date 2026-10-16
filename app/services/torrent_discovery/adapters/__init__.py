"""
AutoSub Cloud V2 — Torrent Discovery Adapters
"""

from app.services.torrent_discovery.adapters.base import BaseTorrentDiscoveryAdapter
from app.services.torrent_discovery.adapters.builtin import BuiltinTorrentDiscoveryAdapter
from app.services.torrent_discovery.adapters.libtorrent_adapter import LibtorrentDiscoveryAdapter

__all__ = [
    "BaseTorrentDiscoveryAdapter",
    "BuiltinTorrentDiscoveryAdapter",
    "LibtorrentDiscoveryAdapter",
]
