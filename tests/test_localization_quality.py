import json

from app.services.localization_quality import evaluate_english_dialogue_cues, repair_english_dialogue_cues
from app.services.simple_workflow import (
    _load_localization_quality_evidence,
    _write_localization_quality_evidence,
)


def cue(cue_id: str, start_ms: int, end_ms: int, text: str) -> dict:
    return {"cue_id": cue_id, "start_ms": start_ms, "end_ms": end_ms, "render_text": text}


def test_quality_gate_passes_readable_non_overlapping_cues() -> None:
    result = evaluate_english_dialogue_cues([
        cue("A", 0, 2000, "This is readable."),
        cue("B", 2100, 4500, "Another subtitle line."),
    ])
    assert result["status"] == "PASS"
    assert result["eligible"] is True
    assert result["violation_count"] == 0


def test_quality_gate_fails_hard_readability_and_timing_violations() -> None:
    result = evaluate_english_dialogue_cues([
        cue("A", 0, 1000, "This line is intentionally much longer than forty two characters."),
        cue("B", 900, 9000, "Line one\nLine two\nLine three"),
    ])
    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "line_too_long" in codes
    assert "reading_speed" in codes
    assert "too_many_lines" in codes
    assert "duration_too_long" in codes
    assert "dialogue_overlap" in codes


def test_quality_gate_uses_attention_band_without_failing() -> None:
    text = "123456789012345678"
    result = evaluate_english_dialogue_cues([cue("A", 0, 1000, text)])
    assert result["status"] == "PASS"
    assert result["eligible"] is True
    assert result["attention"][0]["code"] == "reading_speed_attention"


def test_quality_gate_flags_empty_or_invalid_cue() -> None:
    result = evaluate_english_dialogue_cues([
        cue("A", 1000, 1000, "bad timing"),
        cue("B", 2000, 3000, ""),
    ])
    codes = [item["code"] for item in result["violations"]]
    assert codes == ["invalid_timing", "empty_target_text"]


def test_ass_line_break_marker_is_treated_as_real_line_break() -> None:
    result = evaluate_english_dialogue_cues([cue("A", 0, 3000, "First line\\NSecond line")])
    assert result["status"] == "PASS"


def test_bounded_repair_reflows_without_changing_words() -> None:
    original = "This sentence has enough words to require a balanced two line subtitle layout."
    result = repair_english_dialogue_cues(
        [cue("A", 0, 5000, original)],
        source_duration_ms=6000,
    )
    assert result["eligible"] is True
    repaired = result["cues"][0]["resolved_text"]
    assert "\n" in repaired
    assert repaired.replace("\n", " ") == original
    assert max(len(line) for line in repaired.splitlines()) <= 42


def test_bounded_repair_splits_long_text_without_dropping_content() -> None:
    original = (
        "This is the first clause, and this is the second clause, while this final clause "
        "keeps every original word in the localized subtitle output."
    )
    result = repair_english_dialogue_cues(
        [cue("A", 0, 7000, original)],
        source_duration_ms=8000,
    )
    assert result["eligible"] is True
    assert len(result["cues"]) >= 2
    reconstructed = " ".join(item["resolved_text"].replace("\n", " ") for item in result["cues"])
    assert reconstructed == original
    assert any(action["code"] == "semantic_preserving_split" for action in result["actions"])


def test_bounded_repair_borrows_only_free_neighbor_time_for_reading_speed() -> None:
    result = repair_english_dialogue_cues(
        [
            cue("A", 1000, 2000, "123456789012345678901234567890"),
            cue("B", 4000, 5000, "Short line."),
        ],
        source_duration_ms=6000,
    )
    assert result["eligible"] is True
    first = result["cues"][0]
    assert first["end_ms"] <= result["cues"][1]["start_ms"]
    assert first["end_ms"] - first["start_ms"] >= 1500
    assert any(action["code"] == "safe_timing_extension" for action in result["actions"])


def test_bounded_repair_rebalances_near_contiguous_target_timing_without_rewriting_text() -> None:
    items = [
        cue("A", 0, 1070, "I'll hurt and stop him."),
        cue("B", 1080, 2830, "You can be as handsome as me."),
    ]
    originals = [item["render_text"] for item in items]
    result = repair_english_dialogue_cues(items, source_duration_ms=3000)
    assert result["eligible"] is True
    assert [item["resolved_text"].replace("\n", " ") for item in result["cues"]] == originals
    assert result["cues"][0]["end_ms"] == result["cues"][1]["start_ms"]
    assert abs(result["cues"][0]["end_ms"] - 1070) <= 400
    assert any(action["code"] == "contiguous_timing_rebalance" for action in result["actions"])


def test_bounded_repair_preserves_material_pause_and_bounds_kokoro_display_padding() -> None:
    with_pause = repair_english_dialogue_cues(
        [
            cue("A", 0, 1000, "123456789012345678901"),
            cue("B", 1500, 3000, "A readable neighbour."),
        ],
        source_duration_ms=3200,
    )
    assert not any(action["code"] == "contiguous_timing_rebalance" for action in with_pause["actions"])

    first = cue("A", 0, 1000, "123456789012345678901")
    second = cue("B", 1010, 3000, "A readable neighbour.")
    first["timing_provenance"] = "kokoro_token_alignment"
    second["timing_provenance"] = "kokoro_token_alignment"
    aligned = repair_english_dialogue_cues([first, second], source_duration_ms=3200)
    adjustments = [action for action in aligned["actions"] if action["code"] == "narration_display_timing_rebalance"]
    assert aligned["eligible"] is True
    assert adjustments
    assert abs(adjustments[0]["boundary_shift_from_left_ms"]) <= 120
    assert abs(adjustments[0]["boundary_shift_from_right_ms"]) <= 120
    assert all(item.get("display_timing_adjusted_from_alignment") for item in aligned["cues"])


def test_bounded_repair_does_not_split_distinct_spoken_projection() -> None:
    item = cue("A", 0, 2500, "This target subtitle is deliberately far too long to fit inside two normal subtitle lines without splitting it into parts.")
    item["spoken_text"] = "A deliberately different narration projection that must not be rewritten by display repair."
    result = repair_english_dialogue_cues([item], source_duration_ms=3000)
    assert result["eligible"] is False
    assert len(result["cues"]) == 1


def test_quality_evidence_is_persisted_without_premature_enforcement(tmp_path) -> None:
    resolved = {"cues": [cue("A", 0, 500, "This subtitle is intentionally too dense for its timing.")]}
    evidence = _write_localization_quality_evidence(
        tmp_path,
        resolved,
        {"target_language": "English", "subtitle_quality_profile": "english_v2"},
    )
    assert evidence["status"] == "FAIL"
    assert evidence["enforced"] is False
    persisted = json.loads((tmp_path / "subtitles" / "localization_quality_evidence.json").read_text(encoding="utf-8"))
    assert persisted["violation_count"] >= 1
    assert _load_localization_quality_evidence(tmp_path) == persisted


def test_quality_evidence_is_not_applicable_for_other_target_profiles(tmp_path) -> None:
    evidence = _write_localization_quality_evidence(
        tmp_path,
        {"cues": []},
        {"target_language": "Vietnamese", "subtitle_quality_profile": "vi_v1"},
    )
    assert evidence["status"] == "NOT_APPLICABLE"
    assert evidence["eligible"] is True
