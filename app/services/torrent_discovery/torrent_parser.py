"""
AutoSub Cloud V2 — .torrent File Parser (BEP 3, BEP 52 BitTorrent v2 & Hybrids)
Extracts file inventories, computes authoritative infohashes, and enforces memory boundaries.
"""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from app.services.torrent_discovery.bencode import (
    MAX_TORRENT_FILE_SIZE,
    bdecode_with_info_slice,
    bencode,
    BencodeError,
    BencodeSizeError,
)
from app.services.torrent_discovery.episode_detector import (
    analyze_video_inventory,
    get_file_extension,
    is_video_filename,
)
from app.services.torrent_discovery.models import (
    TorrentDiscoveryResult,
    TorrentFileItem,
    VideoCandidate,
    DiscoveryStatus,
    RecommendedEngine,
)
from app.services.torrent_discovery.redaction import sanitize_tracker_urls


class InvalidTorrentFileError(ValueError):
    """Raised when a .torrent file is invalid, corrupted, or missing required metadata."""
    pass


def _decode_bytes_str(val: Any) -> str:
    """Safely decode bytes or string to str."""
    if isinstance(val, str):
        return val
    if isinstance(val, (bytes, bytearray)):
        try:
            return val.decode("utf-8")
        except UnicodeDecodeError:
            return val.decode("latin1", errors="replace")
    return str(val)


def _parse_v2_file_tree(
    tree: Dict[bytes, Any], current_path: List[str], file_index_start: int
) -> Tuple[List[TorrentFileItem], int]:
    """
    Recursively parse BitTorrent v2 (BEP 52) 'file tree' dictionary structure.
    In BEP 52, files have a key b"" with metadata dict containing b"length" and b"pieces root".
    """
    files: List[TorrentFileItem] = []
    idx = file_index_start

    for raw_name, content in tree.items():
        if raw_name == b"":
            # Leaf file entry for the current path
            if isinstance(content, dict):
                length = content.get(b"length", 0)
                pieces_root = content.get(b"pieces root")
                root_hex = pieces_root.hex().lower() if isinstance(pieces_root, bytes) else None
                file_rel_path = "/".join(current_path)
                ext = get_file_extension(file_rel_path)
                item = TorrentFileItem(
                    path=file_rel_path,
                    size_bytes=int(length),
                    extension=ext,
                    is_video=is_video_filename(file_rel_path),
                    index=idx,
                    pieces_root=root_hex,
                )
                files.append(item)
                idx += 1
        elif isinstance(content, dict):
            # Directory entry
            name_str = _decode_bytes_str(raw_name)
            sub_files, idx = _parse_v2_file_tree(content, current_path + [name_str], idx)
            files.extend(sub_files)

    return files, idx


def parse_torrent_bytes(
    data: bytes, source_name: Optional[str] = None
) -> Tuple[TorrentDiscoveryResult, List[TorrentFileItem], List[VideoCandidate]]:
    """
    Parse raw .torrent file bytes into complete discovery metadata.
    """
    if len(data) > MAX_TORRENT_FILE_SIZE:
        raise BencodeSizeError(
            f"Torrent payload size ({len(data)} bytes) exceeds max limit of {MAX_TORRENT_FILE_SIZE} bytes"
        )

    try:
        decoded, raw_info = bdecode_with_info_slice(data)
    except BencodeError as exc:
        raise InvalidTorrentFileError(f"Corrupted or malformed bencode structure: {exc}") from exc

    if not isinstance(decoded, dict):
        raise InvalidTorrentFileError("Root bencoded object must be a dictionary")

    info = decoded.get(b"info")
    if not isinstance(info, dict) or raw_info is None:
        raise InvalidTorrentFileError("Missing or invalid 'info' dictionary in torrent file")

    # 1. Infohash calculation
    # Canonical BitTorrent v1 infohash = SHA1(bencoded info dict)
    sha1_hasher = hashlib.sha1()
    sha1_hasher.update(raw_info)
    infohash_v1 = sha1_hasher.hexdigest().lower()

    # Canonical BitTorrent v2 infohash = SHA256(bencoded info dict)
    meta_version = info.get(b"meta version", 1)
    has_v2_tree = b"file tree" in info
    infohash_v2 = None
    if meta_version == 2 or has_v2_tree:
        sha256_hasher = hashlib.sha256()
        sha256_hasher.update(raw_info)
        infohash_v2 = sha256_hasher.hexdigest().lower()

    primary_infohash = infohash_v1 if infohash_v1 else (infohash_v2 or "")

    # 2. Torrent name
    raw_torrent_name = info.get(b"name.utf-8") or info.get(b"name")
    torrent_name = _decode_bytes_str(raw_torrent_name) if raw_torrent_name else (source_name or "Unnamed")

    # 3. Piece metadata
    piece_length = info.get(b"piece length")
    pieces = info.get(b"pieces")
    num_pieces = len(pieces) // 20 if isinstance(pieces, bytes) else None

    # 4. File inventory extraction
    file_items: List[TorrentFileItem] = []

    if b"files" in info:
        # Multi-file v1 structure
        raw_files = info.get(b"files", [])
        for idx, file_dict in enumerate(raw_files):
            if isinstance(file_dict, dict):
                length = file_dict.get(b"length", 0)
                raw_path_list = file_dict.get(b"path.utf-8") or file_dict.get(b"path", [])
                path_parts = [_decode_bytes_str(p) for p in raw_path_list]
                rel_path = "/".join(path_parts) if path_parts else f"file_{idx}"
                ext = get_file_extension(rel_path)
                file_items.append(
                    TorrentFileItem(
                        path=rel_path,
                        size_bytes=int(length),
                        extension=ext,
                        is_video=is_video_filename(rel_path),
                        index=idx,
                    )
                )

    elif has_v2_tree:
        # BitTorrent v2 (BEP 52) file tree structure
        file_tree = info.get(b"file tree", {})
        if isinstance(file_tree, dict):
            file_items, _ = _parse_v2_file_tree(file_tree, [], 0)

    elif b"length" in info:
        # Single-file torrent
        length = info.get(b"length", 0)
        ext = get_file_extension(torrent_name)
        file_items.append(
            TorrentFileItem(
                path=torrent_name,
                size_bytes=int(length),
                extension=ext,
                is_video=is_video_filename(torrent_name),
                index=0,
            )
        )

    # 5. Extract Trackers
    raw_trackers: List[str] = []
    if b"announce" in decoded:
        raw_trackers.append(_decode_bytes_str(decoded[b"announce"]))
    if b"announce-list" in decoded:
        ann_list = decoded[b"announce-list"]
        if isinstance(ann_list, list):
            for tier in ann_list:
                if isinstance(tier, list):
                    for tr in tier:
                        raw_trackers.append(_decode_bytes_str(tr))
                elif isinstance(tier, (bytes, str)):
                    raw_trackers.append(_decode_bytes_str(tier))

    sanitized_trackers = sanitize_tracker_urls(raw_trackers)

    # 6. Analyze video candidates and multi-episode status
    video_candidates, is_multi_file, is_multi_episode = analyze_video_inventory(file_items)
    total_size = sum(item.size_bytes for item in file_items)

    result = TorrentDiscoveryResult(
        source_type="torrent",
        status=DiscoveryStatus.METADATA_AVAILABLE.value,
        infohash=primary_infohash,
        infohash_v1=infohash_v1,
        infohash_v2=infohash_v2,
        torrent_name=torrent_name,
        files=[item.to_dict() for item in file_items],
        total_size_bytes=total_size,
        video_candidates=[vc.to_dict() for vc in video_candidates],
        seeders=None,  # Offline parsing does not guess seeders
        metadata_complete=True,
        download_tested=False,
        recommended_engine=RecommendedEngine.LIBTORRENT.value,
        is_multi_file=is_multi_file,
        is_multi_episode=is_multi_episode,
        piece_length=piece_length,
        num_pieces=num_pieces,
        trackers=sanitized_trackers,
    )

    return result, file_items, video_candidates


def parse_torrent_file(
    file_path: Union[str, Path]
) -> Tuple[TorrentDiscoveryResult, List[TorrentFileItem], List[VideoCandidate]]:
    """Read a .torrent file from disk and parse discovery metadata."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Torrent file not found: {path}")

    file_size = path.stat().st_size
    if file_size > MAX_TORRENT_FILE_SIZE:
        raise BencodeSizeError(
            f"Torrent file size ({file_size} bytes) exceeds safety limit of {MAX_TORRENT_FILE_SIZE} bytes"
        )

    data = path.read_bytes()
    return parse_torrent_bytes(data, source_name=path.name)
