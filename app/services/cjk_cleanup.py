from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.core.config import get_settings


CP07_PROJECT_ID = "vertical_slice_cp07"
CJK_CLEANUP_STATE = Path("data/projects/vertical_slice_cp07/operator/cjk_cleanup_state.json")
PUNCTUATION_SEARCH_BBOX = {"left_x": 0, "top_y": 666, "right_x": 395, "bottom_y": 705}


def source_region_union(boxes: list[dict[str, int]], *, width: int = 1280, height: int = 720, padding_x: int = 32, padding_y: int = 16) -> dict[str, int]:
    if not boxes:
        raise ValueError("at least one source box is required")
    left = max(0, min(box["left_x"] for box in boxes) - padding_x)
    top = max(0, min(box["top_y"] for box in boxes) - padding_y)
    right = min(width - 1, max(box["right_x"] for box in boxes) + padding_x)
    bottom = min(height - 1, max(box["bottom_y"] for box in boxes) + padding_y)
    return {"x": left, "y": top, "width": right - left + 1, "height": bottom - top + 1, "right_x": right, "bottom_y": bottom}


def build_hybrid_source_event(
    *,
    event_id: str,
    start_time: float,
    end_time: float,
    source_boxes: list[dict[str, int]],
    sequence_id: str,
    preroll_frames: int = 5,
    postroll_frames: int = 7,
    fps: int = 30,
    padding_x: int = 32,
    padding_y: int = 16,
    plate_opacity: float = 0.9,
) -> dict[str, Any]:
    region = source_region_union(source_boxes, padding_x=padding_x, padding_y=padding_y)
    return {
        "event_id": event_id,
        "sequence_id": sequence_id,
        "start_time": max(0.0, round(start_time - preroll_frames / fps, 3)),
        "end_time": round(end_time + postroll_frames / fps, 3),
        "source_start_time": round(start_time, 3),
        "source_end_time": round(end_time, 3),
        "preroll_frames": preroll_frames,
        "postroll_frames": postroll_frames,
        "source_boxes": source_boxes,
        "union_region": region,
        "confidence": 0.95,
        "source_classification": "dialogue_subtitle",
        "plate_geometry": dict(region),
        "plate_opacity": plate_opacity,
        "suppression_method": "bounded_local_delogo_plus_stable_source_zone_plate",
        "operator_override": None,
        "qa_state": "pending",
    }


def stabilize_sequence_plate_geometry(events: list[dict[str, Any]], *, tolerance_px: int = 48) -> list[dict[str, Any]]:
    stabilized: list[dict[str, Any]] = []
    by_sequence: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_sequence.setdefault(event["sequence_id"], []).append(event)
    for sequence_events in by_sequence.values():
        regions = [event["plate_geometry"] for event in sequence_events]
        widths = [region["width"] for region in regions]
        heights = [region["height"] for region in regions]
        if max(widths) - min(widths) <= tolerance_px and max(heights) - min(heights) <= tolerance_px:
            stable = {
                "x": min(region["x"] for region in regions),
                "y": min(region["y"] for region in regions),
                "right_x": max(region["right_x"] for region in regions),
                "bottom_y": max(region["bottom_y"] for region in regions),
            }
            stable["width"] = stable["right_x"] - stable["x"] + 1
            stable["height"] = stable["bottom_y"] - stable["y"] + 1
            for event in sequence_events:
                item = dict(event)
                item["plate_geometry"] = dict(stable)
                item["union_region"] = dict(stable)
                item["stable_per_sequence_geometry"] = True
                stabilized.append(item)
        else:
            for event in sequence_events:
                item = dict(event)
                item["stable_per_sequence_geometry"] = False
                stabilized.append(item)
    return sorted(stabilized, key=lambda item: (item["start_time"], item["event_id"]))


def source_event_to_interval(event: dict[str, Any]) -> dict[str, Any]:
    region = event["union_region"]
    return {
        "segment_id": event["event_id"],
        "start_time": event["start_time"],
        "end_time": event["end_time"],
        "x": region["x"],
        "y": region["y"],
        "width": region["width"],
        "height": region["height"],
        "source_bbox": {
            "left_x": region["x"],
            "top_y": region["y"],
            "right_x": region["right_x"],
            "bottom_y": region["bottom_y"],
        },
        "source_only": True,
        "temporal_role": "cp08e_hybrid_source_zone_suppression",
    }


def assert_source_containment(source_boxes: list[dict[str, int]], plate_bbox: dict[str, int], *, margin: int = 0) -> dict[str, Any]:
    violations = []
    plate_left = plate_bbox.get("left_x", plate_bbox.get("x"))
    plate_top = plate_bbox.get("top_y", plate_bbox.get("y"))
    plate_right = plate_bbox["right_x"]
    plate_bottom = plate_bbox["bottom_y"]
    for index, box in enumerate(source_boxes):
        expanded = {
            "left_x": max(0, box["left_x"] - margin),
            "top_y": max(0, box["top_y"] - margin),
            "right_x": min(1279, box["right_x"] + margin),
            "bottom_y": min(719, box["bottom_y"] + margin),
        }
        if expanded["left_x"] < plate_left:
            violations.append({"index": index, "edge": "left", "box": expanded})
        if expanded["right_x"] > plate_right:
            violations.append({"index": index, "edge": "right", "box": expanded})
        if expanded["top_y"] < plate_top:
            violations.append({"index": index, "edge": "top", "box": expanded})
        if expanded["bottom_y"] > plate_bottom:
            violations.append({"index": index, "edge": "bottom", "box": expanded})
    return {"status": "PASS" if not violations else "FAIL", "violation_count": len(violations), "violations": violations}


def cjk_cleanup_state_path(root: Path | None = None) -> Path:
    settings = get_settings()
    base = root or settings.root
    return base / CJK_CLEANUP_STATE


def load_cjk_cleanup_state(root: Path | None = None) -> dict[str, Any] | None:
    path = cjk_cleanup_state_path(root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_cjk_cleanup_state(state: dict[str, Any], root: Path | None = None) -> Path:
    path = cjk_cleanup_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def cleanup_issue_summary(state: dict[str, Any]) -> dict[str, int]:
    issues = state.get("issues", [])
    reviewed = sum(1 for issue in issues if issue.get("reviewed"))
    needs_review = sum(1 for issue in issues if issue.get("needs_review") and not issue.get("reviewed"))
    clean_without_review = sum(1 for issue in issues if not issue.get("needs_review"))
    blockers = sum(1 for issue in issues if issue.get("severity") == "blocker" and issue.get("needs_review") and not issue.get("reviewed"))
    warnings = sum(1 for issue in issues if issue.get("severity") == "warning" and issue.get("needs_review") and not issue.get("reviewed"))
    unresolved = sum(1 for issue in issues if issue.get("needs_review") and not issue.get("reviewed"))
    return {
        "total": len(issues),
        "blockers": blockers,
        "warnings": warnings,
        "needs_review": needs_review,
        "reviewed": reviewed,
        "unresolved": unresolved,
        "clean_without_review_requirement": clean_without_review,
    }


def build_cjk_cleanup_summary(state: dict[str, Any]) -> dict[str, Any]:
    state = dict(state)
    state.setdefault("schema_version", 1)
    state.setdefault("project_id", CP07_PROJECT_ID)
    state.setdefault("controls", [])
    state.setdefault("issues", [])
    state.setdefault("approval_gate", default_cleanup_gate())
    state["issue_summary"] = cleanup_issue_summary(state)
    state["reviewed_issue_ids"] = sorted(issue["issue_id"] for issue in state["issues"] if issue.get("reviewed"))
    return state


def default_cleanup_gate() -> dict[str, Any]:
    return {
        "gate_id": "cjk_cleanup_review",
        "label": "Residual CJK cleanup review",
        "state": "Pending human review",
        "approved_at": None,
        "unresolved_issue_count": 0,
        "action_required": "Review the repaired output, then approve cleanup and preservation.",
        "blocks_next": False,
    }


def mark_cleanup_issue_reviewed(state: dict[str, Any], issue_id: str) -> dict[str, Any]:
    updated = dict(state)
    issues = []
    for issue in updated.get("issues", []):
        row = dict(issue)
        if row.get("issue_id") == issue_id:
            row["reviewed"] = True
            row["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        issues.append(row)
    updated["issues"] = issues
    updated["reviewed_issue_ids"] = sorted(issue["issue_id"] for issue in issues if issue.get("reviewed"))
    updated["issue_summary"] = cleanup_issue_summary(updated)
    return updated


def set_cleanup_approval(state: dict[str, Any], *, cleanup: bool | None = None, preservation: bool | None = None) -> dict[str, Any]:
    updated = dict(state)
    approvals = dict(updated.get("approvals", {}))
    if cleanup is not None:
        approvals["cleanup"] = bool(cleanup)
    if preservation is not None:
        approvals["preservation"] = bool(preservation)
    updated["approvals"] = approvals
    gate = dict(updated.get("approval_gate", default_cleanup_gate()))
    if approvals.get("cleanup") and approvals.get("preservation"):
        gate["state"] = "Approved"
        gate["approved_at"] = datetime.now(timezone.utc).isoformat()
    updated["approval_gate"] = gate
    return updated


def detect_punctuation_residue(
    frame_bgr: np.ndarray,
    *,
    plate_boxes: list[dict[str, int]] | None = None,
    search_bbox: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Detect punctuation-only subtitle residue without depending on OCR/CJK text."""
    search = search_bbox or PUNCTUATION_SEARCH_BBOX
    x0 = max(0, int(search["left_x"]))
    y0 = max(0, int(search["top_y"]))
    x1 = min(frame_bgr.shape[1] - 1, int(search["right_x"]))
    y1 = min(frame_bgr.shape[0] - 1, int(search["bottom_y"]))
    if x1 <= x0 or y1 <= y0:
        return {"detected": False, "bbox": None, "components": [], "cluster_count": 0}

    roi = frame_bgr[y0 : y1 + 1, x0 : x1 + 1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bright = cv2.inRange(gray, 205, 255)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(bright, 8)
    components: list[dict[str, int]] = []
    for label in range(1, count):
        rx, ry, width, height, area = [int(value) for value in stats[label]]
        if not (2 <= width <= 16 and 2 <= height <= 16 and 8 <= area <= 120):
            continue
        component = {
            "left_x": x0 + rx,
            "top_y": y0 + ry,
            "right_x": x0 + rx + width - 1,
            "bottom_y": y0 + ry + height - 1,
            "width": width,
            "height": height,
            "area": area,
        }
        if _component_is_inside_plate_text(component, plate_boxes or []):
            continue
        components.append(component)

    clusters = _cluster_punctuation_components(components)
    if not clusters:
        return {"detected": False, "bbox": None, "components": components, "cluster_count": 0}
    bbox = _union_component_boxes([box for cluster in clusters for box in cluster])
    return {
        "detected": True,
        "bbox": bbox,
        "components": components,
        "clusters": clusters,
        "cluster_count": len(clusters),
    }


def _component_is_inside_plate_text(component: dict[str, int], plate_boxes: list[dict[str, int]]) -> bool:
    for plate in plate_boxes:
        inside_x = plate["left_x"] <= component["left_x"] and component["right_x"] <= plate["right_x"]
        inside_y = plate["top_y"] <= component["top_y"] and component["bottom_y"] <= plate["bottom_y"] - 2
        if inside_x and inside_y:
            return True
    return False


def _cluster_punctuation_components(components: list[dict[str, int]]) -> list[list[dict[str, int]]]:
    ordered = sorted(components, key=lambda item: (item["top_y"], item["left_x"]))
    clusters: list[list[dict[str, int]]] = []
    for component in ordered:
        placed = False
        for cluster in clusters:
            cluster_box = _union_component_boxes(cluster)
            same_baseline = abs(((component["top_y"] + component["bottom_y"]) / 2) - ((cluster_box["top_y"] + cluster_box["bottom_y"]) / 2)) <= 8
            close_x = component["left_x"] - cluster_box["right_x"] <= 34 and component["right_x"] >= cluster_box["left_x"] - 34
            if same_baseline and close_x:
                cluster.append(component)
                placed = True
                break
        if not placed:
            clusters.append([component])
    return [
        cluster
        for cluster in clusters
        if _cluster_has_punctuation_geometry(cluster)
    ]


def _cluster_has_punctuation_geometry(cluster: list[dict[str, int]]) -> bool:
    cluster_box = _union_component_boxes(cluster)
    width = cluster_box["right_x"] - cluster_box["left_x"] + 1
    height = cluster_box["bottom_y"] - cluster_box["top_y"] + 1
    if width > 92 or height > 20:
        return False
    return len(cluster) >= 2 or _looks_like_single_punctuation(cluster[0])


def _looks_like_single_punctuation(component: dict[str, int]) -> bool:
    return component["height"] >= 5 and component["width"] <= 12 and component["area"] <= 80


def _union_component_boxes(boxes: list[dict[str, int]]) -> dict[str, int]:
    return {
        "left_x": min(box["left_x"] for box in boxes),
        "top_y": min(box["top_y"] for box in boxes),
        "right_x": max(box["right_x"] for box in boxes),
        "bottom_y": max(box["bottom_y"] for box in boxes),
    }
