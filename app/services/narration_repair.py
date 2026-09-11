from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from app.services.narration_pipeline import build_narration_timeline
from app.services.tts_units import attach_tts_synthesis_units


MIN_CONDENSATION_RATIO = 0.85
MAX_CONDENSATION_TARGET_RATIO = 0.95
CONDENSATION_SAFETY_RATIO = 0.97


class NarrationProjectionRepairError(ValueError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        self.code = code
        self.evidence = evidence or {}
        super().__init__(message)


def _spoken_text(cue: dict[str, Any]) -> str:
    return str(
        cue.get("spoken_text")
        or cue.get("resolved_text")
        or cue.get("translation_text")
        or cue.get("text")
        or ""
    ).strip()


def _number_tokens(text: str) -> Counter[str]:
    return Counter(re.findall(r"(?<![A-Za-z0-9])\d[\d,.]*(?![A-Za-z0-9])", str(text or "")))


def repair_narration_spoken_projection(
    resolved: dict[str, Any],
    failures: list[dict[str, Any]],
    *,
    source_duration_ms: int,
    translator: Any,
) -> dict[str, Any]:
    """Apply one bounded, narration-only semantic rewrite for real PCM overflow.

    Subtitle/translation projections remain untouched. The repair is allowed only
    when the existing narration is already close enough to fit that a <=15%
    shorter spoken projection can plausibly solve it. Gemini retranslates the
    original source cue under a stricter character budget; numeric material facts
    are checked again locally before the projection is accepted.
    """
    cues = [dict(item) for item in list(resolved.get("cues") or [])]
    if not cues or not failures:
        raise NarrationProjectionRepairError("narration_repair_not_applicable", "No narration fit failure is available to repair.")

    timeline = build_narration_timeline({**resolved, "cues": cues}, source_duration_ms=source_duration_ms)
    units = attach_tts_synthesis_units(timeline)
    unit_by_id = {str(unit["id"]): unit for unit in units}
    cue_by_id = {str(cue.get("cue_id") or cue.get("id") or ""): cue for cue in cues}
    cue_order = [str(cue.get("cue_id") or cue.get("id") or "") for cue in cues]

    requested: list[dict[str, str]] = []
    budgets: dict[str, dict[str, Any]] = {}
    unit_budgets: dict[str, dict[str, Any]] = {}
    repaired_unit_ids: list[str] = []
    for failure in failures:
        unit_id = str(failure.get("tts_unit_id") or "")
        unit = unit_by_id.get(unit_id)
        if unit is None:
            raise NarrationProjectionRepairError(
                "narration_repair_unit_missing",
                "The failed narration unit no longer matches the resolved cue identity.",
                {"tts_unit_id": unit_id},
            )
        try:
            available_ms = float(failure.get("available_ms"))
            duration_ms = float(failure.get("tts_duration_ms"))
        except (TypeError, ValueError) as exc:
            raise NarrationProjectionRepairError(
                "narration_repair_fit_evidence_invalid",
                "Narration repair requires measured PCM fit evidence.",
                {"tts_unit_id": unit_id},
            ) from exc
        if available_ms <= 0 or duration_ms <= 0:
            raise NarrationProjectionRepairError(
                "narration_repair_fit_evidence_invalid",
                "Narration repair requires positive measured timing evidence.",
                {"tts_unit_id": unit_id},
            )
        fit_fraction = available_ms / duration_ms
        if fit_fraction < MIN_CONDENSATION_RATIO:
            raise NarrationProjectionRepairError(
                "narration_repair_too_large",
                "The narration would require too much semantic condensation for an automatic repair.",
                {"tts_unit_id": unit_id, "fit_fraction": round(fit_fraction, 6)},
            )
        target_fraction = max(
            MIN_CONDENSATION_RATIO,
            min(MAX_CONDENSATION_TARGET_RATIO, fit_fraction * CONDENSATION_SAFETY_RATIO),
        )
        repaired_unit_ids.append(unit_id)
        segment_ids = [str(value) for value in list(unit.get("segment_ids") or [])]
        unit_before_characters = len(str(unit.get("spoken_text") or ""))
        unit_hard_max_characters = max(4, int(math.floor(unit_before_characters * target_fraction)))
        unit_hard_max_characters = min(unit_hard_max_characters, max(4, unit_before_characters - 1))
        unit_budgets[unit_id] = {
            "before_characters": unit_before_characters,
            "hard_max_characters": unit_hard_max_characters,
            "fit_fraction": round(fit_fraction, 6),
            "target_fraction": round(target_fraction, 6),
            "segment_ids": segment_ids,
        }
        for cue_id in segment_ids:
            cue = cue_by_id.get(cue_id)
            if cue is None:
                raise NarrationProjectionRepairError(
                    "narration_repair_cue_missing",
                    "The failed narration unit references a cue that is no longer available.",
                    {"tts_unit_id": unit_id, "cue_id": cue_id},
                )
            current = _spoken_text(cue)
            source = str(cue.get("source_text") or "").strip()
            if not current or not source:
                raise NarrationProjectionRepairError(
                    "narration_repair_source_missing",
                    "Narration-only repair requires both the canonical source cue and current target wording.",
                    {"tts_unit_id": unit_id, "cue_id": cue_id},
                )
            hard_max = max(4, int(math.floor(len(current) * target_fraction)))
            hard_max = min(hard_max, max(4, len(current) - 1))
            index = cue_order.index(cue_id)
            requested.append(
                {
                    "id": cue_id,
                    "source_text": source,
                    "previous_source_text": str(cues[index - 1].get("source_text") or "") if index > 0 else "",
                    "next_source_text": str(cues[index + 1].get("source_text") or "") if index + 1 < len(cues) else "",
                    "duration_ms": str(max(1, int(cue.get("end_ms") or 0) - int(cue.get("start_ms") or 0))),
                    "preferred_max_characters": str(hard_max),
                    "hard_max_characters": str(hard_max),
                }
            )
            budgets[cue_id] = {
                "tts_unit_id": unit_id,
                "before_characters": len(current),
                "hard_max_characters": hard_max,
                "fit_fraction": round(fit_fraction, 6),
                "target_fraction": round(target_fraction, 6),
                "before_numbers": list(_number_tokens(current).elements()),
            }

    result = translator.translate(requested)
    translations = list(result.translations)
    if len(translations) != len(requested):
        raise NarrationProjectionRepairError(
            "narration_repair_response_mismatch",
            "Narration repair did not return one projection for every failed cue.",
        )

    changed: list[dict[str, Any]] = []
    for expected, item in zip(requested, translations):
        cue_id = str(expected["id"])
        if str(item.get("id") or "") != cue_id:
            raise NarrationProjectionRepairError(
                "narration_repair_response_mismatch",
                "Narration repair changed cue identity.",
                {"cue_id": cue_id},
            )
        new_text = str(item.get("english") or "").strip()
        cue = cue_by_id[cue_id]
        old_text = _spoken_text(cue)
        budget = budgets[cue_id]
        individual_growth_limit = max(len(old_text) + 4, int(math.ceil(len(old_text) * 1.10)))
        if not new_text or len(new_text) > individual_growth_limit:
            raise NarrationProjectionRepairError(
                "narration_repair_cue_growth_excessive",
                "Narration repair expanded one cue too far even though the unit needs condensation.",
                {"cue_id": cue_id, "characters": len(new_text), "growth_limit": individual_growth_limit, **budget},
            )
        if _number_tokens(old_text) != _number_tokens(new_text):
            raise NarrationProjectionRepairError(
                "narration_repair_material_fact_changed",
                "Narration repair changed a numeric material fact.",
                {"cue_id": cue_id, "before_numbers": budget["before_numbers"], "after_numbers": list(_number_tokens(new_text).elements())},
            )
        cue["spoken_text"] = new_text
        cue["narration_projection_repaired"] = True
        changed.append(
            {
                "cue_id": cue_id,
                "tts_unit_id": budget["tts_unit_id"],
                "before_characters": len(old_text),
                "after_characters": len(new_text),
                "requested_cue_budget": budget["hard_max_characters"],
            }
        )

    for unit_id, unit_budget in unit_budgets.items():
        segment_ids = list(unit_budget["segment_ids"])
        after_text = " ".join(_spoken_text(cue_by_id[cue_id]) for cue_id in segment_ids if cue_id in cue_by_id)
        after_characters = len(after_text)
        if after_characters > int(unit_budget["hard_max_characters"]) or after_characters >= int(unit_budget["before_characters"]):
            raise NarrationProjectionRepairError(
                "narration_repair_budget_missed",
                "Narration repair did not satisfy the bounded TTS-unit character budget.",
                {
                    "tts_unit_id": unit_id,
                    "after_characters": after_characters,
                    **unit_budget,
                },
            )
        unit_budget["after_characters"] = after_characters

    evidence = {
        "schema_version": 1,
        "status": "PASS",
        "repair_kind": "bounded_narration_only_projection",
        "repaired_unit_ids": repaired_unit_ids,
        "repaired_cue_count": len(changed),
        "changes": changed,
        "unit_budgets": unit_budgets,
        "provider": getattr(translator, "provider_name", None),
        "model": getattr(result, "model", None),
        "request_count": int(getattr(result, "request_count", 0) or 0),
        "retry_count": int(getattr(result, "retry_count", 0) or 0),
        "cache_hits": int(getattr(result, "cache_hits", 0) or 0),
        "cache_misses": int(getattr(result, "cache_misses", 0) or 0),
        "policy": {
            "minimum_allowed_projection_ratio": MIN_CONDENSATION_RATIO,
            "maximum_target_projection_ratio": MAX_CONDENSATION_TARGET_RATIO,
            "semantic_projection_scope": "spoken_text_only",
            "numeric_material_facts_preserved": True,
        },
    }
    return {"resolved": {**resolved, "cues": cues, "narration_projection_repair": evidence}, "evidence": evidence}
