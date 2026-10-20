from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core.media import media_summary
from app.services.e2_dynamic_sub_mask import build_dynamic_mask_plan
from app.services.hongguo_local_intake import (
    LocalSourceError,
    bind_local_input_identity,
    create_hongguo_local_handoff,
    sha256_file,
)
from app.services.ingestion_contract import create_or_reuse_ingestion_job

SERIES = "7690432295777340441"
EPISODE = "001"


def _video(path: Path, *, audio: bool = True, width: int = 320, height: int = 240) -> Path:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=10:duration=1.0"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1.0", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


def test_media_summary_reads_chinese_metadata_without_unicode_crash(tmp_path: Path):
    source = tmp_path / "Tap_001.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1.0",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
         "-metadata", "title=隐世魔尊，女帝带娃找上门来", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
        check=True)
    summary = media_summary(source)
    assert summary["video"]["width"] == 320
    assert summary["audio"]["codec"] == "aac"


def test_local_handoff_binds_provider_series_episode_and_hash(tmp_path: Path):
    source = _video(tmp_path / "Tap_001.mp4")
    digest = sha256_file(source)
    handoff = create_hongguo_local_handoff(
        source, series_id=SERIES, episode=EPISODE, title="隐世魔尊，女帝带娃找上门来",
        expected_sha256=digest)

    assert handoff.platform == "hongguo_local_file"
    assert handoff.discovery_status == "LOCAL_INPUT"
    assert handoff.access_status == "LOCAL_FILE"
    assert handoff.ingestion_status == "LOCAL_SOURCE_VALIDATED"
    assert handoff.source_url_redacted == ""
    assert handoff.canonical_id == SERIES
    assert handoff.part_or_episode_id == EPISODE
    assert handoff.selected_item_index == 1
    assert handoff.media_sha256 == digest
    assert handoff.media_duration_seconds and handoff.media_duration_seconds > 0
    assert handoff.parts_or_chapters[0]["url_ingest"] is False
    assert sha256_file(source) == digest


def test_local_handoff_rejects_hash_mismatch(tmp_path: Path):
    source = _video(tmp_path / "Tap_001.mp4")
    with pytest.raises(LocalSourceError, match="SOURCE_HASH_MISMATCH"):
        create_hongguo_local_handoff(
            source, series_id=SERIES, episode=EPISODE, title="x", expected_sha256="0" * 64)


def test_local_handoff_rejects_missing_wrong_extension_and_empty_file(tmp_path: Path):
    with pytest.raises(LocalSourceError, match="SOURCE_MISSING"):
        create_hongguo_local_handoff(tmp_path / "missing.mp4", series_id=SERIES,
                                     episode=EPISODE, title="x")
    text = tmp_path / "Tap_001.txt"
    text.write_text("not media", encoding="utf-8")
    with pytest.raises(LocalSourceError, match="UNSUPPORTED_EXTENSION"):
        create_hongguo_local_handoff(text, series_id=SERIES, episode=EPISODE, title="x")
    empty = tmp_path / "Tap_001.mp4"
    empty.write_bytes(b"")
    with pytest.raises(LocalSourceError, match="EMPTY_SOURCE"):
        create_hongguo_local_handoff(empty, series_id=SERIES, episode=EPISODE, title="x")


def test_local_handoff_rejects_truncated_media_that_still_has_metadata(tmp_path: Path):
    source = tmp_path / "full.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=5.0",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=5.0",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "+faststart", str(source)],
        check=True)
    payload = source.read_bytes()
    truncated = tmp_path / "Tap_001.mp4"
    truncated.write_bytes(payload[:int(len(payload) * 0.35)])
    with pytest.raises(LocalSourceError, match="TRUNCATED_MEDIA|CORRUPT_MEDIA|INVALID_CONTAINER"):
        create_hongguo_local_handoff(truncated, series_id=SERIES, episode=EPISODE, title="x")


def test_local_handoff_rejects_video_without_audio(tmp_path: Path):
    source = _video(tmp_path / "Tap_001.mp4", audio=False)
    with pytest.raises(LocalSourceError, match="NO_AUDIO_STREAM"):
        create_hongguo_local_handoff(source, series_id=SERIES, episode=EPISODE, title="x")


def test_local_handoff_rejects_invalid_episode(tmp_path: Path):
    source = _video(tmp_path / "Tap_001.mp4")
    with pytest.raises(LocalSourceError, match="INVALID_EPISODE"):
        create_hongguo_local_handoff(source, series_id=SERIES, episode="00", title="x")


def test_local_identity_fails_closed_on_episode_or_hash_mismatch(tmp_path: Path):
    identity = {"provider": "HONGGUO_LOCAL_FILE", "series_id": SERIES, "episode": "001",
                "source_sha256": "a" * 64, "duration_ms": 93855}
    bind_local_input_identity(tmp_path, identity, allow_create=True)
    bind_local_input_identity(tmp_path, identity, allow_create=False)
    with pytest.raises(LocalSourceError, match="LOCAL_INPUT_MISMATCH"):
        bind_local_input_identity(tmp_path, {**identity, "episode": "002"}, allow_create=False)
    with pytest.raises(LocalSourceError, match="LOCAL_INPUT_MISMATCH"):
        bind_local_input_identity(tmp_path, {**identity, "source_sha256": "b" * 64}, allow_create=False)


def test_local_identity_refuses_unrecorded_existing_job(tmp_path: Path):
    with pytest.raises(LocalSourceError, match="no recorded local input identity"):
        bind_local_input_identity(tmp_path, {"provider": "HONGGUO_LOCAL_FILE"}, allow_create=False)


def test_local_validated_handoff_dispatches_and_reuses_job(tmp_path: Path):
    source = _video(tmp_path / "Tap_001.mp4")
    handoff = create_hongguo_local_handoff(source, series_id=SERIES, episode=EPISODE, title="x")
    registry: dict = {}
    first = create_or_reuse_ingestion_job(handoff, registry)
    second = create_or_reuse_ingestion_job(handoff, registry)
    assert first["reused"] is False
    assert second["reused"] is True
    assert second["job_id"] == first["job_id"]


def test_empty_source_roi_override_keeps_non_bilibili_plate(tmp_path: Path):
    source = _video(tmp_path / "frame.mp4", width=1280, height=720)
    cues = [{"cue_id": "cue_001", "start_ms": 1000, "end_ms": 2000, "text": "Test line"}]
    boxes = {"cue_001": {"bbox": {"left_x": 1000, "top_y": 640, "right_x": 1150, "bottom_y": 680,
                                  "width": 151, "height": 41},
                         "text_height": 41, "confidence": 0.99}}
    font = Path(r"C:\Windows\Fonts\arial.ttf")

    default_plan = build_dynamic_mask_plan(source, cues, boxes, font_path=font)
    custom_plan = build_dynamic_mask_plan(source, cues, boxes, font_path=font, protected_rois={})

    assert default_plan["statuses"] != {"PASS": 1}
    assert custom_plan["protected_rois"] == {}
    assert custom_plan["statuses"] == {"PASS": 1}
