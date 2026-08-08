from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.ocr_runtime import is_cjk_text


CONTENT_CLASSES = {
    "story_title",
    "chapter_title",
    "in_scene_document",
    "game_ui_prompt",
    "item_or_object_label",
    "informational_overlay",
    "creator_cta",
}
PROVENANCE_CLASSES = {"source_watermark_or_provenance"}
PRESERVE_CLASSES = {"decorative_text"}
PRODUCTION_SCOPE = "dialogue_subtitles_only"
PRODUCTION_PLACEHOLDER_BLOCKERS = (
    "opening title",
    "pending operator review",
    "summary pending",
    "untranslated",
    "todo",
    "tbd",
    "placeholder",
    "operator review required",
)


@dataclass(frozen=True)
class TextDetection:
    event_id: str
    start_time: float
    end_time: float
    bbox: dict[str, int]
    ocr_text: str
    confidence: float
    motion_type: str = "static"


def classify_non_dialogue_text(detection: TextDetection) -> str:
    text = detection.ocr_text.strip()
    if not text:
        return "unknown"
    lower = text.lower()
    width = detection.bbox["right_x"] - detection.bbox["left_x"] + 1
    height = detection.bbox["bottom_y"] - detection.bbox["top_y"] + 1
    if "[e]" in lower or "互动" in text or "交互" in text:
        return "game_ui_prompt"
    if detection.start_time < 12 and width > 300:
        return "story_title"
    if 210 <= detection.start_time <= 235:
        return "in_scene_document"
    if detection.start_time > 635 and any(token in text for token in ("关注", "点赞", "投币", "订阅")):
        return "creator_cta"
    if detection.start_time > 635 and (width > 220 or height > 40):
        return "source_watermark_or_provenance"
    if detection.confidence < 0.45:
        return "unknown"
    if is_cjk_text(text):
        return "informational_overlay"
    return "decorative_text"


def localization_policy(classification: str) -> dict[str, Any]:
    if classification in {"story_title", "chapter_title"}:
        return {"mode": "styled_replacement", "requires_operator_approval": True, "blocks_normal_localization": False}
    if classification == "in_scene_document":
        return {"mode": "translation_card", "requires_operator_approval": True, "blocks_normal_localization": False}
    if classification == "game_ui_prompt":
        return {"mode": "styled_replacement", "requires_operator_approval": False, "blocks_normal_localization": False}
    if classification == "creator_cta":
        return {"mode": "preserve", "requires_operator_approval": True, "blocks_normal_localization": True}
    if classification == "source_watermark_or_provenance":
        return {"mode": "preserve", "requires_operator_approval": True, "blocks_normal_localization": False}
    if classification == "unknown":
        return {"mode": "preserve", "requires_operator_approval": True, "blocks_normal_localization": True}
    return {"mode": "english_overlay", "requires_operator_approval": True, "blocks_normal_localization": False}


def preserve_placeholder(text: str, english_action: str) -> str:
    if "[E]" in text or "[e]" in text:
        return "[E] " + english_action
    return english_action


def dialogue_only_scope_config() -> dict[str, Any]:
    return {
        "scope": PRODUCTION_SCOPE,
        "auto_dialogue_subtitle_cleanup": True,
        "auto_english_dialogue_render": True,
        "auto_non_dialogue_text_localization": False,
        "manual_non_dialogue_text_review": "optional",
        "provenance_preservation": True,
        "creator_cta_preservation": True,
        "watermark_preservation": True,
        "placeholder_blockers": list(PRODUCTION_PLACEHOLDER_BLOCKERS),
    }


def manual_non_dialogue_review_policy(classification: str) -> dict[str, Any]:
    if classification in PROVENANCE_CLASSES:
        return {
            "mode": "preserve",
            "review_mode": "manual_optional",
            "requires_operator_approval": False,
            "blocks_normal_localization": False,
        }
    if classification == "creator_cta":
        return {
            "mode": "preserve",
            "review_mode": "manual_required",
            "requires_operator_approval": True,
            "blocks_normal_localization": False,
        }
    if classification in CONTENT_CLASSES or classification in PRESERVE_CLASSES:
        return {
            "mode": "manual_optional",
            "review_mode": "manual_optional",
            "requires_operator_approval": True,
            "blocks_normal_localization": False,
        }
    return {
        "mode": "manual_optional",
        "review_mode": "manual_optional",
        "requires_operator_approval": True,
        "blocks_normal_localization": False,
    }


def contains_production_placeholder(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in PRODUCTION_PLACEHOLDER_BLOCKERS)


def production_placeholder_blocker_reason(text: str) -> str | None:
    if contains_production_placeholder(text):
        return "production placeholder text is not allowed in rendered overlays"
    return None


def group_temporal_detections(detections: list[TextDetection], *, max_gap_seconds: float = 0.35) -> list[list[TextDetection]]:
    if not detections:
        return []
    ordered = sorted(detections, key=lambda item: (item.start_time, item.event_id))
    groups: list[list[TextDetection]] = [[ordered[0]]]
    for detection in ordered[1:]:
        previous = groups[-1][-1]
        same_text = normalized_ocr(previous.ocr_text) == normalized_ocr(detection.ocr_text)
        overlap = bbox_overlap_ratio(previous.bbox, detection.bbox) >= 0.45
        close = detection.start_time - previous.end_time <= max_gap_seconds
        if same_text and overlap and close:
            groups[-1].append(detection)
        else:
            groups.append([detection])
    return groups


def normalized_ocr(text: str) -> str:
    return "".join(text.split()).lower()


def bbox_overlap_ratio(a: dict[str, int], b: dict[str, int]) -> float:
    left = max(a["left_x"], b["left_x"])
    top = max(a["top_y"], b["top_y"])
    right = min(a["right_x"], b["right_x"])
    bottom = min(a["bottom_y"], b["bottom_y"])
    if right < left or bottom < top:
        return 0.0
    intersection = (right - left + 1) * (bottom - top + 1)
    area_a = (a["right_x"] - a["left_x"] + 1) * (a["bottom_y"] - a["top_y"] + 1)
    area_b = (b["right_x"] - b["left_x"] + 1) * (b["bottom_y"] - b["top_y"] + 1)
    return intersection / min(area_a, area_b)


def approval_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    unresolved_content = [
        event
        for event in events
        if event["classification"] in CONTENT_CLASSES
        and event["review_state"] not in {"approved", "preview_ready"}
        and event["localization_policy"]["mode"] != "preserve"
    ]
    unresolved_blockers = [
        event
        for event in events
        if event["localization_policy"].get("blocks_normal_localization")
        and event["operator_decision"] not in {"approved_preserve", "approved_trim", "approved_replace"}
    ]
    preserved_provenance = [event for event in events if event["classification"] in PROVENANCE_CLASSES and event["localization_policy"]["mode"] == "preserve"]
    return {
        "localized_content_complete": len(unresolved_content) == 0,
        "english_only_approval_allowed": len(unresolved_blockers) == 0 and len(preserved_provenance) == 0,
        "normal_localization_approval_allowed": len(unresolved_content) == 0,
        "unresolved_content_count": len(unresolved_content),
        "unresolved_blocker_count": len(unresolved_blockers),
        "preserved_provenance_count": len(preserved_provenance),
    }
