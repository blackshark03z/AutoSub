from app.services.visual_quality import evaluate_visual_localization


def _track(track_id, text, box, start=1000, end=2000, action="localize"):
    return {
        "track_id": track_id,
        "canonical_text": text,
        "start_ms": start,
        "end_ms": end,
        "classification": {"action": action},
        "observations": [{"bbox": list(box)}],
    }


def test_visual_qc_passes_when_source_script_is_absent_after_replacement() -> None:
    localized = {"tracks": [_track("source", "管理员", (100, 100, 250, 140))]}
    final = {"tracks": [_track("target", "Administrator", (100, 100, 250, 140), action="preserve")]}
    plan = {"replacements": [{"track_id": "source"}], "escalations": []}
    result = evaluate_visual_localization(localized, final, replacement_plan=plan)
    assert result["status"] == "PASS"
    assert result["residual_source_track_count"] == 0


def test_visual_qc_fails_on_spatiotemporally_matching_source_residual() -> None:
    localized = {"tracks": [_track("source", "管理员", (100, 100, 250, 140))]}
    final = {"tracks": [_track("residual", "管理员", (105, 100, 255, 140))]}
    plan = {"replacements": [{"track_id": "source"}], "escalations": []}
    result = evaluate_visual_localization(localized, final, replacement_plan=plan)
    assert result["status"] == "FAIL"
    assert result["failures"][0]["code"] == "SOURCE_LANGUAGE_RESIDUAL"


def test_unresolved_tier_escalation_blocks_visual_validity() -> None:
    localized = {"tracks": [_track("source", "管理员", (100, 100, 250, 140))]}
    final = {"tracks": []}
    plan = {"replacements": [], "escalations": [{"track_id": "source", "reason": "unstable_geometry"}]}
    result = evaluate_visual_localization(localized, final, replacement_plan=plan)
    assert result["eligible"] is False
    assert any(item["code"] == "VISUAL_REPLACEMENT_UNRESOLVED" for item in result["failures"])


def test_unrelated_source_text_elsewhere_does_not_false_match_expected_track() -> None:
    localized = {"tracks": [_track("source", "管理员", (100, 100, 250, 140))]}
    final = {"tracks": [_track("other", "强化身体", (800, 400, 1000, 450))]}
    plan = {"replacements": [{"track_id": "source"}], "escalations": []}
    result = evaluate_visual_localization(localized, final, replacement_plan=plan)
    assert result["status"] == "PASS"
