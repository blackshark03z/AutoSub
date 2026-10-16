from __future__ import annotations

from typing import Protocol
from app.services.discovery.models import DiscoveryRequest, DiscoveryResult


class DiscoveryAdapter(Protocol):
    adapter_name: str

    def can_handle(self, url: str, platform: str | None = None) -> bool:
        """Return True if this adapter can probe the given URL/platform."""
        ...

    def probe(self, request: DiscoveryRequest) -> DiscoveryResult:
        """Execute a metadata-only probe and return the DiscoveryResult.
        
        Must never download full video payloads.
        """
        ...
