# AutoSub Cloud V2 — Video Source Capability & Downloader Research Matrix
**Work Package:** WP-DL-001  
**Scope:** Video Source Discovery & Capability Analysis (Discovery Only — No Full Video Download)  
**Status:** Approved Research Baseline  
**Date:** 2026-10-16  

---

## 1. Executive Summary

This document evaluates five candidate media download/extraction engines and their capabilities across major Chinese video platforms (Bilibili, iQIYI, Youku, Tencent Video / WeTV, Douyin, Xigua) as well as open streaming formats (Direct MP4, HLS/M3U8, DASH/MPD).

A fundamental principle in this research: **documented support does NOT equate to universal stream retrieval**. Platforms employ multi-layered anti-crawling, VIP paywalls, geofencing, dynamic signature rotation, and Digital Rights Management (DRM) technologies. Therefore, discovery workers must clearly distinguish between:
1. **Documented Capability**: Mentioned in upstream documentation/README.
2. **Metadata Probe Success**: Successful extraction of title, formats, duration, and stream endpoints without full download.
3. **Full Download Verified**: End-to-end media segment retrieval and multiplexing (strictly out of scope for WP-DL-001, marked as PENDING/NOT TESTED until operator provides authorized testing environments).

---

## 2. Downloader Tool Evaluation Matrix

| Downloader | Upstream Repository | Implementation Language | License | Primary Architecture | Metadata Probe Mechanism | Maintenance Status & Risks | Linux / Docker Headless Suitability |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **yt-dlp** | [github.com/yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | Python 3 (CLI + Library) | Unlicense | Extractor-based plugin architecture with custom stream parsers | `yt-dlp --dump-single-json --skip-download <URL>` or Python API `YoutubeDL({'skip_download': True})` | **Active / High**: Nightly releases, large community, robust plugin ecosystem. Rare breakage quickly patched. | **Excellent**: Pure Python, zero GUI dependencies, small footprint, official Docker images available. |
| **Lux** | [github.com/iawia002/lux](https://github.com/iawia002/lux) | Go (CLI) | MIT | Standalone Go binary with native platform extractors (formerly `annie`) | `lux -i <URL>` (text info) or `lux -j <URL>` (JSON info) | **Moderate / Stale**: Infrequent commits (~monthly/quarterly). Parser updates lag behind Chinese platform API revisions. | **High**: Single static binary, no runtime dependencies, ideal for minimalist containers. |
| **N_m3u8DL-RE** | [github.com/nilaoda/N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE) | C# (.NET AOT / Cross-platform) | MIT | Specialized manifest-based stream parser and segment muxer | `N_m3u8DL-RE <manifest_url> --skip-download` or `--write-meta-json` | **Active**: Specialized in HLS/DASH/MSS. Requires raw manifest URLs (does not parse platform HTML pages). | **High**: Native AOT binaries for Linux x64/ARM64. No full .NET SDK required at runtime. |
| **You-Get** | [github.com/soimort/you-get](https://github.com/soimort/you-get) | Python 3 (CLI + Library) | MIT | Extractor-based scraper | `you-get -i <URL>` (metadata info) or `--json` | **Critical Risk / Deprecated**: Effectively unmaintained (last significant release in 2020-2022). Fails on modern Wbi signatures and anti-bot challenges. | **Fair**: Python-based, but broken extractors cause silent or runtime crashes. |
| **Streamlink** | [github.com/streamlink/streamlink](https://github.com/streamlink/streamlink) | Python 3 (CLI + Library) | Simplified BSD | Plugin-based live stream pipe router | `streamlink --json <URL>` | **Active**: Excellent for live streaming (Huya, Douyu, Bilibili Live), but poor/unsupported for VOD movie/drama catalogs. | **Good**: Headless compatible, but out of scope for VOD drama pipelines. |

---

## 3. Platform & Format Capability Matrix

The table below contrasts **Documented Capability**, **Metadata Probing Capability**, **Authentication Requirements**, **HLS/DASH Support**, and **DRM Limitations**.

| Target Platform / Format | Primary URL Patterns | Documented Downloader Support | Metadata Probe Success Mode | Authentication Requirements | HLS / DASH Protocol Support | DRM & Encryption Limitations | Operational Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Bilibili** (`bilibili.com`) | `bilibili.com/video/BV...`<br>`bilibili.com/bangumi/play/ep...` | yt-dlp, Lux, You-Get | **yt-dlp**: High (JSON metadata returned with `--skip-download`)<br>**Lux**: High (`-i` / `-j`) | Public UGC: None for 360p-1080p.<br>1080p+ / 4K / Bangumi: Requires `SESSDATA` / `bili_jct` cookies or VIP. | DASH (`avc1`, `hev1`, `av01`) + FLV legacy | Free UGC: No DRM.<br>Official Bangumi/Movies: May enforce Widevine CAS or proprietary app-only tokens. | **Recommended Engine: yt-dlp**.<br>Metadata discoverable; auth required for HD VIP. |
| **iQIYI / iq.com** | `iqiyi.com/v_...`<br>`iq.com/play/...` | yt-dlp, Lux | **yt-dlp**: Moderate (frequently throttled or signature changed)<br>**Lux**: Partial | Free teasers: None.<br>Full drama/film: Strict VIP authentication required. | DASH / HLS (Dynamic M3U8 chunking) | **High DRM Barrier**: Domestic iQIYI uses ChinaDRM / custom segment encryption. International iq.com uses Widevine Modular DRM. Free downloads impossible for DRM streams. | **Recommended Engine: yt-dlp**.<br>Discovery only; expect `DRM_SUSPECTED` or `AUTH_REQUIRED` on VIP movies. |
| **Youku** (`youku.com`) | `v.youku.com/v_show/id_...` | yt-dlp, Lux, You-Get | **yt-dlp**: Low to Moderate (subject to CKey updates)<br>**Lux**: Low | Free trailers: None.<br>Full episodes: Youku VIP account cookies. | HLS / M3U8 with dynamic tokenized queries | **High DRM Barrier**: Proprietary Alibaba DRM, CKey token rotation, Widevine on OTT/web clients. Free accounts get 6-minute previews. | **Recommended Engine: yt-dlp (Fallback: Lux)**.<br>Discovery will flag `AUTH_REQUIRED` or `DRM_SUSPECTED`. |
| **Tencent Video / WeTV** (`v.qq.com`, `wetv.vip`) | `v.qq.com/x/cover/...`<br>`wetv.vip/play/...` | yt-dlp, Lux | **yt-dlp**: Moderate (WeTV international cleaner than domestic QQ)<br>**Lux**: Low | Domestic VIP: QQ/WeChat SSO cookies.<br>WeTV: VIP tokens. | HLS and DASH multi-bitrate streams | **High DRM Barrier**: Tencent CKey v8/v9/v10, Widevine DRM, dynamic watermark insertion. Free tier restricted to 480p/720p with ads or previews. | **Recommended Engine: yt-dlp**.<br>Metadata extraction possible; full streams gatekept by VIP/DRM. |
| **Douyin** (`douyin.com`) | `douyin.com/video/...`<br>`v.douyin.com/...` | yt-dlp, Lux | **yt-dlp**: High (redirect resolution + user-agent emulation)<br>**Lux**: High | None for public reels/videos. Cookie needed for user profile bulk scraping. | Direct MP4 / HLS streams | **No DRM**. Watermark-free 1080p streams available via API parsing. | **Recommended Engine: yt-dlp / DirectFile**.<br>Highly discoverable. |
| **Xigua Video** (`ixigua.com`) | `ixigua.com/...` | yt-dlp, Lux | **yt-dlp**: High<br>**Lux**: Moderate | None for free public videos. VIP for full theatrical releases. | DASH / HLS | Free content: Unencrypted.<br>Licensed cinema: ChinaDRM / Widevine. | **Recommended Engine: yt-dlp**.<br>Good discovery profile. |
| **Direct MP4 / WebM** | `https://*/*.mp4`<br>`https://*/*.webm` | Native HTTP, yt-dlp, curl | **Native HTTP Probe**: High (HTTP HEAD / Range request `bytes=0-1024`) | HTTP Basic / Bearer token if private bucket. | Progressive HTTP download | Plain media: No DRM.<br>May contain ISO-CENC (`sinf` atom / `encv` sample entry). | **Recommended Engine: DirectFileDiscoveryAdapter**.<br>Zero overhead, fast probe. |
| **HLS / M3U8** | `https://*/*.m3u8` | N_m3u8DL-RE, yt-dlp | **Manifest Probe**: High (Text GET with byte budget limit) | Query token / expiring signed URLs. | Apple HLS (RFC 8216) | Look for `#EXT-X-KEY`: unencrypted vs AES-128 vs SAMPLE-AES vs `KEYFORMAT="com.apple.streamingkeydelivery"`. | **Recommended Engine: ManifestDiscoveryAdapter**.<br>Fast, parses variant streams & encryption tags. |
| **DASH / MPD** | `https://*/*.mpd` | N_m3u8DL-RE, yt-dlp | **Manifest Probe**: High (XML GET with byte budget limit) | Query token / expiring signed URLs. | MPEG-DASH (ISO/IEC 23009-1) | Look for `<ContentProtection schemeIdUri="...">`. Widevine (`edef8ba9...`), PlayReady (`9a04f079...`), or CENC PSSH. | **Recommended Engine: ManifestDiscoveryAdapter**.<br>Flags `DRM_SUSPECTED` immediately on PSSH. |

---

## 4. In-Depth DRM & Licensing Constraints

### 4.1 Digital Rights Management (DRM) Realities
1. **Widevine Modular / ChinaDRM**:
   - Modern premium streaming platforms (iQIYI, Tencent, Youku) protect theatrical films and licensed TV series with hardware or software DRM.
   - Decryption requires cryptographic licenses issued by a License Server (KMS / Widevine DRM server) tied to authorized client keys (CDM).
   - **Absolute Boundary for AutoSub**: AutoSub and its Discovery Worker **do not bypass, crack, or circumvent DRM**.
   - When an encrypted manifest or PSSH box is discovered, the worker must truthfully report `status: "DRM_SUSPECTED"` and `drm_status: "DRM_SUSPECTED"`.
2. **HLS `#EXT-X-KEY` vs DASH `ContentProtection`**:
   - `#EXT-X-KEY:METHOD=NONE`: Unencrypted (clear stream).
   - `#EXT-X-KEY:METHOD=AES-128,URI="..."`: Standard HLS envelope encryption. Key is retrievable over HTTPS if authorized by session cookie. Not considered hardware DRM, but requires authorization.
   - `#EXT-X-KEY:METHOD=SAMPLE-AES,KEYFORMAT="com.apple.streamingkeydelivery"`: Apple FairPlay DRM. Inaccessible without CDM.
   - DASH `<ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed">`: Google Widevine. Inaccessible without CDM.

### 4.2 Legal, Ethical, and Platform Terms Compliance
- Discovery workers probe publicly accessible or operator-authorized media descriptors.
- Probing uses metadata flags (`--skip-download`, `HTTP HEAD`, manifest text parse) and never pulls video segment payloads.
- Workers strictly enforce an **Authorized Access Confirmation** gate (`authorized_access_confirmed: true`). If an operator has not certified legitimate access or explicit rights to probe, the worker restricts execution.
- Sensitive authentication materials (cookies, session tokens) must never be dumped into logs, stdout, or JSON error messages.

---

## 5. Linux / Docker Headless Deployment Strategy

| Criterion | yt-dlp | Lux | N_m3u8DL-RE |
| :--- | :--- | :--- | :--- |
| **Runtime Dependencies** | Python 3.10+, ffmpeg (optional for probe) | None (Single Go binary) | .NET AOT binary (glibc / musl compatible) |
| **Container Size Impact** | ~50 MB (Python package + wheels) | ~25 MB (Single static executable) | ~35 MB (Single self-contained binary) |
| **Subprocess Execution** | Direct Python module API or CLI subprocess | Subprocess CLI (`lux -j`) | Subprocess CLI (`N_m3u8DL-RE`) |
| **Concurrency / Worker Model** | ThreadPoolExecutor or asyncio process pool | Subprocess pool | Subprocess pool |
| **Anti-Bot / JS Evaluator** | Optional external JS engine (Node.js/Deno/QuickJS) for YouTube n-sig; not required for basic Chinese metadata | Native Go HTTP client | Native .NET HTTP client |

### Recommended Multi-Engine Architecture:
1. **Primary General Worker**: `yt-dlp` (via native Python API and isolated subprocess) provides the richest metadata, format tree, subtitles, and extractor repertoire.
2. **Direct Manifest Parser**: Custom lightweight Python `ManifestDiscoveryAdapter` for `.m3u8` and `.mpd` URLs — zero external binary dependency, instant SSRF-guarded HTTP fetch, native regex/XML parsing for DRM tags.
3. **Direct File Prober**: Custom lightweight Python `DirectFileDiscoveryAdapter` for direct `.mp4`, `.mov`, `.webm` — sends HTTP HEAD / Range 0-1024, parses Content-Type and container atoms.
4. **Secondary Fallback**: `LuxDiscoveryAdapter` can be invoked if the `lux` binary is installed on the host system and `yt-dlp` fails or is unsupported.

---

## 6. Verification Status Summary

| Platform / Source | Documented Status | Metadata Probe Status | Full Download Status |
| :--- | :--- | :--- | :--- |
| Bilibili Free UGC | VERIFIED | VERIFIED | PENDING (Live operator test) |
| Bilibili Bangumi / VIP | VERIFIED | AUTH_REQUIRED | PENDING (Requires VIP cookies) |
| iQIYI Free Preview | VERIFIED | VERIFIED | PENDING |
| iQIYI VIP Drama / Movie | VERIFIED | DRM_SUSPECTED / AUTH_REQUIRED | PENDING (DRM gate) |
| Youku Free Preview | VERIFIED | VERIFIED | PENDING |
| Youku VIP / CKey | VERIFIED | AUTH_REQUIRED / PROBE_ERROR | PENDING |
| Tencent Video / WeTV Free | VERIFIED | VERIFIED | PENDING |
| Tencent VIP / DRM | VERIFIED | DRM_SUSPECTED / AUTH_REQUIRED | PENDING |
| Douyin Public Video | VERIFIED | VERIFIED | PENDING |
| Xigua Public Video | VERIFIED | VERIFIED | PENDING |
| Direct MP4 Link | VERIFIED | VERIFIED | PENDING |
| HLS M3U8 (Clear) | VERIFIED | VERIFIED | PENDING |
| HLS M3U8 (SAMPLE-AES) | VERIFIED | DRM_SUSPECTED | NOT SUPPORTED (DRM boundary) |
| DASH MPD (Clear) | VERIFIED | VERIFIED | PENDING |
| DASH MPD (Widevine PSSH)| VERIFIED | DRM_SUSPECTED | NOT SUPPORTED (DRM boundary) |

*Note: All "Full Download" items remain marked PENDING or NOT SUPPORTED pursuant to WP-DL-001 scope (Discovery Only).*
