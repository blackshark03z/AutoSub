from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from app.services.localization_quality import MAX_READING_CPS, PREFERRED_READING_CPS

MIN_DISPLAY_PROJECTION_RATIO = 0.75
REQUEST_BUDGET_SAFETY_RATIO = 0.90
DISPLAY_REWRITE_PROMPT_VERSION = "subtitle-display-condensation-v3"


class SubtitleProjectionRepairError(ValueError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        self.code = code
        self.evidence = evidence or {}
        super().__init__(message)


def _display_text(cue: dict[str, Any]) -> str:
    for key in ("render_text", "subtitle_text", "resolved_text", "translation_text", "text"):
        value = str(cue.get(key) or "").strip()
        if value:
            return " ".join(value.replace("\\N", " ").replace("\n", " ").split())
    return ""


def _number_tokens(text: str) -> Counter[str]:
    return Counter(re.findall(r"(?<![A-Za-z0-9])\d[\d,.]*(?![A-Za-z0-9])", str(text or "")))


def _material_markers(text: str) -> Counter[str]:
    value = str(text or "").casefold()
    tokens = re.findall(r"[a-z]+(?:'[a-z]+)?", value)
    base_tokens = [token.split("'", 1)[0] for token in tokens]
    markers: Counter[str] = Counter()
    person_groups = {
        "person:first_singular": {"i", "me", "my", "mine", "myself"},
        "person:first_plural": {"we", "us", "our", "ours", "ourselves"},
        "person:second": {"you", "your", "yours", "yourself", "yourselves"},
        "person:third_male": {"he", "him", "his", "himself"},
        "person:third_female": {"she", "her", "hers", "herself"},
        "person:third_plural": {"they", "them", "their", "theirs", "themselves"},
    }
    for marker, words in person_groups.items():
        if any(token in words for token in base_tokens):
            markers[marker] += 1
    negative_forms = {
        "not", "no", "never", "won't", "can't", "cannot", "couldn't", "shouldn't", "wouldn't",
        "mustn't", "isn't", "aren't", "wasn't", "weren't", "don't", "doesn't", "didn't",
        "haven't", "hasn't", "hadn't",
    }
    if any(token in negative_forms or token.endswith("n't") for token in tokens):
        markers["polarity:negative"] += 1
    modal_groups = {
        "modal:will": {"will", "won't"},
        "modal:can": {"can", "can't", "cannot"},
        "modal:could": {"could", "couldn't"},
        "modal:should": {"should", "shouldn't"},
        "modal:would": {"would", "wouldn't"},
        "modal:must": {"must", "mustn't"},
        "modal:may": {"may"},
        "modal:might": {"might"},
    }
    if any(token.endswith("'ll") for token in tokens):
        markers["modal:will"] += 1
    for marker, words in modal_groups.items():
        if any(token in words for token in tokens):
            markers[marker] += 1
    for number, count in _number_tokens(value).items():
        markers[f"number:{number}"] += count
    return markers


def repair_subtitle_display_projection(
    resolved: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    translator: Any,
) -> dict[str, Any]:
    violations = [item for item in list(evaluation.get("violations") or []) if isinstance(item, dict)]
    if not violations or any(str(item.get("code") or "") != "reading_speed" for item in violations):
        raise SubtitleProjectionRepairError(
            "subtitle_projection_repair_not_applicable",
            "Display condensation is only allowed when reading speed is the sole remaining hard violation.",
        )

    cues = [dict(item) for item in list(resolved.get("cues") or [])]
    cue_by_id = {str(cue.get("cue_id") or cue.get("id") or ""): cue for cue in cues}
    cue_order = [str(cue.get("cue_id") or cue.get("id") or "") for cue in cues]
    requests: list[dict[str, str]] = []
    budgets: dict[str, dict[str, Any]] = {}

    for violation in violations:
        cue_id = str(violation.get("cue_id") or "")
        cue = cue_by_id.get(cue_id)
        if cue is None:
            raise SubtitleProjectionRepairError("subtitle_projection_repair_cue_missing", "Quality failure cue missing.", {"cue_id": cue_id})
        source_text = str(cue.get("source_text") or "").strip()
        semantic_text = str(cue.get("translation_text") or _display_text(cue)).strip()
        current_text = _display_text(cue)
        try:
            start_ms = int(cue.get("start_ms"))
            end_ms = int(cue.get("end_ms"))
        except (TypeError, ValueError) as exc:
            raise SubtitleProjectionRepairError("subtitle_projection_repair_timing_invalid", "Valid cue timing is required.", {"cue_id": cue_id}) from exc
        duration_ms = end_ms - start_ms
        if not source_text or not semantic_text or not current_text or duration_ms <= 0:
            raise SubtitleProjectionRepairError("subtitle_projection_repair_source_invalid", "Source, semantic translation and positive timing are required.", {"cue_id": cue_id})
        acceptance_budget = max(1, int(math.floor((duration_ms / 1000.0) * MAX_READING_CPS)))
        before_characters = len(current_text)
        if acceptance_budget >= before_characters:
            raise SubtitleProjectionRepairError("subtitle_projection_repair_stale_violation", "Cue no longer exceeds hard reading-speed budget.", {"cue_id": cue_id})
        minimum_allowed = max(1, int(math.ceil(before_characters * MIN_DISPLAY_PROJECTION_RATIO)))
        if acceptance_budget < minimum_allowed:
            raise SubtitleProjectionRepairError(
                "subtitle_projection_repair_too_large",
                "Subtitle would require too much automatic semantic condensation.",
                {"cue_id": cue_id, "before_characters": before_characters, "acceptance_budget": acceptance_budget, "minimum_allowed_characters": minimum_allowed},
            )
        request_budget = max(minimum_allowed, min(acceptance_budget, int(math.floor(acceptance_budget * REQUEST_BUDGET_SAFETY_RATIO))))
        preferred_budget = max(minimum_allowed, min(request_budget, int(math.floor((duration_ms / 1000.0) * PREFERRED_READING_CPS))))
        index = cue_order.index(cue_id)
        requests.append({
            "id": cue_id,
            "source_text": source_text,
            "previous_source_text": str(cues[index - 1].get("source_text") or "") if index > 0 else "",
            "next_source_text": str(cues[index + 1].get("source_text") or "") if index + 1 < len(cues) else "",
            "duration_ms": str(duration_ms),
            "preferred_max_characters": str(preferred_budget),
            "hard_max_characters": str(request_budget),
            "minimum_characters": str(minimum_allowed),
            "current_english": current_text,
            "rewrite_mode": "subtitle_display_condensation",
            "rewrite_prompt_version": DISPLAY_REWRITE_PROMPT_VERSION,
        })
        budgets[cue_id] = {
            "before_characters": before_characters,
            "acceptance_budget": acceptance_budget,
            "request_budget": request_budget,
            "minimum_allowed_characters": minimum_allowed,
            "before_markers": dict(_material_markers(semantic_text)),
        }

    result = translator.translate(requests)
    translations = list(result.translations)
    if len(translations) != len(requests):
        raise SubtitleProjectionRepairError("subtitle_projection_repair_response_mismatch", "One projection per failed cue is required.")

    changes: list[dict[str, Any]] = []
    for expected, item in zip(requests, translations):
        cue_id = str(expected["id"])
        if str(item.get("id") or "") != cue_id:
            raise SubtitleProjectionRepairError("subtitle_projection_repair_response_mismatch", "Display condensation changed cue identity.", {"cue_id": cue_id})
        cue = cue_by_id[cue_id]
        budget = budgets[cue_id]
        new_text = " ".join(str(item.get("english") or "").strip().split())
        if not new_text:
            raise SubtitleProjectionRepairError("subtitle_projection_repair_empty", "Display condensation returned an empty subtitle.", {"cue_id": cue_id})
        after_characters = len(new_text)
        if after_characters > int(budget["acceptance_budget"]):
            raise SubtitleProjectionRepairError("subtitle_projection_repair_budget_missed", "Display condensation still exceeds hard reading-speed budget.", {"cue_id": cue_id, "after_characters": after_characters, **budget})
        if after_characters < int(budget["minimum_allowed_characters"]):
            raise SubtitleProjectionRepairError("subtitle_projection_repair_too_large", "Display condensation removed too much wording.", {"cue_id": cue_id, "after_characters": after_characters, **budget})
        semantic_text = str(cue.get("translation_text") or _display_text(cue)).strip()
        before_markers = _material_markers(semantic_text)
        after_markers = _material_markers(new_text)
        if before_markers != after_markers:
            raise SubtitleProjectionRepairError(
                "subtitle_projection_repair_material_fact_changed",
                "Display condensation changed a protected participant, polarity, modality, or numeric fact.",
                {"cue_id": cue_id, "before_markers": dict(before_markers), "after_markers": dict(after_markers)},
            )
        before_text = _display_text(cue)
        cue["resolved_text"] = new_text
        cue["subtitle_text"] = new_text
        if "render_text" in cue:
            cue["render_text"] = new_text
        cue["subtitle_projection_repaired"] = True
        changes.append({
            "cue_id": cue_id,
            "before_text": before_text,
            "after_text": new_text,
            "before_characters": len(before_text),
            "after_characters": after_characters,
            "acceptance_budget": budget["acceptance_budget"],
            "request_budget": budget["request_budget"],
        })

    evidence = {
        "schema_version": 1,
        "status": "PASS",
        "repair_kind": "bounded_subtitle_display_projection",
        "repaired_cue_count": len(changes),
        "changes": changes,
        "provider": getattr(translator, "provider_name", None),
        "model": getattr(result, "model", None),
        "request_count": int(getattr(result, "request_count", 0) or 0),
        "retry_count": int(getattr(result, "retry_count", 0) or 0),
        "cache_hits": int(getattr(result, "cache_hits", 0) or 0),
        "cache_misses": int(getattr(result, "cache_misses", 0) or 0),
        "policy": {
            "hard_reading_cps": MAX_READING_CPS,
            "minimum_allowed_projection_ratio": MIN_DISPLAY_PROJECTION_RATIO,
            "semantic_projection_scope": "subtitle_display_only",
            "protected_material_markers": ["participants", "polarity", "modality", "numbers"],
            "translation_text_preserved": True,
            "provider_pass_limit": 1,
        },
    }
    return {"resolved": {**resolved, "cues": cues, "subtitle_projection_repair": evidence}, "evidence": evidence}
