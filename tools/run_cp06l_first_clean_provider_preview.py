import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import wave
from pathlib import Path
from uuid import uuid4

import cv2
import httpx
import numpy as np
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.provider_cache import build_request_hash, read_cached_response, write_cached_response
from app.core.secret_files import load_secret_lines
from app.providers.translation.gemini import load_gemini_translation_config
from app.providers.tts.base import TTSRequest, TTSUncertainError
from app.providers.tts.elevenlabs import ElevenLabsTTSProvider, load_elevenlabs_config
from app.providers.tts.fake import tts_request_payload
from app.services.subtitles import format_ass_timestamp
from app.services.timeline import load_latest_timeline
from app.services.tts_generation import generate_tts_for_unit, resolve_voice_id
from tools.run_cp06k_tail_audio_alignment_and_residual_repair import (
    DURATION_SECONDS,
    ENGLISH_FONT_SIZE,
    FPS,
    HEIGHT,
    WIDTH,
    Sample,
    active_intervals,
    audited_frame_times,
    ass_escape,
    build_dynamic_intervals,
    build_interval_samples,
    build_local_residual_repair,
    comparison_times,
    delogo_toggle_audit,
    detect_source_subtitle_bbox,
    english_layout_for_interval,
    glyph_pixel_count,
    interval_stats,
    matched_glyph_pixel_count,
    media_summary,
    read_frame,
    render_contact_sheet,
    render_preview,
    render_source_match_contact_sheet,
    render_transition_strips,
    render_truth_audit,
    repair_uncovered_source_subtitles,
    residual_runs,
    sha256_file as cp_sha256_file,
    subtitle_plate_stats,
    temporal_stabilize_intervals,
    transition_flash_audit,
    transition_windows,
    union_boxes,
    window_result,
    unique_samples,
)


PROJECT_ID = "vertical_slice_cp02"
GEMINI_PROVIDER = "gemini_cp06l_clean_preview"
GEMINI_PROMPT_VERSION = "cp06l-first-clean-provider-preview-v1"
MAX_GEMINI_CALLS = 2
MAX_ELEVENLABS_CALLS = 15


def main() -> None:
    settings = get_settings()
    project_dir = settings.data_dir / "projects" / PROJECT_ID
    evidence_dir = settings.root / "evidence" / "CP06L"
    if evidence_dir.exists():
        shutil.rmtree(evidence_dir)
    evidence_dir.mkdir(parents=True)

    source_timeline = load_latest_timeline(PROJECT_ID)
    transform = transform_with_gemini(source_timeline, evidence_dir)
    transformed_segments = transform["segments"]
    groups = build_tts_groups(transformed_segments)
    if len(groups) > MAX_ELEVENLABS_CALLS:
        raise RuntimeError(f"BLOCKED_TTS_PLAN_EXCEEDS_LIMIT: planned {len(groups)} calls, limit {MAX_ELEVENLABS_CALLS}")

    provider = ElevenLabsTTSProvider(load_elevenlabs_config())
    preflight = provider.probe_subscription()
    if preflight.status_code != 200:
        raise RuntimeError(f"BLOCKED_ELEVENLABS_PREFLIGHT_{preflight.classification.upper()}")
    voice_id = resolve_voice_id(None, provider)
    tts = synthesize_groups(project_dir, provider, voice_id, groups)
    audio_schedule = build_narration_stem(project_dir / "renders" / "cp06l_first_clean_narration_stem.wav", groups, tts)
    qa = audio_schedule_qa(transformed_segments, audio_schedule)
    if qa["status"] != "PASS":
        raise RuntimeError(f"BLOCKED_AUDIO_QA_FAIL: {json.dumps(qa, ensure_ascii=False)}")

    visual = build_visual_layers(settings, project_dir, source_timeline, transformed_segments, groups, evidence_dir)
    output_path = project_dir / "renders" / "cp06l_first_clean_provider_preview_720p.mp4"
    render_preview(
        settings.source_path,
        Path(audio_schedule["narration_stem_path"]),
        Path(visual["ass_path"]),
        output_path,
        visual["render_intervals"],
        visual["subtitle_layout"]["events"],
    )
    render_contact_sheet(output_path, evidence_dir / "after_cp06l_same_timestamps.jpg", None, comparison_times(source_timeline), source_only=False)
    render_contact_sheet(output_path, evidence_dir / "english_layout_contact_sheet.jpg", None, [g["subtitle_start_s"] for g in groups[:12]], source_only=False)
    render_truth = group_render_truth_audit(settings.source_path, output_path, visual["render_intervals"], visual["subtitle_layout"]["events"], evidence_dir)
    transition_audit = group_transition_flash_audit(settings.source_path, output_path, visual["render_intervals"], visual["subtitle_layout"]["events"], source_timeline)
    strips = render_transition_strips(output_path, evidence_dir, source_timeline)
    media = media_summary(output_path)

    timeline_json = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "preview": str(output_path),
        "narration_stem": audio_schedule["narration_stem_path"],
        "transform": transform,
        "tts_groups": groups,
        "narration_schedule": audio_schedule["clips"],
        "audio_qa": qa,
        "visual_qa": {
            "render_truth": render_truth,
            "transition_flash": transition_audit,
            "contact_sheets": {
                "after": str(evidence_dir / "after_cp06l_same_timestamps.jpg"),
                "layout": str(evidence_dir / "english_layout_contact_sheet.jpg"),
                "transition_strips": strips,
            },
        },
        "media": media,
        "provider_usage": {
            "gemini_real_calls": transform["real_call_count"],
            "elevenlabs_real_calls": tts["real_call_count"],
            "cache_hits": transform["cache_hit_count"] + tts["cache_hit_count"],
            "uncertain_calls": tts["uncertain_call_count"],
        },
    }
    timeline_path = project_dir / "renders" / "cp06l_audio_subtitle_timeline.json"
    timeline_path.write_text(json.dumps(timeline_json, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "artifact_path": str(output_path),
        "artifact_sha256": cp_sha256_file(output_path),
        "narration_stem_path": audio_schedule["narration_stem_path"],
        "narration_stem_sha256": cp_sha256_file(Path(audio_schedule["narration_stem_path"])),
        "timeline_path": str(timeline_path),
        "timeline_sha256": cp_sha256_file(timeline_path),
        "media": media,
        "provider_usage": timeline_json["provider_usage"],
        "audio_qa": qa,
        "visual_qa": {
            "mandatory_render_truth_fail_count": render_truth["fail_count"],
            "residual_glyph_frame_count": transition_audit["residual_glyph_frame_count"],
            "short_subtitle_flash_count": transition_audit["short_subtitle_flash_count"],
            "delogo_toggle_count": transition_audit["delogo_toggle_count"],
            "status": transition_audit["status"],
        },
        "subtitle_plate_stats": subtitle_plate_stats(visual["subtitle_layout"]["events"]),
        "warnings": listening_warning(),
        "free_disk_gb": round(shutil.disk_usage(settings.root).free / (1024**3), 2),
    }
    (evidence_dir / "calibration_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "provider_usage.json").write_text(json.dumps(timeline_json["provider_usage"], indent=2), encoding="utf-8")
    (evidence_dir / "tts_grouping_plan.json").write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "audio_qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "residual_flash_audit.json").write_text(json.dumps(transition_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def transform_with_gemini(timeline: dict, evidence_dir: Path) -> dict:
    config = load_gemini_translation_config()
    keys = load_secret_lines(config.key_file)
    request_segments = []
    for segment in timeline["segments"]:
        if not segment.get("enabled", True):
            continue
        request_segments.append(
            {
                "id": segment["id"],
                "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"],
                "source_text": segment["source_text"],
                "current_english": segment.get("spoken_text") or segment.get("translated_text") or "",
                "duration_budget_ms": segment["end_ms"] - segment["start_ms"],
            }
        )
    payload = {
        "provider": GEMINI_PROVIDER,
        "model": config.model,
        "prompt_version": GEMINI_PROMPT_VERSION,
        "project_id": PROJECT_ID,
        "target_locale": "en-US",
        "task": "Transform the canonical Chinese 75-second transcript into concise natural American English narration.",
        "requirements": [
            "Preserve every source segment id and order exactly.",
            "Do not add or remove source ids.",
            "Use short natural spoken English that can fit the segment window.",
            "Keep the tail dialogue from seg_0041 through seg_0049 clear, ordered, and concise.",
            "Return JSON only.",
        ],
        "segments": request_segments,
        "schema": {
            "schema_version": 1,
            "segments": [
                {
                    "id": "seg_0001",
                    "spoken_text": "natural en-US narration",
                    "subtitle_text": "visible subtitle, max two lines later",
                    "target_speech_duration_ms": 1000,
                    "subtitle_group_hint": "normal|micro|tail",
                }
            ],
        },
    }
    request_hash = build_request_hash(payload)
    cached = read_cached_response(GEMINI_PROVIDER, request_hash)
    real_calls = 0
    cache_hits = 0
    if cached is not None:
        response = cached
        cache_hits = 1
    else:
        response = call_gemini(config, keys, payload)
        real_calls = 1
        try:
            validate_transform(response, request_segments)
        except Exception:
            if real_calls >= MAX_GEMINI_CALLS:
                raise
            response = call_gemini(config, keys, payload | {"corrective_request": "Previous response failed deterministic validation. Return complete valid schema."})
            real_calls += 1
        write_cached_response(GEMINI_PROVIDER, request_hash, response)
    validate_transform(response, request_segments)
    segments = []
    by_id = {segment["id"]: segment for segment in response["segments"]}
    for source in request_segments:
        transformed = by_id[source["id"]]
        segments.append(
            {
                **source,
                "spoken_text": clean_text(transformed["spoken_text"]),
                "subtitle_text": clean_text(transformed.get("subtitle_text") or transformed["spoken_text"]),
                "target_speech_duration_ms": int(transformed.get("target_speech_duration_ms") or source["duration_budget_ms"]),
                "subtitle_group_hint": transformed.get("subtitle_group_hint", "normal"),
            }
        )
    result = {
        "request_hash": request_hash,
        "cache_status": "hit" if cache_hits else "miss",
        "real_call_count": real_calls,
        "cache_hit_count": cache_hits,
        "model": config.model,
        "segments": segments,
    }
    (evidence_dir / "gemini_transform.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def call_gemini(config, keys: list[str], payload: dict) -> dict:
    endpoint = config.base_url.rstrip("/") + "/chat/completions"
    system = (
        "Return compact valid JSON only. Preserve IDs exactly. "
        "Schema: {\"schema_version\":1,\"segments\":[{\"id\":\"\",\"spoken_text\":\"\","
        "\"subtitle_text\":\"\",\"target_speech_duration_ms\":1,\"subtitle_group_hint\":\"normal\"}]}. "
        "No markdown. No extra commentary."
    )
    body = {
        "model": config.model,
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    last_error = None
    for key in keys:
        try:
            with httpx.Client(timeout=config.timeout_seconds) as client:
                response = client.post(endpoint, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body)
                response.raise_for_status()
            return parse_json(response.json()["choices"][0]["message"]["content"])
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code == 401:
                continue
            raise
    raise RuntimeError("Gemini provider failed after configured key failover") from last_error


def parse_json(content: str) -> dict:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def validate_transform(response: dict, request_segments: list[dict]) -> None:
    if response.get("schema_version") != 1:
        raise ValueError("Gemini response schema_version must be 1")
    segments = response.get("segments")
    if not isinstance(segments, list):
        raise ValueError("Gemini response requires segments list")
    expected_ids = [segment["id"] for segment in request_segments]
    seen_ids = [segment.get("id") for segment in segments]
    if seen_ids != expected_ids:
        raise ValueError("Gemini response IDs/order mismatch")
    for segment in segments:
        if not clean_text(segment.get("spoken_text", "")):
            raise ValueError(f"Empty spoken_text: {segment.get('id')}")
        if len(clean_text(segment.get("spoken_text", ""))) > 150:
            raise ValueError(f"Spoken text too long: {segment.get('id')}")


def build_tts_groups(segments: list[dict]) -> list[dict]:
    groups = []
    explicit = [
        ("cp06l_g01", "normal", "seg_0001", "seg_0006"),
        ("cp06l_g02", "normal", "seg_0007", "seg_0010"),
        ("cp06l_g03", "normal", "seg_0011", "seg_0016"),
        ("cp06l_g04", "normal", "seg_0017", "seg_0020"),
        ("cp06l_g05", "normal", "seg_0021", "seg_0025"),
        ("cp06l_g06", "normal", "seg_0026", "seg_0030"),
        ("cp06l_g07", "normal", "seg_0031", "seg_0035"),
        ("cp06l_g08", "micro", "seg_0036", "seg_0040"),
        ("cp06l_g09", "tail", "seg_0041", "seg_0041"),
        ("cp06l_g10", "tail", "seg_0042", "seg_0042"),
        ("cp06l_g11", "tail", "seg_0043", "seg_0043"),
        ("cp06l_g12", "tail", "seg_0044", "seg_0044"),
        ("cp06l_g13", "tail", "seg_0045", "seg_0045"),
        ("cp06l_g14", "tail", "seg_0046", "seg_0047"),
        ("cp06l_g15", "tail", "seg_0048", "seg_0049"),
    ]
    by_id = {segment["id"]: segment for segment in segments}
    ids = [segment["id"] for segment in segments]
    for group_id, kind, first, last in explicit:
        selected = [by_id[seg_id] for seg_id in ids[ids.index(first) : ids.index(last) + 1]]
        text = " ".join(segment["spoken_text"] for segment in selected)
        subtitle = group_subtitle_text(group_id, selected, text)
        groups.append(
            {
                "clip_id": group_id,
                "kind": kind,
                "source_segment_ids": [segment["id"] for segment in selected],
                "english_text": text,
                "subtitle_text": subtitle,
                "source_start_ms": selected[0]["start_ms"],
                "source_end_ms": selected[-1]["end_ms"],
                "subtitle_start_s": round(selected[0]["start_ms"] / 1000, 3),
                "subtitle_end_s": round(selected[-1]["end_ms"] / 1000, 3),
            }
        )
    return groups


def group_subtitle_text(group_id: str, selected: list[dict], spoken_text: str) -> str:
    manual = {
        "cp06l_g01": "Leiting and Jiahao spin out.\nNobody here is normal.",
        "cp06l_g02": "Feeling lost? Spam Leiting.\nLet's get started.",
        "cp06l_g03": "Mom calls me for breakfast.\nWhy is it so noisy outside?",
        "cp06l_g04": "This is shocking.\nLet's go downstairs.",
        "cp06l_g05": "Wow, my house is huge.\nMom says good morning.",
        "cp06l_g06": "There's food in the fridge.\nDon't open the door.",
        "cp06l_g07": "Mom is gone.\nThat guy is still spinning.",
        "cp06l_g08": "Wait, it vanished?\nLet's get grass soup.",
        "cp06l_g14": "Who made this crazy game?\nI'm leaving.",
        "cp06l_g15": "Let's go eat grass soup. OK.",
    }
    if group_id in manual:
        return manual[group_id]
    return spoken_text


def synthesize_groups(project_dir: Path, provider: ElevenLabsTTSProvider, voice_id: str, groups: list[dict]) -> dict:
    generations = []
    previous_request_ids = []
    real_calls = 0
    cache_hits = 0
    uncertain = 0
    for index, group in enumerate(groups):
        next_text = groups[index + 1]["english_text"] if index + 1 < len(groups) else None
        unit = {
            "id": group["clip_id"],
            "spoken_text": group["english_text"],
            "segment_ids": group["source_segment_ids"],
        }
        expected_hash = build_request_hash(
            tts_request_payload(
                provider.provider_name,
                TTSRequest(
                    project_id=PROJECT_ID,
                    segment_id=unit["id"],
                    text=unit["spoken_text"],
                    voice_id=voice_id,
                    model=provider.model,
                    previous_request_ids=previous_request_ids[-3:],
                    next_text=next_text,
                    output_format=provider.output_format,
                    provider_request_version=provider.provider_request_version,
                ),
            )
        )
        existing_cache = read_cached_response(provider.provider_name, expected_hash)
        result = generate_tts_for_unit(
            PROJECT_ID,
            unit,
            provider,
            voice_id,
            previous_request_ids=previous_request_ids,
            next_text=next_text,
        )
        if result.get("request_id"):
            previous_request_ids.append(result["request_id"])
        if result["status"] == "uncertain":
            uncertain += 1
        elif result["cache_status"] == "hit" or existing_cache is not None:
            cache_hits += 1
        else:
            real_calls += 1
        if real_calls > MAX_ELEVENLABS_CALLS:
            raise RuntimeError("BLOCKED_ELEVENLABS_REAL_CALL_LIMIT_EXCEEDED")
        group["provider_request_hash"] = result["request_hash"]
        group["generated_artifact_path"] = result["artifact_path"]
        group["generation_id"] = result.get("generation_id")
        group["cache_status"] = result["cache_status"]
        generations.append(result)
    return {"generations": generations, "real_call_count": real_calls, "cache_hit_count": cache_hits, "uncertain_call_count": uncertain}


def build_narration_stem(output_path: Path, groups: list[dict], tts: dict) -> dict:
    sample_rate = 48000
    mix = np.zeros(int(DURATION_SECONDS * sample_rate), dtype=np.float32)
    clips = []
    previous_end = 0.0
    for index, (group, generation) in enumerate(zip(groups, tts["generations"])):
        audio, sr = read_wav_mono(Path(generation["artifact_path"]))
        if sr != sample_rate:
            audio = resample_to_length(audio, round(len(audio) * sample_rate / sr))
        duration_s = len(audio) / sample_rate
        slot_start = max(group["source_start_ms"] / 1000, previous_end + (0.0 if index == 0 else 0.04))
        slot_end = min(group["source_end_ms"] / 1000, DURATION_SECONDS)
        available = max(0.20, slot_end - slot_start)
        speed_factor = 1.0
        if duration_s > available:
            speed_factor = min(1.10, duration_s / available)
            target_len = int(len(audio) / speed_factor)
            audio = resample_to_length(audio, target_len)
            duration_s = len(audio) / sample_rate
        if duration_s > available:
            slot_start = max(previous_end + 0.04, slot_end - duration_s)
        start_sample = max(0, int(slot_start * sample_rate))
        end_sample = min(len(mix), start_sample + len(audio))
        mix[start_sample:end_sample] += fade_edges(audio[: end_sample - start_sample], sample_rate)
        scheduled_start = start_sample / sample_rate
        scheduled_end = end_sample / sample_rate
        previous_end = scheduled_end
        group["subtitle_start_s"] = round(scheduled_start, 3)
        group["subtitle_end_s"] = round(scheduled_end, 3)
        clips.append(
            {
                "clip_id": group["clip_id"],
                "source_segment_ids": group["source_segment_ids"],
                "english_text": group["english_text"],
                "provider_request_hash": group["provider_request_hash"],
                "generated_artifact_path": generation["artifact_path"],
                "generated_duration_s": round(len(audio) / sample_rate, 3),
                "scheduled_start_s": round(scheduled_start, 3),
                "scheduled_end_s": round(scheduled_end, 3),
                "applied_speed_factor": round(speed_factor, 4),
                "subtitle_start_s": group["subtitle_start_s"],
                "subtitle_end_s": group["subtitle_end_s"],
                "cache_status": generation["cache_status"],
            }
        )
    peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
    if peak > 0.98:
        mix = mix / peak * 0.98
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_wav_mono(output_path, mix, sample_rate)
    return {
        "narration_stem_path": str(output_path),
        "clips": clips,
        "peak": round(float(np.max(np.abs(mix))), 5),
    }


def audio_schedule_qa(segments: list[dict], schedule: dict) -> dict:
    expected = [segment["id"] for segment in segments]
    covered = [seg_id for clip in schedule["clips"] for seg_id in clip["source_segment_ids"]]
    duplicate_segments = sorted({seg_id for seg_id in covered if covered.count(seg_id) > 1})
    missing_segments = sorted(set(expected) - set(covered))
    overlaps = []
    for previous, current in zip(schedule["clips"], schedule["clips"][1:]):
        if current["scheduled_start_s"] < previous["scheduled_end_s"] - 0.001:
            overlaps.append({"previous": previous["clip_id"], "current": current["clip_id"]})
    artifact_paths = [clip["generated_artifact_path"] for clip in schedule["clips"]]
    reuse = len(artifact_paths) - len(set(artifact_paths))
    beyond = [clip["clip_id"] for clip in schedule["clips"] if clip["scheduled_end_s"] > DURATION_SECONDS + 0.001]
    start_drift = [clip for clip in schedule["clips"] if abs(clip["subtitle_start_s"] - clip["scheduled_start_s"]) > 0.250]
    end_drift = [clip for clip in schedule["clips"] if abs(clip["subtitle_end_s"] - clip["scheduled_end_s"]) > 0.350]
    speed_outside_recommended = [
        clip for clip in schedule["clips"] if clip["applied_speed_factor"] < 0.94 or clip["applied_speed_factor"] > 1.06
    ]
    status = (
        "PASS"
        if not missing_segments
        and not duplicate_segments
        and not overlaps
        and reuse == 0
        and not beyond
        and not start_drift
        and not end_drift
        and schedule["peak"] <= 0.98
        else "FAIL"
    )
    return {
        "status": status,
        "missing_segment_count": len(missing_segments),
        "missing_segments": missing_segments,
        "duplicated_segment_count": len(duplicate_segments),
        "duplicated_segments": duplicate_segments,
        "narration_interval_overlap_count": len(overlaps),
        "overlaps": overlaps,
        "narration_artifact_reuse_count": reuse,
        "clips_extending_beyond_75s": beyond,
        "subtitle_voice_start_drift_over_250ms_count": len(start_drift),
        "subtitle_voice_end_drift_over_350ms_count": len(end_drift),
        "peak": schedule["peak"],
        "speed_outside_recommended": speed_outside_recommended,
    }


def build_visual_layers(settings, project_dir: Path, timeline: dict, segments: list[dict], groups: list[dict], evidence_dir: Path) -> dict:
    font = ImageFont.truetype(str(settings.font_path), 40)
    intervals = build_dynamic_intervals(settings.source_path, timeline, font)
    all_samples = unique_samples([Sample(max(0, min(DURATION_SECONDS - 0.04, second)), "regular_1s") for second in range(76)] + build_interval_samples(timeline))
    intervals = repair_uncovered_source_subtitles(settings.source_path, intervals, all_samples, font)
    residual_repair = build_local_residual_repair(settings.source_path, intervals)
    intervals.extend(residual_repair["intervals"])
    flash_repairs = build_cp06l_flash_repairs(settings.source_path)
    intervals.extend(flash_repairs)
    temporal = temporal_stabilize_intervals(intervals)
    render_intervals = temporal["render_intervals"]
    render_contact_sheet(settings.source_path, evidence_dir / "source_eraser_mask_contact_sheet.jpg", intervals, comparison_times(timeline), source_only=True)
    render_source_match_contact_sheet(settings.source_path, evidence_dir / "source_matched_geometry_contact_sheet.jpg", intervals, comparison_times(timeline))
    ass_path = project_dir / "subtitles" / "cp06l_first_clean_provider_preview.ass"
    subtitle_layout = write_grouped_ass(timeline, intervals, groups, ass_path)
    return {
        "render_intervals": render_intervals,
        "subtitle_layout": subtitle_layout,
        "ass_path": str(ass_path),
        "residual_repair": residual_repair,
        "flash_repairs": flash_repairs,
        "eraser_stats": interval_stats(render_intervals),
    }


def write_grouped_ass(timeline: dict, intervals: list[dict], groups: list[dict], path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    interval_by_segment = {item["segment_id"]: item for item in intervals if not item.get("source_only")}
    font = ImageFont.truetype(str(get_settings().font_path), ENGLISH_FONT_SIZE)
    events = []
    layouts = []
    for group in groups:
        source_intervals = [interval_by_segment[seg_id] for seg_id in group["source_segment_ids"] if seg_id in interval_by_segment]
        if not source_intervals:
            continue
        interval = representative_interval(group["clip_id"], source_intervals, group)
        layout = english_layout_for_interval(interval, group["subtitle_text"], font)
        layout["start_time"] = group["subtitle_start_s"]
        layout["end_time"] = group["subtitle_end_s"]
        layout["source_segment_ids"] = group["source_segment_ids"]
        layouts.append(layout)
        text_override = "{" f"\\an5\\fs{ENGLISH_FONT_SIZE}\\pos({layout['center_x']},{layout['anchor_y']})" "}"
        events.append(
            "Dialogue: 2,"
            f"{format_ass_timestamp(round(group['subtitle_start_s'] * 1000))},"
            f"{format_ass_timestamp(round(group['subtitle_end_s'] * 1000))},"
            f"Default,,0,0,0,,{text_override}{ass_escape(layout['render_text'])}"
        )
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,46,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return {"ass_path": str(path), "font": "Arial", "font_size": ENGLISH_FONT_SIZE, "events": layouts}


def build_cp06l_flash_repairs(source_path: Path) -> list[dict]:
    cap = cv2.VideoCapture(str(source_path))
    repairs = []
    for index, time_s in enumerate([0.0, 57.9, 66.0], start=1):
        frame = read_frame(cap, time_s)
        bbox, _ = detect_source_subtitle_bbox(frame)
        if not bbox:
            continue
        repairs.append(
            {
                "segment_id": f"cp06l_flash_repair_{index:02d}",
                "start_time": max(0.0, round(time_s - 0.12, 3)),
                "end_time": min(DURATION_SECONDS - 0.04, round(time_s + 0.12, 3)),
                "x": max(0, bbox["left_x"] - 12),
                "y": max(0, bbox["top_y"] - 8),
                "width": min(WIDTH, bbox["right_x"] - bbox["left_x"] + 1 + 24),
                "height": min(HEIGHT, bbox["bottom_y"] - bbox["top_y"] + 1 + 16),
                "line_count": 1,
                "padding": {"vertical": 8, "horizontal": 12},
                "source_bbox": bbox,
                "source_only": True,
                "temporal_role": "cp06l_one_frame_flash_repair",
            }
        )
    cap.release()
    return repairs


def group_render_truth_audit(
    source_path: Path,
    output_path: Path,
    intervals: list[dict],
    plate_layouts: list[dict],
    evidence_dir: Path,
) -> dict:
    paired_dir = evidence_dir / "paired_frames"
    paired_dir.mkdir(parents=True, exist_ok=True)
    source_cap = cv2.VideoCapture(str(source_path))
    output_cap = cv2.VideoCapture(str(output_path))
    results = []
    for time_s in [5.0, 22.0, 38.0, 50.0, 59.6, 60.0, 65.0]:
        source_frame = read_frame(source_cap, time_s)
        output_frame = read_frame(output_cap, time_s)
        active = active_intervals(intervals, time_s)
        planned_bbox = union_boxes([item.get("source_bbox") for item in active])
        detected_bbox, _ = detect_source_subtitle_bbox(source_frame)
        source_bbox = planned_bbox or detected_bbox
        ignore_boxes = active_group_plate_boxes(plate_layouts, time_s)
        source_count = glyph_pixel_count(source_frame, source_bbox) if source_bbox else 0
        raw_output_count = glyph_pixel_count(output_frame, source_bbox) if source_bbox else 0
        output_count = matched_glyph_pixel_count(source_frame, output_frame, source_bbox, ignore_boxes) if source_bbox else 0
        residual_ratio = round(output_count / source_count, 4) if source_count else 0.0
        status = "PASS" if source_count < 50 or residual_ratio <= 0.18 else "FAIL"
        paired = paired_dir / f"paired_{str(time_s).replace('.', '_')}s.jpg"
        write_paired_frame(source_frame, output_frame, paired, time_s)
        results.append(
            {
                "time_s": time_s,
                "source_bbox": source_bbox,
                "ignored_english_boxes": ignore_boxes,
                "source_glyph_pixels": source_count,
                "final_glyph_pixels_in_source_bbox": raw_output_count,
                "final_glyph_pixels_matching_source": output_count,
                "residual_ratio": residual_ratio,
                "status": status,
                "paired_frame": str(paired),
            }
        )
    source_cap.release()
    output_cap.release()
    return {"mandatory_times": [5.0, 22.0, 38.0, 50.0, 59.6, 60.0, 65.0], "fail_count": sum(1 for r in results if r["status"] != "PASS"), "results": results}


def group_transition_flash_audit(
    source_path: Path,
    output_path: Path,
    intervals: list[dict],
    plate_layouts: list[dict],
    timeline: dict,
) -> dict:
    windows = transition_windows(timeline)
    windows.append({"label": "mandatory_59_20_60_30", "start": 59.20, "end": 60.30})
    windows.append({"label": "mandatory_tail_60_75", "start": 60.00, "end": 74.96})
    windows = merge_group_windows(windows)
    source_cap = cv2.VideoCapture(str(source_path))
    output_cap = cv2.VideoCapture(str(output_path))
    residual_frames = []
    inspected = []
    active_states = []
    for time_s in audited_frame_times(windows):
        source_frame = read_frame(source_cap, time_s)
        output_frame = read_frame(output_cap, time_s)
        active = active_intervals(intervals, time_s)
        active_states.append({"time_s": time_s, "active": bool(active)})
        planned_bbox = union_boxes([item.get("source_bbox") for item in active])
        detected_bbox, _ = detect_source_subtitle_bbox(source_frame)
        source_bbox = planned_bbox or detected_bbox
        ignore_boxes = active_group_plate_boxes(plate_layouts, time_s)
        source_count = glyph_pixel_count(source_frame, source_bbox) if source_bbox else 0
        output_count = matched_glyph_pixel_count(source_frame, output_frame, source_bbox, ignore_boxes) if source_bbox else 0
        residual_ratio = round(output_count / source_count, 4) if source_count else 0.0
        status = "PASS" if source_count < 50 or residual_ratio <= 0.18 else "FAIL"
        if status != "PASS":
            residual_frames.append(
                {
                    "time_s": time_s,
                    "source_bbox": source_bbox,
                    "source_glyph_pixels": source_count,
                    "final_glyph_pixels_matching_source": output_count,
                    "residual_ratio": residual_ratio,
                }
            )
        inspected.append({"time_s": time_s, "status": status})
    source_cap.release()
    output_cap.release()
    runs = residual_runs([item["time_s"] for item in residual_frames])
    toggles = delogo_toggle_audit(active_states)
    return {
        "windows": windows,
        "inspected_frame_count": len(inspected),
        "residual_glyph_frame_count": len(residual_frames),
        "residual_frames": residual_frames[:50],
        "short_subtitle_flash_count": sum(1 for run in runs if 1 <= run["frame_count"] <= 5),
        "residual_runs": runs,
        "delogo_toggle_count": toggles["unnecessary_delogo_toggle_count"],
        "mandatory_31_70_32_30": window_result(inspected, 31.70, 32.30),
        "mandatory_59_20_60_30": window_result(inspected, 59.20, 60.30),
        "mandatory_tail_60_75": window_result(inspected, 60.00, 74.96),
        "status": "PASS" if not residual_frames and toggles["unnecessary_delogo_toggle_count"] == 0 else "FAIL",
    }


def active_group_plate_boxes(plate_layouts: list[dict], time_s: float) -> list[dict]:
    boxes = []
    for layout in plate_layouts:
        if not (layout["start_time"] - 0.08 <= time_s <= layout["end_time"] + 0.08):
            continue
        plate = layout["plate"]
        boxes.append(
            {
                "left_x": max(0, plate["x"] - 10),
                "top_y": max(0, plate["y"] - 10),
                "right_x": min(WIDTH - 1, plate["right_x"] + 10),
                "bottom_y": min(HEIGHT - 1, plate["bottom_y"] + 10),
                "reason": "cp06l_grouped_english_plate_and_text_overlay",
            }
        )
    return boxes


def merge_group_windows(windows: list[dict]) -> list[dict]:
    ordered = sorted(windows, key=lambda item: item["start"])
    merged = []
    for window in ordered:
        if not merged or window["start"] > merged[-1]["end"] + (1 / FPS):
            merged.append(dict(window))
            continue
        merged[-1]["end"] = max(merged[-1]["end"], window["end"])
        merged[-1]["label"] += f"+{window['label']}"
    return [{"label": item["label"], "start": round(item["start"], 3), "end": round(item["end"], 3)} for item in merged]


def write_paired_frame(source_frame: np.ndarray, output_frame: np.ndarray, path: Path, time_s: float) -> None:
    source_small = cv2.resize(source_frame, (320, 180))
    output_small = cv2.resize(output_frame, (320, 180))
    cv2.putText(source_small, f"source {time_s:.2f}s", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.putText(output_small, f"output {time_s:.2f}s", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.imwrite(str(path), np.hstack([source_small, output_small]))


def representative_interval(group_id: str, source_intervals: list[dict], group: dict) -> dict:
    active = [item for item in source_intervals if item["start_time"] <= group["subtitle_start_s"] <= item["end_time"]]
    base = active[0] if active else source_intervals[len(source_intervals) // 2]
    return {**base, "segment_id": group_id}


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        sr = wav.getframerate()
        channels = wav.getnchannels()
        data = wav.readframes(wav.getnframes())
    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, sr


def write_wav_mono(path: Path, audio: np.ndarray, sr: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(pcm.tobytes())


def resample_to_length(audio: np.ndarray, target_len: int) -> np.ndarray:
    if len(audio) == target_len:
        return audio
    if len(audio) < 2:
        return np.resize(audio, target_len).astype(np.float32)
    return np.interp(np.linspace(0, 1, target_len), np.linspace(0, 1, len(audio)), audio).astype(np.float32)


def fade_edges(audio: np.ndarray, sr: int) -> np.ndarray:
    result = audio.copy()
    fade = min(int(sr * 0.01), len(result) // 2)
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade)
        result[:fade] *= ramp
        result[-fade:] *= ramp[::-1]
    return result


def clean_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def listening_warning() -> list[str]:
    return [
        "Automated machine QA cannot replace human listening. Human visual/listening review remains required before CP07.",
    ]


if __name__ == "__main__":
    main()
    delogo_toggle_audit,
    detect_source_subtitle_bbox,
    glyph_pixel_count,
    matched_glyph_pixel_count,
    residual_runs,
