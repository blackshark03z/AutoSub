import json
import subprocess
from pathlib import Path

import pytest

from app.core.hashing import sha256_file


def test_cp06_media_contract_when_local_artifact_is_available():
    artifact = Path("data/projects/vertical_slice_cp02/renders/cp06_vertical_slice_720p.mp4")
    if not artifact.exists():
        pytest.skip("ignored CP06 production artifact is not present")
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,sample_rate,channels",
            "-of",
            "json",
            str(artifact),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(probe.stdout)
    video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in payload["streams"] if stream["codec_type"] == "audio")
    assert sha256_file(artifact) == "c24a0168699c44f26f85f2dd2e9f57cccde0cd10399e667a0e9f2c5566057838"
    assert float(payload["format"]["duration"]) == pytest.approx(75.0, abs=0.001)
    assert (video["width"], video["height"]) == (1280, 720)
    assert (audio["codec_name"], int(audio["sample_rate"]), audio["channels"]) == ("aac", 48000, 1)
    assert len([stream for stream in payload["streams"] if stream["codec_type"] == "audio"]) == 1
