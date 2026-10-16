# AutoSub Cloud V2 — Torrent Discovery Capability & Architecture Matrix (WP-DL-001B)

**Date:** 2026-10-16  
**Status:** Completed  
**Author:** AutoSub Cloud Team (Gemini B)  
**Deliverable:** D1 — Research Matrix  
**Work Package:** WP-DL-001B (`feat/torrent-source-discovery`)

---

## 1. Executive Overview & Problem Statement

AutoSub Cloud V2 aims to support pre-download discovery for media sources before allocating expensive transcode, transcription, OCR, or GPU resources. In BitTorrent ecosystems, sources are provided either as:
1. **Magnet URIs** (`magnet:?xt=urn:btih:...` or `urn:btmh:...`): Contain cryptographic hash identity and optional tracker endpoints, but **no file inventory, piece layout, or guaranteed size** until swarm metadata exchange (BEP 9 / BEP 52) occurs.
2. **`.torrent` Files** (Bencoded dictionaries): Contain the complete metadata payload (file inventory, file sizes, piece hashes, tracker announces) locally encoded.

The objective of **Torrent Discovery** is strictly bounded:
- Validate input identity (infohash v1/v2, announce endpoints, schema).
- Extract file inventory, video candidates, sizes, and multi-episode structures without downloading any payload media or seeding data.
- Determine whether metadata is immediately available (offline parse of `.torrent` or complete magnet) or pending remote swarm resolution.
- Provide a non-blocking, safe, CPU-friendly discovery service for headless Linux and local Windows execution.

---

## 2. Technology Research & Evaluation Matrix

We evaluated five reference projects and architectures from the BitTorrent and automated media management ecosystem:
1. **[arvidn/libtorrent](https://github.com/arvidn/libtorrent)** — The C++ BitTorrent library standard with Python bindings (`libtorrent-rasterbar`).
2. **[qbittorrent/qBittorrent](https://github.com/qbittorrent/qBittorrent)** — C++/Qt BitTorrent client with `qbittorrent-nox` headless WebUI daemon.
3. **[autobrr/autobrr](https://github.com/autobrr/autobrr)** — Go-based IRC announce filter and Torznab torrent grabber.
4. **[Sonarr/Sonarr](https://github.com/Sonarr/Sonarr)** — C#/.NET media management PVR with scene episode parser.
5. **[Prowlarr/Prowlarr](https://github.com/Prowlarr/Prowlarr)** — C#/.NET indexer aggregation proxy.

### 2.1 Detailed Comparison Matrix

| Capability / Metric | `libtorrent` (v2.0.x / 1.2.x) | `qBittorrent-nox` (WebUI) | `autobrr` | `Sonarr` | `Prowlarr` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Primary Language & Runtime** | C++17 / Python bindings | C++17 / Qt6 Core & Network | Go (statically compiled) | C# (.NET 8 runtime) | C# (.NET 8 runtime) |
| **Read `.torrent` Files** | Native full bencode parser (BEP 3, 52) | Via libtorrent backend | Native Go bencode parser | Via BencodeNET parser | Reads `.torrent` via HTTP proxy |
| **Read Magnet URIs** | Full parsing (BEP 9, BEP 53) | Via libtorrent backend | Parses regex/query params | Parses URI for infohash/tr | Parses URI from indexer feeds |
| **Swarm Metadata Probe (BEP 9)** | **Native** (downloads metadata without payload) | Yes (via daemon pause trick) | No (relies on external clients) | No (delegates to download client) | No (indexer metadata only) |
| **Infohash v1 (SHA-1 / 20B)** | Supported (hex & base32) | Supported | Supported | Supported | Supported |
| **Infohash v2 (SHA-256 / 32B)**| Supported (libtorrent 2.0+) | Supported (v4.3+) | Partial (depends on parser) | Not authoritative for swarm | Torznab infoHash attribute |
| **File Inventory Extraction** | Native `file_storage` API | Exposes `/api/v2/torrents/files` | File list from `.torrent` only | Parses file names for episodes | Parses Torznab enclosure XML |
| **Seed / Peer Detection** | Native DHT (BEP 5), Scrape (BEP 48), PEX | Web API `/api/v2/torrents/info` | Tracker scrape if supported | Torznab XML `seeders` attribute | Torznab XML `seeders` attribute |
| **Headless Linux Support** | Excellent (headless library) | Excellent (`qbittorrent-nox`) | Excellent (single daemon) | Excellent (systemd / docker) | Excellent (systemd / docker) |
| **Docker Compatibility** | Needs Boost/OpenSSL packages | Moderate (Qt runtime dependencies) | Exceptional (<25MB scratch/alpine) | Heavy (~150MB base dotnet) | Heavy (~150MB base dotnet) |
| **Idle Memory Footprint** | ~10 – 30 MB (session overhead) | ~60 – 120 MB | ~12 – 25 MB | ~150 – 300 MB | ~150 – 250 MB |
| **CPU Cost per Discovery Probe** | <0.5% CPU (transient async) | <2% CPU (daemon poll) | <0.1% CPU (local parse) | N/A (not a swarm probe) | N/A (HTTP scrape only) |
| **Security Risks & Attack Surface** | C++ memory safety, bencode recursion | WebUI auth bypass, CSRF | Minimal (Go memory safety) | Deserialization, large footprint | XML external entity, credentials |
| **License Compatibility** | **BSD-3-Clause** (Permissive) | **GPLv2+ / GPLv3** (Viral) | **GPLv3** (Viral) | **GPLv3** (Viral) | **GPLv3** (Viral) |

---

## 3. Deep Architectural Analysis of Candidates

### 3.1 `libtorrent` (arvidn/libtorrent)
- **Strengths:** 
  - Absolute standard for BitTorrent protocol compliance.
  - Supports BEP 9 (`ut_metadata`) which allows connecting to peers, exchanging metadata dictionaries, and terminating the connection **before downloading a single block of payload media**.
  - Permissive BSD-3-Clause license allows embedding into any proprietary or open cloud service without license contamination.
  - Native support for BitTorrent v2 (BEP 52) merkle trees and hybrid torrents.
- **Weaknesses:**
  - C++ native binary dependency. Precompiled Python wheels (`python-libtorrent` / `libtorrent`) are not universally pre-packaged across all Python versions and platforms without compiling Boost.
  - Starting a full libtorrent session for a simple offline `.torrent` inspect adds unnecessary process and network initialization overhead.

### 3.2 `qbittorrent-nox`
- **Strengths:** Rich REST Web API, battle-tested client.
- **Weaknesses:** 
  - Heavyweight for a discovery service. It is designed as a persistent download manager, not a lightweight discovery microservice.
  - Requires running a separate daemon, managing session tokens, pausing torrents immediately upon addition to prevent accidental media download, and cleaning up state.
  - GPL license complicates commercial packaging.

### 3.3 `autobrr`
- **Strengths:** Blazing fast, clean Go codebase, minimal memory footprint.
- **Weaknesses:** Does not participate in the BitTorrent swarm. It cannot resolve metadata for a naked magnet URI (`magnet:?xt=urn:btih:...`) that lacks a pre-existing `.torrent` file.

### 3.4 `Sonarr` & `Prowlarr`
- **Strengths:**
  - Sonarr possesses industry-leading scene release regex patterns (`S01E01`, `1080p`, multi-episode ranges, release groups) for classifying video inventory.
  - Prowlarr handles tracker authentication and proxying.
- **Weaknesses:**
  - Neither performs direct BitTorrent wire protocol communication or swarm metadata resolution.
  - Both require heavy .NET runtimes and are PVR orchestration systems, not discovery adapters.

---

## 4. Synthesis & Architectural Strategy for AutoSub Cloud V2

To meet AutoSub's requirements (**zero bloat, headless Linux CPU, Windows compatibility, zero external binary hard dependency, strict isolation, and absolute media non-download**), we adopt a **Dual-Engine Hybrid Adapter Architecture**:

```
+-------------------------------------------------------------------------+
|                  AutoSub Torrent Discovery Adapter                      |
|                                                                         |
|  [Input: Magnet URI or .torrent bytes]                                  |
|         |                                                               |
|         v                                                               |
|  +--------------------+       Offline Mode                              |
|  | Input Inspector    | ------------------------+                       |
|  +--------------------+                         |                       |
|         |                                       v                       |
|         |                     +---------------------------------------+ |
|         |                     | Builtin Pure-Python Engine            | |
|         |                     | - Safe Bencode Parser (BEP 3, BEP 52) | |
|         |                     | - Magnet RFC Parser (v1 / v2)         | |
|         |                     | - Secret & Passkey Redaction          | |
|         |                     | - Video Inventory & Episode Heuristics| |
|         |                     | - DoS & Depth Limits                  | |
|         |                     +---------------------------------------+ |
|         |                                       |                       |
|         | Network Probe (Operator Authorized)   |                       |
|         v                                       v                       |
|  +--------------------------------------------------------------------+ |
|  | Network Probe Subsystem                                            | |
|  | - Tracker Scrape (BEP 48 HTTP/UDP Scrape - Seeds/Leechers verify)  | |
|  | - Pluggable Libtorrent Adapter (BEP 9 metadata-only fetch)         | |
|  | - Strict Socket Timeouts & Fail-Safe Handling                      | |
|  +--------------------------------------------------------------------+ |
|                                 |                                       |
|                                 v                                       |
|  +--------------------------------------------------------------------+ |
|  | Normalized Discovery Result Schema (D4 Contract)                   | |
|  +--------------------------------------------------------------------+ |
+-------------------------------------------------------------------------+
```

### Key Engineering Decisions:

1. **Zero-Hard-Dependency Core:**
   A native pure-Python Bencode decoder and Magnet URI parser provides 100% testability, instant startup, zero binary compilation, and complete cross-platform fidelity on both Windows and Linux CPU.
2. **Safe Bencode Parser Invariants:**
   - Maximum recursion depth limit (protects against stack overflow DoS).
   - Maximum file size boundary (default 10 MB limit for `.torrent` metadata to prevent memory exhaustion).
   - Non-standard/malformed bencode rejection without uncaught exceptions.
3. **Strict Non-Download & Non-Seeding Guarantee:**
   - Offline mode never opens any network socket.
   - Network probe mode is restricted to:
     - Tracker Scrape (BEP 48) to query swarm health without connecting to peers.
     - BEP 9 metadata exchange without requesting piece data.
     - Never seeds, never creates media storage files.
4. **Credential & Secret Redaction:**
   - Private tracker announce URLs frequently contain user passkeys (e.g. `?passkey=xxxx`, `/announce/xxxx`, `?auth=xxxx`).
   - The redaction engine sanitizes all URLs before logging or returning discovery results.
5. **Video & Episode Classification:**
   - Detects all common video formats (`.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.flv`, `.ts`, `.wmv`).
   - Parses season/episode markers (`S01E02`, `EP03`, `[04]`) to classify whether the torrent is a single movie, a full season pack, or multi-episode bundle.
6. **Pluggable Engine Recommendation:**
   - The discovery result outputs `"recommended_engine": "libtorrent"` for downstream download worker dispatch, since `libtorrent` is the optimal engine for actual swarm transfer when authorized.
