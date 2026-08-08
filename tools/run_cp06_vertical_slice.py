import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.db.session import init_db
from app.providers.tts.elevenlabs import ElevenLabsTTSProvider, load_elevenlabs_config
from app.services.artifacts import register_artifact
from app.services.preview_render import build_video_filter, evaluate_tts_fit, write_tts_mix
from app.services.subtitles import write_ass, write_srt
from app.services.timeline import load_latest_timeline, save_timeline_revision
from app.services.tts_generation import generate_tts_for_timeline, resolve_voice_id


def main() -> None:
    init_db()
    project_id = "vertical_slice_cp02"
    settings = get_settings()
    timeline = load_latest_timeline(project_id)
    provider = ElevenLabsTTSProvider(load_elevenlabs_config())
    voice_id = resolve_voice_id(None, provider)
    generations = generate_tts_for_timeline(project_id, timeline, provider, voice_id)
    for unit, result in zip(timeline["tts_units"], generations, strict=True):
        if result["status"] != "ready" or not result["artifact_path"] or not Path(result["artifact_path"]).exists():
            raise RuntimeError(f"TTS generation not ready for synthesis unit {unit['id']}")
    fit_results = evaluate_tts_fit(timeline, generations)
    fit_failures = [result for result in fit_results if result["status"] == "FAIL"]
    if fit_failures:
        raise RuntimeError(f"TTS fit gate failed for {len(fit_failures)} synthesis units")
    timeline_revision = save_timeline_revision(project_id, timeline)
    project_dir = settings.data_dir / "projects" / project_id
    subtitle_dir = project_dir / "subtitles"
    render_dir = project_dir / "renders"
    srt_path = write_srt(timeline, subtitle_dir / "cp06_final.srt")
    ass_path = write_ass(timeline, subtitle_dir / "cp06_final.ass")
    mix_path = write_tts_mix(timeline, generations, project_dir / "audio" / "cp06_real_tts_mix.wav")
    preview_path = render_dir / "cp06_vertical_slice_720p.mp4"
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
            "75.000",
            "-i",
            str(settings.source_path),
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
            "27",
            "-c:a",
            "aac",
            "-shortest",
            str(preview_path),
        ],
        check=True,
    )
    media = media_summary(preview_path)
    srt_artifact = register_artifact(project_id, "cp06_srt", srt_path)
    ass_artifact = register_artifact(project_id, "cp06_ass", ass_path)
    mix_artifact = register_artifact(project_id, "cp06_tts_mix", mix_path)
    preview_artifact = register_artifact(project_id, "cp06_preview_720p", preview_path)
    cache_hits = sum(1 for item in generations if item["cache_status"] == "hit")
    real_calls = sum(1 for item in generations if item["cache_status"] == "miss")
    print(f"project_id={project_id}")
    print(f"audio_policy=replace_all_audio")
    print(f"source_audio_mapped=False")
    print(f"voice_configured=True")
    print(f"tts_generations={len(generations)}")
    print(f"subtitle_units={len(timeline['segments'])}")
    print(f"tts_synthesis_units={len(timeline['tts_units'])}")
    print(f"timeline_revision_id={timeline_revision['revision_id']}")
    print(f"tts_fit_pass={sum(result['status'] == 'PASS' for result in fit_results)}")
    print(f"tts_fit_warn={sum(result['status'] == 'WARN' for result in fit_results)}")
    print(f"tts_fit_fail={len(fit_failures)}")
    print(f"elevenlabs_cache_hits={cache_hits}")
    print(f"elevenlabs_real_calls={real_calls}")
    print(f"srt_path={srt_path}")
    print(f"ass_path={ass_path}")
    print(f"tts_mix_path={mix_path}")
    print(f"preview_path={preview_path}")
    print(f"preview_sha256={sha256_file(preview_path)}")
    print(f"preview_width={media['video']['width']}")
    print(f"preview_height={media['video']['height']}")
    print(f"preview_duration={media['duration_seconds']:.6f}")
    print(f"srt_sha256={srt_artifact['sha256']}")
    print(f"ass_sha256={ass_artifact['sha256']}")
    print(f"tts_mix_sha256={mix_artifact['sha256']}")
    print(f"artifact_registered=True")
    print(f"free_disk_gb={shutil.disk_usage(settings.root).free / (1024 ** 3):.2f}")


if __name__ == "__main__":
    main()
