from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.providers.asr.base import ASRProvider
from app.providers.asr.faster_whisper_provider import FasterWhisperASRProvider
from app.services.audio import extract_asr_audio
from app.services.asr_models import (
    SIMPLE_UI_MODEL_DIRECTORY,
    SIMPLE_UI_MODEL_ID,
    SIMPLE_UI_MODEL_NAME,
    SIMPLE_UI_MODEL_POLICY,
    SIMPLE_UI_MODEL_SOURCE,
    model_directory_name,
    normalize_simple_ui_model_name,
    resolve_simple_ui_model_path,
)
from app.services.subtitle_tracks import (
    SubtitleContentUnavailableError,
    active_track_provenance,
    create_local_transcription_track,
)


MODEL_DEFAULT_NAME = SIMPLE_UI_MODEL_NAME
MODEL_ID = SIMPLE_UI_MODEL_ID
MODEL_SNAPSHOT = "bundled"
MODEL_LICENSE = "MIT"
MODEL_REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
MODEL_DIRECTORY_NAME = SIMPLE_UI_MODEL_DIRECTORY
ASR_PROVIDER_NAME = "faster_whisper"
ASR_DEVICE = "cpu"
ASR_COMPUTE_TYPE = "int8"

MODEL_MISSING_MESSAGE = (
    "Không thể tải mô hình phiên âm chất lượng đã cài đặt. Video chưa được xuất."
)
ASR_FAILED_MESSAGE = (
    "Không thể nhận dạng lời nói bằng mô hình ngoại tuyến. "
    "Video chưa được xuất để tránh tạo kết quả sai."
)
NO_SPEECH_MESSAGE = (
    "Không phát hiện được lời nói để tạo phụ đề. "
    "Video chưa được xuất để tránh tạo kết quả sai."
)


def resolve_local_model_path(model_name: str | None = None) -> Path:
    try:
        normalize_simple_ui_model_name(model_name)
        return resolve_simple_ui_model_path()
    except FileNotFoundError as exc:
        raise SubtitleContentUnavailableError(MODEL_MISSING_MESSAGE) from exc


def create_local_asr_provider(model_path: Path) -> ASRProvider:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return FasterWhisperASRProvider(
        model_name=model_path,
        device=ASR_DEVICE,
        compute_type=ASR_COMPUTE_TYPE,
        local_files_only=True,
    )


def ensure_local_transcription_track(
    run_id: str,
    *,
    source_path: Path,
    run_directory: Path,
    source_duration_seconds: float,
    target_language: str,
    source_language: str | None = None,
    model_name: str | None = None,
    model_path: Path | None = None,
    provider_factory: Callable[[Path], ASRProvider] = create_local_asr_provider,
) -> dict[str, Any]:
    existing = active_track_provenance(run_id)
    if existing in {"user_import", "user_authored", "local_transcription", "provider_transcription"}:
        return {"status": "SKIPPED", "subtitle_provenance": existing}

    audio_path = run_directory / "work" / "source_asr_16khz_mono.wav"
    log_path = run_directory / "logs" / "local_asr.json"
    try:
        resolved_model_name = normalize_simple_ui_model_name(model_name)
        if model_path is None:
            try:
                model_path = resolve_local_model_path(resolved_model_name)
            except TypeError:
                model_path = resolve_local_model_path()
        model_path = Path(model_path).resolve()
        _write_json(
            log_path,
            {
                "status": "PREPARED",
                "asr_provider": ASR_PROVIDER_NAME,
                "asr_model_name": resolved_model_name,
                "asr_model": SIMPLE_UI_MODEL_ID,
                "asr_model_path": str(model_path),
                "asr_model_source": SIMPLE_UI_MODEL_SOURCE,
                "asr_model_policy": SIMPLE_UI_MODEL_POLICY,
                "asr_local_files_only": True,
                "network_attempts": 0,
                "fallback_attempts": 0,
                "implicit_download": False,
            },
        )
        extraction_started = time.perf_counter()
        extract_asr_audio(source_path, audio_path, start_seconds=0.0, duration_seconds=None)
        extraction_seconds = time.perf_counter() - extraction_started
        model_load_started = time.perf_counter()
        try:
            provider = provider_factory(model_path)
        except Exception as exc:
            raise SubtitleContentUnavailableError(MODEL_MISSING_MESSAGE) from exc
        model_load_seconds = time.perf_counter() - model_load_started
        _assert_loaded_model(provider, model_path)
        # The local-transcription track is the source track.  Its semantic
        # operation must not depend on the requested display/translation
        # target: Whisper translation belongs to a distinct translation track.
        task = "transcribe"
        normalized_language = source_language
        if not normalized_language or str(normalized_language).strip().lower() == "auto":
            normalized_language = None
        asr_started = time.perf_counter()
        segments = provider.transcribe(audio_path, language=normalized_language, task=task)
        asr_seconds = time.perf_counter() - asr_started
        _assert_loaded_model(provider, model_path)
    except SubtitleContentUnavailableError:
        raise
    except Exception as exc:
        _write_error_log(run_directory, exc)
        raise SubtitleContentUnavailableError(ASR_FAILED_MESSAGE) from exc

    duration_ms = max(int(source_duration_seconds * 1000), 1)
    cues = []
    for segment in segments:
        text = str(segment.text or "").strip()
        start_ms = max(0, int(round(float(segment.start) * 1000)))
        end_ms = min(duration_ms, int(round(float(segment.end) * 1000)))
        if not text or end_ms <= start_ms:
            continue
        cues.append(
            {
                "cue_id": f"ASR_{len(cues) + 1:05d}",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
            }
        )
    if not cues:
        _write_json(
            log_path,
            {
                "status": "FAIL",
                "reason": "zero_usable_segments",
                "raw_segment_count": len(segments),
                "provider": ASR_PROVIDER_NAME,
                "model": MODEL_ID,
            },
        )
        raise SubtitleContentUnavailableError(NO_SPEECH_MESSAGE)

    provider_metadata = getattr(provider, "last_metadata", {}) or {}
    detected_language = provider_metadata.get("language") or normalized_language or "unknown"
    snapshot_name = None
    model_path_obj = Path(model_path)
    if "snapshots" in model_path_obj.parts:
        snapshot_name = model_path_obj.name
    metadata_path = model_path_obj / "MODEL_METADATA.json"
    if metadata_path.is_file():
        try:
            snapshot_name = json.loads(metadata_path.read_text(encoding="utf-8")).get("snapshot") or snapshot_name
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    metadata = {
        "asr_provider": ASR_PROVIDER_NAME,
        "asr_model_name": resolved_model_name,
        "asr_model": SIMPLE_UI_MODEL_ID,
        "asr_model_directory": model_directory_name(resolved_model_name),
        "asr_model_snapshot": snapshot_name,
        "asr_model_path": str(model_path),
        "asr_loaded_model_path": str(model_path),
        "asr_model_license": MODEL_LICENSE,
        "asr_device": ASR_DEVICE,
        "asr_compute_type": ASR_COMPUTE_TYPE,
        "asr_local_files_only": True,
        "asr_model_source": SIMPLE_UI_MODEL_SOURCE,
        "asr_model_policy": SIMPLE_UI_MODEL_POLICY,
        "fallback_attempts": 0,
        "network_attempts": 0,
        "implicit_download": False,
        "source_language": detected_language,
        "subtitle_language": detected_language,
        "requested_target_language": target_language,
        "asr_task": task,
        "audio_filename": audio_path.name,
        "audio_sha256": sha256_file(audio_path),
        "audio_duration_seconds": source_duration_seconds,
        "audio_extraction_seconds": round(extraction_seconds, 3),
        "model_load_seconds": round(model_load_seconds, 3),
        "asr_processing_seconds": round(asr_seconds, 3),
        "segment_count": len(cues),
        "network_mode": "offline",
    }
    track = create_local_transcription_track(run_id, cues=cues, metadata=metadata)
    _write_json(
        log_path,
        {
            "status": "PASS",
            **metadata,
            "cues": cues,
        },
    )
    return {
        "status": "PASS",
        "track_id": track["track_id"],
        "subtitle_provenance": "local_transcription",
        "metadata": metadata,
        "cues": cues,
    }


def _assert_loaded_model(provider: ASRProvider, expected_path: Path) -> None:
    provider_metadata = getattr(provider, "last_metadata", {}) or {}
    reported = provider_metadata.get("model") or getattr(provider, "model_name", None)
    if not reported:
        return
    try:
        matches = os.path.normcase(str(Path(str(reported)).resolve())) == os.path.normcase(str(expected_path.resolve()))
    except (OSError, ValueError):
        matches = False
    if not matches:
        raise SubtitleContentUnavailableError(MODEL_MISSING_MESSAGE)


def _write_error_log(run_directory: Path, exc: Exception) -> None:
    (run_directory / "logs").mkdir(parents=True, exist_ok=True)
    (run_directory / "logs" / "local_asr_error.log").write_text(
        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        encoding="utf-8",
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
