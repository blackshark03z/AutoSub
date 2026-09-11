from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Callable

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from app.services.visual_ocr_evidence import classify_visual_track_text
from app.services.visual_text_tracks import normalize_text_identity


OCRBatchRunner = Callable[[list[Path]], dict[str, Any]]
ATTENTION_CONFIDENCE = 0.90
CONSENSUS_MIN_SCORE = 0.60


def _representative_observation(track: dict[str, Any]) -> dict[str, Any] | None:
    observations = [item for item in list(track.get("observations") or []) if isinstance(item, dict)]
    if not observations:
        return None
    return max(observations, key=lambda item: float(item.get("confidence") or 0.0))


def _crop_variants(
    frame_path: Path,
    bbox: list[int] | tuple[int, int, int, int],
    output_dir: Path,
    track_id: str,
    *,
    scale: int = 4,
) -> list[Path]:
    image = Image.open(frame_path).convert("RGB")
    left, top, right, bottom = (int(value) for value in bbox)
    width = max(1, right - left)
    height = max(1, bottom - top)
    pad = max(8, min(24, round(max(width, height) * 0.10)))
    box = (
        max(0, left - pad),
        max(0, top - pad),
        min(image.width, right + pad),
        min(image.height, bottom + pad),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return []
    crop = image.crop(box)
    resized = crop.resize((max(1, crop.width * scale), max(1, crop.height * scale)), Image.Resampling.LANCZOS)
    variants = {
        "orig": resized,
        "contrast": ImageOps.autocontrast(resized.convert("L")).convert("RGB"),
        "sharp": ImageEnhance.Contrast(resized).enhance(1.8).filter(ImageFilter.SHARPEN),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, variant in variants.items():
        path = output_dir / f"{track_id}__{name}.png"
        variant.save(path, format="PNG")
        paths.append(path)
    return paths


def _best_candidate(frame: dict[str, Any]) -> dict[str, Any] | None:
    items: list[dict[str, Any]] = []
    for item in list(frame.get("texts") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        if not text or score < 0 or score > 1:
            continue
        items.append({"text": text, "score": score})
    return max(items, key=lambda item: item["score"]) if items else None


def _choose_refined_text(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        key = normalize_text_identity(str(candidate.get("text") or ""))
        if key:
            grouped[key].append(candidate)
    if not grouped:
        return None
    ranked = sorted(
        grouped.values(),
        key=lambda items: (
            len(items),
            mean(float(item["score"]) for item in items),
            max(float(item["score"]) for item in items),
        ),
        reverse=True,
    )
    winning = ranked[0]
    representative = max(winning, key=lambda item: float(item["score"]))
    avg_score = mean(float(item["score"]) for item in winning)
    max_score = max(float(item["score"]) for item in winning)
    if len(winning) >= 2 and avg_score >= CONSENSUS_MIN_SCORE:
        return {
            "text": representative["text"],
            "score": avg_score,
            "votes": len(winning),
            "method": "crop_transform_consensus",
        }
    if max_score >= ATTENTION_CONFIDENCE:
        return {
            "text": representative["text"],
            "score": max_score,
            "votes": 1,
            "method": "crop_high_confidence",
        }
    return None


def refine_uncertain_visual_text_evidence(
    evidence: dict[str, Any],
    *,
    frame_paths: dict[str, Path],
    ocr_runner: OCRBatchRunner,
    work_dir: Path,
    attention_confidence: float = ATTENTION_CONFIDENCE,
) -> dict[str, Any]:
    """Refine uncertain source-script tracks without rerunning whole-frame OCR.

    A sub-0.90 confidence is an attention signal, not a standalone reject. For
    those tracks we re-read one representative crop after several deterministic
    image transforms. Agreement may verify a weak source reading; strong
    non-source evidence may refute an initial false CJK hit. Genuine source
    disagreement remains explicit and fail-closed for a stronger OCR tier.
    """
    tracks = [dict(track) for track in list(evidence.get("tracks") or [])]
    refine_dir = Path(work_dir).resolve()
    manifest: list[tuple[str, list[Path]]] = []
    try:
        for track in tracks:
            classification = track.get("classification") or {}
            if classification.get("action") != "localize":
                continue
            confidence = float(track.get("confidence") or 0.0)
            if confidence >= float(attention_confidence):
                track["recognition"] = {
                    "status": "verified",
                    "method": "whole_frame_confidence",
                    "score": confidence,
                }
                continue
            observation = _representative_observation(track)
            if observation is None:
                track["classification"] = {"action": "unresolved", "reason": "source_ocr_missing_observation"}
                track["recognition"] = {"status": "unresolved", "method": "missing_observation"}
                continue
            frame_id = str(observation.get("frame_id") or "")
            bbox = observation.get("bbox")
            frame_path = frame_paths.get(frame_id)
            if frame_path is None or not frame_path.is_file() or not isinstance(bbox, list) or len(bbox) != 4:
                track["classification"] = {"action": "unresolved", "reason": "source_ocr_refinement_input_missing"}
                track["recognition"] = {"status": "unresolved", "method": "refinement_input_missing"}
                continue
            paths = _crop_variants(frame_path, bbox, refine_dir, str(track.get("track_id") or "track"))
            if paths:
                manifest.append((str(track.get("track_id") or ""), paths))

        all_paths = [path for _, paths in manifest for path in paths]
        returned_frames: list[dict[str, Any]] = []
        if all_paths:
            payload = ocr_runner(all_paths)
            returned_frames = list(payload.get("frames") or []) if isinstance(payload, dict) else []
            if len(returned_frames) != len(all_paths):
                raise ValueError("visual OCR refinement returned a mismatched crop count")

        cursor = 0
        by_id = {str(track.get("track_id") or ""): track for track in tracks}
        for track_id, paths in manifest:
            track = by_id[track_id]
            frames = returned_frames[cursor : cursor + len(paths)]
            cursor += len(paths)
            candidates = [candidate for frame in frames if (candidate := _best_candidate(frame)) is not None]
            refined = _choose_refined_text(candidates)
            source_candidates = [
                candidate
                for candidate in candidates
                if classify_visual_track_text(str(candidate.get("text") or ""))["action"] == "localize"
            ]
            if refined is not None:
                refined_classification = classify_visual_track_text(str(refined["text"]))
                track["recognition_original_canonical_text"] = str(track.get("canonical_text") or "")
                track["canonical_text"] = str(refined["text"])
                track["confidence"] = round(float(refined["score"]), 6)
                track["classification"] = refined_classification
                track["recognition"] = {
                    "status": "verified",
                    "method": refined["method"],
                    "score": round(float(refined["score"]), 6),
                    "votes": int(refined["votes"]),
                    "candidate_count": len(candidates),
                }
                continue
            if not source_candidates:
                track["classification"] = {"action": "ignore", "reason": "low_confidence_source_not_reproduced"}
                track["recognition"] = {
                    "status": "refuted",
                    "method": "crop_source_script_not_reproduced",
                    "candidate_count": len(candidates),
                }
                continue
            # A sub-attention-threshold source reading that occurs in exactly
            # one sampled frame has no temporal corroboration.  If deterministic
            # crop transforms still disagree on its identity, preserve it as a
            # non-material attention item instead of either translating a guess
            # or blocking the entire run.  Repeated or >=attention-confidence
            # source evidence still fails closed and requires a stronger OCR tier.
            if int(track.get("observation_count") or 0) <= 1 and float(track.get("confidence") or 0.0) < float(attention_confidence):
                track["classification"] = {"action": "ignore", "reason": "transient_single_observation_unverified"}
                track["recognition"] = {
                    "status": "attention",
                    "method": "transient_single_observation_disagreement",
                    "candidate_count": len(candidates),
                    "source_candidate_count": len(source_candidates),
                    "candidates": [
                        {"text": str(item["text"]), "score": round(float(item["score"]), 6)} for item in candidates
                    ],
                }
                continue
            track["classification"] = {"action": "unresolved", "reason": "source_ocr_refinement_disagreement"}
            track["recognition"] = {
                "status": "unresolved",
                "method": "crop_transform_disagreement",
                "candidate_count": len(candidates),
                "source_candidate_count": len(source_candidates),
                "candidates": [
                    {"text": str(item["text"]), "score": round(float(item["score"]), 6)} for item in candidates
                ],
            }

        localizable = sum(
            1 for track in tracks if ((track.get("classification") or {}).get("action") == "localize")
        )
        unresolved = sum(
            1 for track in tracks if ((track.get("classification") or {}).get("action") == "unresolved")
        )
        attention_count = sum(
            1 for track in tracks if (track.get("recognition") or {}).get("status") == "attention"
        )
        refined_count = sum(
            1
            for track in tracks
            if (track.get("recognition") or {}).get("method") not in {None, "whole_frame_confidence"}
        )
        result = dict(evidence)
        result["tracks"] = tracks
        result["localizable_track_count"] = localizable
        result["unresolved_recognition_track_count"] = unresolved
        result["recognition_attention_track_count"] = attention_count
        result["recognition_refined_track_count"] = refined_count
        return result
    finally:
        if refine_dir.exists():
            shutil.rmtree(refine_dir, ignore_errors=True)
