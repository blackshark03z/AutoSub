import subprocess
from pathlib import Path
import pytest

from app.services.ingestion_contract import (
    IngestionHandoffRecord,
    create_bilibili_ingestion_handoff,
    create_kylin_ingestion_handoff,
    attach_and_validate_local_media,
    create_or_reuse_ingestion_job,
)


@pytest.fixture
def synthetic_video_fixture(tmp_path: Path) -> Path:
    fixture_path = tmp_path / "valid_fixture_720p.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15:duration=1.0",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(fixture_path),
    ]
    subprocess.run(cmd, check=True)
    return fixture_path


def test_contract_01_recognized_link_without_media_is_not_downloaded():
    """Handoff contract: discovery metadata identified, but ingestion status must be METADATA_ONLY."""
    url = "https://b23.tv/gK7gvDO"
    handoff = create_bilibili_ingestion_handoff(url, canonical_id="BV1bPeu6cEkG")
    
    assert handoff.canonical_id == "BV1bPeu6cEkG"
    assert handoff.platform == "bilibili"
    assert handoff.ingestion_status == "METADATA_ONLY"
    assert handoff.discovery_status == "METADATA_ONLY"
    assert handoff.media_validation_status == "NOT_RUN"
    assert handoff.media_file_path is None
    assert handoff.media_sha256 is None
    assert handoff.media_duration_seconds is None


def test_contract_02_multipart_selection_maps_distinct_parts():
    """Handoff contract: Bilibili 2-part container cleanly maps Part 1 vs Part 2 without metadata confusion."""
    url = "https://b23.tv/gK7gvDO"
    pages = [
        {"part_index": 1, "cid": "41959818910", "title": "Part 1", "duration_seconds": 28312.88},
        {"part_index": 2, "cid": "41960997330", "title": "Part 2", "duration_seconds": 14438.14},
    ]
    
    handoff_p1 = create_bilibili_ingestion_handoff(url, "BV1bPeu6cEkG", pages=pages, selected_part_index=1)
    handoff_p2 = create_bilibili_ingestion_handoff(url, "BV1bPeu6cEkG", pages=pages, selected_part_index=2)
    
    assert handoff_p1.source_id == "bilibili:BV1bPeu6cEkG:p1"
    assert handoff_p1.part_or_episode_id == "41959818910"
    assert handoff_p1.selected_item_index == 1
    
    assert handoff_p2.source_id == "bilibili:BV1bPeu6cEkG:p2"
    assert handoff_p2.part_or_episode_id == "41960997330"
    assert handoff_p2.selected_item_index == 2
    
    # Metadata for Part 1 must not leak into Part 2
    assert handoff_p1.source_id != handoff_p2.source_id
    assert handoff_p1.part_or_episode_id != handoff_p2.part_or_episode_id


def test_contract_03_app_only_restricted_source_is_blocked():
    """Handoff contract: Kylin source classified as APP_ONLY_RESTRICTED must set INGESTION_BLOCKED."""
    kylin_url = "https://kylin.hainanyuyue.com/s/i9J6kMK62PE/"
    handoff = create_kylin_ingestion_handoff(
        kylin_url,
        title="太玄胎珠传",
        serial_count=113,
        shared_chapter_order=2,
        web_preview_duration=30.0,
    )
    
    assert handoff.discovery_status == "APP_ONLY_RESTRICTED"
    assert handoff.access_status == "APP_ONLY"
    assert handoff.ingestion_status == "INGESTION_BLOCKED"
    assert handoff.error_code == "APP_ONLY_RESTRICTED"
    assert "restricted to mobile app" in handoff.error_message
    assert handoff.part_or_episode_id == "chapter_002"
    assert handoff.selected_item_index == 2
    
    # Attempting to attach media to a blocked source preserves the blocked state
    res = attach_and_validate_local_media(handoff, "some_path.mp4")
    assert res.ingestion_status == "INGESTION_BLOCKED"


def test_contract_04_missing_media_file_fails_validation(tmp_path: Path):
    """Handoff contract: Media file missing returns MEDIA_MISSING error code."""
    url = "https://b23.tv/gK7gvDO"
    handoff = create_bilibili_ingestion_handoff(url, selected_part_index=1)
    
    non_existent = tmp_path / "does_not_exist.mp4"
    res = attach_and_validate_local_media(handoff, non_existent)
    
    assert res.ingestion_status == "INGESTION_FAILED"
    assert res.media_validation_status == "FAILED"
    assert res.error_code == "MEDIA_MISSING"


def test_contract_05_corrupt_media_file_fails_validation(tmp_path: Path):
    """Handoff contract: Corrupt 0-byte media file returns CORRUPT_MEDIA error code."""
    url = "https://b23.tv/gK7gvDO"
    handoff = create_bilibili_ingestion_handoff(url, selected_part_index=1)
    
    corrupt_file = tmp_path / "empty_video.mp4"
    corrupt_file.write_bytes(b"")
    res = attach_and_validate_local_media(handoff, corrupt_file)
    
    assert res.ingestion_status == "INGESTION_FAILED"
    assert res.media_validation_status == "FAILED"
    assert res.error_code == "CORRUPT_MEDIA"


def test_contract_06_valid_local_media_passes_validation(synthetic_video_fixture: Path):
    """Handoff contract: Valid local fixture triggers LOCAL_FIXTURE_VALIDATED with hash and duration,
    while verified live media triggers LIVE_MEDIA_VALIDATED."""
    url = "https://b23.tv/gK7gvDO"
    handoff_local = create_bilibili_ingestion_handoff(url, selected_part_index=1)
    res_local = attach_and_validate_local_media(handoff_local, synthetic_video_fixture, is_live_media=False)
    
    assert res_local.ingestion_status == "LOCAL_FIXTURE_VALIDATED"
    assert res_local.media_validation_status == "VALIDATED"
    assert res_local.media_file_path is not None
    assert res_local.media_sha256 is not None and len(res_local.media_sha256) == 64
    assert res_local.media_duration_seconds is not None and res_local.media_duration_seconds > 0.0
    assert res_local.error_code is None

    # Test verified live media flag
    handoff_live = create_bilibili_ingestion_handoff(url, selected_part_index=1)
    res_live = attach_and_validate_local_media(handoff_live, synthetic_video_fixture, is_live_media=True)
    assert res_live.ingestion_status == "LIVE_MEDIA_VALIDATED"
    assert res_live.media_validation_status == "VALIDATED"


def test_contract_07_job_deduplication_prevents_duplicate_processing(synthetic_video_fixture: Path):
    """Handoff contract: Re-submitting the same media hash or canonical item reuses existing job."""
    url = "https://b23.tv/gK7gvDO"
    handoff = create_bilibili_ingestion_handoff(url, selected_part_index=1)
    attach_and_validate_local_media(handoff, synthetic_video_fixture)
    
    jobs_registry = {}
    job_1 = create_or_reuse_ingestion_job(handoff, jobs_registry)
    assert job_1["reused"] is False
    
    # Retry with identical handoff
    job_2 = create_or_reuse_ingestion_job(handoff, jobs_registry)
    assert job_2["reused"] is True
    assert job_2["job_id"] == job_1["job_id"]


def test_contract_08_invalid_part_index_raises_clear_error():
    """Handoff contract: Requesting a non-existent part index raises a clear ValueError."""
    url = "https://b23.tv/gK7gvDO"
    pages = [
        {"part_index": 1, "cid": "41959818910", "title": "Part 1", "duration_seconds": 28312.88},
        {"part_index": 2, "cid": "41960997330", "title": "Part 2", "duration_seconds": 14438.14},
    ]
    with pytest.raises(ValueError, match="Part index 99 does not exist"):
        create_bilibili_ingestion_handoff(url, canonical_id="BV1bPeu6cEkG", pages=pages, selected_part_index=99)


def test_contract_09_arbitrary_bv_id_has_no_hardcoded_cids():
    """Handoff contract: Arbitrary BV ID does not inherit hardcoded CIDs from BV1bPeu6cEkG."""
    arbitrary_bv = "BV1xx411c7mD"
    handoff = create_bilibili_ingestion_handoff("https://bilibili.com/video/BV1xx411c7mD", canonical_id=arbitrary_bv)
    assert handoff.parts_or_chapters == []
    assert handoff.canonical_id == arbitrary_bv


def test_contract_10_redact_url_sanitizes_userinfo_and_query_credentials():
    """Handoff contract: URL containing user credentials in userinfo or query params is redacted."""
    from app.core.redaction import redact_url
    url_with_creds = "https://user:my_secret_pwd@api.bilibili.com/x/player/playurl?cid=123&bili_jct=supersecrettoken&token=xyz"
    redacted = redact_url(url_with_creds)
    assert "my_secret_pwd" not in redacted
    assert "supersecrettoken" not in redacted
    assert "xyz" not in redacted
    assert "user:<redacted>@api.bilibili.com" in redacted
    assert "bili_jct=%3Credacted%3E" in redacted or "bili_jct=<redacted>" in redacted


def test_contract_11_four_ingestion_states_distinct_and_independent(synthetic_video_fixture: Path):
    """Handoff contract: METADATA_ONLY, LOCAL_FIXTURE_VALIDATED, INGESTION_BLOCKED,
    and LIVE_MEDIA_VALIDATED are mutually distinct states."""
    # 1. METADATA_ONLY
    h1 = create_bilibili_ingestion_handoff("https://b23.tv/gK7gvDO", canonical_id="BV1bPeu6cEkG")
    assert h1.ingestion_status == "METADATA_ONLY"

    # 2. LOCAL_FIXTURE_VALIDATED
    h2 = attach_and_validate_local_media(create_bilibili_ingestion_handoff("https://b23.tv/gK7gvDO", selected_part_index=1), synthetic_video_fixture, is_live_media=False)
    assert h2.ingestion_status == "LOCAL_FIXTURE_VALIDATED"

    # 3. INGESTION_BLOCKED
    h3 = create_kylin_ingestion_handoff("https://kylin.hainanyuyue.com/s/i9J6kMK62PE/")
    assert h3.ingestion_status == "INGESTION_BLOCKED"

    # 4. LIVE_MEDIA_VALIDATED
    h4 = attach_and_validate_local_media(create_bilibili_ingestion_handoff("https://b23.tv/gK7gvDO", selected_part_index=1), synthetic_video_fixture, is_live_media=True)
    assert h4.ingestion_status == "LIVE_MEDIA_VALIDATED"

    distinct_states = {h1.ingestion_status, h2.ingestion_status, h3.ingestion_status, h4.ingestion_status}
    assert len(distinct_states) == 4, f"Expected 4 distinct states, got {distinct_states}"

