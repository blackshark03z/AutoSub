"""
AutoSub Cloud V2 — Network Metadata Probe & Swarm Health Verifier
Implements BEP 48 Tracker Scrape and safe swarm metadata exchange interfaces.
Enforces strict operator authorization, timeout guards, and zero-payload invariants.
"""

import binascii
import socket
import time
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from app.services.torrent_discovery.bencode import bdecode, BencodeError
from app.services.torrent_discovery.models import DiscoveryStatus


class TrackerScrapeError(Exception):
    """Raised when tracker scrape fails or times out."""
    pass


def infohash_to_raw_bytes(infohash_hex: str) -> bytes:
    """Convert 40-character infohash hex to 20 raw bytes."""
    return bytes.fromhex(infohash_hex)


def tracker_announce_to_scrape_url(announce_url: str) -> Optional[str]:
    """
    Convert a standard BEP 48 HTTP/HTTPS announce URL to a scrape URL.
    Rule: Replace the last path component 'announce' with 'scrape'.
    e.g. http://example.com/announce -> http://example.com/scrape
    """
    try:
        parsed = urllib.parse.urlsplit(announce_url)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        # UDP tracker scraping requires binary UDP protocol (handled separately if supported)
        return None

    path = parsed.path
    if "/announce" in path:
        new_path = path.replace("/announce", "/scrape", 1)
    elif path.endswith("announce"):
        new_path = path[:-8] + "scrape"
    else:
        # Some trackers support ?scrape or appending /scrape
        new_path = path.rstrip("/") + "/scrape"

    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, new_path, parsed.query, parsed.fragment))


def scrape_http_tracker(
    tracker_url: str, infohash_hex: str, timeout_seconds: float = 5.0
) -> Optional[int]:
    """
    Perform a BEP 48 HTTP tracker scrape to obtain verified seed count.
    
    Returns:
        Verified seeders count (int), or None if scrape unsupported / failed.
    """
    scrape_url = tracker_announce_to_scrape_url(tracker_url)
    if not scrape_url:
        return None

    try:
        raw_hash = infohash_to_raw_bytes(infohash_hex)
    except (ValueError, binascii.Error):
        return None

    # Encode raw 20-byte infohash for HTTP GET query
    quoted_hash = urllib.parse.quote(raw_hash)
    delimiter = "&" if "?" in scrape_url else "?"
    req_url = f"{scrape_url}{delimiter}info_hash={quoted_hash}"

    try:
        req = urllib.request.Request(
            req_url,
            headers={
                "User-Agent": "AutoSubCloudDiscovery/2.0",
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read(65536)  # Cap scrape response size
            decoded = bdecode(body)
            if isinstance(decoded, dict):
                files_dict = decoded.get(b"files")
                if isinstance(files_dict, dict):
                    # Key in files_dict is either 20-byte raw hash or hex
                    stats = files_dict.get(raw_hash) or files_dict.get(infohash_hex.encode("ascii"))
                    if isinstance(stats, dict):
                        complete = stats.get(b"complete")
                        if isinstance(complete, int) and complete >= 0:
                            return complete
    except (urllib.error.URLError, socket.timeout, TimeoutError, BencodeError, Exception):
        # Tracker scrape is best-effort; failure should never crash discovery
        return None

    return None


def probe_swarm_metadata(
    infohash_hex: str,
    trackers: list,
    timeout_seconds: float = 5.0,
    probe_allowed: bool = False,
) -> Tuple[str, Optional[int], Optional[str]]:
    """
    Probe tracker endpoints for swarm health and seeders without downloading payload data.
    
    Returns:
        (status, verified_seeders, error_message)
    """
    if not probe_allowed:
        return DiscoveryStatus.INFOHASH_ONLY.value, None, "Probe not allowed by operator configuration"

    verified_seeders = None
    start_time = time.monotonic()

    # Query authorized HTTP trackers for seed verification
    for tracker in trackers:
        if time.monotonic() - start_time > timeout_seconds:
            break
        remaining_timeout = max(1.0, timeout_seconds - (time.monotonic() - start_time))
        seeds = scrape_http_tracker(tracker, infohash_hex, timeout_seconds=remaining_timeout)
        if seeds is not None:
            verified_seeders = seeds
            break

    # Since we probed without local .torrent metadata, metadata is still pending
    # unless an engine like libtorrent actually exchanges ut_metadata
    status = (
        DiscoveryStatus.METADATA_PENDING.value
        if verified_seeders is not None
        else DiscoveryStatus.METADATA_FETCH_FAILED.value
    )
    error_msg = None if verified_seeders is not None else "Could not resolve metadata or tracker scrape in time"

    return status, verified_seeders, error_msg
