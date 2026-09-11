from __future__ import annotations

from typing import Any


DIALOGUE_CAPTION = "dialogue_caption"
VISUAL_TEXT = "visual_text"


def classify_resolved_cue_role(
    cue: dict[str, Any],
    *,
    frame_width: int,
    frame_height: int,
) -> dict[str, Any]:
    bbox = cue.get("source_bbox") if isinstance(cue.get("source_bbox"), dict) else None
    if not bbox:
        return {
            "role": DIALOGUE_CAPTION,
            "confidence": 0.7,
            "reason": "no_visual_bbox_treat_as_spoken_dialogue_evidence",
        }
    try:
        left = float(bbox["left_x"])
        top = float(bbox["top_y"])
        right = float(bbox["right_x"])
        bottom = float(bbox["bottom_y"])
    except (KeyError, TypeError, ValueError):
        return {"role": VISUAL_TEXT, "confidence": 0.5, "reason": "invalid_bbox_requires_visual_review"}
    if frame_width <= 0 or frame_height <= 0 or right <= left or bottom <= top:
        return {"role": VISUAL_TEXT, "confidence": 0.5, "reason": "invalid_frame_geometry_requires_visual_review"}

    center_y = (top + bottom) / 2.0
    center_y_norm = center_y / frame_height
    bottom_norm = bottom / frame_height
    width_norm = (right - left) / frame_width

    # Burned dialogue captions in the supported source genre occupy the lower
    # presentation band. Geometry is an initial role signal, not semantic truth;
    # ASR/cross-frame evidence may later override low-confidence cases.
    if center_y_norm >= 0.70 or bottom_norm >= 0.78:
        confidence = 0.94 if width_norm >= 0.18 else 0.82
        return {
            "role": DIALOGUE_CAPTION,
            "confidence": confidence,
            "reason": "lower_caption_band_geometry",
            "geometry": {
                "center_y_norm": round(center_y_norm, 6),
                "bottom_norm": round(bottom_norm, 6),
                "width_norm": round(width_norm, 6),
            },
        }
    return {
        "role": VISUAL_TEXT,
        "confidence": 0.94,
        "reason": "outside_dialogue_caption_band",
        "geometry": {
            "center_y_norm": round(center_y_norm, 6),
            "bottom_norm": round(bottom_norm, 6),
            "width_norm": round(width_norm, 6),
        },
    }


def partition_resolved_cues(
    resolved: dict[str, Any],
    *,
    frame_width: int,
    frame_height: int,
) -> dict[str, Any]:
    dialogue: list[dict[str, Any]] = []
    visual: list[dict[str, Any]] = []
    for cue in list(resolved.get("cues") or []):
        classification = classify_resolved_cue_role(cue, frame_width=frame_width, frame_height=frame_height)
        copy = dict(cue)
        copy["localization_role"] = classification["role"]
        copy["role_classification"] = classification
        if classification["role"] == DIALOGUE_CAPTION:
            dialogue.append(copy)
        else:
            visual.append(copy)
    return {
        "dialogue_cues": dialogue,
        "visual_text_cues": visual,
        "counts": {
            "input": len(list(resolved.get("cues") or [])),
            "dialogue": len(dialogue),
            "visual_text": len(visual),
        },
    }


def resolved_dialogue_projection(
    resolved: dict[str, Any],
    *,
    frame_width: int,
    frame_height: int,
) -> dict[str, Any]:
    partitioned = partition_resolved_cues(resolved, frame_width=frame_width, frame_height=frame_height)
    return {
        **resolved,
        "cues": partitioned["dialogue_cues"],
        "visual_text_candidates": partitioned["visual_text_cues"],
        "role_partition": partitioned["counts"],
    }
