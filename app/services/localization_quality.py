from __future__ import annotations

import math
import re
from typing import Any

MAX_DIALOGUE_LINES = 2
MAX_CHARS_PER_LINE = 42
MAX_READING_CPS = 20.0
PREFERRED_READING_CPS = 17.0
MAX_DIALOGUE_DURATION_SECONDS = 7.0
TARGET_MIN_DISPLAY_SECONDS = 0.83
MAX_NARRATION_DISPLAY_ALIGNMENT_SHIFT_MS = 200


def _cue_text(cue: dict[str, Any]) -> str:
    for key in ("render_text", "subtitle_text", "resolved_text", "translation_text", "text"):
        value = str(cue.get(key) or "").strip()
        if value:
            return value.replace("\\N", "\n")
    return ""


def _cue_times(cue: dict[str, Any]) -> tuple[int, int]:
    try:
        start_ms = int(cue.get("start_ms"))
        end_ms = int(cue.get("end_ms"))
    except (TypeError, ValueError) as exc:
        raise ValueError("cue start_ms/end_ms must be integers") from exc
    return start_ms, end_ms


def _visible_characters(text: str) -> int:
    return len(text.replace("\n", ""))


def _flatten_display_text(text: str) -> str:
    return " ".join(str(text or "").replace("\\N", " ").replace("\n", " ").split())


def _wrap_two_lines(text: str) -> str | None:
    flat = _flatten_display_text(text)
    if len(flat) <= MAX_CHARS_PER_LINE:
        return flat
    if len(flat) > MAX_CHARS_PER_LINE * MAX_DIALOGUE_LINES:
        return None
    words = flat.split()
    if len(words) < 2:
        return None
    candidates: list[tuple[int, int, str]] = []
    for index in range(1, len(words)):
        left = " ".join(words[:index])
        right = " ".join(words[index:])
        if len(left) <= MAX_CHARS_PER_LINE and len(right) <= MAX_CHARS_PER_LINE:
            punctuation_bonus = 0 if re.search(r"[,;:.!?]$", left) else 8
            candidates.append((max(len(left), len(right)), abs(len(left) - len(right)) + punctuation_bonus, left + "\n" + right))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _split_once_near_middle(text: str) -> tuple[str, str] | None:
    flat = _flatten_display_text(text)
    positions = [match.start() for match in re.finditer(r"\s+", flat)]
    if not positions:
        return None
    midpoint = len(flat) / 2
    scored: list[tuple[float, int]] = []
    for position in positions:
        left = flat[:position].rstrip()
        right = flat[position:].lstrip()
        if not left or not right:
            continue
        punctuation_bonus = -12 if re.search(r"[,;:.!?]$", left) else 0
        scored.append((abs(position - midpoint) + punctuation_bonus, position))
    if not scored:
        return None
    position = min(scored)[1]
    return flat[:position].rstrip(), flat[position:].lstrip()


def _semantic_chunks(text: str, required_parts: int) -> list[str] | None:
    chunks = [_flatten_display_text(text)]
    required = max(1, int(required_parts))
    while len(chunks) < required or any(_wrap_two_lines(chunk) is None for chunk in chunks):
        candidates = [
            (len(chunk), index, _split_once_near_middle(chunk))
            for index, chunk in enumerate(chunks)
        ]
        candidates = [item for item in candidates if item[2] is not None]
        if not candidates:
            return None
        _, index, split = max(candidates, key=lambda item: item[0])
        assert split is not None
        chunks[index:index + 1] = [split[0], split[1]]
        if len(chunks) > 32:
            return None
    return chunks


def _projection_is_safe_to_split(cue: dict[str, Any], display_text: str) -> bool:
    display = _flatten_display_text(display_text)
    for key in ("spoken_text", "render_text", "subtitle_text", "resolved_text", "translation_text"):
        value = str(cue.get(key) or "").strip()
        if value and _flatten_display_text(value) != display:
            return False
    return True


def _rebalance_contiguous_cue_timing(
    cues: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    *,
    max_join_gap_ms: int = 120,
    max_boundary_shift_ms: int = 400,
) -> None:
    """Borrow time across one nearby boundary while preserving block endpoints.

    A target-language cue can be a little longer than its source-language cue.
    If the adjacent cue has enough slack, move only their shared boundary instead
    of rewriting text.  This is deliberately local: large pauses and generated
    narration alignment stay untouched, and no boundary moves more than 400 ms.
    """
    if len(cues) < 2:
        return

    def _alignment_locked(cue: dict[str, Any]) -> bool:
        provenance = str(cue.get("timing_provenance") or "").casefold()
        return "kokoro" in provenance or "alignment" in provenance

    def _required_ms(cue: dict[str, Any]) -> int:
        count = _visible_characters(_cue_text(cue))
        return math.ceil((count / MAX_READING_CPS) * 1000) if count else 1

    # Two passes let slack move through a short chain (A -> B -> C) without
    # globally redistributing every cue in a fast dialogue sequence.
    for _ in range(2):
        for index in range(len(cues) - 1):
            left = cues[index]
            right = cues[index + 1]
            alignment_locked = _alignment_locked(left) or _alignment_locked(right)
            # Narration token timings are the speech anchor, not an obligation to
            # show each subtitle for exactly the token span. A tiny bounded lead/
            # lag around a shared token boundary is safe for readability while
            # keeping the displayed cue perceptually aligned to the spoken line.
            effective_max_boundary_shift_ms = min(
                max_boundary_shift_ms,
                MAX_NARRATION_DISPLAY_ALIGNMENT_SHIFT_MS,
            ) if alignment_locked else max_boundary_shift_ms
            try:
                left_start, left_end = _cue_times(left)
                right_start, right_end = _cue_times(right)
            except ValueError:
                continue
            gap = right_start - left_end
            if gap < 0 or gap > max_join_gap_ms:
                continue
            left_required = _required_ms(left)
            right_required = _required_ms(right)
            left_current = left_end - left_start
            right_current = right_end - right_start
            if left_current >= left_required and right_current >= right_required:
                continue
            pair_available = right_end - left_start
            if left_required + right_required > pair_available:
                continue

            # Pick the closest feasible shared boundary to the original gap.
            original_boundary = round((left_end + right_start) / 2)
            minimum_boundary = left_start + left_required
            maximum_boundary = right_end - right_required
            boundary = min(max(original_boundary, minimum_boundary), maximum_boundary)
            if (
                abs(boundary - left_end) > effective_max_boundary_shift_ms
                or abs(boundary - right_start) > effective_max_boundary_shift_ms
            ):
                continue
            if boundary == left_end and boundary == right_start:
                continue

            left["end_ms"] = boundary
            right["start_ms"] = boundary
            if alignment_locked:
                left["display_timing_adjusted_from_alignment"] = True
                right["display_timing_adjusted_from_alignment"] = True
            actions.append({
                "cue_id": str(left.get("cue_id") or ""),
                "next_cue_id": str(right.get("cue_id") or ""),
                "code": "narration_display_timing_rebalance" if alignment_locked else "contiguous_timing_rebalance",
                "boundary_shift_from_left_ms": boundary - left_end,
                "boundary_shift_from_right_ms": boundary - right_start,
                "alignment_tolerance_ms": effective_max_boundary_shift_ms if alignment_locked else None,
            })


def repair_english_dialogue_cues(
    cues: list[dict[str, Any]],
    *,
    source_duration_ms: int,
    allow_split: bool = True,
) -> dict[str, Any]:
    """Apply only bounded, meaning-preserving structural subtitle repairs.

    The repair may reflow lines, split identical target projections at whitespace,
    and borrow unused adjacent display time. It never deletes or rewrites words.
    """
    source_duration = max(1, int(source_duration_ms))
    actions: list[dict[str, Any]] = []
    expanded: list[dict[str, Any]] = []

    for index, original in enumerate(cues):
        cue = dict(original)
        cue_id = str(cue.get("cue_id") or cue.get("id") or f"cue_{index + 1:04d}")
        text = _cue_text(cue)
        try:
            start_ms, end_ms = _cue_times(cue)
        except ValueError:
            expanded.append(cue)
            continue
        duration_ms = max(1, end_ms - start_ms)
        flat = _flatten_display_text(text)
        required_for_length = max(1, math.ceil(len(flat) / (MAX_CHARS_PER_LINE * MAX_DIALOGUE_LINES)))
        required_for_duration = max(1, math.ceil(duration_ms / round(MAX_DIALOGUE_DURATION_SECONDS * 1000)))
        required_parts = max(required_for_length, required_for_duration)
        chunks = [flat]
        if required_parts > 1:
            if not allow_split or not _projection_is_safe_to_split(cue, text):
                expanded.append(cue)
                continue
            split = _semantic_chunks(flat, required_parts)
            if not split:
                expanded.append(cue)
                continue
            chunks = split
            actions.append({"cue_id": cue_id, "code": "semantic_preserving_split", "part_count": len(chunks)})

        weights = [max(1, len(chunk.replace(" ", ""))) for chunk in chunks]
        total_weight = sum(weights)
        cursor = start_ms
        for part_index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
            if part_index == len(chunks):
                part_end = end_ms
            else:
                remaining = end_ms - cursor
                allocated = max(1, round(duration_ms * weight / total_weight))
                minimum_remaining = len(chunks) - part_index
                part_end = min(end_ms - minimum_remaining, cursor + allocated)
            wrapped = _wrap_two_lines(chunk)
            child = dict(cue)
            child_id = cue_id if len(chunks) == 1 else f"{cue_id}__q{part_index}"
            child["cue_id"] = child_id
            child["start_ms"] = cursor
            child["end_ms"] = part_end
            child["resolved_text"] = wrapped if wrapped is not None else chunk
            child["subtitle_text"] = wrapped if wrapped is not None else chunk
            if "render_text" in child:
                child["render_text"] = wrapped if wrapped is not None else chunk
            if len(chunks) > 1:
                child["translation_text"] = chunk
                if cue.get("spoken_text"):
                    child["spoken_text"] = chunk
                child["quality_parent_cue_id"] = cue_id
                child["quality_split_part"] = part_index
                child["quality_split_parts"] = len(chunks)
            if wrapped is not None and wrapped != flat and len(chunks) == 1:
                actions.append({"cue_id": cue_id, "code": "line_reflow"})
            elif wrapped is not None and "\n" in wrapped:
                actions.append({"cue_id": child_id, "code": "line_reflow"})
            expanded.append(child)
            cursor = part_end

    expanded.sort(key=lambda item: (int(item.get("start_ms") or 0), int(item.get("end_ms") or 0), str(item.get("cue_id") or "")))
    _rebalance_contiguous_cue_timing(expanded, actions)
    for index, cue in enumerate(expanded):
        text = _cue_text(cue)
        try:
            start_ms, end_ms = _cue_times(cue)
        except ValueError:
            continue
        visible = _visible_characters(text)
        required_ms = math.ceil((visible / MAX_READING_CPS) * 1000) if visible else 0
        current_ms = end_ms - start_ms
        if required_ms <= current_ms:
            continue
        previous_end = int(expanded[index - 1].get("end_ms") or 0) if index > 0 else 0
        next_start = int(expanded[index + 1].get("start_ms") or source_duration) if index + 1 < len(expanded) else source_duration
        needed = required_ms - current_ms
        grow_right = min(max(0, next_start - end_ms), needed)
        end_ms += grow_right
        needed -= grow_right
        grow_left = min(max(0, start_ms - previous_end), needed)
        start_ms -= grow_left
        needed -= grow_left
        if grow_right or grow_left:
            cue["start_ms"] = start_ms
            cue["end_ms"] = end_ms
            actions.append({
                "cue_id": str(cue.get("cue_id") or ""),
                "code": "safe_timing_extension",
                "extended_before_ms": grow_left,
                "extended_after_ms": grow_right,
            })

    evaluation = evaluate_english_dialogue_cues(expanded)
    return {
        "cues": expanded,
        "actions": actions,
        "repair_count": len(actions),
        "evaluation": evaluation,
        "eligible": bool(evaluation.get("eligible")),
    }


def evaluate_english_dialogue_cues(cues: list[dict[str, Any]]) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    attention: list[dict[str, Any]] = []
    normalized: list[tuple[int, int, str, str]] = []

    for index, cue in enumerate(cues):
        cue_id = str(cue.get("cue_id") or cue.get("id") or f"cue_{index + 1:04d}")
        text = _cue_text(cue)
        try:
            start_ms, end_ms = _cue_times(cue)
        except ValueError:
            violations.append({"cue_id": cue_id, "code": "invalid_timing"})
            continue
        if start_ms < 0 or end_ms <= start_ms:
            violations.append({"cue_id": cue_id, "code": "invalid_timing", "start_ms": start_ms, "end_ms": end_ms})
            continue
        if not text:
            violations.append({"cue_id": cue_id, "code": "empty_target_text"})
            continue

        lines = text.splitlines() or [text]
        duration_seconds = (end_ms - start_ms) / 1000.0
        cps = _visible_characters(text) / duration_seconds
        max_line_length = max(len(line) for line in lines)

        if len(lines) > MAX_DIALOGUE_LINES:
            violations.append({"cue_id": cue_id, "code": "too_many_lines", "value": len(lines), "limit": MAX_DIALOGUE_LINES})
        if max_line_length > MAX_CHARS_PER_LINE:
            violations.append({"cue_id": cue_id, "code": "line_too_long", "value": max_line_length, "limit": MAX_CHARS_PER_LINE})
        if cps > MAX_READING_CPS:
            violations.append({"cue_id": cue_id, "code": "reading_speed", "value": round(cps, 3), "limit": MAX_READING_CPS})
        elif cps > PREFERRED_READING_CPS:
            attention.append({"cue_id": cue_id, "code": "reading_speed_attention", "value": round(cps, 3), "preferred": PREFERRED_READING_CPS})
        if duration_seconds > MAX_DIALOGUE_DURATION_SECONDS:
            violations.append({"cue_id": cue_id, "code": "duration_too_long", "value": round(duration_seconds, 3), "limit": MAX_DIALOGUE_DURATION_SECONDS})
        if duration_seconds < TARGET_MIN_DISPLAY_SECONDS:
            attention.append({"cue_id": cue_id, "code": "short_display_time", "value": round(duration_seconds, 3), "target": TARGET_MIN_DISPLAY_SECONDS})

        normalized.append((start_ms, end_ms, cue_id, text))

    normalized.sort(key=lambda item: (item[0], item[1], item[2]))
    for previous, current in zip(normalized, normalized[1:]):
        if current[0] < previous[1]:
            violations.append({
                "cue_id": current[2],
                "code": "dialogue_overlap",
                "previous_cue_id": previous[2],
                "overlap_ms": previous[1] - current[0],
            })

    counts: dict[str, int] = {}
    for item in violations:
        counts[item["code"]] = counts.get(item["code"], 0) + 1

    return {
        "status": "PASS" if not violations else "FAIL",
        "eligible": not violations,
        "cue_count": len(cues),
        "checked_cue_count": len(normalized),
        "violation_count": len(violations),
        "violation_counts": counts,
        "violations": violations,
        "attention": attention,
        "profile": {
            "max_lines": MAX_DIALOGUE_LINES,
            "max_chars_per_line": MAX_CHARS_PER_LINE,
            "preferred_cps": PREFERRED_READING_CPS,
            "max_cps": MAX_READING_CPS,
            "max_duration_seconds": MAX_DIALOGUE_DURATION_SECONDS,
            "target_min_display_seconds": TARGET_MIN_DISPLAY_SECONDS,
        },
    }
