import subprocess
from pathlib import Path


def extract_asr_audio(
    source_path: Path,
    output_path: Path,
    start_seconds: float = 0.0,
    duration_seconds: float | None = 75.0,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_seconds),
    ]
    if duration_seconds is not None:
        command.extend(["-t", str(duration_seconds)])
    command.extend(
        [
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return output_path
