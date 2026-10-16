# AutoSub Cloud V2 — Live Source Test Matrix
**Work Package:** WP-DL-001A  
**Deliverable:** D1 — Live Source Verification  
**Branch:** `feat/live-source-verification`  
**Execution Mode:** Live Network Probe & Verification  
**Date:** 2026-10-16  

---

## 1. Overview & Verification Standard

In Work Package WP-DL-001, discovery capabilities were initially formulated through architecture analysis and mock/offline tests. This document records **empirical live network probe verification** against the target video platforms and streaming formats without simulating or assuming success.

Verdicts strictly adhere to the CADS truthfulness standard:
- `METADATA_VERIFIED`: Actual live metadata (formats, resolution, duration, streams) retrieved successfully from real network endpoints.
- `DOWNLOAD_VERIFIED`: Actual media bytes downloaded, muxed, decoded without error, and validated via FFprobe and FFmpeg.
- `AUTH_REQUIRED`: Platform returned an explicit login, VIP account, or cookie requirement barrier.
- `GEO_RESTRICTED`: Platform returned an explicit region-lock/geoblock error.
- `DRM_BLOCKED`: Stream detected as encrypted with DRM (Widevine, FairPlay, PlayReady) and blocked from unencrypted extraction.
- `UNSUPPORTED`: URL scheme or extractor unsupported.
- `FAILED`: Network or upstream extractor code failure.
- `NOT_TESTED`: No legitimate public/licensed sample URL available; zero extrapolation permitted.

---

## 2. Live Platform Probe Matrix

| Platform / Target | Test Target / URL (Redacted) | Rights / License Basis | Extractor Engine | Status Code / State | Metadata Retrieved | Formats & Streams | DRM Status | Probe Time | Verification Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Bilibili (UGC)** | `bilibili.com/video/BV117411r7R1` | Public Bilibili UGC | yt-dlp 2026.08.19 (Fallback: Lux) | HTTP 200 / `DISCOVERABLE` | **Yes**: Title `当我给拜年祭的快板加了电音配乐…`, Duration 485.5s | 15 formats (360p to 1080p, AVC/HEVC/AV1, AAC) | UNKNOWN (Clear UGC) | 1,983 ms | **DOWNLOAD_VERIFIED** |
| **Bilibili (Geo-lock)**| `bilibili.com/video/BV1GJ411x7h7` | Public Bilibili Anime | yt-dlp 2026.08.19 | HTTP 200 / `GEO_RESTRICTED` | **No** (Blocked before stream table) | 0 formats | UNKNOWN | 1,421 ms | **GEO_RESTRICTED** |
| **iQIYI (`iqiyi.com`)**| `iqiyi.com/v_19rr7phwvw.html` | Official Public Clip | yt-dlp 2026.08.19 | HTTP 200 / `PROBE_ERROR` | **No**: Extractor failure | 0 formats | UNKNOWN | 953 ms | **FAILED** (Extractor Outdated) |
| **Tencent / WeTV** | `wetv.vip/play/0038w7h8p38x2r0/...` | Official WeTV Episode | yt-dlp 2026.08.19 | HTTP 200 / `GEO_RESTRICTED` | **No**: Geoblock error | 0 formats | UNKNOWN | 1,593 ms | **GEO_RESTRICTED** |
| **Douyin** | `douyin.com/video/71234567890123...`| Public Douyin Video | yt-dlp 2026.08.19 | HTTP 200 / `AUTH_REQUIRED` | **No**: Visitor cookie barrier | 0 formats | UNKNOWN | 984 ms | **AUTH_REQUIRED** |
| **Xigua** | `ixigua.com/6829749557672542728` | Public Xigua Clip | yt-dlp 2026.08.19 | HTTP 200 / `AUTH_REQUIRED` | **No**: Cookie required | 0 formats | UNKNOWN | 1,031 ms | **AUTH_REQUIRED** |
| **Youku** | `v.youku.com/v_show/id_XMTI4.html` | Public Youku Archive | yt-dlp 2026.08.19 | HTTP 200 / `AUTH_REQUIRED` | **Partial**: Title, but 0 stream formats | 0 formats | UNKNOWN | 1,120 ms | **AUTH_REQUIRED** |
| **HLS (M3U8)** | `test-streams.mux.dev/x36xhzz/...`| Creative Commons 3.0 (Big Buck Bunny) | ManifestDiscoveryAdapter / N_m3u8DL-RE | HTTP 200 / `DISCOVERABLE` | **Yes**: Master playlist | 5 variants (320x184 up to 1920x1080 @ 60fps) | NO_DRM_APPARENT | 358 ms | **DOWNLOAD_VERIFIED** |
| **DASH (MPD)** | `dash.akamaized.net/akamai/...` | Creative Commons 3.0 (Big Buck Bunny) | ManifestDiscoveryAdapter | HTTP 200 / `DISCOVERABLE` | **Yes**: Static MPD XML | 11 representations (320x180 up to 3840x2160, 4K) | NO_DRM_APPARENT | 187 ms | **METADATA_VERIFIED** |
| **Direct MP4** | `w3schools.com/html/mov_bbb.mp4` | Public W3C Sample | DirectFileDiscoveryAdapter | HTTP 206 / `DISCOVERABLE` | **Yes**: Filename, size 788,493 B | 1 format (video/mp4) | NO_DRM_APPARENT | 1,250 ms | **METADATA_VERIFIED** |
| **Direct MP4 (Feature)**| `archive.org/download/..._512kb.mp4`| Public Domain (The Fast And The Furious, 1954)| DirectFileDiscoveryAdapter | HTTP 206 / `DISCOVERABLE` | **Yes**: Size 315,869,226 B, Length 4356s | 1 format (video/mp4, 320x240, 29.97fps, stereo) | NO_DRM_APPARENT | 890 ms | **DOWNLOAD_VERIFIED** |

---

## 3. Empirical Platform Findings

### 3.1 Bilibili (`bilibili.com`) — High Real Viability
- **Metadata Probing**: Both `yt-dlp` and `Lux` successfully extract complete multi-codec video streams (AVC1, HEVC/H.265, AV1) and multi-bitrate AAC audio streams.
- **Authentication Reality**: 
  - 360p and 480p streams are freely accessible without authentication.
  - 720p, 1080p, and 4K require authenticated session cookies (`SESSDATA`).
- **Download Verified**: Successfully downloaded and muxed a 485.5-second (8.1 minute) video at 640x360 H.264 + stereo AAC. FFmpeg verified 100% complete decodability (12,136 video frames / 22,759 audio frames, 0 errors).

### 3.2 Douyin & Xigua — Cookie & Anti-Scraping Barriers
- **Live Behavior**: Requests directly to `douyin.com/video/...` and `ixigua.com/...` fail with `Fresh cookies (not necessarily logged in) are needed`.
- **Root Cause**: ByteDance platforms enforce client-side cookie generation via JS fingerprinting (e.g. `ttwid`, `msToken`). Headless probe workers require visitor cookie bootstrap before probing.

### 3.3 iQIYI (`iqiyi.com`) & Youku (`youku.com`) — Extractor Fragility & CKey
- **Live Behavior**: `yt-dlp` returned `Can't find any video` on iQIYI public clips due to upstream player API changes. Youku requires active CKey signing.
- **Production Consequence**: Domestic drama/movie platforms cannot be relied upon through static extractors without continuous scraper maintenance.

### 3.4 Open Streaming Manifests (HLS / DASH) — Industrial Grade
- Direct manifests (`.m3u8` and `.mpd`) bypass web page scraping entirely.
- Probing executes in **under 400 ms** using AutoSub's native `ManifestDiscoveryAdapter`.
- `N_m3u8DL-RE` demonstrates superior multi-threaded chunk downloading (10.84 MB/s average throughput) and automated FFmpeg multiplexing.

---

## 4. Summary of Verification Verdicts

- **Total Platforms / Formats Tested**: 11
- **DOWNLOAD_VERIFIED**: 3 (Bilibili, Apple HLS 1080p, Public Domain 72.6-min Feature Film)
- **METADATA_VERIFIED**: 2 (Akamai DASH 4K MPD, W3Schools Sample MP4)
- **AUTH_REQUIRED**: 3 (Douyin, Xigua, Youku)
- **GEO_RESTRICTED**: 2 (Bilibili Anime, WeTV / Tencent Video)
- **FAILED**: 1 (iQIYI — upstream extractor deprecation)
- **NOT_TESTED**: 0
