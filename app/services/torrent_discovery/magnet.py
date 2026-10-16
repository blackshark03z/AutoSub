"""
AutoSub Cloud V2 — RFC/BEP-Compliant Magnet URI Parser
Supports BitTorrent v1 (urn:btih: 40-hex / 32-base32) and BitTorrent v2 (urn:btmh: multihash).
"""

import base64
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Optional

from app.services.torrent_discovery.redaction import sanitize_tracker_urls


from app.services.torrent_discovery.models import TorrentFormat


class InvalidMagnetUriError(ValueError):
    """Raised when a magnet URI is malformed or lacks a valid BitTorrent topic."""
    pass


@dataclass
class ParsedMagnet:
    """Parsed representation of a BitTorrent Magnet URI."""
    raw_uri: str
    infohash: str  # Canonical primary infohash (v1 hex 40-char, or v2 hex 64-char)
    torrent_format: str = TorrentFormat.UNKNOWN.value  # "v1" | "v2" | "hybrid"
    infohash_v1: Optional[str] = None  # 40-char lowercase hex
    infohash_v2: Optional[str] = None  # 64-char lowercase hex
    display_name: Optional[str] = None
    trackers: List[str] = field(default_factory=list)  # Redacted tracker URLs
    web_seeds: List[str] = field(default_factory=list)
    exact_length: Optional[int] = None
    _raw_trackers: List[str] = field(default_factory=list, repr=False)  # In-memory operational only

    @property
    def has_v1(self) -> bool:
        return self.infohash_v1 is not None

    @property
    def has_v2(self) -> bool:
        return self.infohash_v2 is not None

    @property
    def is_hybrid(self) -> bool:
        return bool(self.infohash_v1 and self.infohash_v2)


def is_magnet_uri(uri: str) -> bool:
    """Check if the string starts with the magnet URI scheme."""
    if not isinstance(uri, str):
        return False
    return uri.strip().startswith("magnet:?")


def parse_magnet_uri(uri: str) -> ParsedMagnet:
    """
    Parse a BitTorrent Magnet URI into a structured ParsedMagnet object.
    
    Handles:
        - BitTorrent v1 40-char hex: urn:btih:4a8c9b...
        - BitTorrent v1 32-char base32: urn:btih:JJMZU3X...
        - BitTorrent v2 multihash: urn:btmh:1220<64-hex>
        - Multiple trackers, display name, exact length, passkey redaction.
    """
    if not is_magnet_uri(uri):
        raise InvalidMagnetUriError(f"String does not start with 'magnet:?': {uri[:30]!r}")

    query_string = uri.strip()[len("magnet:?") :]
    params = urllib.parse.parse_qs(query_string, keep_blank_values=False)

    xt_values = params.get("xt", [])
    if not xt_values:
        raise InvalidMagnetUriError("Magnet URI missing required 'xt' (exact topic) parameter")

    infohash_v1: Optional[str] = None
    infohash_v2: Optional[str] = None

    for xt in xt_values:
        xt_lower = xt.lower()
        if xt_lower.startswith("urn:btih:"):
            hash_str = xt[len("urn:btih:") :].strip()
            # 40-character hex
            if len(hash_str) == 40 and re.match(r"^[0-9a-fA-F]{40}$", hash_str):
                infohash_v1 = hash_str.lower()
            # 32-character base32
            elif len(hash_str) == 32 and re.match(r"^[2-7a-zA-Z]{32}$", hash_str):
                try:
                    raw_bytes = base64.b32decode(hash_str.upper())
                    if len(raw_bytes) == 20:
                        infohash_v1 = raw_bytes.hex().lower()
                except Exception as exc:
                    raise InvalidMagnetUriError(f"Invalid base32 infohash: {hash_str}") from exc
            else:
                raise InvalidMagnetUriError(f"Invalid btih infohash format (must be 40-hex or 32-base32): {hash_str}")

        elif xt_lower.startswith("urn:btmh:"):
            # BitTorrent v2 multihash format:
            # urn:btmh:1220<64-hex> (0x12 = sha2-256, 0x20 = 32 bytes)
            multihash = xt[len("urn:btmh:") :].strip()
            if multihash.startswith("1220") and len(multihash) == 68:
                v2_hash = multihash[4:].lower()
                if re.match(r"^[0-9a-fA-F]{64}$", v2_hash):
                    infohash_v2 = v2_hash
            elif len(multihash) == 64 and re.match(r"^[0-9a-fA-F]{64}$", multihash):
                infohash_v2 = multihash.lower()
            else:
                raise InvalidMagnetUriError(f"Invalid btmh v2 multihash format: {multihash}")

    if not infohash_v1 and not infohash_v2:
        raise InvalidMagnetUriError(f"No valid BitTorrent infohash found in xt parameters: {xt_values}")

    if infohash_v1 and infohash_v2:
        torrent_format = TorrentFormat.HYBRID.value
    elif infohash_v2:
        torrent_format = TorrentFormat.V2.value
    else:
        torrent_format = TorrentFormat.V1.value

    primary_infohash = infohash_v1 if infohash_v1 else infohash_v2

    # Display name (dn)
    dn_values = params.get("dn", [])
    display_name = dn_values[0] if dn_values else None

    # Trackers (tr)
    raw_trackers = params.get("tr", [])
    sanitized_trackers = sanitize_tracker_urls(raw_trackers)

    # Web seeds (ws)
    web_seeds = params.get("ws", [])

    # Exact length (xl)
    exact_length = None
    xl_values = params.get("xl", [])
    if xl_values:
        try:
            exact_length = int(xl_values[0])
        except ValueError:
            exact_length = None

    return ParsedMagnet(
        raw_uri=uri,
        infohash=primary_infohash,
        torrent_format=torrent_format,
        infohash_v1=infohash_v1,
        infohash_v2=infohash_v2,
        display_name=display_name,
        trackers=sanitized_trackers,
        web_seeds=web_seeds,
        exact_length=exact_length,
        _raw_trackers=raw_trackers,
    )
