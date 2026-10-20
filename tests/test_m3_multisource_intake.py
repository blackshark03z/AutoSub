"""Focused M3 discovery/negative checks for the existing intake contract.

Phase A adds no platform adapter and no allowlist entry. These tests prove that an
unselected platform stays rejected, the Kylin app-only teaser cannot be promoted to a
full episode, lookalike/redirected hosts are not admitted, and no job or media artifact
is fabricated from metadata or a missing file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ingestion_contract import (
    attach_and_validate_local_media,
    create_bilibili_ingestion_handoff,
    create_kylin_ingestion_handoff,
    create_or_reuse_ingestion_job,
)
from tools.run_m1_local_url_to_mp4 import validate_source_url

KYLIN_URL = "https://kylin.hainanyuyue.com/s/i9J6kMK62PE/"
YOUTUBE_URL = "https://www.youtube.com/watch?v=2TeSCz4vcSQ"


@pytest.mark.parametrize(
    "url,reason",
    [
        (YOUTUBE_URL, "not in allowlist"),
        ("https://kylin.hainanyuyue.com/s/i9J6kMK62PE/", "not in allowlist"),
        ("https://bilibili.com.evil.example/video/BV1bPeu6cEkG", "not in allowlist"),
        ("https://evilbilibili.com/video/BV1bPeu6cEkG", "not in allowlist"),
    ],
)
def test_unselected_or_lookalike_host_is_rejected(url: str, reason: str):
    with pytest.raises(SystemExit) as excinfo:
        validate_source_url(url)
    assert "URL_REJECTED" in str(excinfo.value)
    assert reason in str(excinfo.value)


def test_non_http_scheme_is_rejected_before_network():
    with pytest.raises(SystemExit, match="unsupported scheme"):
        validate_source_url("ftp://b23.tv/gK7gvDO")


def test_kylin_teaser_cannot_be_promoted_to_full_episode(tmp_path: Path):
    handoff = create_kylin_ingestion_handoff(KYLIN_URL)
    teaser = tmp_path / "web_teaser_30s.mp4"
    teaser.write_bytes(b"not-a-real-teaser")

    result = attach_and_validate_local_media(handoff, teaser, is_live_media=True)

    assert result.discovery_status == "APP_ONLY_RESTRICTED"
    assert result.ingestion_status == "INGESTION_BLOCKED"
    assert result.error_code == "APP_ONLY_RESTRICTED"
    assert result.media_file_path is None
    assert result.media_sha256 is None
    assert result.selected_item_index == 2
    assert result.part_or_episode_id == "chapter_002"
    assert any(part.get("type") == "web_teaser" for part in result.parts_or_chapters)
    assert not any(part.get("type") == "full_episode" for part in result.parts_or_chapters)


def test_metadata_only_handoff_cannot_dispatch_a_job():
    handoff = create_bilibili_ingestion_handoff("https://b23.tv/gK7gvDO", selected_part_index=1)
    assert handoff.ingestion_status == "METADATA_ONLY"

    registry: dict[str, dict] = {}
    with pytest.raises(ValueError, match="Cannot dispatch ingestion job"):
        create_or_reuse_ingestion_job(handoff, registry)
    assert registry == {}


def test_missing_media_creates_no_fake_artifact(tmp_path: Path):
    missing = tmp_path / "never_downloaded.mp4"
    handoff = create_bilibili_ingestion_handoff("https://b23.tv/gK7gvDO", selected_part_index=1)

    result = attach_and_validate_local_media(handoff, missing, is_live_media=True)

    assert result.ingestion_status == "INGESTION_FAILED"
    assert result.error_code == "MEDIA_MISSING"
    assert result.media_validation_status == "FAILED"
    assert result.media_file_path is None
    assert not missing.exists()
    with pytest.raises(ValueError):
        create_or_reuse_ingestion_job(result, {})
