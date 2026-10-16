"""
AutoSub Cloud V2 — Video Identification & Episode Detection Heuristics
Classifies video inventory, extracts season/episode numbers, and identifies multi-episode torrents.
"""

import os
import re
from typing import List, Optional, Tuple

from app.services.torrent_discovery.models import TorrentFileItem, VideoCandidate

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".m2ts",
    ".iso",
    ".vob",
    ".ogv",
    ".3gp",
    ".rmvb",
    ".divx",
}

# Regex patterns for season and episode extraction
SEASON_EPISODE_PATTERN = re.compile(
    r"[sS](\d{1,2})[\s._-]*[eE](\d{1,3})", re.IGNORECASE
)
EPISODE_ONLY_PATTERN = re.compile(
    r"(?:^|[\s._\-\[])(?:ep|episode|e)[\s._\-]*(\d{1,3})(?:[\s._\-\]]|$)", re.IGNORECASE
)
ANIME_BRACKET_EP_PATTERN = re.compile(
    r"\[(\d{2,3})\]", re.IGNORECASE
)
DASH_SEPARATED_EP_PATTERN = re.compile(
    r"(?:^|[\s._\-])(\d{2,3})(?:[\s._\-](?:1080p|720p|4k|2160p|hevc|x264|x265|aac|bluray|web-dl|hdtv)|$)", re.IGNORECASE
)


def is_video_filename(filename: str) -> bool:
    """Check if file extension corresponds to a known video format."""
    _, ext = os.path.splitext(filename.lower())
    return ext in VIDEO_EXTENSIONS


def get_file_extension(filename: str) -> str:
    """Return lowercase file extension with leading period."""
    _, ext = os.path.splitext(filename.lower())
    return ext


def parse_episode_info(filename: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """
    Extract episode hint, season number, and episode number from filename.
    Returns: (episode_hint, season_number, episode_number)
    """
    base = os.path.basename(filename)

    # 1. Check standard S01E02 pattern
    m = SEASON_EPISODE_PATTERN.search(base)
    if m:
        season = int(m.group(1))
        episode = int(m.group(2))
        return f"S{season:02d}E{episode:02d}", season, episode

    # 2. Check Episode only: EP02 / E02
    m = EPISODE_ONLY_PATTERN.search(base)
    if m:
        episode = int(m.group(1))
        return f"EP{episode:02d}", 1, episode

    # 3. Check Anime brackets: [02]
    m = ANIME_BRACKET_EP_PATTERN.search(base)
    if m:
        episode = int(m.group(1))
        return f"EP{episode:02d}", 1, episode

    # 4. Check Dash separated episode numbers
    m = DASH_SEPARATED_EP_PATTERN.search(base)
    if m:
        episode = int(m.group(1))
        return f"EP{episode:02d}", 1, episode

    return None, None, None


def analyze_video_inventory(
    files: List[TorrentFileItem]
) -> Tuple[List[VideoCandidate], bool, bool]:
    """
    Analyze file inventory, extract video candidates, and determine multi-file / multi-episode status.
    
    Returns:
        (video_candidates, is_multi_file, is_multi_episode)
    """
    is_multi_file = len(files) > 1
    candidates: List[VideoCandidate] = []
    seen_episodes = set()

    for item in files:
        if item.is_video:
            hint, season, ep = parse_episode_info(item.path)
            candidate = VideoCandidate(
                path=item.path,
                size_bytes=item.size_bytes,
                extension=item.extension,
                file_index=item.index,
                episode_hint=hint,
                season_number=season,
                episode_number=ep,
            )
            candidates.append(candidate)
            if ep is not None:
                seen_episodes.add((season or 1, ep))

    # Sort video candidates:
    # If episode information exists, sort by season and episode, then path
    # Otherwise sort by size descending
    if seen_episodes:
        candidates.sort(
            key=lambda c: (
                c.season_number if c.season_number is not None else 999,
                c.episode_number if c.episode_number is not None else 999,
                c.path,
            )
        )
    else:
        candidates.sort(key=lambda c: (-c.size_bytes, c.path))

    # Classification of multi-episode:
    # A torrent is considered multi-episode if:
    # - There are 2 or more distinct episodes identified, OR
    # - There are 2 or more video candidates each >= 50MB (likely series/collection)
    is_multi_episode = len(seen_episodes) > 1 or (
        len(candidates) > 1 and sum(1 for c in candidates if c.size_bytes >= 50 * 1024 * 1024) > 1
    )

    return candidates, is_multi_file, is_multi_episode
