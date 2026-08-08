import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.db.session import init_db, session_scope
from app.domain.models import TTSGeneration
from app.providers.tts.elevenlabs import ElevenLabsTTSProvider, load_elevenlabs_config
from app.providers.tts.fake import FakeTTSProvider
from app.services.artifacts import register_artifact
from app.services.preview_render import build_video_filter, evaluate_tts_fit, write_tts_mix
from app.services.subtitles import write_ass, write_srt
from app.services.timeline import load_latest_timeline, save_timeline_revision, validate_timeline
from app.services.tts_generation import generate_tts_for_timeline, resolve_voice_id
from app.services.tts_units import attach_tts_synthesis_units


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CP06B grouped vertical-slice validation.")
    parser.add_argument("--provider", choices=["fake", "elevenlabs"], default="elevenlabs")
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--evidence-dir", default="evidence/CP06B")
    parser.add_argument("--max-real-calls", type=int, default=7)
    args = parser.parse_args()

    init_db()
    settings = get_settings()
    project_id = "vertical_slice_cp02"
    evidence_dir = (settings.root / args.evidence_dir).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    timeline = load_latest_timeline(project_id)
    units = attach_tts_synthesis_units(timeline)
    validate_timeline(timeline)
    if len(units) >= len(timeline["segments"]):
        raise RuntimeError("BLOCKED_GROUPING_REGRESSION: TTS units still match subtitle fragments")
    if len(units) > args.max_real_calls:
        raise RuntimeError(f"BLOCKED_CALL_BUDGET: grouped unit count {len(units)} exceeds {args.max_real_calls}")

    provider = FakeTTSProvider() if args.provider == "fake" else ElevenLabsTTSProvider(load_elevenlabs_config())
    voice_id = resolve_voice_id(None, provider)
    char_count = sum(len(unit["spoken_text"]) for unit in units)
    grouping_manifest = {
        "project_id": project_id,
        "subtitle_units": len(timeline["segments"]),
        "tts_units": len(units),
        "total_spoken_characters": char_count,
        "units": [
            {
                "id": unit["id"],
                "start_ms": unit["start_ms"],
                "end_ms": unit["end_ms"],
                "duration_ms": unit["end_ms"] - unit["start_ms"],
                "characters": len(unit["spoken_text"]),
                "segment_count": len(unit["segment_ids"]),
                "segment_ids": unit["segment_ids"],
            }
            for unit in units
        ],
    }
    _write_json(evidence_dir / f"{args.run_label}_grouping_manifest.json", grouping_manifest)

    generations = generate_tts_for_timeline(project_id, timeline, provider, voice_id)
    for unit, result in zip(timeline["tts_units"], generations, strict=True):
        if result["status"] != "ready" or not result["artifact_path"] or not Path(result["artifact_path"]).exists():
            raise RuntimeError(f"TTS generation not ready for grouped synthesis unit {unit['id']}")
    generations = ensure_local_fit_adjustments(project_id, timeline, generations, evidence_dir, args.run_label)

    fit_results = evaluate_tts_fit(timeline, generations)
    fit_failures = [result for result in fit_results if result["status"] == "FAIL"]
    _write_json(evidence_dir / f"{args.run_label}_fit_ratios.json", fit_results)
    if fit_failures:
        raise RuntimeError(f"TTS fit gate failed for {len(fit_failures)} grouped synthesis units")

    binding_audit = _audit_active_bindings(timeline)
    _write_json(evidence_dir / f"{args.run_label}_active_binding_audit.json", binding_audit)
    if not binding_audit["pass"]:
        raise RuntimeError(f"Active binding audit failed: {binding_audit['errors']}")

    timeline_revision = save_timeline_revision(project_id, timeline)
    project_dir = settings.data_dir / "projects" / project_id
    subtitle_dir = project_dir / "subtitles"
    render_dir = project_dir / "renders"
    render_dir.mkdir(parents=True, exist_ok=True)

    srt_path = write_srt(timeline, subtitle_dir / "cp06b_grouped_final.srt")
    ass_path = write_ass(timeline, subtitle_dir / "cp06b_grouped_final.ass")
    mix_path = write_tts_mix(timeline, generations, project_dir / "audio" / "cp06b_grouped_tts_mix.wav")
    preview_path = render_dir / "cp06b_grouped_vertical_slice_720p.mp4"
    _render_preview(settings.source_path, mix_path, ass_path, timeline, preview_path)

    media = media_summary(preview_path)
    ffprobe_path = evidence_dir / f"{args.run_label}_ffprobe.json"
    ffprobe_path.write_text(json.dumps(media, indent=2), encoding="utf-8")
    contact_sheet_path = evidence_dir / f"{args.run_label}_contact_sheet.jpg"
    _write_contact_sheet(preview_path, contact_sheet_path)

    artifacts = {
        "srt": register_artifact(project_id, "cp06b_srt", srt_path),
        "ass": register_artifact(project_id, "cp06b_ass", ass_path),
        "mix": register_artifact(project_id, "cp06b_tts_mix", mix_path),
        "preview": register_artifact(project_id, "cp06b_preview_720p", preview_path),
    }
    cache_hits = sum(1 for item in generations if item["cache_status"] == "hit")
    real_calls = sum(1 for item in generations if item["cache_status"] == "miss")
    run_summary = {
        "project_id": project_id,
        "provider": provider.provider_name,
        "audio_policy": "replace_all_audio",
        "source_audio_mapped": False,
        "subtitle_units": len(timeline["segments"]),
        "tts_synthesis_units": len(timeline["tts_units"]),
        "timeline_revision_id": timeline_revision["revision_id"],
        "tts_fit_pass": sum(result["status"] == "PASS" for result in fit_results),
        "tts_fit_warn": sum(result["status"] == "WARN" for result in fit_results),
        "tts_fit_fail": len(fit_failures),
        "cache_hits": cache_hits,
        "real_calls": real_calls,
        "generation_ids": [item["generation_id"] for item in generations],
        "artifact_paths": {
            "srt": str(srt_path),
            "ass": str(ass_path),
            "tts_mix": str(mix_path),
            "preview": str(preview_path),
            "contact_sheet": str(contact_sheet_path),
            "ffprobe": str(ffprobe_path),
        },
        "artifact_sha256": {
            "preview": sha256_file(preview_path),
            "tts_mix": sha256_file(mix_path),
            "srt": artifacts["srt"]["sha256"],
            "ass": artifacts["ass"]["sha256"],
        },
        "media": media,
        "binding_audit": binding_audit,
        "free_disk_gb": round(shutil.disk_usage(settings.root).free / (1024**3), 2),
    }
    summary_path = evidence_dir / f"{args.run_label}_summary.json"
    _write_json(summary_path, run_summary)
    print(json.dumps(run_summary, indent=2))


def _audit_active_bindings(timeline: dict) -> dict:
    enabled_spoken = {
        segment["id"]
        for segment in timeline["segments"]
        if segment.get("enabled", True) and segment.get("spoken_text", "").strip()
    }
    covered: dict[str, str] = {}
    active_generation_ids: list[str] = []
    errors: list[str] = []
    for unit in timeline.get("tts_units", []):
        generation_id = unit.get("active_tts_generation_id")
        if not generation_id:
            errors.append(f"{unit['id']} missing active generation")
            continue
        active_generation_ids.append(generation_id)
        for segment_id in unit.get("segment_ids", []):
            if segment_id in covered:
                errors.append(f"{segment_id} covered by both {covered[segment_id]} and {unit['id']}")
            covered[segment_id] = unit["id"]
    missing_segments = sorted(enabled_spoken - set(covered))
    extra_segments = sorted(set(covered) - enabled_spoken)
    if missing_segments:
        errors.append(f"missing spoken segment bindings: {missing_segments}")
    if extra_segments:
        errors.append(f"unexpected segment bindings: {extra_segments}")
    generation_rows = _generation_rows(active_generation_ids)
    fragmented = [
        row["generation_id"]
        for row in generation_rows
        if not row["segment_id"].startswith("ttsu_") or row["status"] != "ready"
    ]
    if fragmented:
        errors.append(f"active bindings include non-grouped or non-ready generations: {fragmented}")
    return {
        "pass": not errors,
        "errors": errors,
        "enabled_spoken_units": len(enabled_spoken),
        "covered_spoken_units": len(covered),
        "active_tts_bindings": len(active_generation_ids),
        "active_generation_ids": active_generation_ids,
        "generation_rows": generation_rows,
    }


def _generation_rows(generation_ids: list[str]) -> list[dict]:
    if not generation_ids:
        return []
    with session_scope() as session:
        rows = (
            session.query(TTSGeneration)
            .filter(TTSGeneration.generation_id.in_(generation_ids))
            .order_by(TTSGeneration.segment_id)
            .all()
        )
        return [
            {
                "generation_id": row.generation_id,
                "segment_id": row.segment_id,
                "status": row.status,
                "cache_status": row.cache_status,
                "character_count": row.character_count,
                "artifact_path": row.artifact_path,
                "sha256": row.sha256,
            }
            for row in rows
        ]


def _render_preview(source_path: Path, mix_path: Path, ass_path: Path, timeline: dict, preview_path: Path) -> None:
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
            "27",
            "-c:a",
            "aac",
            "-shortest",
            str(preview_path),
        ],
        check=True,
    )


def _write_contact_sheet(video_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vf",
            "fps=1/10,scale=320:-1,tile=4x2",
            "-frames:v",
            "1",
            str(output_path),
        ],
        check=True,
    )


def ensure_local_fit_adjustments(
    project_id: str,
    timeline: dict,
    generations: list[dict],
    evidence_dir: Path,
    run_label: str,
    *,
    max_fit_ratio: float = 1.12,
    target_fit_ratio: float = 1.115,
    max_speed_factor: float = 1.12,
) -> list[dict]:
    adjusted = list(generations)
    adjustment_records: list[dict] = []
    fit_results = evaluate_tts_fit(timeline, adjusted)
    by_unit_id = {unit["id"]: unit for unit in timeline["tts_units"]}
    by_generation_id = {generation["generation_id"]: index for index, generation in enumerate(adjusted)}
    for fit in fit_results:
        if fit["fit_ratio"] <= max_fit_ratio:
            continue
        unit = by_unit_id[fit["tts_unit_id"]]
        original_id = unit.get("active_tts_generation_id")
        if not original_id or original_id not in by_generation_id:
            raise RuntimeError(f"Cannot fit-adjust missing generation for {unit['id']}")
        original = adjusted[by_generation_id[original_id]]
        speed_factor = fit["fit_ratio"] / target_fit_ratio
        if speed_factor > max_speed_factor:
            raise RuntimeError(f"Local fit adjustment for {unit['id']} would require {speed_factor:.6f}x speed")
        adjusted_generation = _ensure_local_adjusted_generation(
            project_id,
            unit,
            original,
            speed_factor=speed_factor,
        )
        adjusted[by_generation_id[original_id]] = adjusted_generation
        unit["active_tts_generation_id"] = adjusted_generation["generation_id"]
        adjustment_records.append(
            {
                "tts_unit_id": unit["id"],
                "original_generation_id": original_id,
                "adjusted_generation_id": adjusted_generation["generation_id"],
                "original_fit_ratio": fit["fit_ratio"],
                "target_fit_ratio": target_fit_ratio,
                "speed_factor": round(speed_factor, 6),
                "artifact_path": adjusted_generation["artifact_path"],
                "sha256": adjusted_generation["sha256"],
            }
        )
    _write_json(evidence_dir / f"{run_label}_local_fit_adjustments.json", adjustment_records)
    return adjusted


def _ensure_local_adjusted_generation(project_id: str, unit: dict, original: dict, *, speed_factor: float) -> dict:
    settings = get_settings()
    generation_id = f"{original['generation_id']}_fit1115"
    request_hash = hashlib.sha256(
        f"{original['request_hash']}|fit1115|{speed_factor:.6f}".encode("utf-8")
    ).hexdigest()
    output_dir = settings.data_dir / "projects" / project_id / "tts" / "fit_adjusted"
    output_path = output_dir / f"{unit['id']}_{generation_id}.wav"
    with session_scope() as session:
        existing = session.query(TTSGeneration).filter_by(generation_id=generation_id).one_or_none()
        if existing is not None and Path(existing.artifact_path).exists():
            return {
                "generation_id": existing.generation_id,
                "request_hash": existing.request_hash,
                "cache_status": "local_fit_hit",
                "request_id": existing.request_id,
                "artifact_path": existing.artifact_path,
                "sha256": existing.sha256,
                "character_count": existing.character_count,
                "status": existing.status,
                "tts_unit_id": unit["id"],
                "segment_ids": list(unit.get("segment_ids", [unit["id"]])),
            }
    output_dir.mkdir(parents=True, exist_ok=True)
    _speed_adjust_wav(Path(original["artifact_path"]), output_path, speed_factor)
    digest = sha256_file(output_path)
    with session_scope() as session:
        session.add(
            TTSGeneration(
                project_id=project_id,
                segment_id=unit["id"],
                generation_id=generation_id,
                provider="elevenlabs_local_fit",
                model="local_fit_adjustment",
                voice_id="same_as_source_generation",
                request_hash=request_hash,
                cache_status="local_fit",
                status="ready",
                artifact_path=str(output_path.resolve()),
                sha256=digest,
                character_count=original["character_count"],
                request_id="local_fit_adjustment",
                credential_ref=None,
            )
        )
    return {
        "generation_id": generation_id,
        "request_hash": request_hash,
        "cache_status": "local_fit",
        "request_id": "local_fit_adjustment",
        "artifact_path": str(output_path.resolve()),
        "sha256": digest,
        "character_count": original["character_count"],
        "status": "ready",
        "tts_unit_id": unit["id"],
        "segment_ids": list(unit.get("segment_ids", [unit["id"]])),
    }


def _speed_adjust_wav(input_path: Path, output_path: Path, speed_factor: float) -> None:
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-filter:a",
            f"atempo={speed_factor:.6f}",
            "-ac",
            "1",
            "-ar",
            "48000",
            str(temp_path),
        ],
        check=True,
    )
    temp_path.replace(output_path)


def _write_json(path: Path, payload: dict | list) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
