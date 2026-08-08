from pathlib import Path

from app.core.config import get_settings
from app.core.media import media_summary
from app.db.session import init_db
from app.db.session import session_scope
from app.domain.models import Project
from app.providers.asr.base import ASRProvider
from app.providers.asr.faster_whisper_provider import FasterWhisperASRProvider
from app.services.audio import extract_asr_audio
from app.services.timeline import build_timeline, save_timeline_revision


def run_cp02_vertical_slice(
    project_id: str = "vertical_slice_cp02",
    provider: ASRProvider | None = None,
    source_path: Path | None = None,
    duration_seconds: float = 75.0,
) -> dict:
    init_db()
    settings = get_settings()
    source = source_path or settings.source_path
    with session_scope() as session:
        if session.query(Project).filter(Project.project_id == project_id).one_or_none() is None:
            session.add(Project(project_id=project_id, title="CP02 vertical slice"))
    project_dir = settings.data_dir / "projects" / project_id
    audio_path = project_dir / "audio" / "source_asr_000_075.wav"
    extract_asr_audio(source, audio_path, start_seconds=0.0, duration_seconds=duration_seconds)
    audio_summary = media_summary(audio_path)
    source_duration_ms = max(1, int(audio_summary["duration_seconds"] * 1000))
    asr_provider = provider or FasterWhisperASRProvider(model_name="tiny", device="cpu", compute_type="int8")
    segments = asr_provider.transcribe(audio_path, language="zh")
    timeline = build_timeline(project_id=project_id, source_duration_ms=source_duration_ms, asr_segments=segments)
    revision = save_timeline_revision(project_id, timeline)
    summary = media_summary(source)
    return {"project_id": project_id, "audio_path": str(audio_path), "timeline": revision, "source_media": summary}
