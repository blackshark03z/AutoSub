"""Focused tests for M1 input-identity binding and log/URL redaction.

Covers the two P0/P1 acceptance gaps:
- a job directory is immutable input: a changed URL/part/section (or tampered media)
  must fail closed instead of silently attaching stale media to new provenance;
- resolved share URLs and argv logs must not leak query credentials.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.redaction import canonical_url, redact_text
from tools.run_m1_local_url_to_mp4 import (
    INPUT_IDENTITY_FILE,
    _canonical_id_from,
    bind_input_identity,
    load_input_identity,
)

SHARE_URL = (
    "https://www.bilibili.com/video/BV1bPeu6cEkG?share_source=copy_web&share_medium=web"
    "&buvid=XY1234567890&mid=999999&share_session_id=abcdef&timestamp=1700000000&unique_k=abc"
)


def _media(path: Path, payload: bytes = b"fake-mp4-bytes") -> Path:
    path.write_bytes(payload)
    return path


def _identity(**overrides) -> dict:
    base = {
        "requested_host": "www.bilibili.com",
        "canonical_id": "BV1bPeu6cEkG",
        "part_index": 1,
        "end_seconds": 360,
        "url_redacted": "<redacted-url>",
    }
    base.update(overrides)
    return base


def test_canonical_url_drops_query_and_fragment():
    assert canonical_url(SHARE_URL) == "https://www.bilibili.com/video/BV1bPeu6cEkG"
    assert "mid=" not in canonical_url(SHARE_URL)
    assert "buvid" not in canonical_url(SHARE_URL)
    assert "share_session_id" not in canonical_url(SHARE_URL)


def test_canonical_url_strips_userinfo():
    assert canonical_url("https://user:pw@host.tld/p?q=1") == "https://host.tld/p"


def test_redact_text_masks_embedded_url_query_params():
    text = f"download failed for {SHARE_URL} after 2 attempts"
    out = redact_text(text)
    assert "mid=999999" not in out
    assert "buvid=XY1234567890" not in out
    assert "share_session_id=abcdef" not in out
    assert "download failed" in out


def test_canonical_id_falls_back_to_bv_in_url_when_flat_entry_has_none():
    # yt-dlp --flat-playlist yields placeholder entries with id=None; the id must still
    # be recovered (from the top-level id or the BV token in the URL) for provenance.
    assert _canonical_id_from(
        {"id": None, "entries": [{"_type": "url", "id": None}]},
        {"id": None, "url": None},
        "https://www.bilibili.com/video/BV1bPeu6cEkG?share_source=copy_web",
    ) == "BV1bPeu6cEkG"
    assert _canonical_id_from({"id": "BV1TOPME"}, {}, SHARE_URL) == "BV1TOPME"
    assert _canonical_id_from({}, {}, "https://example.com/x") is None


def test_bind_input_identity_writes_then_verifies(tmp_path: Path):
    media = _media(tmp_path / "source.mp4")
    record = bind_input_identity(tmp_path, _identity(), media)
    assert record["canonical_id"] == "BV1bPeu6cEkG"
    assert record["source_sha256"]
    assert (tmp_path / INPUT_IDENTITY_FILE).exists()
    # Re-binding the same request verifies and reuses the recorded identity.
    again = bind_input_identity(tmp_path, _identity(), media)
    assert again["source_sha256"] == record["source_sha256"]


@pytest.mark.parametrize("field,value", [
    ("canonical_id", "BV1OTHER0000"),
    ("part_index", 2),
    ("end_seconds", 600),
    ("requested_host", "b23.tv"),
])
def test_bind_input_identity_fails_closed_on_changed_request(tmp_path: Path, field: str, value):
    media = _media(tmp_path / "source.mp4")
    bind_input_identity(tmp_path, _identity(), media)
    with pytest.raises(SystemExit, match="IMMUTABLE_JOB_INPUT_MISMATCH"):
        bind_input_identity(tmp_path, _identity(**{field: value}), media)


def test_bind_input_identity_fails_closed_on_tampered_media(tmp_path: Path):
    media = _media(tmp_path / "source.mp4")
    bind_input_identity(tmp_path, _identity(), media)
    media.write_bytes(b"different-bytes")
    with pytest.raises(SystemExit, match="IMMUTABLE_JOB_INPUT_MISMATCH"):
        bind_input_identity(tmp_path, _identity(), media)


def test_bind_input_identity_fails_closed_on_unreadable_record(tmp_path: Path):
    media = _media(tmp_path / "source.mp4")
    (tmp_path / INPUT_IDENTITY_FILE).write_text("{not json", encoding="utf-8")
    assert load_input_identity(tmp_path) is None
    with pytest.raises(SystemExit, match="unreadable input identity"):
        bind_input_identity(tmp_path, _identity(), media)
