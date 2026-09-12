from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.core.media import media_summary
from app.services.visual_localization_analysis import analyze_whole_frame_visual_text
from app.services.visual_quality import evaluate_visual_localization
from app.services.visual_replacement import build_tier_a_filter, build_tier_a_replacement_plan, write_tier_a_ass
from app.services.visual_text_localization import localize_visual_text_tracks
from app.services.visual_display_projection import (
    VisualDisplayProjectionRepairError,
    repair_visual_display_projection,
)


OCRRunner = Callable[[list[Path]], dict[str, Any]]
RenderRunner = Callable[[list[str], float], None]


class VisualLocalizationError(ValueError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        self.code = code
        self.evidence = evidence or {}
        super().__init__(message)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _release_ocr_resources_before_render(ocr_runner: OCRRunner) -> None:
    release = getattr(ocr_runner, "release_local_session", None)
    if callable(release):
        release()


MAX_RESIDUAL_REPAIR_MASKS = 64


def _build_residual_repair_masks(
    quality: dict[str, Any],
    final_evidence: dict[str, Any],
    *,
    output_width: int,
    output_height: int,
    sample_seconds: float,
) -> list[dict[str, Any]]:
    failures = list(quality.get("failures") or [])
    if not failures or any(item.get("code") != "SOURCE_LANGUAGE_RESIDUAL" for item in failures):
        return []
    tracks = {
        str(track.get("track_id") or ""): track
        for track in list(final_evidence.get("tracks") or [])
    }
    uncertainty_ms = max(1, round(sample_seconds * 1000))
    repair_masks: list[dict[str, Any]] = []
    seen: set[tuple[int, tuple[int, int, int, int]]] = set()
    for failure_index, failure in enumerate(failures):
        final_track_id = str(failure.get("final_track_id") or "")
        track = tracks.get(final_track_id)
        if track is None:
            return []
        observations = [
            item for item in list(track.get("observations") or [])
            if isinstance(item, dict) and isinstance(item.get("bbox"), list) and len(item["bbox"]) == 4
        ]
        if not observations:
            return []
        for observation_index, observation in enumerate(observations):
            left, top, right, bottom = [int(value) for value in observation["bbox"]]
            if right <= left or bottom <= top:
                return []
            time_ms = int(observation.get("time_ms") or track.get("start_ms") or 0)
            key = (time_ms, (left, top, right, bottom))
            if key in seen:
                continue
            seen.add(key)
            box_width = right - left
            box_height = bottom - top
            pad_x = max(6, round(box_width * 0.15))
            pad_y = max(4, round(box_height * 0.18))
            x = max(0, left - pad_x)
            y = max(0, top - pad_y)
            x2 = min(output_width - 1, right + pad_x)
            y2 = min(output_height - 1, bottom + pad_y)
            repair_masks.append({
                "track_id": f"residual_repair_{failure_index:03d}_{observation_index:03d}",
                "start_ms": max(0, time_ms - uncertainty_ms),
                "end_ms": time_ms + uncertainty_ms,
                "mask": {
                    "x": x,
                    "y": y,
                    "width": x2 - x + 1,
                    "height": y2 - y + 1,
                },
                "mask_only": True,
                "repair_for_source_track_id": failure.get("source_track_id"),
                "repair_for_final_track_id": final_track_id,
            })
            if len(repair_masks) > MAX_RESIDUAL_REPAIR_MASKS:
                return []
    return repair_masks


def _merge_time_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted((max(0, int(start)), max(0, int(end))) for start, end in windows):
        if end < start:
            start, end = end, start
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _repair_verification_windows(
    repair_masks: list[dict[str, Any]],
    *,
    sample_seconds: float,
) -> list[tuple[int, int]]:
    margin_ms = max(1, round(float(sample_seconds) * 1000))
    return _merge_time_windows([
        (
            max(0, int(item.get("start_ms") or 0) - margin_ms),
            int(item.get("end_ms") or 0) + margin_ms,
        )
        for item in repair_masks
    ])


def _localized_failure_subset(localized: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    failed_source_ids = {
        str(item.get("source_track_id") or "")
        for item in list(quality.get("failures") or [])
        if item.get("code") == "SOURCE_LANGUAGE_RESIDUAL"
    }
    tracks = [
        track for track in list(localized.get("tracks") or [])
        if str(track.get("track_id") or "") in failed_source_ids
    ]
    return {
        **localized,
        "tracks": tracks,
        "translated_track_count": len(tracks),
    }


def _merge_targeted_repair_quality(
    first_pass_quality: dict[str, Any],
    targeted_quality: dict[str, Any],
) -> dict[str, Any]:
    if targeted_quality.get("eligible") is True:
        return {
            **first_pass_quality,
            "status": "PASS",
            "eligible": True,
            "residual_source_track_count": 0,
            "failure_count": 0,
            "failures": [],
            "repair_verification_mode": "targeted_residual_windows",
        }
    failures = list(targeted_quality.get("failures") or [])
    return {
        **first_pass_quality,
        "status": "FAIL",
        "eligible": False,
        "residual_source_track_count": int(targeted_quality.get("residual_source_track_count") or 0),
        "failure_count": len(failures),
        "failures": failures,
        "repair_verification_mode": "targeted_residual_windows",
    }


def _render_tier_a_candidate(
    source_path: Path,
    candidate_path: Path,
    *,
    plan: dict[str, Any],
    ass_path: Path,
    duration_seconds: float,
    render_runner: RenderRunner,
) -> None:
    filter_value = build_tier_a_filter(plan, ass_path)
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vf",
        filter_value,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(candidate_path),
    ]
    for attempt in range(2):
        candidate_path.unlink(missing_ok=True)
        try:
            render_runner(command, duration_seconds)
            break
        except subprocess.CalledProcessError:
            if attempt >= 1:
                raise
    if not candidate_path.is_file() or candidate_path.stat().st_size <= 0:
        raise VisualLocalizationError("VISUAL_RENDER_MISSING", "Full Visual candidate was not created")


def execute_full_visual_localization(
    source_path: Path,
    run_directory: Path,
    output_path: Path,
    *,
    ocr_runner: OCRRunner,
    translator: Any,
    font_path: Path,
    render_runner: RenderRunner,
    target_language: str = "English",
    ocr_provider: str = "local_paddleocr",
    ocr_model: str = "local_ch_det_rec",
    sample_seconds: float = 0.5,
) -> dict[str, Any]:
    source = Path(source_path).resolve()
    run_dir = Path(run_directory).resolve()
    final_output = Path(output_path).resolve()
    if not source.is_file():
        raise VisualLocalizationError("VISUAL_SOURCE_MISSING", "Full Visual source stage is missing")
    media = media_summary(source)
    width = int(media.get("video", {}).get("width") or 0)
    height = int(media.get("video", {}).get("height") or 0)
    duration = float(media.get("duration_seconds") or 0)
    if width <= 0 or height <= 0 or duration <= 0:
        raise VisualLocalizationError("VISUAL_MEDIA_INVALID", "Full Visual source media is invalid")

    visual_dir = run_dir / "visual"
    evidence = analyze_whole_frame_visual_text(
        source,
        run_dir,
        ocr_runner=ocr_runner,
        provider=ocr_provider,
        model=ocr_model,
        sample_seconds=sample_seconds,
        evidence_filename="visual_text_source.json",
    )
    unresolved_recognition = [
        track
        for track in list(evidence.get("tracks") or [])
        if ((track.get("classification") or {}).get("action") == "unresolved")
    ]
    if unresolved_recognition:
        evidence_payload = {
            "status": "FAIL",
            "eligible": False,
            "code": "VISUAL_OCR_VERIFICATION_REQUIRED",
            "unresolved_recognition_track_count": len(unresolved_recognition),
            "tracks": [
                {
                    "track_id": track.get("track_id"),
                    "canonical_text": track.get("canonical_text"),
                    "recognition": track.get("recognition"),
                }
                for track in unresolved_recognition
            ],
        }
        _write_json(visual_dir / "visual_quality_evidence.json", evidence_payload)
        raise VisualLocalizationError(
            "VISUAL_OCR_VERIFICATION_REQUIRED",
            "Some source-language visual text still needs stronger OCR verification before translation",
            evidence_payload,
        )
    localized = localize_visual_text_tracks(
        evidence,
        translator=translator,
        target_language=target_language,
    )
    _write_json(visual_dir / "visual_text_localized.json", localized)

    plan = build_tier_a_replacement_plan(
        localized,
        output_width=width,
        output_height=height,
        font_path=font_path,
        sample_seconds=sample_seconds,
    )
    escalations = list(plan.get("escalations") or [])
    if escalations and all(str(item.get("reason") or "") == "target_text_does_not_fit" for item in escalations):
        _write_json(visual_dir / "replacement_plan_before_display_projection.json", plan)
        try:
            projection = repair_visual_display_projection(localized, plan, translator=translator)
        except VisualDisplayProjectionRepairError as exc:
            evidence_payload = {
                "schema_version": 1,
                "status": "FAIL",
                "code": exc.code,
                "message": str(exc),
                "evidence": exc.evidence,
            }
            _write_json(visual_dir / "visual_display_projection_repair_evidence.json", evidence_payload)
            raise VisualLocalizationError(exc.code, str(exc), evidence_payload) from exc
        localized = projection["localized"]
        _write_json(visual_dir / "visual_display_projection_repair_evidence.json", projection["evidence"])
        _write_json(visual_dir / "visual_text_localized.json", localized)
        plan = build_tier_a_replacement_plan(
            localized,
            output_width=width,
            output_height=height,
            font_path=font_path,
            sample_seconds=sample_seconds,
        )
        escalations = list(plan.get("escalations") or [])
    _write_json(visual_dir / "replacement_plan.json", plan)
    if escalations:
        evidence_payload = {
            "status": "FAIL",
            "eligible": False,
            "code": "VISUAL_REPLACEMENT_ESCALATION_REQUIRED",
            "escalation_count": len(escalations),
            "escalations": escalations,
        }
        _write_json(visual_dir / "visual_quality_evidence.json", evidence_payload)
        raise VisualLocalizationError(
            "VISUAL_REPLACEMENT_ESCALATION_REQUIRED",
            "Some visual text requires Tier-B reconstruction before Full Visual can be accepted",
            evidence_payload,
        )

    candidate = run_dir / "work" / "full_visual_candidate.mp4"
    candidate.unlink(missing_ok=True)
    replacements = list(plan.get("replacements") or [])
    ass_path: Path | None = None
    if replacements:
        ass_path = visual_dir / "visual_localization.ass"
        write_tier_a_ass(
            ass_path,
            plan,
            output_width=width,
            output_height=height,
        )
        _release_ocr_resources_before_render(ocr_runner)
        _render_tier_a_candidate(
            source,
            candidate,
            plan=plan,
            ass_path=ass_path,
            duration_seconds=duration,
            render_runner=render_runner,
        )
    else:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, candidate)

    final_evidence = analyze_whole_frame_visual_text(
        candidate,
        run_dir,
        ocr_runner=ocr_runner,
        provider=ocr_provider,
        model=ocr_model,
        sample_seconds=sample_seconds,
        evidence_filename="visual_text_final.json",
    )
    quality = evaluate_visual_localization(localized, final_evidence, replacement_plan=plan)
    repair_evidence: dict[str, Any] | None = None
    repair_masks: list[dict[str, Any]] = []
    residual_repair_performed = False
    if quality.get("eligible") is not True:
        _write_json(visual_dir / "visual_quality_first_pass.json", quality)
        repair_masks = _build_residual_repair_masks(
            quality,
            final_evidence,
            output_width=width,
            output_height=height,
            sample_seconds=sample_seconds,
        )
        if repair_masks and ass_path is not None:
            residual_repair_performed = True
            repair_plan = {
                **plan,
                "replacements": [*replacements, *repair_masks],
            }
            candidate.unlink(missing_ok=True)
            _release_ocr_resources_before_render(ocr_runner)
            _render_tier_a_candidate(
                source,
                candidate,
                plan=repair_plan,
                ass_path=ass_path,
                duration_seconds=duration,
                render_runner=render_runner,
            )
            verification_windows = _repair_verification_windows(
                repair_masks,
                sample_seconds=sample_seconds,
            )
            repaired_final_evidence = analyze_whole_frame_visual_text(
                candidate,
                run_dir,
                ocr_runner=ocr_runner,
                provider=ocr_provider,
                model=ocr_model,
                sample_seconds=sample_seconds,
                evidence_filename="visual_text_final_repair.json",
                time_windows_ms=verification_windows,
            )
            targeted_quality = evaluate_visual_localization(
                _localized_failure_subset(localized, quality),
                repaired_final_evidence,
                replacement_plan=None,
            )
            repaired_quality = _merge_targeted_repair_quality(quality, targeted_quality)
            repair_evidence = {
                "schema_version": 1,
                "status": "PASS" if repaired_quality.get("eligible") is True else "FAIL",
                "repair_pass_count": 1,
                "repair_mask_count": len(repair_masks),
                "first_pass_residual_source_track_count": int(quality.get("residual_source_track_count") or 0),
                "final_residual_source_track_count": int(repaired_quality.get("residual_source_track_count") or 0),
                "verification_mode": "targeted_residual_windows",
                "verification_windows_ms": [[start, end] for start, end in verification_windows],
                "verification_sampled_frame_count": int(repaired_final_evidence.get("sampled_frame_count") or 0),
                "verification_full_sampled_frame_count": int(repaired_final_evidence.get("full_sampled_frame_count") or 0),
                "verified_source_track_count": len(list(_localized_failure_subset(localized, quality).get("tracks") or [])),
                "repair_masks": repair_masks,
            }
            _write_json(visual_dir / "visual_residual_repair.json", repair_evidence)
            final_evidence = repaired_final_evidence
            quality = repaired_quality
        _write_json(visual_dir / "visual_quality_evidence.json", quality)
        if quality.get("eligible") is not True:
            raise VisualLocalizationError(
                "VISUAL_LOCALIZATION_QC_FAILED",
                "Final Full Visual OCR still contains unresolved source-language text",
                quality,
            )
    else:
        _write_json(visual_dir / "visual_quality_evidence.json", quality)

    final_output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(candidate, final_output)
    summary = {
        "schema_version": 1,
        "status": "PASS",
        "source_track_count": int(evidence.get("track_count") or 0),
        "localizable_track_count": int(evidence.get("localizable_track_count") or 0),
        "translated_track_count": int(localized.get("translated_track_count") or 0),
        "replacement_count": int(plan.get("replacement_count") or 0),
        "residual_repair_performed": residual_repair_performed,
        "residual_repair_mask_count": len(repair_masks) if residual_repair_performed else 0,
        "final_residual_source_track_count": int(quality.get("residual_source_track_count") or 0),
        "output_path": str(final_output),
    }
    _write_json(visual_dir / "localization_summary.json", summary)
    return {
        "source_evidence": evidence,
        "localized": localized,
        "replacement_plan": plan,
        "final_evidence": final_evidence,
        "quality": quality,
        "residual_repair": repair_evidence,
        "output_path": final_output,
        "summary": summary,
    }
