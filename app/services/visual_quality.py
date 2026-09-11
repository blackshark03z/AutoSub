from __future__ import annotations

from typing import Any

from app.services.visual_ocr_evidence import classify_visual_track_text
from app.services.visual_text_tracks import bbox_iou


def _track_bbox(track: dict[str, Any]) -> tuple[int, int, int, int] | None:
    observations = list(track.get("observations") or [])
    boxes = [item.get("bbox") for item in observations if isinstance(item, dict)]
    boxes = [box for box in boxes if isinstance(box, list) and len(box) == 4]
    if not boxes:
        return None
    xs0 = sorted(int(box[0]) for box in boxes)
    ys0 = sorted(int(box[1]) for box in boxes)
    xs1 = sorted(int(box[2]) for box in boxes)
    ys1 = sorted(int(box[3]) for box in boxes)
    mid = len(boxes) // 2
    return xs0[mid], ys0[mid], xs1[mid], ys1[mid]


def _time_overlaps(left: dict[str, Any], right: dict[str, Any], *, tolerance_ms: int) -> bool:
    left_start = int(left.get("start_ms") or 0) - tolerance_ms
    left_end = int(left.get("end_ms") or 0) + tolerance_ms
    right_start = int(right.get("start_ms") or 0)
    right_end = int(right.get("end_ms") or 0)
    return right_end >= left_start and right_start <= left_end


def evaluate_visual_localization(
    localized: dict[str, Any],
    final_visual_evidence: dict[str, Any],
    *,
    replacement_plan: dict[str, Any] | None = None,
    time_tolerance_ms: int = 500,
    minimum_spatial_iou: float = 0.12,
) -> dict[str, Any]:
    expected = [
        track for track in list(localized.get("tracks") or [])
        if ((track.get("classification") or {}).get("action") == "localize")
    ]
    final_tracks = list(final_visual_evidence.get("tracks") or [])
    residual_candidates = [
        track for track in final_tracks
        if classify_visual_track_text(str(track.get("canonical_text") or ""))["action"] == "localize"
    ]

    failures: list[dict[str, Any]] = []
    residuals: list[dict[str, Any]] = []
    for source in expected:
        source_box = _track_bbox(source)
        if source_box is None:
            failures.append({"code": "SOURCE_TRACK_GEOMETRY_MISSING", "track_id": source.get("track_id")})
            continue
        for candidate in residual_candidates:
            candidate_box = _track_bbox(candidate)
            if candidate_box is None or not _time_overlaps(source, candidate, tolerance_ms=time_tolerance_ms):
                continue
            overlap = bbox_iou(source_box, candidate_box)
            if overlap < minimum_spatial_iou:
                continue
            residual = {
                "code": "SOURCE_LANGUAGE_RESIDUAL",
                "source_track_id": source.get("track_id"),
                "final_track_id": candidate.get("track_id"),
                "source_text": source.get("canonical_text"),
                "residual_text": candidate.get("canonical_text"),
                "spatial_iou": round(overlap, 6),
                "start_ms": candidate.get("start_ms"),
                "end_ms": candidate.get("end_ms"),
            }
            residuals.append(residual)
            failures.append(residual)

    plan = replacement_plan or {}
    escalations = list(plan.get("escalations") or [])
    for item in escalations:
        failures.append({
            "code": "VISUAL_REPLACEMENT_UNRESOLVED",
            "track_id": item.get("track_id"),
            "reason": item.get("reason"),
        })

    replacement_items = list(plan.get("replacements") or [])
    replacement_ids: set[str] = set()
    for item in replacement_items:
        replacement_ids.add(str(item.get("track_id") or ""))
        parent_track_id = str(item.get("parent_track_id") or "")
        if parent_track_id:
            replacement_ids.add(parent_track_id)
    expected_ids = {str(item.get("track_id") or "") for item in expected}
    if replacement_plan is not None:
        for missing in sorted(expected_ids - replacement_ids - {str(item.get("track_id") or "") for item in escalations}):
            failures.append({"code": "VISUAL_REPLACEMENT_PLAN_MISSING", "track_id": missing})

    return {
        "status": "PASS" if not failures else "FAIL",
        "eligible": not failures,
        "expected_localized_track_count": len(expected),
        "planned_replacement_count": len(replacement_items),
        "residual_source_track_count": len(residuals),
        "unresolved_replacement_count": len(escalations),
        "failure_count": len(failures),
        "failures": failures,
    }
