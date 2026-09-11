from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

from app.core.hashing import sha256_file
from app.providers.tts.kokoro_local import KokoroLocalTTSProvider
from app.services.kokoro_local import KokoroPersistentWorkerSession
from app.services.localization_run_config import MAX_KOKORO_SPEED
from app.services.tts_generation import generate_tts_for_unit
from app.services.tts_units import attach_tts_synthesis_units


class NarrationFitError(ValueError):
    def __init__(self, failures: list[dict[str, Any]]) -> None:
        self.failures = failures
        super().__init__(f"Narration timing fit failed for {len(failures)} synthesis units")


MAX_NARRATION_SLACK_SHIFT_MS = 750


def _unit_narration_start_ms(unit: dict[str, Any]) -> int:
    return int(unit.get("narration_start_ms", unit.get("start_ms") or 0))


def _generation_duration_ms(generation: dict[str, Any]) -> float:
    alignment = generation.get("alignment") if isinstance(generation.get("alignment"), dict) else {}
    artifact_path = Path(str(generation.get("artifact_path") or ""))
    if artifact_path.is_file():
        return _wav_duration_ms(artifact_path)
    return float(alignment.get("duration_seconds")) * 1000.0


def allocate_narration_timing_slack(
    timeline: dict[str, Any],
    generations: list[dict[str, Any]],
    *,
    max_shift_ms: int = MAX_NARRATION_SLACK_SHIFT_MS,
) -> list[dict[str, Any]]:
    """Consume existing inter-unit silence before rewriting spoken content.

    Source cue timing remains immutable in ``start_ms``/``end_ms``.  Only the
    narration placement anchor may move earlier, never beyond the previous
    narration audio end and never more than the bounded shift.  This lets a
    slightly long later unit borrow already-silent time without truncation,
    overlap, or speeding beyond the selected policy.
    """
    units = sorted(list(timeline.get("tts_units") or []), key=lambda item: (int(item["start_ms"]), str(item["id"])))
    by_generation = {item["generation_id"]: item for item in generations}
    durations: list[float] = []
    for unit in units:
        generation = by_generation.get(unit.get("active_tts_generation_id"))
        if generation is None:
            durations.append(0.0)
            continue
        durations.append(_generation_duration_ms(generation))

    adjustments: list[dict[str, Any]] = []

    def shift_earlier(index: int, requested_ms: float) -> float:
        if requested_ms <= 0 or index < 0:
            return 0.0
        unit = units[index]
        original_start = int(unit["start_ms"])
        current_start = _unit_narration_start_ms(unit)
        max_by_policy = max(0.0, float(max_shift_ms) - float(original_start - current_start))
        if max_by_policy <= 0:
            return 0.0
        if index == 0:
            lower_bound = 0.0
        else:
            previous = units[index - 1]
            previous_start = float(_unit_narration_start_ms(previous))
            lower_bound = previous_start + durations[index - 1]
        direct_capacity = max(0.0, float(current_start) - lower_bound)
        direct = min(float(requested_ms), direct_capacity, max_by_policy)
        shifted = direct
        if direct > 0:
            unit["narration_start_ms"] = int(round(float(current_start) - direct))
        remaining = float(requested_ms) - shifted
        if remaining > 0 and index > 0 and max_by_policy - shifted > 0:
            prior_shift = shift_earlier(index - 1, min(remaining, max_by_policy - shifted))
            if prior_shift > 0:
                current_start = _unit_narration_start_ms(unit)
                previous = units[index - 1]
                lower_bound = float(_unit_narration_start_ms(previous)) + durations[index - 1]
                new_capacity = max(0.0, float(current_start) - lower_bound)
                extra = min(remaining, new_capacity, max_by_policy - shifted)
                if extra > 0:
                    unit["narration_start_ms"] = int(round(float(current_start) - extra))
                    shifted += extra
        return shifted

    source_duration_ms = int(timeline.get("source_duration_ms") or 0)
    for index in range(len(units) - 1, -1, -1):
        unit = units[index]
        start_ms = _unit_narration_start_ms(unit)
        available_end_ms = _unit_narration_start_ms(units[index + 1]) if index + 1 < len(units) else source_duration_ms
        overflow = max(0.0, durations[index] - max(0.0, float(available_end_ms - start_ms)))
        if overflow <= 0:
            continue
        shifted = shift_earlier(index, overflow)
        if shifted > 0:
            adjustments.append({
                "tts_unit_id": unit["id"],
                "requested_shift_ms": round(overflow, 3),
                "applied_shift_ms": round(shifted, 3),
                "source_start_ms": int(unit["start_ms"]),
                "narration_start_ms": _unit_narration_start_ms(unit),
            })

    return adjustments


def build_narration_timeline(resolved: dict[str, Any], *, source_duration_ms: int) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    for index, cue in enumerate(list(resolved.get("cues") or [])):
        text = str(cue.get("spoken_text") or cue.get("resolved_text") or cue.get("translation_text") or cue.get("text") or "").strip()
        if not text:
            continue
        start_ms = int(cue.get("start_ms") or 0)
        end_ms = int(cue.get("end_ms") or 0)
        if start_ms < 0 or end_ms <= start_ms:
            continue
        cue_id = str(cue.get("cue_id") or cue.get("id") or f"cue_{index + 1:04d}")
        segments.append({
            "id": cue_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "spoken_text": text,
            "subtitle_text": str(cue.get("subtitle_text") or cue.get("resolved_text") or cue.get("translation_text") or text).strip(),
            "translated_text": str(cue.get("resolved_text") or cue.get("translation_text") or text).strip(),
            "enabled": True,
            "status": "ready",
            "speaker_id": str(cue.get("speaker_id") or "narrator"),
            "voice_profile_id": cue.get("voice_profile_id"),
            "timing_policy": "fit_slot",
            "locks": {},
        })
    return {"source_duration_ms": int(source_duration_ms), "segments": segments}


def evaluate_narration_fit(timeline: dict[str, Any], generations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_generation = {item["generation_id"]: item for item in generations}
    units = list(timeline.get("tts_units") or [])
    ordered = sorted(units, key=lambda item: (int(item["start_ms"]), int(item["end_ms"]), str(item["id"])))
    source_duration_ms = int(timeline.get("source_duration_ms") or 0)
    results: list[dict[str, Any]] = []
    for index, unit in enumerate(ordered):
        generation_id = unit.get("active_tts_generation_id")
        generation = by_generation.get(generation_id)
        if generation is None:
            results.append({"tts_unit_id": unit["id"], "status": "FAIL", "code": "generation_missing"})
            continue
        alignment = generation.get("alignment") if isinstance(generation.get("alignment"), dict) else {}
        artifact_path = Path(str(generation.get("artifact_path") or ""))
        # The PCM artifact is the authoritative duration because it is what the
        # mixer actually writes. Alignment metadata can differ by a few frames;
        # treating that estimate as authoritative can report PASS and then fail
        # deterministically while composing the WAV.
        if artifact_path.is_file():
            duration_ms = _wav_duration_ms(artifact_path)
            duration_source = "wav_pcm"
        else:
            try:
                duration_ms = float(alignment.get("duration_seconds")) * 1000.0
                duration_source = "alignment_metadata"
            except (TypeError, ValueError):
                results.append({"tts_unit_id": unit["id"], "status": "FAIL", "code": "duration_unavailable"})
                continue
        narration_start_ms = _unit_narration_start_ms(unit)
        available_end_ms = _unit_narration_start_ms(ordered[index + 1]) if index + 1 < len(ordered) else source_duration_ms
        available_ms = max(0, available_end_ms - narration_start_ms)
        overflow_ms = max(0.0, duration_ms - available_ms)
        results.append({
            "tts_unit_id": unit["id"],
            "generation_id": generation_id,
            "start_ms": narration_start_ms,
            "source_start_ms": int(unit["start_ms"]),
            "nominal_end_ms": int(unit["end_ms"]),
            "available_end_ms": available_end_ms,
            "available_ms": available_ms,
            "tts_duration_ms": round(duration_ms, 3),
            "duration_source": duration_source,
            "overflow_ms": round(overflow_ms, 3),
            "fit_ratio": round(duration_ms / max(available_ms, 1), 6),
            # Any real PCM overflow can collide with the next unit or exceed the
            # source duration. The retry path already applies bounded speed-up,
            # so do not hide up to 30 ms and fail later in write_narration_mix.
            "status": "PASS" if overflow_ms <= 0.0 else "FAIL",
        })
    return results


def _wav_duration_ms(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        if audio.getnframes() <= 0 or audio.getframerate() <= 0:
            raise ValueError("Narration WAV is empty")
        return audio.getnframes() * 1000.0 / audio.getframerate()


def _read_kokoro_pcm(path: Path, *, expected_sample_rate: int = 24_000) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as audio:
        channels = audio.getnchannels()
        width = audio.getsampwidth()
        rate = audio.getframerate()
        frames = audio.getnframes()
        if (channels, width, rate) != (1, 2, expected_sample_rate) or frames <= 0:
            raise ValueError("Narration artifact must be mono 16-bit PCM at the Kokoro sample rate")
        return audio.readframes(frames), frames


def write_narration_mix(
    timeline: dict[str, Any],
    generations: list[dict[str, Any]],
    output_path: Path,
    *,
    sample_rate: int = 24_000,
) -> Path:
    fit = evaluate_narration_fit(timeline, generations)
    failures = [item for item in fit if item.get("status") != "PASS"]
    if failures:
        raise NarrationFitError(failures)

    by_generation = {item["generation_id"]: item for item in generations}
    units = sorted(list(timeline.get("tts_units") or []), key=lambda item: int(item["start_ms"]))
    total_frames = round(int(timeline.get("source_duration_ms") or 0) * sample_rate / 1000)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    cursor = 0
    with wave.open(str(temporary), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        for unit in units:
            generation = by_generation.get(unit.get("active_tts_generation_id"))
            if generation is None:
                raise NarrationFitError([{"tts_unit_id": unit["id"], "status": "FAIL", "code": "generation_missing"}])
            start_frame = round(_unit_narration_start_ms(unit) * sample_rate / 1000)
            if start_frame < cursor:
                raise NarrationFitError([{"tts_unit_id": unit["id"], "status": "FAIL", "code": "audio_overlap"}])
            _write_silence(output, start_frame - cursor)
            pcm, frame_count = _read_kokoro_pcm(Path(generation["artifact_path"]), expected_sample_rate=sample_rate)
            output.writeframes(pcm)
            cursor = start_frame + frame_count
        if cursor > total_frames:
            raise NarrationFitError([{"tts_unit_id": units[-1]["id"] if units else None, "status": "FAIL", "code": "audio_exceeds_source_duration"}])
        _write_silence(output, total_frames - cursor)
    temporary.replace(output_path)
    return output_path


def _write_silence(output: wave.Wave_write, frame_count: int) -> None:
    remaining = max(0, int(frame_count))
    chunk_frames = 24_000
    silence = b"\x00\x00" * chunk_frames
    while remaining:
        count = min(remaining, chunk_frames)
        output.writeframes(silence[: count * 2])
        remaining -= count


def retime_resolved_cues_to_narration(
    resolved: dict[str, Any],
    timeline: dict[str, Any],
    generations: list[dict[str, Any]],
) -> dict[str, Any]:
    by_generation = {item["generation_id"]: item for item in generations}
    timing_by_segment: dict[str, tuple[int, int]] = {}
    for unit in list(timeline.get("tts_units") or []):
        generation = by_generation.get(unit.get("active_tts_generation_id"))
        alignment = generation.get("alignment") if isinstance((generation or {}).get("alignment"), dict) else None
        if not alignment:
            raise ValueError(f"Narration alignment missing for {unit['id']}")
        tokens = [token for token in list(alignment.get("tokens") or []) if isinstance(token, dict)]
        for span in list(unit.get("segment_text_spans") or []):
            source_start = int(span["source_start"])
            source_end = int(span["source_end"])
            matching = [
                token for token in tokens
                if int(token.get("source_end") or 0) > source_start and int(token.get("source_start") or 0) < source_end
            ]
            if not matching:
                continue
            narration_start_ms = _unit_narration_start_ms(unit)
            start_ms = narration_start_ms + round(float(matching[0]["start"]) * 1000)
            end_ms = narration_start_ms + round(float(matching[-1]["end"]) * 1000)
            if end_ms > start_ms:
                timing_by_segment[str(span["segment_id"])] = (start_ms, end_ms)

    updated = []
    for index, cue in enumerate(list(resolved.get("cues") or [])):
        cue_id = str(cue.get("cue_id") or cue.get("id") or f"cue_{index + 1:04d}")
        copy = dict(cue)
        timing = timing_by_segment.get(cue_id)
        if timing:
            copy["source_start_ms"] = int(cue.get("start_ms") or 0)
            copy["source_end_ms"] = int(cue.get("end_ms") or 0)
            copy["narration_token_start_ms"], copy["narration_token_end_ms"] = timing
            copy["start_ms"], copy["end_ms"] = timing
            copy["timing_provenance"] = "kokoro_token_alignment"
        updated.append(copy)
    return {**resolved, "cues": updated, "narration_timing": {"aligned_cue_count": len(timing_by_segment)}}


def generate_kokoro_narration(
    *,
    project_id: str,
    resolved: dict[str, Any],
    source_duration_ms: int,
    voice_id: str,
    base_speed: float,
    run_directory: Path,
) -> dict[str, Any]:
    with KokoroPersistentWorkerSession() as session:
        provider = KokoroLocalTTSProvider(session=session)
        return _generate_kokoro_narration_with_provider(
            project_id=project_id,
            resolved=resolved,
            source_duration_ms=source_duration_ms,
            voice_id=voice_id,
            base_speed=base_speed,
            run_directory=run_directory,
            provider=provider,
        )


def _generate_kokoro_narration_with_provider(
    *,
    project_id: str,
    resolved: dict[str, Any],
    source_duration_ms: int,
    voice_id: str,
    base_speed: float,
    run_directory: Path,
    provider: KokoroLocalTTSProvider,
) -> dict[str, Any]:
    if not provider.validate_credentials():
        raise ValueError("Kokoro runtime is not ready")
    timeline = build_narration_timeline(resolved, source_duration_ms=source_duration_ms)
    units = attach_tts_synthesis_units(timeline)
    generations: list[dict[str, Any]] = []
    for unit in units:
        generation = generate_tts_for_unit(
            project_id,
            unit,
            provider,
            voice_id,
            speed=base_speed,
            provider_request_version=provider.provider_request_version,
        )
        if generation.get("status") != "ready" or not generation.get("alignment"):
            raise ValueError(f"Kokoro narration generation is not alignment-ready for {unit['id']}")
        generations.append(generation)

    # Fit against the real PCM artifacts and retry only failing units. Kokoro's
    # duration response is not perfectly linear with the requested speed, so one
    # ratio-derived retry can still miss the slot by a few frames/milliseconds.
    # Iterate a small bounded number of times rather than either truncating audio
    # or jumping straight to the maximum speed.
    generation_by_unit = {unit["id"]: generation for unit, generation in zip(units, generations)}
    speed_by_unit = {unit["id"]: float(base_speed) for unit in units}
    first_fit = evaluate_narration_fit(timeline, generations)
    current_fit = first_fit
    for _attempt in range(3):
        failed_by_unit = {item["tts_unit_id"]: item for item in current_fit if item.get("status") != "PASS"}
        if not failed_by_unit:
            break
        progressed = False
        for unit in units:
            failure = failed_by_unit.get(unit["id"])
            if not failure:
                continue
            current_speed = speed_by_unit[unit["id"]]
            required_multiplier = max(1.0, float(failure.get("fit_ratio") or 1.0)) * 1.01
            retry_speed = min(
                MAX_KOKORO_SPEED,
                round(max(current_speed + 0.01, current_speed * required_multiplier), 3),
            )
            if retry_speed <= current_speed + 0.0005:
                continue
            generation = generate_tts_for_unit(
                project_id,
                unit,
                provider,
                voice_id,
                speed=retry_speed,
                provider_request_version=provider.provider_request_version,
            )
            if generation.get("status") == "ready" and generation.get("alignment"):
                generation_by_unit[unit["id"]] = generation
                speed_by_unit[unit["id"]] = retry_speed
                progressed = True
        generations = [generation_by_unit[unit["id"]] for unit in units]
        current_fit = evaluate_narration_fit(timeline, generations)
        if not progressed:
            break

    timing_adjustments = allocate_narration_timing_slack(timeline, generations)
    final_fit = evaluate_narration_fit(timeline, generations)
    failures = [item for item in final_fit if item.get("status") != "PASS"]
    if failures:
        raise NarrationFitError(failures)

    audio_dir = run_directory / "audio"
    mix_path = write_narration_mix(timeline, generations, audio_dir / "narration_mix.wav")
    retimed = retime_resolved_cues_to_narration(resolved, timeline, generations)
    expected_retimed = len(list(timeline.get("segments") or []))
    actual_retimed = int((retimed.get("narration_timing") or {}).get("aligned_cue_count") or 0)
    if actual_retimed != expected_retimed:
        raise NarrationFitError([
            {
                "status": "FAIL",
                "code": "token_alignment_incomplete",
                "expected_retimed_cue_count": expected_retimed,
                "retimed_cue_count": actual_retimed,
            }
        ])
    manifest = {
        "schema_version": 1,
        "status": "PASS",
        "provider": provider.provider_name,
        "model": provider.model,
        "voice_id": voice_id,
        "base_speed": float(base_speed),
        "effective_unit_speeds": {unit_id: speed for unit_id, speed in speed_by_unit.items()},
        "tts_unit_count": len(units),
        "generation_count": len(generations),
        "fit": final_fit,
        "timing_adjustments": timing_adjustments,
        "mix_path": str(mix_path.resolve()),
        "mix_sha256": sha256_file(mix_path),
        "expected_retimed_cue_count": expected_retimed,
        "retimed_cue_count": actual_retimed,
        "generations": [
            {
                "generation_id": item.get("generation_id"),
                "request_hash": item.get("request_hash"),
                "sha256": item.get("sha256"),
                "artifact_path": item.get("artifact_path"),
                "cache_status": item.get("cache_status"),
                "alignment_path": (item.get("alignment") or {}).get("path") if isinstance(item.get("alignment"), dict) else None,
            }
            for item in generations
        ],
    }
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "narration_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"timeline": timeline, "generations": generations, "fit": final_fit, "mix_path": mix_path, "resolved": retimed, "manifest": manifest}
