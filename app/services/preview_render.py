import os
import subprocess
import wave
from pathlib import Path

from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.config import get_settings
from app.providers.tts.fake import FakeTTSProvider
from app.services.subtitles import write_ass, write_srt
from app.services.timeline import load_latest_timeline
from app.services.tts_generation import generate_tts_for_timeline, resolve_voice_id


def ensure_fake_tts_for_preview(project_id: str, timeline: dict) -> list[dict]:
    provider = FakeTTSProvider()
    voice_id = resolve_voice_id(None, provider)
    return generate_tts_for_timeline(project_id, timeline, provider, voice_id)


def write_tts_mix(timeline: dict, generations: list[dict], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration_seconds = timeline["source_duration_ms"] / 1000
    sample_rate = 48000
    total_samples = int(duration_seconds * sample_rate)
    samples = [0] * total_samples
    by_generation = {generation["generation_id"]: generation for generation in generations}
    placements = timeline.get("tts_units", timeline["segments"])
    fit_results = evaluate_tts_fit(timeline, generations)
    failed = [result for result in fit_results if result["status"] == "FAIL"]
    if failed:
        raise ValueError(f"TTS timing fit failed for {len(failed)} synthesis units")
    for placement in placements:
        generation_id = placement.get("active_tts_generation_id")
        if not generation_id or generation_id not in by_generation:
            continue
        artifact_path = Path(by_generation[generation_id]["artifact_path"])
        if not artifact_path.exists():
            raise FileNotFoundError(f"TTS artifact missing for {generation_id}")
        artifact_samples = _read_mono_wav_samples(artifact_path)
        start = int(placement["start_ms"] * sample_rate / 1000)
        if start >= total_samples:
            continue
        for offset, value in enumerate(artifact_samples):
            index = start + offset
            if index >= total_samples:
                break
            samples[index] += value
    temp_path = output_path.with_suffix(".tmp.wav")
    with wave.open(str(temp_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for value in samples:
            clipped = max(-32768, min(32767, value))
            wav.writeframesraw(clipped.to_bytes(2, "little", signed=True))
    os.replace(temp_path, output_path)
    return output_path


def evaluate_tts_fit(timeline: dict, generations: list[dict]) -> list[dict]:
    by_generation = {generation["generation_id"]: generation for generation in generations}
    placements = timeline.get("tts_units", timeline["segments"])
    results = []
    for placement in placements:
        generation_id = placement.get("active_tts_generation_id")
        if not generation_id or generation_id not in by_generation:
            continue
        artifact_path = Path(by_generation[generation_id]["artifact_path"])
        with wave.open(str(artifact_path), "rb") as wav:
            tts_duration_ms = wav.getnframes() * 1000 / wav.getframerate()
        slot_duration_ms = placement["end_ms"] - placement["start_ms"]
        fit_ratio = tts_duration_ms / slot_duration_ms
        status = "PASS" if fit_ratio <= 1.03 else "WARN" if fit_ratio <= 1.12 else "FAIL"
        results.append(
            {
                "tts_unit_id": placement["id"],
                "slot_duration_ms": slot_duration_ms,
                "tts_duration_ms": round(tts_duration_ms, 3),
                "overflow_ms": round(max(0.0, tts_duration_ms - slot_duration_ms), 3),
                "fit_ratio": round(fit_ratio, 6),
                "status": status,
            }
        )
    return results


def render_preview(project_id: str, source_path: Path, duration_seconds: float = 75.0) -> dict:
    timeline = load_latest_timeline(project_id)
    project_dir = get_settings().data_dir / "projects" / project_id
    subtitle_dir = project_dir / "subtitles"
    render_dir = project_dir / "renders"
    srt_path = write_srt(timeline, subtitle_dir / "preview.srt")
    ass_path = write_ass(timeline, subtitle_dir / "preview.ass")
    generations = ensure_fake_tts_for_preview(project_id, timeline)
    mix_path = write_tts_mix(timeline, generations, project_dir / "audio" / "preview_tts_mix.wav")
    preview_path = render_dir / "cp05_preview_720p.mp4"
    render_dir.mkdir(parents=True, exist_ok=True)
    video_filter = build_video_filter(timeline, ass_path)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0",
            "-t",
            f"{duration_seconds:.3f}",
            "-i",
            str(source_path),
            "-i",
            str(mix_path),
            "-filter_complex",
            f"[0:v]{video_filter}[v]",
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-shortest",
            str(preview_path),
        ],
        check=True,
    )
    summary = media_summary(preview_path)
    return {
        "project_id": project_id,
        "audio_policy": "replace_all_audio",
        "source_audio_mapped": False,
        "srt_path": str(srt_path),
        "ass_path": str(ass_path),
        "tts_mix_path": str(mix_path),
        "preview_path": str(preview_path),
        "preview_sha256": sha256_file(preview_path),
        "preview_media": summary,
        "generations": len(generations),
    }


def _read_mono_wav_samples(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        if sample_width != 2:
            raise ValueError("Only 16-bit PCM WAV TTS artifacts are supported")
        frames = wav.readframes(wav.getnframes())
    samples = []
    step = sample_width * channels
    for index in range(0, len(frames), step):
        samples.append(int.from_bytes(frames[index : index + 2], "little", signed=True))
    return samples


def _ffmpeg_subtitle_path(path: Path) -> str:
    escaped = str(path.resolve()).replace("\\", "/")
    escaped = escaped.replace(":", "\\:").replace("'", r"\'")
    return escaped


def build_video_filter(timeline: dict, ass_path: Path, width: int = 1280, height: int = 720) -> str:
    filters = [f"scale={width}:-2"]
    for mask in timeline.get("masks", []):
        if mask.get("mode") != "always":
            continue
        effective = _effective_mask(mask)
        x = round(effective["x_norm"] * width)
        y = round(effective["y_norm"] * height)
        box_width = round(effective["width_norm"] * width)
        box_height = round(effective["height_norm"] * height)
        opacity = effective.get("opacity", 1.0)
        filters.append(
            f"drawbox=x={x}:y={y}:w={box_width}:h={box_height}:color=black@{opacity:.3f}:t=fill"
        )
    filters.append(f"subtitles='{_ffmpeg_subtitle_path(ass_path)}'")
    return ",".join(filters)


def _effective_mask(mask: dict) -> dict:
    # Upgrade the known CP05/CP06 legacy bottom mask that leaked source glyphs.
    if (
        mask.get("id") == "mask_bottom"
        and mask.get("y_norm") == 0.82
        and mask.get("height_norm") == 0.16
    ):
        return {**mask, "y_norm": 0.76, "height_norm": 0.21, "opacity": 1.0}
    return mask
