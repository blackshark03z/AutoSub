from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.verify_chinese_video import parse_fps, verify_media_evidence


def test_parse_fps():
    assert parse_fps("25/1") == 25.0
    assert parse_fps("30000/1001") == pytest.approx(29.97002997)
    assert parse_fps("29.97") == 29.97
    assert parse_fps("0/0") is None
    assert parse_fps("") is None
    assert parse_fps(None) is None


def test_verify_media_nonexistent_file(tmp_path: Path):
    nonexistent = tmp_path / "not_found.mp4"
    res = verify_media_evidence(nonexistent, mode="full")
    assert res["verdict"] == "VERIFICATION_FAILED"
    assert "Target file does not exist" in res["errors"][0]


@patch("subprocess.run")
def test_verify_media_ffprobe_failure(mock_subproc, tmp_path: Path):
    video = tmp_path / "dummy.mp4"
    video.write_bytes(b"dummy")

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "Invalid data found when processing input"
    mock_subproc.return_value = mock_proc

    res = verify_media_evidence(video, mode="full")
    assert res["verdict"] == "VERIFICATION_FAILED"
    assert "FFprobe failed" in res["errors"][0]


@patch("subprocess.run")
def test_verify_media_missing_frame_progress(mock_subproc, tmp_path: Path):
    video = tmp_path / "dummy.mp4"
    video.write_bytes(b"dummy")

    # 1. FFprobe success
    probe_output = {
        "format": {"duration": "10.0", "size": "1000", "bit_rate": "800000", "format_name": "mov,mp4"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480, "r_frame_rate": "25/1"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2},
        ],
    }
    probe_proc = MagicMock()
    probe_proc.returncode = 0
    probe_proc.stdout = json.dumps(probe_output)

    # 2. Sample decodes pass
    sample_proc = MagicMock()
    sample_proc.returncode = 0
    sample_proc.stderr = ""

    # 3. Full decode exits 0 but stderr has NO frame= statistics (missing progress)
    full_proc = MagicMock()
    full_proc.returncode = 0
    full_proc.stderr = "ffmpeg version 9.0.1\nconfiguration: ...\nsome generic info without frame progress"

    mock_subproc.side_effect = [probe_proc, sample_proc, full_proc]

    res = verify_media_evidence(video, mode="full")
    assert res["verdict"] == "DECODE_FAILED"
    assert any("Missing or zero decoded frames" in err for err in res["errors"])


@patch("subprocess.run")
def test_verify_media_decoder_error(mock_subproc, tmp_path: Path):
    video = tmp_path / "dummy.mp4"
    video.write_bytes(b"dummy")

    probe_output = {
        "format": {"duration": "10.0", "size": "1000", "bit_rate": "800000", "format_name": "mov,mp4"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480, "r_frame_rate": "25/1"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2},
        ],
    }
    probe_proc = MagicMock()
    probe_proc.returncode = 0
    probe_proc.stdout = json.dumps(probe_output)

    sample_proc = MagicMock()
    sample_proc.returncode = 0
    sample_proc.stderr = ""

    # Full decode exits 0 and reports 250 frames, BUT logs corrupt/error lines
    full_proc = MagicMock()
    full_proc.returncode = 0
    full_proc.stderr = "[h264 @ 0x123] error: corrupt macroblock detected\nframe=  250 fps=50 q=-1.0 Lsize=N/A"

    mock_subproc.side_effect = [probe_proc, sample_proc, full_proc]

    res = verify_media_evidence(video, mode="full")
    assert res["verdict"] == "DECODE_FAILED"
    assert any("Decoder logged" in err for err in res["errors"])


@patch("subprocess.run")
def test_verify_media_truncated_frames(mock_subproc, tmp_path: Path):
    video = tmp_path / "dummy.mp4"
    video.write_bytes(b"dummy")

    # 10s @ 25fps = 250 expected frames. 90% threshold = 225 frames.
    probe_output = {
        "format": {"duration": "10.0", "size": "1000", "bit_rate": "800000", "format_name": "mov,mp4"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480, "r_frame_rate": "25/1"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2},
        ],
    }
    probe_proc = MagicMock()
    probe_proc.returncode = 0
    probe_proc.stdout = json.dumps(probe_output)

    sample_proc = MagicMock()
    sample_proc.returncode = 0
    sample_proc.stderr = ""

    # Only 50 frames decoded (stream truncated silently)
    full_proc = MagicMock()
    full_proc.returncode = 0
    full_proc.stderr = "frame=   50 fps=50 q=-1.0 Lsize=N/A"

    mock_subproc.side_effect = [probe_proc, sample_proc, full_proc]

    res = verify_media_evidence(video, mode="full")
    assert res["verdict"] == "DECODE_FAILED"
    assert any("Insufficient decoded frames" in err for err in res["errors"])


@patch("subprocess.run")
def test_verify_media_full_decode_success(mock_subproc, tmp_path: Path):
    video = tmp_path / "dummy.mp4"
    video.write_bytes(b"dummy")

    probe_output = {
        "format": {"duration": "10.0", "size": "1000", "bit_rate": "800000", "format_name": "mov,mp4"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480, "r_frame_rate": "25/1"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2},
        ],
    }
    probe_proc = MagicMock()
    probe_proc.returncode = 0
    probe_proc.stdout = json.dumps(probe_output)

    sample_proc = MagicMock()
    sample_proc.returncode = 0
    sample_proc.stderr = ""

    # Complete 250 frames with 0 errors
    full_proc = MagicMock()
    full_proc.returncode = 0
    full_proc.stderr = "frame=  250 fps=50 q=-1.0 Lsize=N/A speed=2.0x"

    mock_subproc.side_effect = [probe_proc, sample_proc, full_proc]

    res = verify_media_evidence(video, mode="full")
    assert res["verdict"] == "FULL_DECODE_VERIFIED"
    assert res["decode_verification"]["full_test"]["actual_frames_decoded"] == 250
    assert len(res["errors"]) == 0
