from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.providers.asr.autosubs_provider import AutoSubsASRProvider, AutoSubsRuntimeError, discover_autosubs_config
from app.providers.asr.base import ASRProvider
from app.services.audio import extract_asr_audio
from app.services.subtitle_tracks import SubtitleContentUnavailableError, active_track_provenance, create_local_transcription_track


ASR_PROVIDER_NAME = "autosubs"
ASR_FAILED_MESSAGE = "AutoSubs khong the tao phu de nguon cuc bo. Kiem tra ban cai dat AutoSubs va model small, sau do thu lai."
NO_SPEECH_MESSAGE = "AutoSubs khong phat hien duoc loi noi co moc thoi gian. Video chua duoc xuat."


def create_external_asr_provider() -> ASRProvider:
    return AutoSubsASRProvider(discover_autosubs_config(get_settings().root))


def ensure_external_transcription_track(
    run_id: str,
    *,
    source_path: Path,
    run_directory: Path,
    source_duration_seconds: float,
    target_language: str,
    source_language: str | None = None,
    provider_factory: Callable[[], ASRProvider] = create_external_asr_provider,
) -> dict[str, Any]:
    existing = active_track_provenance(run_id)
    if existing in {"user_import", "user_authored", "local_transcription", "provider_transcription"}:
        return {"status": "SKIPPED", "subtitle_provenance": existing}

    audio_path = run_directory / "work" / "source_asr_16khz_mono.wav"
    log_path = run_directory / "logs" / "external_asr.json"
    normalized_language = None if not source_language or str(source_language).strip().lower() == "auto" else str(source_language).strip()
    try:
        extraction_started = time.perf_counter()
        extract_asr_audio(source_path, audio_path, start_seconds=0.0, duration_seconds=None)
        extraction_seconds = time.perf_counter() - extraction_started
        provider = provider_factory()
        asr_started = time.perf_counter()
        segments = provider.transcribe(audio_path, language=normalized_language, task="transcribe")
        asr_seconds = time.perf_counter() - asr_started
    except AutoSubsRuntimeError as exc:
        _write_error_log(run_directory, exc)
        raise SubtitleContentUnavailableError(f"{ASR_FAILED_MESSAGE} {exc}") from exc
    except Exception as exc:
        _write_error_log(run_directory, exc)
        raise SubtitleContentUnavailableError(ASR_FAILED_MESSAGE) from exc

    duration_ms = max(int(source_duration_seconds * 1000), 1)
    cues = []
    for segment in segments:
        text = str(segment.text or "")
        start_ms = max(0, int(round(float(segment.start) * 1000)))
        end_ms = min(duration_ms, int(round(float(segment.end) * 1000)))
        if not text.strip() or end_ms <= start_ms:
            continue
        cues.append({"cue_id": f"ASR_{len(cues) + 1:05d}", "start_ms": start_ms, "end_ms": end_ms, "text": text})
    if not cues:
        _write_json(log_path, {"status": "FAIL", "reason": "zero_usable_segments", "provider": ASR_PROVIDER_NAME})
        raise SubtitleContentUnavailableError(NO_SPEECH_MESSAGE)

    provider_metadata = getattr(provider, "last_metadata", {}) or {}
    detected_language = provider_metadata.get("language") or normalized_language or "unknown"
    metadata = {
        "asr_provider": ASR_PROVIDER_NAME,
        "asr_engine": "AutoSubs",
        "asr_engine_version": provider_metadata.get("engine_version"),
        "asr_model": provider_metadata.get("model", "small"),
        "asr_task": "transcribe",
        "source_language": detected_language,
        "subtitle_language": detected_language,
        "requested_target_language": target_language,
        "audio_filename": audio_path.name,
        "audio_sha256": sha256_file(audio_path),
        "audio_duration_seconds": source_duration_seconds,
        "audio_extraction_seconds": round(extraction_seconds, 3),
        "asr_processing_seconds": round(asr_seconds, 3),
        "segment_count": len(cues),
        "fallback_attempts": 0,
        "engine_translation": False,
        "forced_alignment": False,
    }
    track = create_local_transcription_track(run_id, cues=cues, metadata=metadata)
    _write_json(log_path, {"status": "PASS", **metadata, "cues": cues})
    return {"status": "PASS", "track_id": track["track_id"], "subtitle_provenance": "local_transcription", "metadata": metadata, "cues": cues}


def _write_error_log(run_directory: Path, exc: Exception) -> None:
    (run_directory / "logs").mkdir(parents=True, exist_ok=True)
    (run_directory / "logs" / "external_asr_error.log").write_text(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}", encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
