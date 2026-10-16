# AutoSub Cloud V2 — Unified Source Discovery Integration Contract
## Merging HTTP Discovery (WP-DL-001) & Torrent Discovery (WP-DL-001B)

**Date:** 2026-10-16  
**Status:** Proposed Standard  
**Authors:** AutoSub Cloud Team (Gemini A & Gemini B)  
**Deliverable:** D4 — Integration Contract  
**Work Package:** WP-DL-001B (`feat/torrent-source-discovery`)

---

## 1. Executive Context & Objective

AutoSub Cloud V2 requires a unified pre-download discovery layer capable of inspecting any external video source before allocating expensive cloud compute (GPU inference, PaddleOCR, Whisper ASR, Kokoro TTS).

Two independent work packages have established the discovery foundation:
- **WP-DL-001:** HTTP direct video, HLS/DASH manifest probing, yt-dlp site extractors, and Lux engine adapters.
- **WP-DL-001B:** BitTorrent Magnet URI parsing, `.torrent` bencode file analysis, v1/v2 infohash calculation, passkey sanitization, and swarm health probes.

This document defines the **Unified Source Discovery Contract** (`UnifiedSourceDiscoveryResult`), specifying the common schema, router dispatch protocol, and downstream staging hand-off.

---

## 2. Router Dispatch Protocol

The intake orchestrator inspects the source URI and routes to the appropriate discovery adapter:

```
                                    Incoming Source URI
                                             |
                   +-------------------------+-------------------------+
                   |                                                   |
           BitTorrent Source                                      HTTP / Web URL
                   |                                                   |
        +----------+----------+                         +--------------+--------------+
        |                     |                         |              |              |
    Magnet URI         .torrent File               Manifest URL    Direct Container  Web Video URL
  (magnet:?xt=...)     (*.torrent)               (.m3u8 / .mpd)    (.mp4 / .mkv)    (bilibili, etc)
        |                     |                         |              |              |
        v                     v                         v              v              v
   +-------------------------------+               +--------------------------------------+
   | TorrentDiscoveryService       |               | HTTP Discovery Worker (WP-DL-001)    |
   | (WP-DL-001B)                  |               | - Manifest / Direct HTTP / yt-dlp    |
   +-------------------------------+               +--------------------------------------+
                   \                                                   /
                    \                                                 /
                     v                                               v
               +-----------------------------------------------------------+
               |        UnifiedSourceDiscoveryResult Normalizer            |
               +-----------------------------------------------------------+
                                             |
                                             v
                           Next Stage: Downloader Staging (R2)
```

### 2.1 Sniffing Rules

1. **BitTorrent Scheme:** If `uri.startswith("magnet:?")`, route immediately to `TorrentDiscoveryService`.
2. **BitTorrent File:** If `uri.endswith(".torrent")` or raw input has bencoded dictionary with `b"info"`, route to `TorrentDiscoveryService`.
3. **HTTP/HTTPS Manifests:** If URL contains `.m3u8` or `.mpd`, route to `ManifestAdapter`.
4. **HTTP/HTTPS Direct Video:** If HEAD request returns video MIME type or `.mp4`/`.mkv`/`.mov`/`.webm` extension, route to `DirectHttpAdapter`.
5. **Web Video Platforms:** For video sites (e.g. YouTube, Bilibili, Douyin), route to `YtdlpAdapter` or `LuxAdapter`.

---

## 3. Unified Discovery Schema (`UnifiedSourceDiscoveryResult`)

All discovery adapters normalize their output into the canonical schema below:

```json
{
  "source_uri": "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=Sample",
  "source_protocol": "torrent",
  "specific_adapter": "builtin_torrent",
  "status": "INFOHASH_ONLY",
  "title": "Sample",
  "total_size_bytes": 0,
  "is_size_authoritative": false,
  "media_items": [],
  "video_candidates": [],
  "is_multi_item": false,
  "is_multi_episode": false,
  "metadata_complete": false,
  "download_tested": false,
  "recommended_downloader": "libtorrent",
  "probe_duration_ms": 3.42,
  "protocol_telemetry": {
    "infohash": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
    "infohash_v1": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
    "infohash_v2": null,
    "seeders": null,
    "trackers": [
      "http://tracker.example.com/announce?passkey=[REDACTED]"
    ],
    "piece_length": null,
    "num_pieces": null
  },
  "error": null
}
```

### 3.1 HTTP Source Discovery Example in the Unified Schema

```json
{
  "source_uri": "https://cdn.example.com/videos/master.m3u8",
  "source_protocol": "http_manifest",
  "specific_adapter": "manifest_hls",
  "status": "METADATA_AVAILABLE",
  "title": "master",
  "total_size_bytes": 145023900,
  "is_size_authoritative": true,
  "media_items": [
    {
      "path": "stream_1080p.ts",
      "size_bytes": 145023900,
      "extension": ".ts",
      "is_video": true,
      "index": 0
    }
  ],
  "video_candidates": [
    {
      "path": "stream_1080p.ts",
      "size_bytes": 145023900,
      "extension": ".ts",
      "file_index": 0
    }
  ],
  "is_multi_item": false,
  "is_multi_episode": false,
  "metadata_complete": true,
  "download_tested": false,
  "recommended_downloader": "ffmpeg",
  "probe_duration_ms": 112.5,
  "protocol_telemetry": {
    "manifest_type": "HLS",
    "variants_count": 3,
    "selected_bandwidth": 5000000,
    "codecs": "avc1.640028,mp4a.40.2"
  },
  "error": null
}
```

---

## 4. Normalization Field Mappings

| Field | Torrent Discovery (WP-DL-001B) | HTTP Discovery (WP-DL-001) |
| :--- | :--- | :--- |
| `source_protocol` | `"torrent"` | `"http_direct"`, `"http_manifest"`, `"web_extractor"` |
| `status` | `METADATA_AVAILABLE` / `INFOHASH_ONLY` | `AVAILABLE` / `PROBE_FAILED` |
| `title` | `torrent_name` (from `name` or `dn`) | Extracted title or filename |
| `total_size_bytes` | Sum of all files or `length` | `Content-Length` or manifest estimate |
| `is_size_authoritative` | `true` if .torrent, `false` if magnet | `true` if `Content-Length` given |
| `media_items` | `files` inventory | Extracted files / segments |
| `video_candidates` | Filtered video files with episode info | Identified video stream(s) |
| `is_multi_episode` | `is_multi_episode` | Multi-chapter or series list |
| `metadata_complete` | `metadata_complete` | True if stream geometry known |
| `download_tested` | Always `false` | Always `false` |
| `recommended_downloader`| `libtorrent` | `aria2c` / `ffmpeg` / `yt-dlp` |
| `protocol_telemetry` | `infohash`, `trackers`, `seeders`, pieces | HTTP headers, TLS, manifests, SSRF checks |

---

## 5. Security, SSRF & Isolation Contracts (Hardened in WP-DL-001B.2)

1. **Libtorrent Metadata-Only Safety (P0-01):**
   - Libtorrent network metadata probe mode is strictly **disabled by default** (`network_probe_enabled=False`, `allow_libtorrent_network=False`) returning `PROBE_NOT_ALLOWED`.
   - No arbitrary listen ports are opened (`session.listen_on` is avoided/eliminated).
   - Media piece download/upload is completely disabled (`upload_mode`, `default_dont_download`, `stop_when_ready`).
   - Session and torrent handle lifecycle cleanup is strictly enforced in `try ... finally` blocks across all execution outcomes (success, timeout, exception).
   - `download_tested=False` is documented as an operational indicator (download was not invoked), not a cryptographic proof of zero-download.

2. **Tracker SSRF Hardening & DNS Rebinding TOCTOU Defense (P0-02):**
   - Eliminates DNS rebinding TOCTOU vulnerability by pinning the pre-validated IP and connecting TCP sockets directly to the pinned IP (`PinnedIPHTTPConnection`, `PinnedIPHTTPSConnection`).
   - For HTTPS, preserves SNI and TLS certificate hostname validation against the original hostname while connecting to the pinned IP.
   - Blocks abnormal IP representations and encodings:
     - Decimal integer IPs (e.g. `2130706433` -> `127.0.0.1`)
     - Hexadecimal IPs (e.g. `0x7f000001` -> `127.0.0.1`)
     - IPv4-mapped IPv6 addresses (`::ffff:127.0.0.1`, `::ffff:169.254.169.254`)
     - 6to4 prefix (`2002::/16`) embedding private or loopback addresses
     - Teredo prefix (`2001:0000::/32`) embedding private addresses
     - Userinfo credentials embedded in URL (`user:pass@host`)
     - Dangerous service ports (21, 22, 23, 25, 445, 2375, 6379, 10250, 11211, 27017, etc.)
   - Multi-hop redirect protection via `SafeRedirectHandler`: enforces maximum 3 hops, re-validates each destination URL against SSRF, and updates pinned IP mappings.

3. **Private Tracker Credential Handling (P1-01):**
   - In-memory operational raw URLs (`_raw_trackers`) are strictly segregated from redacted output URLs (`trackers`).
   - `_raw_trackers` is marked `repr=False` and excluded from serialization (`to_dict()`), logs, and string representations.
   - Private tracker probes are **blocked by default** (`allow_private_tracker_probe=False`) to protect user passkeys and share ratios.
   - All exception and log messages pass through `sanitize_error_message()` to scrub passkeys, tokens, and raw URLs.

4. **Torrent Protocol Identity Correctness (P1-02):**
   - Distinguishes `v1`, `v2`, and `hybrid` torrent formats per BEP 3 and BEP 52.
   - **Critical invariant:** v2-only torrents strictly report `infohash_v1 = None` and `torrent_format = "v2"`. An invented SHA-1 hash calculated over the v2 info dict is NEVER assigned as a v1 infohash.
   - Hybrid torrents report both valid `infohash_v1` (SHA-1) and `infohash_v2` (SHA-256).
   - BEP 48 tracker scraping uses protocol-appropriate hashes (32-byte or truncated 20-byte for v2; 20-byte for v1).

5. **Truthful Seeders Provenance Tracking:**
   - Telemetry tracks `seeders_provenance`:
     - `"unverified"`: default state when offline or unprobed.
     - `"live_tracker_scrape"`: verified against a live, reachable network tracker.
     - `"simulated_contract_test"`: simulated test fixture during unit/integration tests.
   - Prohibits claiming mock/simulated results as live swarm observations.

6. **Operator Authorization Gate:**
   - Neither adapter may perform active swarm probes or unauthenticated outbound scans unless `probe_allowed=True` is explicitly passed by the caller.

7. **Payload Download Prohibition:**
   - In Discovery Mode, neither adapter may download actual video content, segment files, or participate in BitTorrent seeding.

8. **Denial of Service (DoS) Defense:**
   - Torrent: 10 MB payload cap, 32-level bencode recursion limit, 50 MB string safety cap.
   - HTTP: 1 MB response cap on manifest headers, SSRF private IP rejection.

---

## 6. Implementation Transition Plan

Once both WP-DL-001 and WP-DL-001B are independently merged into `main`:
1. Create `app/services/discovery/unified.py`.
2. Wrap `TorrentDiscoveryService` and `HttpDiscoveryWorker` behind `UnifiedDiscoveryRouter`.
3. Provide single entrypoint:
   ```python
   router = UnifiedDiscoveryRouter()
   result: UnifiedSourceDiscoveryResult = router.discover(source_uri)
   ```
4. Downstream download staging service consumes `result.recommended_downloader` and `result.video_candidates` to initiate authorized media transfer to Cloud R2 staging.
