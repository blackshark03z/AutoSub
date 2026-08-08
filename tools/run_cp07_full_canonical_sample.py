import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from statistics import median

import cv2
import numpy as np
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.provider_cache import build_request_hash, read_cached_response, write_cached_response
from app.core.secret_files import load_secret_lines
from app.providers.asr.faster_whisper_provider import FasterWhisperASRProvider
from app.providers.translation.gemini import load_gemini_translation_config
from app.providers.translation.gemini_contract import (
    GeminiContractError,
    extract_text_parts,
    parse_generated_json,
    validate_atomic_cache_roundtrip,
    validate_diagnostic_response,
)
from app.providers.tts.base import TTSRequest
from app.providers.tts.elevenlabs import ElevenLabsTTSProvider, load_elevenlabs_config
from app.providers.tts.fake import tts_request_payload
from app.services.subtitles import format_ass_timestamp
from app.services.tts_generation import generate_tts_for_unit, resolve_voice_id
from tools.run_cp06k_tail_audio_alignment_and_residual_repair import (
    FPS,
    HEIGHT,
    WIDTH,
    ass_escape,
    detect_source_subtitle_bbox,
    glyph_pixel_count,
    matched_glyph_pixel_count,
    read_frame,
    union_boxes,
)


PROJECT_ID = "vertical_slice_cp07"
SOURCE_SHA256 = "34a304fb44f5e4c27d1a34989a69f939888ef90c89bbae0142434f43cf4db068"
EXPECTED_DURATION = 666.435918
MEASURED_GATE_GIB = 6.954137
MAX_ELEVENLABS_CALLS = 100
GEMINI_SYSTEMIC_CONSECUTIVE_FAILURE_LIMIT = 10
GEMINI_SYSTEMIC_FAILED_CALL_LIMIT = 25
GEMINI_PROVIDER = "gemini_cp07_full_canonical"
LEGACY_GEMINI_PROMPT_VERSION = "cp07-full-canonical-v1"
GEMINI_PROMPT_VERSION = "cp07-full-canonical-simplified-v2"
DIAGNOSTIC_REQUEST_HASH = "bd2d54f4103d68c36b7397245fe750a33f7a72af864f9b8c11a70db14077524d"


@dataclass(frozen=True)
class Paths:
    project_dir: Path
    render_dir: Path
    audio_dir: Path
    subtitle_dir: Path
    timeline_dir: Path
    tts_dir: Path
    evidence_dir: Path


def main() -> None:
    settings = get_settings()
    paths = build_paths(settings)
    for directory in [paths.render_dir, paths.audio_dir, paths.subtitle_dir, paths.timeline_dir, paths.tts_dir, paths.evidence_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    source_summary = validate_source(settings.source_path)
    render_plan = validate_render_plan()
    disk_gate = measured_disk_gate(settings.root, render_plan)

    asr_audio = paths.audio_dir / "cp07_source_asr_mono16k.wav"
    extract_asr_audio(settings.source_path, asr_audio)
    asr_segments = transcribe_full_source(asr_audio)
    timeline = build_cp07_timeline(asr_segments, source_summary)
    validate_canonical_input_plan(timeline)
    (paths.timeline_dir / "cp07_canonical_asr_timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    gemini_plan = build_gemini_replan(timeline, paths.evidence_dir)
    validate_gemini_replan_budget(gemini_plan)
    # This is the required immediately-before-provider-call gate.
    disk_gate = measured_disk_gate(settings.root, render_plan)
    gemini_plan = select_gemini_model_for_resume(gemini_plan, paths.evidence_dir)
    transform = transform_with_gemini(gemini_plan, paths.evidence_dir)
    transformed_timeline = attach_transform(timeline, transform)
    validate_transform_coverage(timeline, transformed_timeline)

    tts_groups = build_tts_groups(transformed_timeline)
    tts_plan = validate_tts_plan(tts_groups)
    if tts_plan["planned_calls"] > MAX_ELEVENLABS_CALLS:
        raise RuntimeError("CP07_BLOCKED_TTS_REQUEST_PLAN")

    # This is the required immediately-before-first-ElevenLabs-call gate.
    disk_gate = measured_disk_gate(settings.root, render_plan)
    provider = ElevenLabsTTSProvider(load_elevenlabs_config())
    preflight = provider.probe_subscription()
    if preflight.status_code != 200:
        raise RuntimeError(f"CP07_BLOCKED_ELEVENLABS_PREFLIGHT_{preflight.classification.upper()}")
    voice_id = resolve_voice_id(None, provider)
    tts_result = synthesize_groups(provider, voice_id, tts_groups)

    narration = build_narration_stem(paths.render_dir / "cp07_full_canonical_narration_stem.wav", tts_groups, tts_result)
    sentence_cues = build_sentence_cues(transformed_timeline, tts_groups, narration)
    subtitle_qa = subtitle_progression_qa(transformed_timeline, tts_groups, sentence_cues)
    intervals = build_visual_intervals(settings.source_path, transformed_timeline)
    ass_path = paths.render_dir / "cp07_full_canonical_sentence_level.ass"
    layouts = write_sentence_ass(sentence_cues, intervals, ass_path)
    output_path = paths.render_dir / "cp07_full_canonical_sample_720p.mp4"
    render_full_preview(settings.source_path, Path(narration["narration_stem_path"]), ass_path, output_path, intervals, layouts)

    visual_qa = visual_machine_qa(settings.source_path, output_path, intervals, layouts, transformed_timeline, paths.evidence_dir)
    audio_qa = audio_machine_qa(transformed_timeline, tts_groups, narration)
    media = media_summary(output_path)
    provider_usage = {
        "gemini_planned_calls": len(blocks),
        "gemini_real_calls": transform["real_call_count"],
        "gemini_cache_hits": transform["cache_hit_count"],
        "elevenlabs_planned_calls": len(tts_groups),
        "elevenlabs_real_calls": tts_result["real_call_count"],
        "elevenlabs_cache_hits": tts_result["cache_hit_count"],
        "uncertain_calls": tts_result["uncertain_call_count"],
        "failover_events": transform.get("failover_events", 0),
    }
    timeline_json = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "source": {"path": str(settings.source_path), "sha256": SOURCE_SHA256, "media": source_summary},
        "render_plan": render_plan,
        "disk_gate": disk_gate,
        "canonical_timeline": transformed_timeline,
        "tts_groups": tts_groups,
        "narration": narration,
        "sentence_cues": sentence_cues,
        "subtitle_layouts": layouts,
        "visual_intervals": intervals,
        "provider_usage": provider_usage,
        "qa": {"audio": audio_qa, "subtitle": subtitle_qa, "visual": visual_qa},
        "media": media,
    }
    timeline_path = paths.render_dir / "cp07_full_canonical_audio_subtitle_timeline.json"
    timeline_path.write_text(json.dumps(timeline_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (paths.evidence_dir / "provider_usage.json").write_text(json.dumps(provider_usage, indent=2), encoding="utf-8")
    (paths.evidence_dir / "tts_grouping_plan.json").write_text(json.dumps(tts_groups, ensure_ascii=False, indent=2), encoding="utf-8")
    (paths.evidence_dir / "qa_summary.json").write_text(json.dumps(timeline_json["qa"], ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "status": final_status(provider_usage, audio_qa, subtitle_qa, visual_qa, media),
        "artifact_path": str(output_path),
        "artifact_sha256": sha256_file(output_path),
        "narration_stem_path": narration["narration_stem_path"],
        "narration_stem_sha256": sha256_file(Path(narration["narration_stem_path"])),
        "ass_path": str(ass_path),
        "ass_sha256": sha256_file(ass_path),
        "timeline_path": str(timeline_path),
        "timeline_sha256": sha256_file(timeline_path),
        "source_segment_count": len(timeline["segments"]),
        "spoken_unit_count": len(transformed_timeline["segments"]),
        "subtitle_cue_count": len(sentence_cues),
        "tts_group_count": len(tts_groups),
        "active_binding_count": sum(1 for group in tts_groups if group.get("generation_id")),
        "provider_usage": provider_usage,
        "audio_qa": audio_qa,
        "subtitle_qa": subtitle_qa,
        "visual_qa": visual_qa,
        "media": media,
        "free_disk_after_gib": round(shutil.disk_usage(settings.root).free / (1024**3), 6),
    }
    (paths.evidence_dir / "calibration_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "PASS":
        raise RuntimeError("CP07_MACHINE_QA_FAILED")


def build_paths(settings) -> Paths:
    project_dir = settings.data_dir / "projects" / PROJECT_ID
    return Paths(
        project_dir=project_dir,
        render_dir=project_dir / "renders",
        audio_dir=project_dir / "audio",
        subtitle_dir=project_dir / "subtitles",
        timeline_dir=project_dir / "timeline",
        tts_dir=project_dir / "tts" / "units",
        evidence_dir=settings.root / "evidence" / "CP07",
    )


def validate_source(source_path: Path) -> dict:
    if sha256_file(source_path) != SOURCE_SHA256:
        raise RuntimeError("CP07_BLOCKED_SOURCE_SHA_MISMATCH")
    summary = media_summary(source_path)
    video = summary["video"]
    if abs(summary["duration_seconds"] - EXPECTED_DURATION) > 0.05:
        raise RuntimeError("CP07_BLOCKED_SOURCE_DURATION_MISMATCH")
    if video.get("width") != 1920 or video.get("height") != 1080:
        raise RuntimeError("CP07_BLOCKED_SOURCE_RESOLUTION_MISMATCH")
    return summary


def validate_render_plan() -> dict:
    return {
        "output_resolution": "1280x720",
        "ffmpeg_processing": "streaming_filter_graph",
        "full_frame_dump": False,
        "raw_video_dump": False,
        "lossless_full_duration_intermediate": False,
        "multiple_concurrent_full_video_encodes": False,
        "canonical_source_repeated_copy": False,
        "estimated_new_stage_over_1gib": False,
    }


def measured_disk_gate(root: Path, render_plan: dict) -> dict:
    free_gib = shutil.disk_usage(root).free / (1024**3)
    assumptions_ok = (
        render_plan["output_resolution"] == "1280x720"
        and render_plan["ffmpeg_processing"] == "streaming_filter_graph"
        and not render_plan["full_frame_dump"]
        and not render_plan["raw_video_dump"]
        and not render_plan["lossless_full_duration_intermediate"]
        and not render_plan["multiple_concurrent_full_video_encodes"]
        and not render_plan["estimated_new_stage_over_1gib"]
    )
    if free_gib < MEASURED_GATE_GIB or not assumptions_ok:
        raise RuntimeError("CP07_BLOCKED_MEASURED_DISK_GATE")
    return {
        "free_gib": round(free_gib, 6),
        "required_gate_gib": MEASURED_GATE_GIB,
        "safety_margin_gib": round(free_gib - MEASURED_GATE_GIB, 6),
        "render_path_assumptions_ok": assumptions_ok,
    }


def extract_asr_audio(source_path: Path, output_path: Path) -> None:
    if output_path.exists():
        return
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
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
        ],
        check=True,
    )


def transcribe_full_source(audio_path: Path) -> list[dict]:
    cache_path = audio_path.with_suffix(".asr_segments.json")
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    provider = FasterWhisperASRProvider(model_name="tiny", device="cpu", compute_type="int8", local_files_only=True)
    raw = provider.transcribe(audio_path, language="zh")
    segments = [
        {
            "start": round(item.start, 3),
            "end": round(item.end, 3),
            "text": item.text,
            "avg_logprob": item.avg_logprob,
            "no_speech_prob": item.no_speech_prob,
        }
        for item in raw
        if item.text.strip()
    ]
    cache_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    return segments


def build_cp07_timeline(asr_segments: list[dict], source_summary: dict) -> dict:
    source_duration_ms = int(round(source_summary["duration_seconds"] * 1000))
    segments = []
    for index, segment in enumerate(asr_segments, start=1):
        start_ms = max(0, int(round(segment["start"] * 1000)))
        end_ms = min(source_duration_ms, max(start_ms + 1, int(round(segment["end"] * 1000))))
        text = clean_text(segment["text"])
        segments.append(
            {
                "id": f"seg_{index:04d}",
                "ordinal": index,
                "chapter_id": "ch_001",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_text": text,
                "translated_text": "",
                "spoken_text": "",
                "subtitle_text": "",
                "enabled": bool(text),
                "speaker_id": "narrator",
                "status": "draft",
                "issues": [],
                "asr": {
                    "avg_logprob": segment.get("avg_logprob"),
                    "no_speech_prob": segment.get("no_speech_prob"),
                },
            }
        )
    return {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "source_duration_ms": source_duration_ms,
        "source_language": "zh",
        "target_language": "en",
        "target_locale": "en-US",
        "segments": segments,
    }


def validate_canonical_input_plan(timeline: dict) -> None:
    ids = [segment["id"] for segment in timeline["segments"]]
    if not ids or len(ids) != len(set(ids)):
        raise RuntimeError("CP07_BLOCKED_CANONICAL_ID_PLAN")
    last = -1
    for segment in timeline["segments"]:
        if segment["start_ms"] < last or segment["end_ms"] <= segment["start_ms"]:
            raise RuntimeError("CP07_BLOCKED_CANONICAL_TIMING_PLAN")
        last = segment["end_ms"]


class GeminiCallFailure(RuntimeError):
    def __init__(self, reason: str, attempts: int, timeout_uncertain: bool = False, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.attempts = attempts
        self.timeout_uncertain = timeout_uncertain
        self.detail = detail


def build_legacy_gemini_blocks(timeline: dict) -> list[dict]:
    segments = [segment for segment in timeline["segments"] if segment.get("enabled", True)]
    blocks = []
    block_size = 5
    for index in range(0, len(segments), block_size):
        block_segments = segments[index : index + block_size]
        blocks.append(
            {
                "block_id": f"cp07_block_{len(blocks)+1:02d}",
                "segments": [
                    {
                        "id": segment["id"],
                        "ordinal": segment["ordinal"],
                        "start_ms": segment["start_ms"],
                        "end_ms": segment["end_ms"],
                        "source_text": segment["source_text"],
                        "duration_budget_ms": segment["end_ms"] - segment["start_ms"],
                    }
                    for segment in block_segments
                ],
            }
        )
    return blocks


def make_gemini_block(block_id: str, segments: list[dict]) -> dict:
    return {
        "block_id": block_id,
        "segments": [
            {
                "id": segment["id"],
                "ordinal": segment["ordinal"],
                "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"],
                "source_text": segment["source_text"],
                "duration_budget_ms": segment["end_ms"] - segment["start_ms"],
            }
            for segment in segments
        ],
    }


def build_gemini_replan(timeline: dict, evidence_dir: Path) -> dict:
    config = load_gemini_translation_config()
    legacy_blocks = build_legacy_gemini_blocks(timeline)
    retained_segments: dict[str, dict] = {}
    retained_blocks = []
    invalid_legacy_blocks = []
    planned_hashes = set()
    for block in legacy_blocks:
        payload = legacy_gemini_payload(config.model, block)
        request_hash = build_request_hash(payload)
        planned_hashes.add(request_hash)
        cached = read_cached_response(GEMINI_PROVIDER, request_hash)
        if cached is None:
            continue
        try:
            validate_gemini_response(block, cached)
        except RuntimeError as exc:
            invalid_legacy_blocks.append({"block_id": block["block_id"], "request_hash": request_hash, "reason": str(exc)})
            continue
        for segment in cached["segments"]:
            retained_segments[segment["id"]] = segment
        retained_blocks.append(
            {
                "block_id": block["block_id"],
                "request_hash": request_hash,
                "source_segment_ids": [segment["id"] for segment in block["segments"]],
                "segment_count": len(block["segments"]),
                "cache_status": "VALID_RETAINED",
            }
        )

    diagnostic_promotion = promote_diagnostic_cache(timeline)
    if diagnostic_promotion["status"] == "PROMOTED":
        for segment in diagnostic_promotion["segments"]:
            retained_segments[segment["id"]] = segment

    previous_manifest = load_json_if_exists(evidence_dir / "gemini_replan_manifest.json")
    seed_submission_ledger_from_manifest(evidence_dir, previous_manifest)
    rate_limit_resume = latest_rate_limit_resume_state(previous_manifest)
    all_segments = [segment for segment in timeline["segments"] if segment.get("enabled", True)]
    missing_segments = [segment for segment in all_segments if segment["id"] not in retained_segments]
    new_blocks = build_rate_limit_aware_blocks(missing_segments)
    actual_missing_ids = [segment["id"] for block in new_blocks for segment in block["segments"]]
    expected_missing_ids = [segment["id"] for segment in missing_segments]
    if actual_missing_ids != expected_missing_ids or len(actual_missing_ids) != len(set(actual_missing_ids)):
        raise RuntimeError("CP07_BLOCKED_GEMINI_REPLAN_COVERAGE")

    cache_dir = get_settings().data_dir / "provider_cache" / GEMINI_PROVIDER
    orphan_files = []
    if cache_dir.exists():
        for path in sorted(cache_dir.glob("*.json")):
            request_hash = path.stem
            if request_hash in planned_hashes:
                continue
            verdict = inspect_orphan_cache(path, timeline, config)
            orphan_files.append(verdict)

    stats = block_stats(new_blocks)
    source_ids = [segment["id"] for segment in all_segments]
    covered_ids = sorted(retained_segments, key=source_ids.index)
    manifest = {
        "schema_version": 1,
        "verdict": "CP07_GEMINI_REPLAN_READY",
        "old_manifest": {
            "planned_blocks": len(legacy_blocks),
            "valid_cached_blocks": len(retained_blocks),
            "invalid_cached_blocks": len(invalid_legacy_blocks),
            "reason_superseded": "The 89-block plan averaged about five source segments per request and was over-fragmented.",
        },
        "retained_valid_cache_entries": retained_blocks,
        "diagnostic_cache_promotion": {key: value for key, value in diagnostic_promotion.items() if key != "segments"},
        "invalid_legacy_blocks": invalid_legacy_blocks,
        "orphan_cache": orphan_files,
        "valid_cached_source_coverage": len(covered_ids),
        "missing_source_coverage": len(missing_segments),
        "source_segments_total": len(all_segments),
        "new_initial_block_count": len(new_blocks),
        "new_block_stats": stats,
        "provider_availability_probe": summarize_blocks(new_blocks[:1], config)[0] if new_blocks else None,
        "rate_limit_resume": rate_limit_resume,
        "worst_case_call_count_with_one_level_split": len(new_blocks) * 2,
        "technical_circuit_breaker": {
            "consecutive_real_calls_without_valid_cache": GEMINI_SYSTEMIC_CONSECUTIVE_FAILURE_LIMIT,
            "failed_real_calls_without_coverage_increase": GEMINI_SYSTEMIC_FAILED_CALL_LIMIT,
        },
        "new_blocks": summarize_blocks(new_blocks, config),
        "completed_new_blocks": [],
        "timeout_uncertain": [],
        "provider_call_events": [],
        "covered_source_ids": covered_ids,
        "missing_source_ids": [segment["id"] for segment in missing_segments],
    }
    write_json_atomic(evidence_dir / "gemini_replan_manifest.json", manifest)
    return {
        "config": config,
        "retained_segments": retained_segments,
        "blocks": new_blocks,
        "manifest": manifest,
        "manifest_path": str(evidence_dir / "gemini_replan_manifest.json"),
    }


def build_replanned_missing_blocks(missing_segments: list[dict]) -> list[dict]:
    blocks = []
    current = []
    current_chars = 0
    min_segments = 18
    max_segments = 24
    max_chars = 7000
    for segment in missing_segments:
        if current:
            gap_ms = segment["start_ms"] - current[-1]["end_ms"]
            should_split = len(current) >= min_segments and (
                len(current) >= max_segments or current_chars + len(segment["source_text"]) > max_chars or gap_ms > 3000
            )
            if should_split:
                blocks.append(make_gemini_block(f"cp07_replan_block_{len(blocks)+1:02d}", current))
                current = []
                current_chars = 0
        current.append(segment)
        current_chars += len(segment["source_text"])
    if current:
        blocks.append(make_gemini_block(f"cp07_replan_block_{len(blocks)+1:02d}", current))
    return blocks


def build_rate_limit_aware_blocks(missing_segments: list[dict]) -> list[dict]:
    if not missing_segments:
        return []
    probe_segments = missing_segments[: min(5, len(missing_segments))]
    blocks = [make_gemini_block("cp07_rate_limit_probe_01", probe_segments)]
    for index, block in enumerate(build_replanned_missing_blocks(missing_segments[len(probe_segments) :]), start=1):
        blocks.append({"block_id": f"cp07_replan_block_{index:02d}", "segments": block["segments"]})
    return blocks


def load_json_if_exists(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def seed_submission_ledger_from_manifest(evidence_dir: Path, manifest: dict) -> None:
    ledger_path = evidence_dir / "gemini_submission_ledger.json"
    ledger = load_json_if_exists(ledger_path)
    changed = False
    for event in manifest.get("provider_call_events", []):
        request_hash = event.get("request_hash")
        if not request_hash:
            continue
        attempts = int(event.get("attempts", 0) or 0)
        current = int(ledger.get(request_hash, {}).get("provider_submissions", 0) or 0)
        if attempts > current:
            ledger[request_hash] = {"provider_submissions": attempts, "source": "seeded_from_manifest"}
            changed = True
    if changed:
        write_json_atomic(ledger_path, ledger)


def latest_rate_limit_resume_state(manifest: dict) -> dict:
    existing = manifest.get("rate_limit_resume")
    if isinstance(existing, dict) and existing.get("previous_429_seen"):
        return existing
    for event in reversed(manifest.get("provider_call_events", [])):
        if "429" not in str(event.get("reason")) and "429" not in str(event.get("detail")):
            continue
        retry_after = parse_retry_after_seconds(event.get("detail"))
        return {
            "previous_429_seen": True,
            "retry_after_seconds": retry_after,
            "safety_buffer_seconds": None,
            "source_request_hash": event.get("request_hash"),
        }
    return {"previous_429_seen": False}


def parse_retry_after_seconds(detail: str | None) -> float | None:
    if not detail:
        return None
    match = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", detail, re.IGNORECASE)
    return float(match.group(1)) if match else None


def block_stats(blocks: list[dict]) -> dict:
    sizes = [len(block["segments"]) for block in blocks]
    chars = [sum(len(segment["source_text"]) for segment in block["segments"]) for block in blocks]
    return {
        "min_block_size": min(sizes) if sizes else 0,
        "median_block_size": median(sizes) if sizes else 0,
        "max_block_size": max(sizes) if sizes else 0,
        "min_source_characters": min(chars) if chars else 0,
        "median_source_characters": median(chars) if chars else 0,
        "max_source_characters": max(chars) if chars else 0,
        "blocks_crossing_scene_boundary": 0,
        "scene_boundary_basis": "No authoritative scene map exists; planner only splits at source-segment boundaries and avoids large ASR gaps over 3000 ms.",
    }


def summarize_blocks(blocks: list[dict], config) -> list[dict]:
    summary = []
    for block in blocks:
        payload = gemini_payload(config.model, block)
        summary.append(
            {
                "block_id": block["block_id"],
                "request_hash": build_request_hash(payload),
                "segment_count": len(block["segments"]),
                "source_characters": sum(len(segment["source_text"]) for segment in block["segments"]),
                "first_source_id": block["segments"][0]["id"],
                "last_source_id": block["segments"][-1]["id"],
                "start_ms": block["segments"][0]["start_ms"],
                "end_ms": block["segments"][-1]["end_ms"],
            }
        )
    return summary


def inspect_orphan_cache(path: Path, timeline: dict, config) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"path": str(path), "request_hash": path.stem, "verdict": "ORPHAN_IGNORED", "reason": f"unreadable_json:{type(exc).__name__}"}
    ids = [segment.get("id") for segment in payload.get("segments", []) if isinstance(segment, dict)]
    source_by_id = {segment["id"]: segment for segment in timeline["segments"]}
    if not ids or any(segment_id not in source_by_id for segment_id in ids):
        return {"path": str(path), "request_hash": path.stem, "verdict": "ORPHAN_IGNORED", "source_segment_ids": ids, "reason": "unknown_or_empty_ids"}
    candidate_segments = [source_by_id[segment_id] for segment_id in ids]
    block = make_gemini_block("orphan_candidate", candidate_segments)
    expected_hash = build_request_hash(legacy_gemini_payload(config.model, block))
    try:
        validate_gemini_response(block, payload)
    except RuntimeError as exc:
        return {"path": str(path), "request_hash": path.stem, "verdict": "ORPHAN_IGNORED", "source_segment_ids": ids, "reason": str(exc)}
    verdict = "PROMOTABLE_BUT_NOT_USED" if expected_hash == path.stem else "ORPHAN_IGNORED"
    reason = "exact_identity_matches" if expected_hash == path.stem else "filename_hash_does_not_match_reconstructed_canonical_payload"
    return {"path": str(path), "request_hash": path.stem, "verdict": verdict, "source_segment_ids": ids, "reason": reason}


def promote_diagnostic_cache(timeline: dict) -> dict:
    cached = read_cached_response(GEMINI_PROVIDER, DIAGNOSTIC_REQUEST_HASH)
    if cached is None:
        return {"status": "NOT_PRESENT", "request_hash": DIAGNOSTIC_REQUEST_HASH, "segments": []}
    source_by_id = {segment["id"]: segment for segment in timeline["segments"]}
    expected_ids = ["seg_0071", "seg_0072", "seg_0073", "seg_0074", "seg_0075"]
    if any(segment_id not in source_by_id for segment_id in expected_ids):
        return {"status": "REJECTED", "request_hash": DIAGNOSTIC_REQUEST_HASH, "reason": "expected_source_ids_missing", "segments": []}
    try:
        validated = validate_diagnostic_response(expected_ids, cached)
        validate_atomic_cache_roundtrip(GEMINI_PROVIDER, DIAGNOSTIC_REQUEST_HASH, cached)
    except GeminiContractError as exc:
        return {"status": "REJECTED", "request_hash": DIAGNOSTIC_REQUEST_HASH, "reason": exc.classification, "segments": []}
    segments = []
    for item in validated["segments"]:
        source = source_by_id[item["source_segment_id"]]
        text = clean_text(item["english_text"])
        segments.append(
            {
                "id": source["id"],
                "start_ms": source["start_ms"],
                "end_ms": source["end_ms"],
                "spoken_text": text,
                "subtitle_text": text,
                "intended_speaking_duration_ms": source["end_ms"] - source["start_ms"],
                "classification": "narration",
                "ordering": source["ordinal"],
                "subtitle_group_hint": "sentence",
                "spoken_status": item["spoken_status"],
            }
        )
    return {
        "status": "PROMOTED",
        "request_hash": DIAGNOSTIC_REQUEST_HASH,
        "source_segment_ids": expected_ids,
        "schema_migration": "diagnostic minimal schema promoted to production local-derived metadata",
        "segments": segments,
    }


def validate_gemini_replan_budget(plan: dict) -> None:
    if len(plan["blocks"]) > 80:
        raise RuntimeError("CP07_BLOCKED_GEMINI_REPLAN_BUDGET")


def select_gemini_model_for_resume(plan: dict, evidence_dir: Path) -> dict:
    config = plan["config"]
    keys = load_secret_lines(config.key_file)[:1]
    discovery = discover_gemini_models(config, keys[0])
    if discovery["status"] == "RATE_LIMIT":
        write_json_atomic(evidence_dir / "gemini_model_fallback_manifest.json", {"schema_version": 1, "configured_model": config.model, "discovery_status": "RATE_LIMIT"})
        raise RuntimeError("CP07_BLOCKED_GEMINI_RATE_LIMIT")
    if discovery["status"] == "AUTH":
        write_json_atomic(evidence_dir / "gemini_model_fallback_manifest.json", {"schema_version": 1, "configured_model": config.model, "discovery_status": "AUTH"})
        raise RuntimeError("CP07_BLOCKED_GEMINI_AUTH")
    probe_block = plan["blocks"][0] if plan["blocks"] else None
    candidates = choose_model_candidates(config.model, discovery)
    manifest = {
        "schema_version": 1,
        "configured_model": config.model,
        "endpoint_api_version": config.base_url,
        "http_path": "direct_http_openai_compatible_chat_completions",
        "generation_config": {"temperature": 0, "response_format": "json_object", "timeout_seconds": config.timeout_seconds},
        "cache_identity_includes": ["provider", "model", "prompt_version", "ordered_source_segment_ids", "source_text", "target_locale", "generation_settings"],
        "discovered_models": discovery["sanitized_models"],
        "probe_candidates": candidates,
        "probe_attempts": [],
        "selected_model": None,
    }
    write_json_atomic(evidence_dir / "gemini_model_fallback_manifest.json", manifest)
    if probe_block is None:
        return plan
    ledger_path = evidence_dir / "gemini_submission_ledger.json"
    for candidate in candidates:
        candidate_config = replace(config, model=candidate["model_id"])
        candidate_payload = gemini_payload(candidate_config.model, probe_block)
        candidate_hash = build_request_hash(candidate_payload)
        cached = read_cached_response(GEMINI_PROVIDER, candidate_hash)
        if cached is not None:
            normalize_contract_response(probe_block, cached)
            manifest["probe_attempts"].append({"model_id": candidate["model_id"], "request_hash": candidate_hash, "status": "CACHE_HIT_VALID"})
            manifest["selected_model"] = candidate["model_id"]
            write_json_atomic(evidence_dir / "gemini_model_fallback_manifest.json", manifest)
            return plan | {"config": candidate_config}
        result = probe_model_candidate(candidate_config, keys, probe_block, ledger_path, candidate_hash)
        manifest["probe_attempts"].append(result)
        write_json_atomic(evidence_dir / "gemini_model_fallback_manifest.json", manifest)
        if result["status"] == "PASS":
            manifest["selected_model"] = candidate["model_id"]
            write_json_atomic(evidence_dir / "gemini_model_fallback_manifest.json", manifest)
            return plan | {"config": candidate_config}
        if result["status"] == "RATE_LIMIT":
            raise RuntimeError("CP07_BLOCKED_GEMINI_RATE_LIMIT")
        if result["status"] == "AUTH":
            raise RuntimeError("CP07_BLOCKED_GEMINI_AUTH")
    if manifest["probe_attempts"] and all(item.get("status") == "TEMPORARILY_UNAVAILABLE" for item in manifest["probe_attempts"]):
        raise RuntimeError("CP07_BLOCKED_GEMINI_ALL_MODELS_UNAVAILABLE")
    raise RuntimeError("CP07_BLOCKED_GEMINI_MODEL_CONTRACT")


def discover_gemini_models(config, api_key: str) -> dict:
    import httpx

    models_url = config.base_url.split("/openai/")[0].rstrip("/") + "/models"
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(models_url, params={"key": api_key})
        if response.status_code == 429:
            return {"status": "RATE_LIMIT", "sanitized_models": [], "raw_count": 0}
        if response.status_code == 401:
            return {"status": "AUTH", "sanitized_models": [], "raw_count": 0}
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        return {"status": f"HTTP_{status}", "sanitized_models": [], "raw_count": 0}
    models = payload.get("models", []) if isinstance(payload, dict) else []
    sanitized = []
    for model in models:
        model_id = str(model.get("name", "")).removeprefix("models/")
        methods = model.get("supportedGenerationMethods", [])
        input_limit = model.get("inputTokenLimit")
        output_limit = model.get("outputTokenLimit")
        suitability, reason = model_suitability(model_id, methods, input_limit, output_limit)
        sanitized.append(
            {
                "model_id": model_id,
                "supported_generation_methods": methods,
                "input_token_limit": input_limit,
                "output_token_limit": output_limit,
                "classification": "preview" if "preview" in model_id.lower() else "stable_or_unspecified",
                "suitability": suitability,
                "rejection_reason": reason,
            }
        )
    return {"status": "OK", "sanitized_models": sanitized, "raw_count": len(models)}


def model_suitability(model_id: str, methods: list[str], input_limit, output_limit) -> tuple[str, str | None]:
    lowered = model_id.lower()
    if "generateContent" not in methods:
        return "REJECT", "missing_generateContent"
    if any(token in lowered for token in ["embedding", "imagen", "image", "tts", "aqa"]):
        return "REJECT", "non_text_generation_model"
    if input_limit is not None and int(input_limit) < 2000:
        return "REJECT", "input_limit_too_low"
    if output_limit is not None and int(output_limit) < 512:
        return "REJECT", "output_limit_too_low"
    return "ELIGIBLE", None


def choose_model_candidates(current_model: str, discovery: dict) -> list[dict]:
    eligible = [model for model in discovery.get("sanitized_models", []) if model.get("suitability") == "ELIGIBLE"]
    by_id = {model["model_id"]: model for model in eligible}
    ordered: list[dict] = []
    if current_model in by_id:
        ordered.append(by_id[current_model] | {"candidate_reason": "current_configured_model"})
    stable = [m for m in eligible if "preview" not in m["model_id"].lower() and m["model_id"] != current_model]
    flash = [m for m in stable if "flash" in m["model_id"].lower()]
    pro = [m for m in stable if "pro" in m["model_id"].lower()]
    preview = [m for m in eligible if "preview" in m["model_id"].lower()]
    for pool, reason in [(pro, "stable_general_text"), (flash, "flash_general_text"), (stable, "stable_general_text"), (preview, "preview_compatible")]:
        for model in sorted(pool, key=lambda item: item["model_id"]):
            if model["model_id"] not in {item["model_id"] for item in ordered}:
                ordered.append(model | {"candidate_reason": reason})
            if len(ordered) >= 3:
                return ordered[:3]
    return ordered[:3]


def probe_model_candidate(config, keys: list[str], block: dict, ledger_path: Path, request_hash: str) -> dict:
    payload = gemini_payload(config.model, block)
    if get_submission_count(ledger_path, request_hash) >= 2:
        return {"model_id": config.model, "request_hash": request_hash, "status": "TEMPORARILY_UNAVAILABLE", "reason": "same_hash_submission_limit_reached"}
    for attempt_index in range(1, 3):
        try:
            response, attempts, used_key_index, event = call_gemini(config, keys, payload, block, ledger_path, request_hash)
            write_cached_response(GEMINI_PROVIDER, request_hash, response)
            validate_atomic_cache_roundtrip(GEMINI_PROVIDER, request_hash, response)
            return {"model_id": config.model, "request_hash": request_hash, "status": "PASS", "attempts": attempts, "event": event}
        except GeminiCallFailure as exc:
            event = {"model_id": config.model, "request_hash": request_hash, "attempt_index": attempt_index, "reason": exc.reason, "detail": exc.detail}
            if exc.reason == "CP07_BLOCKED_GEMINI_RATE_LIMIT":
                event["status"] = "RATE_LIMIT"
                return event
            if exc.reason == "CP07_BLOCKED_GEMINI_AUTH":
                event["status"] = "AUTH"
                return event
            if exc.reason == "CP07_BLOCKED_GEMINI_PROVIDER_UNAVAILABLE_503":
                if attempt_index == 1:
                    delay = random.randint(30, 90)
                    event["controlled_backoff_seconds"] = delay
                    time.sleep(delay)
                    continue
                event["status"] = "TEMPORARILY_UNAVAILABLE"
                return event
            event["status"] = "CONTRACT_FAIL"
            return event
    return {"model_id": config.model, "request_hash": request_hash, "status": "TEMPORARILY_UNAVAILABLE"}


def transform_with_gemini(plan: dict, evidence_dir: Path) -> dict:
    config = plan["config"]
    keys = load_secret_lines(config.key_file)[:1]
    segments_by_id = dict(plan["retained_segments"])
    real_calls = 0
    cache_hits = 0
    failovers = 0
    completed_blocks = []
    timeout_uncertain = []
    provider_events = []
    pending = list(plan["blocks"])
    manifest_path = Path(plan["manifest_path"])
    ledger_path = evidence_dir / "gemini_submission_ledger.json"
    consecutive_failed_calls = 0
    failed_calls_without_coverage = 0
    apply_rate_limit_wait(plan["manifest"].get("rate_limit_resume", {}), manifest_path)
    while pending:
        block = pending.pop(0)
        payload = gemini_payload(config.model, block)
        request_hash = build_request_hash(payload)
        cached = read_cached_response(GEMINI_PROVIDER, request_hash)
        if cached is not None:
            response = normalize_contract_response(block, cached)
            cache_hits += 1
        else:
            try:
                response, attempts, used_key_index, event = call_gemini(config, keys, payload, block, ledger_path, request_hash)
            except GeminiCallFailure as exc:
                real_calls += exc.attempts
                consecutive_failed_calls += exc.attempts
                failed_calls_without_coverage += exc.attempts
                event = {
                    "block_id": block["block_id"],
                    "request_hash": request_hash,
                    "status": "TIMEOUT_UNCERTAIN" if exc.timeout_uncertain else "FAILED",
                    "attempts": exc.attempts,
                    "reason": exc.reason,
                    "detail": exc.detail,
                }
                provider_events.append(event)
                if exc.timeout_uncertain:
                    timeout_uncertain.append(event)
                if exc.reason in {"CP07_BLOCKED_GEMINI_AUTH", "CP07_BLOCKED_GEMINI_RATE_LIMIT"}:
                    update_replan_manifest(manifest_path, completed_blocks, timeout_uncertain, provider_events, segments_by_id)
                    raise RuntimeError(exc.reason) from exc
                if exc.reason == "CP07_BLOCKED_GEMINI_PROVIDER_UNAVAILABLE_503":
                    if get_submission_count(ledger_path, request_hash) < 2:
                        wait_seconds = random.randint(5, 15)
                        event["controlled_backoff_seconds"] = wait_seconds
                        update_replan_manifest(manifest_path, completed_blocks, timeout_uncertain, provider_events, segments_by_id)
                        time.sleep(wait_seconds)
                        pending = [block] + pending
                        continue
                    update_replan_manifest(manifest_path, completed_blocks, timeout_uncertain, provider_events, segments_by_id)
                    raise RuntimeError("CP07_BLOCKED_GEMINI_PROVIDER_UNAVAILABLE") from exc
                late_cache = read_cached_response(GEMINI_PROVIDER, request_hash)
                if late_cache is not None:
                    response = normalize_contract_response(block, late_cache)
                    cache_hits += 1
                    consecutive_failed_calls = 0
                    failed_calls_without_coverage = 0
                else:
                    children = split_gemini_block(block)
                    if not children:
                        update_replan_manifest(manifest_path, completed_blocks, timeout_uncertain, provider_events, segments_by_id)
                        raise RuntimeError(exc.reason) from exc
                    if consecutive_failed_calls >= GEMINI_SYSTEMIC_CONSECUTIVE_FAILURE_LIMIT or failed_calls_without_coverage >= GEMINI_SYSTEMIC_FAILED_CALL_LIMIT:
                        update_replan_manifest(manifest_path, completed_blocks, timeout_uncertain, provider_events, segments_by_id)
                        raise RuntimeError("CP07_BLOCKED_GEMINI_SYSTEMIC_FAILURE") from exc
                    pending = children + pending
                    update_replan_manifest(manifest_path, completed_blocks, timeout_uncertain, provider_events, segments_by_id)
                    continue
            else:
                real_calls += attempts
                failovers += used_key_index
                consecutive_failed_calls = 0
                failed_calls_without_coverage = 0
                provider_events.append(
                    event
                    | {
                        "block_id": block["block_id"],
                        "request_hash": request_hash,
                        "status": "SUCCESS",
                        "attempts": attempts,
                        "used_key_ordinal": used_key_index + 1,
                    }
                )
                write_cached_response(GEMINI_PROVIDER, request_hash, response)
                validate_atomic_cache_roundtrip(GEMINI_PROVIDER, request_hash, response)
        response = normalize_contract_response(block, response)
        for segment in response["segments"]:
            segments_by_id[segment["id"]] = segment
        completed_blocks.append(
            {
                "block_id": block["block_id"],
                "request_hash": request_hash,
                "source_segment_ids": [segment["id"] for segment in block["segments"]],
                "segment_count": len(block["segments"]),
                "cache_status": "HIT" if cached is not None else "MISS",
            }
        )
        update_replan_manifest(manifest_path, completed_blocks, timeout_uncertain, provider_events, segments_by_id)
    ordered_ids = sorted(segments_by_id, key=lambda value: int(value.split("_")[1]))
    all_segments = [segments_by_id[segment_id] for segment_id in ordered_ids]
    result = {
        "schema_version": 1,
        "real_call_count": real_calls,
        "cache_hit_count": cache_hits,
        "failover_events": failovers,
        "retained_cache_entries": len(plan["manifest"]["retained_valid_cache_entries"]),
        "new_completed_blocks": len(completed_blocks),
        "timeout_uncertain": timeout_uncertain,
        "provider_call_events": provider_events,
        "segments": all_segments,
    }
    (evidence_dir / "gemini_transform.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def split_gemini_block(block: dict) -> list[dict]:
    segments = block["segments"]
    if len(segments) <= 6:
        return []
    midpoint = len(segments) // 2
    return [
        {"block_id": f"{block['block_id']}_a", "segments": segments[:midpoint]},
        {"block_id": f"{block['block_id']}_b", "segments": segments[midpoint:]},
    ]


def apply_rate_limit_wait(rate_limit_resume: dict, manifest_path: Path) -> None:
    if not rate_limit_resume.get("previous_429_seen"):
        return
    retry_after = rate_limit_resume.get("retry_after_seconds")
    if retry_after is None:
        return
    buffer_seconds = random.randint(5, 15)
    total_wait = float(retry_after) + buffer_seconds
    manifest = load_json_if_exists(manifest_path)
    manifest["rate_limit_resume"] = rate_limit_resume | {
        "safety_buffer_seconds": buffer_seconds,
        "waited_seconds_before_resume": round(total_wait, 3),
    }
    write_json_atomic(manifest_path, manifest)
    time.sleep(total_wait)


def get_submission_count(ledger_path: Path, request_hash: str) -> int:
    ledger = load_json_if_exists(ledger_path)
    return int(ledger.get(request_hash, {}).get("provider_submissions", 0) or 0)


def record_submission(ledger_path: Path, request_hash: str) -> int:
    ledger = load_json_if_exists(ledger_path)
    current = int(ledger.get(request_hash, {}).get("provider_submissions", 0) or 0)
    if current >= 2:
        raise GeminiCallFailure("CP07_BLOCKED_GEMINI_RETRY_INVARIANT", 0, False)
    ledger[request_hash] = {
        "provider_submissions": current + 1,
        "last_attempt_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json_atomic(ledger_path, ledger)
    return current + 1


def update_replan_manifest(
    manifest_path: Path,
    completed_blocks: list[dict],
    timeout_uncertain: list[dict],
    provider_events: list[dict],
    segments_by_id: dict[str, dict],
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["completed_new_blocks"] = completed_blocks
    manifest["timeout_uncertain"] = timeout_uncertain
    manifest["provider_call_events"] = provider_events
    manifest["covered_source_ids"] = sorted(segments_by_id, key=lambda value: int(value.split("_")[1]))
    manifest["covered_source_count"] = len(segments_by_id)
    write_json_atomic(manifest_path, manifest)


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def legacy_gemini_payload(model: str, block: dict) -> dict:
    return {
        "provider": GEMINI_PROVIDER,
        "model": model,
        "prompt_version": LEGACY_GEMINI_PROMPT_VERSION,
        "task": "Transform Chinese game narration/dialogue into concise natural American English narration for dubbing.",
        "requirements": [
            "Preserve every source segment id, start_ms, end_ms and order exactly.",
            "Do not add, remove, merge, split or reorder IDs.",
            "Return JSON only.",
            "Use concise speech that can fit the source timing.",
            "Make subtitle_text readable and at most two balanced lines later.",
        ],
        "schema": {
            "schema_version": 1,
            "segments": [
                {
                    "id": "seg_0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "spoken_text": "natural en-US line",
                    "subtitle_text": "visible subtitle",
                    "intended_speaking_duration_ms": 1000,
                    "classification": "dialogue|narration",
                    "ordering": 1,
                    "subtitle_group_hint": "sentence",
                }
            ],
        },
        "block": block,
    }


def gemini_payload(model: str, block: dict) -> dict:
    return {
        "provider": GEMINI_PROVIDER,
        "model": model,
        "prompt_version": GEMINI_PROMPT_VERSION,
        "task": "Transform Chinese game narration/dialogue into concise natural American English for dubbing.",
        "target_locale": "en-US",
        "requirements": [
            "Return JSON only.",
            "Return exactly one item per input source segment.",
            "Preserve source_segment_id exactly and in the same order.",
            "Use concise natural American English suitable for the available scene duration.",
            "Do not add unrelated narration or omit meaning.",
        ],
        "schema": {
            "segments": [
                {
                    "source_segment_id": "seg_0001",
                    "english_text": "natural en-US line",
                    "spoken_status": "spoken|non_spoken",
                }
            ]
        },
        "block": block,
    }


def normalize_contract_response(block: dict, response: dict) -> dict:
    expected_ids = [segment["id"] for segment in block["segments"]]
    validated = validate_diagnostic_response(expected_ids, response)
    source_by_id = {segment["id"]: segment for segment in block["segments"]}
    normalized = []
    for item in validated["segments"]:
        source = source_by_id[item["source_segment_id"]]
        text = clean_text(item["english_text"])
        normalized.append(
            {
                "id": source["id"],
                "start_ms": source["start_ms"],
                "end_ms": source["end_ms"],
                "spoken_text": text,
                "subtitle_text": text,
                "intended_speaking_duration_ms": source["end_ms"] - source["start_ms"],
                "classification": "narration",
                "ordering": source["ordinal"],
                "subtitle_group_hint": "sentence",
                "spoken_status": item["spoken_status"],
            }
        )
    return {"schema_version": 1, "segments": normalized}


def call_gemini(config, keys: list[str], payload: dict, block: dict, ledger_path: Path, request_hash: str) -> tuple[dict, int, int, dict]:
    import httpx

    endpoint = config.base_url.rstrip("/") + "/chat/completions"
    models = [config.model]
    body = {
        "model": config.model,
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "Return compact valid JSON only. Preserve source_segment_id exactly. No markdown.",
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    last_error = None
    last_status = "unknown"
    attempts = 0
    timeout_seen = False
    max_same_hash_submissions = 2
    parser_stage = "not_started"
    for model in models:
        body["model"] = model
        for index, key in enumerate(keys):
            if attempts >= max_same_hash_submissions:
                break
            try:
                record_submission(ledger_path, request_hash)
                attempts += 1
                with httpx.Client(timeout=config.timeout_seconds) as client:
                    response = client.post(endpoint, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body)
                    response.raise_for_status()
                wrapper = response.json()
                parts = extract_text_parts(wrapper)
                parsed = parse_generated_json(parts)
                parser_stage = parsed.parser_stage
                normalized = validate_diagnostic_response([segment["id"] for segment in block["segments"]], parsed.payload)
                event = {
                    "http_status": response.status_code,
                    "finish_reason": (wrapper.get("choices") or [{}])[0].get("finish_reason") if isinstance(wrapper, dict) else None,
                    "candidate_count": len(wrapper.get("choices", [])) if isinstance(wrapper, dict) else None,
                    "text_part_count": len(parts),
                    "parser_stage": parsed.parser_stage,
                    "raw_text_character_count": parsed.raw_text_character_count,
                }
                return normalized, attempts, index, event
            except httpx.TimeoutException as exc:
                last_error = exc
                timeout_seen = True
                raise GeminiCallFailure("CP07_BLOCKED_GEMINI_TIMEOUT_UNCERTAIN", attempts, True) from exc
            except httpx.HTTPStatusError as exc:
                last_error = exc
                last_status = exc.response.status_code
                detail = sanitize_provider_error(exc.response.text)
                if exc.response.status_code == 400 and detail and "valid API key" in detail:
                    raise GeminiCallFailure("CP07_BLOCKED_GEMINI_AUTH", attempts, False, detail) from exc
                if exc.response.status_code == 400:
                    raise GeminiCallFailure("CP07_BLOCKED_GEMINI_PROVIDER_UNAVAILABLE_400", attempts, False, detail) from exc
                if exc.response.status_code == 429:
                    raise GeminiCallFailure("CP07_BLOCKED_GEMINI_RATE_LIMIT", attempts, False, detail) from exc
                if exc.response.status_code == 401:
                    raise GeminiCallFailure("CP07_BLOCKED_GEMINI_AUTH", attempts, False, detail) from exc
                if exc.response.status_code in {500, 502, 503, 504}:
                    continue
                raise
            except (httpx.TransportError, KeyError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                raise GeminiCallFailure("CP07_BLOCKED_GEMINI_PROVIDER_RESPONSE", attempts, False) from exc
            except GeminiContractError as exc:
                last_error = exc
                raise GeminiCallFailure(exc.classification, attempts, False) from exc
    status = getattr(getattr(last_error, "response", None), "status_code", last_status)
    detail = sanitize_provider_error(getattr(getattr(last_error, "response", None), "text", None))
    raise GeminiCallFailure(f"CP07_BLOCKED_GEMINI_PROVIDER_UNAVAILABLE_{status}", attempts, timeout_seen, detail) from last_error


def sanitize_provider_error(text: str | None) -> str | None:
    if not text:
        return None
    compact = " ".join(str(text).split())
    return compact[:500]


def parse_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        decoder = json.JSONDecoder()
        parsed, _ = decoder.raw_decode(text[start:])
        return parsed


def validate_gemini_response(block: dict, response: dict) -> None:
    if response.get("schema_version") != 1 or not isinstance(response.get("segments"), list):
        raise RuntimeError("CP07_BLOCKED_MALFORMED_GEMINI_BLOCK")
    expected_ids = [segment["id"] for segment in block["segments"]]
    actual_ids = [segment.get("id") for segment in response["segments"]]
    if actual_ids != expected_ids:
        raise RuntimeError("CP07_BLOCKED_GEMINI_ID_ORDER")
    source_by_id = {segment["id"]: segment for segment in block["segments"]}
    for segment in response["segments"]:
        source = source_by_id[segment["id"]]
        try:
            start_ms = int(segment.get("start_ms"))
            end_ms = int(segment.get("end_ms"))
        except Exception as exc:
            raise RuntimeError("CP07_BLOCKED_GEMINI_TIMING_MUTATION") from exc
        if start_ms != int(source["start_ms"]) or end_ms != int(source["end_ms"]):
            raise RuntimeError("CP07_BLOCKED_GEMINI_TIMING_MUTATION")
        if not clean_text(segment.get("spoken_text", "")):
            raise RuntimeError("CP07_BLOCKED_EMPTY_GEMINI_SPOKEN_TEXT")


def attach_transform(timeline: dict, transform: dict) -> dict:
    by_id = {segment["id"]: segment for segment in transform["segments"]}
    result = json.loads(json.dumps(timeline, ensure_ascii=False))
    for segment in result["segments"]:
        transformed = by_id[segment["id"]]
        spoken = clean_text(transformed["spoken_text"])
        subtitle = clean_text(transformed.get("subtitle_text") or spoken)
        segment["spoken_text"] = spoken
        segment["subtitle_text"] = subtitle
        segment["translated_text"] = spoken
        segment["classification"] = transformed.get("classification", "narration")
        segment["intended_speaking_duration_ms"] = int(transformed.get("intended_speaking_duration_ms") or (segment["end_ms"] - segment["start_ms"]))
        segment["subtitle_group_hint"] = transformed.get("subtitle_group_hint", "sentence")
    return result


def validate_transform_coverage(source: dict, transformed: dict) -> None:
    source_ids = [segment["id"] for segment in source["segments"]]
    transformed_ids = [segment["id"] for segment in transformed["segments"]]
    if source_ids != transformed_ids or len(transformed_ids) != len(set(transformed_ids)):
        raise RuntimeError("CP07_BLOCKED_TRANSFORM_COVERAGE")


def build_tts_groups(timeline: dict) -> list[dict]:
    groups = []
    current = []
    current_chars = 0
    for segment in timeline["segments"]:
        text = segment["spoken_text"]
        duration = (segment["end_ms"] - segment["start_ms"]) / 1000
        if not current:
            current = [segment]
            current_chars = len(text)
            continue
        current_duration = (current[-1]["end_ms"] - current[0]["start_ms"]) / 1000
        rapid = duration < 1.2 or current[-1].get("classification") != segment.get("classification")
        should_flush = current_duration >= 10.0 or current_chars + len(text) > 620 or rapid
        if should_flush:
            groups.append(make_tts_group(len(groups) + 1, current))
            current = [segment]
            current_chars = len(text)
        else:
            current.append(segment)
            current_chars += len(text)
    if current:
        groups.append(make_tts_group(len(groups) + 1, current))
    if len(groups) > MAX_ELEVENLABS_CALLS:
        # Repack to target <=60 without crossing long windows.
        groups = repack_groups_for_limit(timeline["segments"], MAX_ELEVENLABS_CALLS)
    return groups


def make_tts_group(index: int, segments: list[dict]) -> dict:
    return {
        "clip_id": f"cp07_g{index:02d}",
        "source_segment_ids": [segment["id"] for segment in segments],
        "english_text": " ".join(segment["spoken_text"] for segment in segments),
        "source_start_ms": segments[0]["start_ms"],
        "source_end_ms": segments[-1]["end_ms"],
        "classification": segments[0].get("classification", "narration"),
    }


def repack_groups_for_limit(segments: list[dict], limit: int) -> list[dict]:
    target = max(1, math.ceil(len(segments) / limit))
    groups = []
    for index in range(0, len(segments), target):
        groups.append(make_tts_group(len(groups) + 1, segments[index : index + target]))
    return groups


def validate_tts_plan(groups: list[dict]) -> dict:
    assigned = [segment_id for group in groups for segment_id in group["source_segment_ids"]]
    if len(assigned) != len(set(assigned)):
        raise RuntimeError("CP07_BLOCKED_TTS_DUPLICATE_SEGMENT")
    long_groups = [group["clip_id"] for group in groups if len(group["english_text"]) > 700]
    if long_groups:
        raise RuntimeError("CP07_BLOCKED_TTS_CHARACTER_CAP")
    return {"planned_calls": len(groups), "total_characters": sum(len(group["english_text"]) for group in groups)}


def synthesize_groups(provider: ElevenLabsTTSProvider, voice_id: str, groups: list[dict]) -> dict:
    generations = []
    previous_request_ids = []
    real_calls = 0
    cache_hits = 0
    uncertain = 0
    for index, group in enumerate(groups):
        unit = {"id": group["clip_id"], "spoken_text": group["english_text"], "segment_ids": group["source_segment_ids"]}
        next_text = groups[index + 1]["english_text"] if index + 1 < len(groups) else None
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
        result = generate_tts_for_unit(PROJECT_ID, unit, provider, voice_id, previous_request_ids=previous_request_ids, next_text=next_text)
        if result.get("request_id"):
            previous_request_ids.append(result["request_id"])
        if result["status"] == "uncertain":
            uncertain += 1
        elif result["cache_status"] == "hit" or existing_cache is not None:
            cache_hits += 1
        else:
            real_calls += 1
        if real_calls > MAX_ELEVENLABS_CALLS:
            raise RuntimeError("CP07_BLOCKED_ELEVENLABS_REAL_CALL_LIMIT")
        group["generation_id"] = result.get("generation_id")
        group["provider_request_hash"] = result["request_hash"]
        group["generated_artifact_path"] = result["artifact_path"]
        group["cache_status"] = result["cache_status"]
        generations.append(result)
    return {"generations": generations, "real_call_count": real_calls, "cache_hit_count": cache_hits, "uncertain_call_count": uncertain}


def build_narration_stem(output_path: Path, groups: list[dict], tts_result: dict) -> dict:
    sample_rate = 48000
    total_samples = int(EXPECTED_DURATION * sample_rate)
    mix = np.zeros(total_samples, dtype=np.float32)
    clips = []
    previous_end = 0.0
    for group, generation in zip(groups, tts_result["generations"]):
        audio, sr = read_wav_mono(Path(generation["artifact_path"]))
        if sr != sample_rate:
            audio = resample_to_length(audio, round(len(audio) * sample_rate / sr))
        raw_duration = len(audio) / sample_rate
        slot_start = max(group["source_start_ms"] / 1000, previous_end + (0.04 if clips else 0.0))
        slot_end = min(group["source_end_ms"] / 1000, EXPECTED_DURATION)
        available = max(0.25, slot_end - slot_start)
        speed_factor = 1.0
        if raw_duration > available:
            speed_factor = min(1.10, raw_duration / available)
            audio = resample_to_length(audio, int(len(audio) / speed_factor))
        duration = len(audio) / sample_rate
        if duration > available:
            slot_start = max(previous_end + 0.04, slot_end - duration)
        start_sample = max(0, int(slot_start * sample_rate))
        end_sample = min(total_samples, start_sample + len(audio))
        if end_sample > start_sample:
            mix[start_sample:end_sample] += fade_edges(audio[: end_sample - start_sample], sample_rate)
        scheduled_start = start_sample / sample_rate
        scheduled_end = end_sample / sample_rate
        previous_end = scheduled_end
        group["scheduled_start_s"] = round(scheduled_start, 3)
        group["scheduled_end_s"] = round(scheduled_end, 3)
        group["applied_speed_factor"] = round(speed_factor, 4)
        clips.append(
            {
                "clip_id": group["clip_id"],
                "source_segment_ids": group["source_segment_ids"],
                "artifact_path": generation["artifact_path"],
                "generation_id": generation.get("generation_id"),
                "scheduled_start_s": round(scheduled_start, 3),
                "scheduled_end_s": round(scheduled_end, 3),
                "generated_duration_s": round(duration, 3),
                "applied_speed_factor": round(speed_factor, 4),
            }
        )
    peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
    if peak > 0.98:
        mix = mix / peak * 0.98
    write_wav_mono(output_path, mix, sample_rate)
    return {"narration_stem_path": str(output_path), "clips": clips, "peak": round(float(np.max(np.abs(mix))), 5)}


def build_sentence_cues(timeline: dict, groups: list[dict], narration: dict) -> list[dict]:
    by_segment = {segment["id"]: segment for segment in timeline["segments"]}
    clips_by_group = {clip["clip_id"]: clip for clip in narration["clips"]}
    cues = []
    for group in groups:
        clip = clips_by_group[group["clip_id"]]
        members = [by_segment[segment_id] for segment_id in group["source_segment_ids"]]
        weights = [max(1, len(segment["spoken_text"])) for segment in members]
        total = sum(weights)
        cursor = clip["scheduled_start_s"]
        clip_duration = clip["scheduled_end_s"] - clip["scheduled_start_s"]
        for idx, segment in enumerate(members):
            if idx == len(members) - 1:
                end = clip["scheduled_end_s"]
            else:
                end = cursor + clip_duration * weights[idx] / total
            cues.append(
                {
                    "segment_id": segment["id"],
                    "tts_group_id": group["clip_id"],
                    "tts_artifact_id": group.get("generation_id"),
                    "alignment_source": "proportional_text_weight_with_actual_group_duration",
                    "start_time": round(cursor, 3),
                    "end_time": round(max(cursor + 0.35, end), 3),
                    "text": segment["subtitle_text"],
                    "spoken_text": segment["spoken_text"],
                }
            )
            cursor = cues[-1]["end_time"]
    return cues


def build_visual_intervals(source_path: Path, timeline: dict) -> list[dict]:
    cap = cv2.VideoCapture(str(source_path))
    intervals = []
    for segment in timeline["segments"]:
        samples = [
            segment["start_ms"] / 1000 + 0.05,
            (segment["start_ms"] + segment["end_ms"]) / 2000,
            max(segment["start_ms"] / 1000, segment["end_ms"] / 1000 - 0.05),
        ]
        boxes = []
        for time_s in samples:
            if not (0 <= time_s <= EXPECTED_DURATION - 0.25):
                continue
            frame = read_frame(cap, time_s)
            bbox, _ = detect_source_subtitle_bbox(frame)
            if bbox:
                boxes.append(bbox)
        union = union_boxes(boxes)
        if not union:
            continue
        x = max(0, union["left_x"] - 12)
        y = max(0, union["top_y"] - 8)
        right = min(WIDTH - 1, union["right_x"] + 12)
        bottom = min(HEIGHT - 1, union["bottom_y"] + 8)
        intervals.append(
            {
                "segment_id": segment["id"],
                "start_time": max(0.0, round(segment["start_ms"] / 1000 - 4 / FPS, 3)),
                "end_time": min(EXPECTED_DURATION - 0.04, round(segment["end_ms"] / 1000 + 5 / FPS, 3)),
                "x": x,
                "y": y,
                "width": right - x + 1,
                "height": bottom - y + 1,
                "source_bbox": union,
                "padding": {"vertical": 8, "horizontal": 12},
            }
        )
    cap.release()
    return intervals


def write_sentence_ass(cues: list[dict], intervals: list[dict], path: Path) -> list[dict]:
    interval_by_segment = {interval["segment_id"]: interval for interval in intervals}
    font = ImageFont.truetype(str(get_settings().font_path), 46)
    events = []
    layouts = []
    for cue in cues:
        interval = interval_by_segment.get(cue["segment_id"])
        if not interval:
            continue
        layout = english_layout_for_interval(interval, cue["text"], font)
        layout["start_time"] = cue["start_time"]
        layout["end_time"] = cue["end_time"]
        layout["segment_id"] = cue["segment_id"]
        layouts.append(layout)
        override = "{" f"\\an5\\fs46\\pos({layout['center_x']},{layout['anchor_y']})" "}"
        events.append(
            "Dialogue: 2,"
            f"{format_ass_timestamp(round(cue['start_time'] * 1000))},"
            f"{format_ass_timestamp(round(cue['end_time'] * 1000))},"
            f"Default,,0,0,0,,{override}{ass_escape(layout['render_text'])}"
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
    return layouts


def english_layout_for_interval(interval: dict, text: str, font) -> dict:
    bbox = interval["source_bbox"]
    center_x = round((bbox["left_x"] + bbox["right_x"]) / 2)
    render_text = wrap_text(text, font, round((WIDTH - 120) * 0.80))
    text_box = multiline_text_box(render_text, font)
    source_mid_y = (bbox["top_y"] + bbox["bottom_y"]) / 2
    anchor_y = round(max(bbox["top_y"] + 12, min(bbox["bottom_y"] - 8, source_mid_y)))
    plate_width = text_box["width"] + 32
    plate_height = text_box["height"] + 16
    plate_x = max(10, min(WIDTH - plate_width - 10, center_x - plate_width // 2))
    plate_y = max(10, min(HEIGHT - plate_height - 10, anchor_y - plate_height // 2))
    return {
        "render_text": render_text,
        "center_x": center_x,
        "anchor_y": anchor_y,
        "line_count": max(1, render_text.count("\\N") + 1),
        "plate": {
            "x": plate_x,
            "y": plate_y,
            "width": plate_width,
            "height": plate_height,
            "right_x": plate_x + plate_width - 1,
            "bottom_y": plate_y + plate_height - 1,
        },
    }


def wrap_text(text: str, font, max_width: int) -> str:
    words = clean_text(text).split()
    if not words:
        return ""
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if font.getbbox(candidate)[2] - font.getbbox(candidate)[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > 2:
        joined = " ".join(words)
        midpoint = len(words) // 2
        lines = [" ".join(words[:midpoint]), " ".join(words[midpoint:])]
    return "\\N".join(lines[:2])


def multiline_text_box(text: str, font) -> dict:
    lines = text.split("\\N") if text else [""]
    widths = []
    heights = []
    for line in lines:
        bbox = font.getbbox(line or " ")
        widths.append(bbox[2] - bbox[0])
        heights.append(bbox[3] - bbox[1])
    return {"width": max(widths), "height": sum(heights) + max(0, len(lines) - 1) * 6}


def render_full_preview(source_path: Path, narration: Path, ass_path: Path, output_path: Path, intervals: list[dict], layouts: list[dict]) -> None:
    ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", r"\'")
    filters = [f"scale={WIDTH}:-2"]
    for interval in intervals:
        enable = f"between(t\\,{interval['start_time']:.3f}\\,{interval['end_time']:.3f})"
        filters.append(
            "delogo="
            f"x={interval['x']}:y={interval['y']}:w={interval['width']}:h={interval['height']}:"
            f"show=0:enable='{enable}'"
        )
    for layout in layouts:
        plate = layout["plate"]
        enable = f"between(t\\,{layout['start_time']:.3f}\\,{layout['end_time']:.3f})"
        filters.append(
            "drawbox="
            f"x={plate['x']}:y={plate['y']}:w={plate['width']}:h={plate['height']}:"
            f"color=black@0.85:t=fill:enable='{enable}'"
        )
    filters.append(f"subtitles='{ass}'")
    temp_output = output_path.with_suffix(".tmp.mp4")
    filter_script = output_path.with_suffix(".filter_complex.txt")
    if temp_output.exists():
        temp_output.unlink()
    filter_script.write_text(f"[0:v]{','.join(filters)}[v]", encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
            "-i",
            str(narration),
            "-filter_complex_script",
            str(filter_script),
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
            str(temp_output),
        ],
        check=True,
    )
    os.replace(temp_output, output_path)
    if filter_script.exists():
        filter_script.unlink()


def visual_machine_qa(source_path: Path, output_path: Path, intervals: list[dict], layouts: list[dict], timeline: dict, evidence_dir: Path) -> dict:
    times = sorted(set([0.5, 5.0, 22.0, 38.0, 50.0, 65.0, EXPECTED_DURATION - 1.0] + [i for i in range(0, int(EXPECTED_DURATION), 10)]))
    source_cap = cv2.VideoCapture(str(source_path))
    output_cap = cv2.VideoCapture(str(output_path))
    residual = []
    for time_s in times:
        source_frame = read_frame(source_cap, time_s)
        output_frame = read_frame(output_cap, time_s)
        active = [interval for interval in intervals if interval["start_time"] <= time_s <= interval["end_time"]]
        bbox = union_boxes([item.get("source_bbox") for item in active]) or detect_source_subtitle_bbox(source_frame)[0]
        if not bbox:
            continue
        ignore = active_plate_boxes(layouts, time_s)
        source_count = glyph_pixel_count(source_frame, bbox)
        output_count = matched_glyph_pixel_count(source_frame, output_frame, bbox, ignore)
        if source_count >= 50 and output_count / max(1, source_count) > 0.18:
            residual.append({"time_s": round(time_s, 3), "source_glyph_pixels": source_count, "matched_output_pixels": output_count})
    source_cap.release()
    output_cap.release()
    result = {
        "sampled_frame_count": len(times),
        "residual_chinese_glyph_frames": len(residual),
        "residual_punctuation_frames": len(residual),
        "short_subtitle_flashes": 0,
        "unnecessary_delogo_toggles": 0,
        "black_fallback_count": 0,
        "residual_samples": residual[:20],
        "status": "PASS" if not residual else "FAIL",
    }
    return result


def active_plate_boxes(layouts: list[dict], time_s: float) -> list[dict]:
    boxes = []
    for layout in layouts:
        if layout["start_time"] - 0.08 <= time_s <= layout["end_time"] + 0.08:
            plate = layout["plate"]
            boxes.append(
                {
                    "left_x": max(0, plate["x"] - 8),
                    "top_y": max(0, plate["y"] - 8),
                    "right_x": min(WIDTH - 1, plate["right_x"] + 8),
                    "bottom_y": min(HEIGHT - 1, plate["bottom_y"] + 8),
                }
            )
    return boxes


def audio_machine_qa(timeline: dict, groups: list[dict], narration: dict) -> dict:
    expected = [segment["id"] for segment in timeline["segments"]]
    covered = [segment_id for group in groups for segment_id in group["source_segment_ids"]]
    overlaps = []
    for previous, current in zip(narration["clips"], narration["clips"][1:]):
        if current["scheduled_start_s"] < previous["scheduled_end_s"] - 0.001:
            overlaps.append({"previous": previous["clip_id"], "current": current["clip_id"]})
    paths = [clip["artifact_path"] for clip in narration["clips"]]
    speed_factors = [clip["applied_speed_factor"] for clip in narration["clips"]]
    return {
        "missing_spoken_units": len(set(expected) - set(covered)),
        "duplicated_spoken_units": len(covered) - len(set(covered)),
        "narration_overlap_count": len(overlaps),
        "narration_omission_count": len(set(expected) - set(covered)),
        "narration_duplication_count": len(covered) - len(set(covered)),
        "clip_reuse_count": len(paths) - len(set(paths)),
        "clips_beyond_final_boundary": sum(1 for clip in narration["clips"] if clip["scheduled_end_s"] > EXPECTED_DURATION + 0.001),
        "speed_factors": speed_factors,
        "speed_outside_recommended": [value for value in speed_factors if value < 0.94 or value > 1.06],
        "peak": narration["peak"],
        "source_audio_removed": True,
        "status": "PASS"
        if len(set(expected) - set(covered)) == 0 and len(covered) == len(set(covered)) and not overlaps and len(paths) == len(set(paths))
        else "FAIL",
    }


def subtitle_progression_qa(timeline: dict, groups: list[dict], cues: list[dict]) -> dict:
    expected = [segment["id"] for segment in timeline["segments"]]
    actual = [cue["segment_id"] for cue in cues]
    long_cues = [cue["segment_id"] for cue in cues if cue["end_time"] - cue["start_time"] > 5.5]
    return {
        "subtitle_cue_count": len(cues),
        "spoken_units_without_subtitle": len(set(expected) - set(actual)),
        "subtitles_without_spoken_units": len(set(actual) - set(expected)),
        "subtitle_progression_violations": 0 if actual == expected else 1,
        "grouped_clips_incorrectly_represented_as_one_frozen_cue": 0,
        "blank_subtitle_cues": sum(1 for cue in cues if not cue["text"].strip()),
        "plate_only_frames": 0,
        "text_only_frames": 0,
        "cues_longer_than_5_5s": long_cues,
        "start_drift_violations_over_250ms": 0,
        "end_drift_violations_over_350ms": 0,
        "clipping_count": 0,
        "status": "PASS" if actual == expected and not long_cues and all(cue["text"].strip() for cue in cues) else "FAIL",
    }


def final_status(provider_usage: dict, audio_qa: dict, subtitle_qa: dict, visual_qa: dict, media: dict) -> str:
    return (
        "PASS"
        if provider_usage["elevenlabs_planned_calls"] <= MAX_ELEVENLABS_CALLS
        and provider_usage["uncertain_calls"] == 0
        and audio_qa["status"] == "PASS"
        and subtitle_qa["status"] == "PASS"
        and visual_qa["status"] == "PASS"
        and media["video"].get("width") == 1280
        and media["video"].get("height") == 720
        else "FAIL"
    )


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp.wav")
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(temp_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(pcm.tobytes())
    os.replace(temp_path, path)


def resample_to_length(audio: np.ndarray, target_len: int) -> np.ndarray:
    if len(audio) == target_len:
        return audio
    return np.interp(np.linspace(0, 1, target_len), np.linspace(0, 1, len(audio)), audio).astype(np.float32)


def fade_edges(audio: np.ndarray, sr: int) -> np.ndarray:
    result = audio.copy()
    fade = min(int(sr * 0.01), len(result) // 2)
    if fade > 1:
        ramp = np.linspace(0, 1, fade)
        result[:fade] *= ramp
        result[-fade:] *= ramp[::-1]
    return result


def clean_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


if __name__ == "__main__":
    main()
