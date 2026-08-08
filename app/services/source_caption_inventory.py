from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable


INVENTORY_SCHEMA_VERSION = "source_caption_window_inventory_v1"
MANUAL_REVIEW_PROVENANCE = "operator_reviewed_from_pixels_and_audio"
PIXEL_REVIEW_PROVENANCE = "human_reviewed_from_pixels"
TECH_LEAD_AUTHORITY = "TECH_LEAD_APPROVED"


@dataclass(frozen=True)
class FrozenCaptionCandidate:
    candidate_id: str
    window_id: str
    start_time: float
    end_time: float
    source_bbox: dict[str, int]
    fingerprint: str
    schema_version: str


@dataclass(frozen=True)
class ReviewedCaptionInstance:
    instance_id: str
    window_id: str
    start_time: float
    end_time: float
    source_bbox: dict[str, int]
    caption_fingerprint: str
    chinese: str
    english: str
    status: str
    provenance: str
    authority: str
    record_hash: str


def freeze_caption_inventory(
    windows: Iterable[dict[str, Any]],
    *,
    source_video_sha256: str,
) -> list[FrozenCaptionCandidate]:
    """Create the only allowed candidate universe from authoritative pixel windows."""
    frozen: list[FrozenCaptionCandidate] = []
    seen: set[str] = set()
    for window in sorted(windows, key=lambda item: (float(item["start_time"]), str(item["window_id"]))):
        window_id = str(window.get("window_id") or "").strip()
        if not window_id:
            raise ValueError("caption window is missing window_id")
        if window_id in seen:
            raise ValueError(f"duplicate caption window id: {window_id}")
        seen.add(window_id)
        bbox = _normal_bbox(window.get("source_bbox"))
        start = round(float(window["start_time"]), 3)
        end = round(float(window["end_time"]), 3)
        if end <= start:
            raise ValueError(f"invalid caption window timing: {window_id}")
        geometry_schema = str(window.get("schema_version") or "").strip()
        fingerprint = caption_window_fingerprint(
            source_video_sha256=source_video_sha256,
            window_id=window_id,
            start_time=start,
            end_time=end,
            source_bbox=bbox,
            geometry_schema_version=geometry_schema,
        )
        frozen.append(
            FrozenCaptionCandidate(
                candidate_id=f"CAPWIN_{fingerprint[:16]}",
                window_id=window_id,
                start_time=start,
                end_time=end,
                source_bbox=bbox,
                fingerprint=fingerprint,
                schema_version=INVENTORY_SCHEMA_VERSION,
            )
        )
    return frozen


def caption_window_fingerprint(
    *,
    source_video_sha256: str,
    window_id: str,
    start_time: float,
    end_time: float,
    source_bbox: dict[str, int],
    geometry_schema_version: str,
) -> str:
    payload = {
        "source_video_sha256": str(source_video_sha256).lower(),
        "window_id": window_id,
        "start_time": round(float(start_time), 3),
        "end_time": round(float(end_time), 3),
        "source_bbox": _normal_bbox(source_bbox),
        "geometry_schema_version": geometry_schema_version,
        "schema_version": INVENTORY_SCHEMA_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reconcile_checkpoint_results(
    candidates: Iterable[FrozenCaptionCandidate],
    provider_results: dict[str, dict[str, Any]],
    *,
    visual_groups: Iterable[dict[str, Any]],
    invalid_results: dict[str, Any] | None = None,
    transition_fragments: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify visual-fragment results against the frozen pixel-window inventory."""
    frozen = list(candidates)
    by_group_id = {str(group.get("id") or ""): group for group in visual_groups}
    transition_ids = {str(item.get("id") or "") for item in (transition_fragments or []) if isinstance(item, dict)}
    invalid_ids = set((invalid_results or {}).keys())
    retained: dict[str, list[dict[str, Any]]] = {candidate.window_id: [] for candidate in frozen}
    duplicates: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for result_id, result in sorted((provider_results or {}).items()):
        if result_id in invalid_ids:
            invalid.append({"result_id": result_id, "reason": "invalid_provider_result"})
            continue
        group = result.get("group") if isinstance(result, dict) else None
        if not isinstance(group, dict):
            group = by_group_id.get(str(result_id))
        if not isinstance(group, dict):
            orphans.append({"result_id": result_id, "reason": "missing_visual_group"})
            continue
        match = best_window_match(group, frozen)
        if match is None:
            classification = "transition_fragment" if result_id in transition_ids else "orphan_no_authoritative_window"
            orphans.append({"result_id": result_id, "reason": classification})
            continue
        retained[match.window_id].append(
            {
                "result_id": result_id,
                "window_id": match.window_id,
                "overlap_seconds": round(_temporal_overlap(group, match), 3),
                "source_chinese": result.get("source_chinese"),
                "english": result.get("english"),
                "provenance": result.get("provenance"),
                "confidence": result.get("confidence"),
            }
        )

    for window_id, items in retained.items():
        if len(items) > 1:
            for item in items[1:]:
                duplicates.append({"result_id": item["result_id"], "window_id": window_id, "reason": "extra_fragment_same_window"})

    return {
        "retained_by_window": retained,
        "duplicates": duplicates,
        "orphans": orphans,
        "invalid": invalid,
    }


def best_window_match(group: dict[str, Any], candidates: Iterable[FrozenCaptionCandidate]) -> FrozenCaptionCandidate | None:
    best: tuple[float, FrozenCaptionCandidate] | None = None
    for candidate in candidates:
        overlap = _temporal_overlap(group, candidate)
        if overlap <= 0:
            continue
        score = overlap * max(0.1, _bbox_overlap_ratio(group.get("bbox"), candidate.source_bbox))
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best else None


def validate_operator_review_record(record: dict[str, Any]) -> None:
    required = ("video_sha256", "window_ids", "start_time", "end_time", "caption_fingerprint", "chinese", "english", "provenance", "record_hash")
    missing = [key for key in required if key not in record]
    if missing:
        raise ValueError(f"operator review record missing fields: {', '.join(missing)}")
    if record["provenance"] != MANUAL_REVIEW_PROVENANCE:
        raise ValueError("operator review record provenance is invalid")
    if not str(record.get("chinese") or "").strip() or not str(record.get("english") or "").strip():
        raise ValueError("operator review record text is incomplete")
    expected = operator_review_record_hash({key: value for key, value in record.items() if key != "record_hash"})
    if str(record["record_hash"]) != expected:
        raise ValueError("operator review record hash mismatch")


def apply_reviewed_window_decisions(
    candidates: Iterable[FrozenCaptionCandidate],
    decisions: Iterable[dict[str, Any]],
    *,
    source_video_sha256: str,
) -> dict[str, Any]:
    """Apply human-reviewed HUD exclusions and caption splits without expanding the inventory."""
    by_window = {candidate.window_id: candidate for candidate in candidates}
    accounted: set[str] = set()
    exclusions: list[dict[str, Any]] = []
    instances: list[ReviewedCaptionInstance] = []

    for decision in decisions:
        window_id = str(decision.get("window_id") or "").strip()
        if window_id not in by_window:
            raise ValueError(f"review decision references unknown window: {window_id}")
        if window_id in accounted:
            raise ValueError(f"duplicate review decision for window: {window_id}")
        accounted.add(window_id)
        candidate = by_window[window_id]
        provenance = str(decision.get("provenance") or "").strip()
        authority = str(decision.get("authority") or "").strip()
        if provenance != PIXEL_REVIEW_PROVENANCE:
            raise ValueError("review decision provenance is invalid")
        if authority != TECH_LEAD_AUTHORITY:
            raise ValueError("review decision authority is invalid")

        action = str(decision.get("action") or "").strip()
        if action == "HUD_NON_CAPTION":
            exclusions.append(
                {
                    "window_id": window_id,
                    "candidate_id": candidate.candidate_id,
                    "status": "HUD_NON_CAPTION",
                    "provenance": provenance,
                    "authority": authority,
                    "reason": str(decision.get("reason") or "").strip(),
                }
            )
            continue
        if action != "SPLIT_CAPTION_WINDOW":
            raise ValueError(f"unsupported review decision action: {action}")

        segments = decision.get("segments")
        if not isinstance(segments, list) or not segments:
            raise ValueError("split review decision has no segments")
        previous_end: float | None = None
        for index, segment in enumerate(segments, start=1):
            if not isinstance(segment, dict):
                raise ValueError("split segment is invalid")
            start = round(float(segment["start_time"]), 3)
            end = round(float(segment["end_time"]), 3)
            if start < candidate.start_time or end > candidate.end_time or end <= start:
                raise ValueError("split segment timing is outside the reviewed window")
            if previous_end is not None and start < previous_end:
                raise ValueError("split segments overlap or are out of order")
            previous_end = end
            chinese = str(segment.get("chinese") or "").strip()
            english = str(segment.get("english") or "").strip()
            if not chinese or not english:
                raise ValueError("split segment text is incomplete")
            instance_payload = {
                "video_sha256": source_video_sha256.lower(),
                "window_ids": [window_id],
                "start_time": start,
                "end_time": end,
                "caption_fingerprint": caption_window_fingerprint(
                    source_video_sha256=source_video_sha256,
                    window_id=f"{window_id}:split:{index}",
                    start_time=start,
                    end_time=end,
                    source_bbox=candidate.source_bbox,
                    geometry_schema_version=candidate.schema_version,
                ),
                "chinese": chinese,
                "english": english,
                "provenance": provenance,
                "authority": authority,
            }
            record_hash = operator_review_record_hash(instance_payload)
            instances.append(
                ReviewedCaptionInstance(
                    instance_id=f"{candidate.candidate_id}_S{index:02d}",
                    window_id=window_id,
                    start_time=start,
                    end_time=end,
                    source_bbox=dict(candidate.source_bbox),
                    caption_fingerprint=str(instance_payload["caption_fingerprint"]),
                    chinese=chinese,
                    english=english,
                    status="MAPPED",
                    provenance=provenance,
                    authority=authority,
                    record_hash=record_hash,
                )
            )

    return {
        "instances": instances,
        "exclusions": exclusions,
        "accounted_window_ids": sorted(accounted),
        "unreviewed_window_ids": sorted(set(by_window) - accounted),
    }


def operator_review_record_hash(record_without_hash: dict[str, Any]) -> str:
    encoded = json.dumps(record_without_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normal_bbox(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("caption bbox is invalid")
    bbox = {key: int(value[key]) for key in ("left_x", "top_y", "right_x", "bottom_y")}
    if bbox["right_x"] < bbox["left_x"] or bbox["bottom_y"] < bbox["top_y"]:
        raise ValueError("caption bbox is inverted")
    return bbox


def _temporal_overlap(group: dict[str, Any], candidate: FrozenCaptionCandidate) -> float:
    start = max(float(group.get("start_time") or 0), candidate.start_time)
    end = min(float(group.get("end_time") or 0), candidate.end_time)
    return max(0.0, end - start)


def _bbox_overlap_ratio(a: Any, b: Any) -> float:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return 0.0
    left = max(int(a.get("left_x") or 0), int(b.get("left_x") or 0))
    top = max(int(a.get("top_y") or 0), int(b.get("top_y") or 0))
    right = min(int(a.get("right_x") or 0), int(b.get("right_x") or 0))
    bottom = min(int(a.get("bottom_y") or 0), int(b.get("bottom_y") or 0))
    if right < left or bottom < top:
        return 0.0
    intersection = (right - left + 1) * (bottom - top + 1)
    area_a = (int(a.get("right_x") or 0) - int(a.get("left_x") or 0) + 1) * (int(a.get("bottom_y") or 0) - int(a.get("top_y") or 0) + 1)
    area_b = (int(b.get("right_x") or 0) - int(b.get("left_x") or 0) + 1) * (int(b.get("bottom_y") or 0) - int(b.get("top_y") or 0) + 1)
    return intersection / max(1, min(area_a, area_b))
