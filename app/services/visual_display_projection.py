from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from app.services.subtitle_projection_repair import _material_markers


MIN_VISUAL_DISPLAY_PROJECTION_RATIO = 0.75
MAX_VISUAL_DISPLAY_PROJECTION_RATIO = 0.80
VISUAL_DISPLAY_REWRITE_PROMPT_VERSION = "visual-display-condensation-v1"


class VisualDisplayProjectionRepairError(ValueError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        self.code = code
        self.evidence = evidence or {}
        super().__init__(message)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def repair_visual_display_projection(
    localized: dict[str, Any],
    plan: dict[str, Any],
    *,
    translator: Any,
) -> dict[str, Any]:
    """Condense only visual display projections that cannot fit their Tier-A box.

    Semantic translation is immutable. This is a single bounded provider pass whose
    output is revalidated by the normal Tier-A geometry/fit planner before render.
    Any escalation other than a pure target-text fit failure remains fail-closed.
    """

    escalations = [item for item in list(plan.get("escalations") or []) if isinstance(item, dict)]
    if not escalations or any(str(item.get("reason") or "") != "target_text_does_not_fit" for item in escalations):
        raise VisualDisplayProjectionRepairError(
            "visual_display_projection_not_applicable",
            "Visual display projection is only allowed when target-text fit is the sole replacement escalation.",
        )

    tracks = [dict(item) for item in list(localized.get("tracks") or [])]
    track_by_id = {str(track.get("track_id") or ""): track for track in tracks}
    groups: dict[tuple[str, str, str, int, int], list[str]] = defaultdict(list)
    budgets: dict[str, dict[str, Any]] = {}

    for escalation in escalations:
        track_id = str(escalation.get("track_id") or "")
        track = track_by_id.get(track_id)
        if track is None:
            raise VisualDisplayProjectionRepairError(
                "visual_display_projection_track_missing",
                "Replacement escalation track is missing from localized visual evidence.",
                {"track_id": track_id},
            )
        source_text = _clean_text(track.get("canonical_text"))
        semantic_text = _clean_text(track.get("semantic_translation") or track.get("display_text"))
        current_text = _clean_text(track.get("display_text"))
        if not source_text or not semantic_text or not current_text:
            raise VisualDisplayProjectionRepairError(
                "visual_display_projection_source_invalid",
                "Source, semantic translation and current visual display text are required.",
                {"track_id": track_id},
            )
        before_characters = len(current_text)
        minimum_allowed = max(1, int(math.ceil(before_characters * MIN_VISUAL_DISPLAY_PROJECTION_RATIO)))
        hard_maximum = max(minimum_allowed, int(math.floor(before_characters * MAX_VISUAL_DISPLAY_PROJECTION_RATIO)))
        if hard_maximum >= before_characters:
            raise VisualDisplayProjectionRepairError(
                "visual_display_projection_budget_unavailable",
                "Visual label is too short for a bounded automatic condensation pass.",
                {"track_id": track_id, "before_characters": before_characters},
            )
        group_key = (source_text, semantic_text, current_text, minimum_allowed, hard_maximum)
        groups[group_key].append(track_id)
        budgets[track_id] = {
            "before_characters": before_characters,
            "minimum_allowed_characters": minimum_allowed,
            "hard_max_characters": hard_maximum,
            "before_markers": dict(_material_markers(semantic_text)),
        }

    requests: list[dict[str, str]] = []
    representative_groups: dict[str, tuple[tuple[str, str, str, int, int], list[str]]] = {}
    for group_key, track_ids in groups.items():
        source_text, semantic_text, current_text, minimum_allowed, hard_maximum = group_key
        representative_id = track_ids[0]
        requests.append(
            {
                "id": representative_id,
                "source_text": source_text,
                "previous_source_text": "",
                "next_source_text": "",
                "current_english": current_text,
                "preferred_max_characters": str(hard_maximum),
                "hard_max_characters": str(hard_maximum),
                "minimum_characters": str(minimum_allowed),
                "rewrite_mode": "visual_display_condensation",
                "rewrite_prompt_version": VISUAL_DISPLAY_REWRITE_PROMPT_VERSION,
            }
        )
        representative_groups[representative_id] = (group_key, track_ids)

    result = translator.translate(requests)
    translations = list(getattr(result, "translations", []) or [])
    if len(translations) != len(requests):
        raise VisualDisplayProjectionRepairError(
            "visual_display_projection_response_mismatch",
            "One visual display projection per unique failed label is required.",
        )

    changes: list[dict[str, Any]] = []
    for expected, item in zip(requests, translations):
        representative_id = str(expected["id"])
        if str(item.get("id") or "") != representative_id:
            raise VisualDisplayProjectionRepairError(
                "visual_display_projection_response_mismatch",
                "Visual display projection changed track identity.",
                {"track_id": representative_id},
            )
        group_key, track_ids = representative_groups[representative_id]
        _source_text, semantic_text, current_text, minimum_allowed, hard_maximum = group_key
        projected_text = _clean_text(item.get("english"))
        if not projected_text:
            raise VisualDisplayProjectionRepairError(
                "visual_display_projection_empty",
                "Visual display projection returned an empty label.",
                {"track_id": representative_id},
            )
        after_characters = len(projected_text)
        if after_characters < minimum_allowed or after_characters > hard_maximum:
            raise VisualDisplayProjectionRepairError(
                "visual_display_projection_budget_missed",
                "Visual display projection did not satisfy the bounded character budget.",
                {
                    "track_id": representative_id,
                    "after_characters": after_characters,
                    "minimum_allowed_characters": minimum_allowed,
                    "hard_max_characters": hard_maximum,
                },
            )
        before_markers = _material_markers(semantic_text)
        after_markers = _material_markers(projected_text)
        if before_markers != after_markers:
            raise VisualDisplayProjectionRepairError(
                "visual_display_projection_material_fact_changed",
                "Visual display projection changed a protected participant, polarity, modality, or numeric fact.",
                {
                    "track_id": representative_id,
                    "before_markers": dict(before_markers),
                    "after_markers": dict(after_markers),
                },
            )
        for track_id in track_ids:
            track = track_by_id[track_id]
            before_text = _clean_text(track.get("display_text"))
            track["display_text"] = projected_text
            track["visual_display_projection_repaired"] = True
            track["visual_display_projection_prompt_version"] = VISUAL_DISPLAY_REWRITE_PROMPT_VERSION
            changes.append(
                {
                    "track_id": track_id,
                    "before_text": before_text,
                    "after_text": projected_text,
                    "before_characters": len(before_text),
                    "after_characters": after_characters,
                    "minimum_allowed_characters": minimum_allowed,
                    "hard_max_characters": hard_maximum,
                    "semantic_translation_preserved": _clean_text(track.get("semantic_translation")) == semantic_text,
                }
            )

    evidence = {
        "schema_version": 1,
        "status": "PASS",
        "repair_kind": "bounded_visual_display_projection",
        "repaired_track_count": len(changes),
        "unique_projection_request_count": len(requests),
        "changes": changes,
        "provider": getattr(translator, "provider_name", None),
        "model": getattr(result, "model", getattr(translator, "model", None)),
        "request_count": int(getattr(result, "request_count", 0) or 0),
        "retry_count": int(getattr(result, "retry_count", 0) or 0),
        "cache_hits": int(getattr(result, "cache_hits", 0) or 0),
        "cache_misses": int(getattr(result, "cache_misses", 0) or 0),
        "policy": {
            "minimum_allowed_projection_ratio": MIN_VISUAL_DISPLAY_PROJECTION_RATIO,
            "maximum_requested_projection_ratio": MAX_VISUAL_DISPLAY_PROJECTION_RATIO,
            "semantic_projection_scope": "visual_display_only",
            "semantic_translation_preserved": True,
            "provider_pass_limit": 1,
            "post_projection_tier_a_fit_recheck_required": True,
        },
    }
    return {
        "localized": {**localized, "tracks": tracks, "visual_display_projection_repair": evidence},
        "evidence": evidence,
    }
