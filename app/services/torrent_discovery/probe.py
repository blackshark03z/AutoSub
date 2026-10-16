"""
AutoSub Cloud V2 — Network Metadata Probe & Swarm Health Verifier
Implements BEP 48 Tracker Scrape and safe swarm metadata exchange interfaces.
Enforces strict operator authorization, timeout guards, SSRF defenses, and zero-payload invariants.
"""

import binascii
import socket
import time
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from app.services.torrent_discovery.bencode import bdecode, BencodeError
from app.services.torrent_discovery.models import DiscoveryStatus
from app.services.torrent_discovery.redaction import (
    is_private_tracker_url,
    sanitize_error_message,
    sanitize_tracker_url,
)
from app.services.torrent_discovery.ssrf import (
    validate_tracker_url,
    create_safe_tracker_opener,
    SSRFBlockedError,
)


class TrackerScrapeError(Exception):
    """Raised when tracker scrape fails or times out."""
    pass


def infohash_to_raw_bytes(infohash_hex: str) -> bytes:
    """Convert infohash hex (40-char v1 or 64-char v2) to raw bytes."""
    clean_hex = infohash_hex.strip()
    return bytes.fromhex(clean_hex)


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

    if parsed.scheme.lower() not in ("http", "https"):
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
    tracker_url: str,
    infohash_hex: str,
    timeout_seconds: float = 5.0,
    allow_private_tracker_probe: bool = False,
) -> Tuple[Optional[int], str, Optional[str]]:
    """
    Perform a BEP 48 HTTP tracker scrape to obtain verified seed count with:
    - DNS rebinding prevention via pinned IP socket connection
    - SSRF defense against private IPs, loopback, and cloud metadata
    - Private tracker credential protection (disabled by default)
    
    Returns:
        (seeders_count, provenance, error_message)
    """
    scrape_url = tracker_announce_to_scrape_url(tracker_url)
    if not scrape_url:
        return None, "unverified", "Unsupported tracker URL scheme or format"

    # Private Tracker Credential Check (P1-01)
    if is_private_tracker_url(tracker_url) and not allow_private_tracker_probe:
        return (
            None,
            "unverified",
            "Private tracker probe is disabled by default to protect user passkey and ratio; credential safely redacted.",
        )

    # SSRF & DNS Rebinding Pre-validation
    try:
        opener, validated_ip = create_safe_tracker_opener(scrape_url, timeout_seconds=timeout_seconds)
    except SSRFBlockedError as ssrf_err:
        return None, "unverified", sanitize_error_message(f"SSRF blocked: {ssrf_err}")
    except Exception as exc:
        return None, "unverified", sanitize_error_message(f"Tracker URL validation failed: {exc}")

    try:
        raw_hash = infohash_to_raw_bytes(infohash_hex)
    except (ValueError, binascii.Error):
        return None, "unverified", "Invalid infohash format"

    # Encode raw infohash for HTTP GET query (20-byte for v1, 32-byte or truncated 20-byte for v2)
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

        with opener.open(req, timeout=timeout_seconds) as resp:
            body = resp.read(65536)  # Cap scrape response size
            decoded = bdecode(body)
            if isinstance(decoded, dict):
                files_dict = decoded.get(b"files")
                if isinstance(files_dict, dict):
                    # Key in files_dict could be raw 20-byte, raw 32-byte (v2), truncated 20-byte (v2), or hex
                    stats = (
                        files_dict.get(raw_hash)
                        or files_dict.get(raw_hash[:20])
                        or files_dict.get(infohash_hex.encode("ascii"))
                        or files_dict.get(infohash_hex[:40].encode("ascii"))
                    )
                    if isinstance(stats, dict):
                        complete = stats.get(b"complete")
                        if isinstance(complete, int) and complete >= 0:
                            return complete, "live_tracker_scrape", None
    except SSRFBlockedError as ssrf_err:
        return None, "unverified", sanitize_error_message(f"SSRF blocked during redirect: {ssrf_err}")
    except (urllib.error.URLError, socket.timeout, TimeoutError, BencodeError, Exception) as exc:
        # Tracker scrape is best-effort; failure should never crash discovery
        return None, "unverified", sanitize_error_message(str(exc))

    return None, "unverified", "No seed stats found in tracker scrape response"


def probe_swarm_metadata(
    infohash_hex: str,
    trackers: list,
    timeout_seconds: float = 5.0,
    probe_allowed: bool = False,
    allow_private_tracker_probe: bool = False,
    raw_trackers: Optional[list] = None,
) -> Tuple[str, Optional[int], str, Optional[str]]:
    """
    Probe tracker endpoints for swarm health and seeders without downloading payload data.
    
    Returns:
        (status, verified_seeders, seeders_provenance, error_message)
    """
    if not probe_allowed:
        return (
            DiscoveryStatus.INFOHASH_ONLY.value,
            None,
            "unverified",
            "Probe not allowed by operator configuration",
        )

    verified_seeders = None
    seeders_provenance = "unverified"
    last_err: Optional[str] = None
    start_time = time.monotonic()

    # Use raw trackers in memory if available and authorized; otherwise redacted trackers
    active_trackers = (raw_trackers if (raw_trackers and allow_private_tracker_probe) else trackers) or []

    for tracker in active_trackers:
        if time.monotonic() - start_time > timeout_seconds:
            break
        remaining_timeout = max(1.0, timeout_seconds - (time.monotonic() - start_time))
        seeds, prov, err = scrape_http_tracker(
            tracker,
            infohash_hex,
            timeout_seconds=remaining_timeout,
            allow_private_tracker_probe=allow_private_tracker_probe,
        )
        if seeds is not None:
            verified_seeders = seeds
            seeders_provenance = prov
            last_err = None
            break
        else:
            if err:
                last_err = err

    # Since we probed without local .torrent metadata, metadata is still pending
    # unless an engine like libtorrent actually exchanges ut_metadata
    status = (
        DiscoveryStatus.METADATA_PENDING.value
        if verified_seeders is not None
        else DiscoveryStatus.METADATA_FETCH_FAILED.value
    )
    error_msg = (
        None
        if verified_seeders is not None
        else (last_err or "Could not resolve metadata or tracker scrape in time")
    )

    return status, verified_seeders, seeders_provenance, error_msg
