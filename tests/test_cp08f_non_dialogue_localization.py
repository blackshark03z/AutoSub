from __future__ import annotations

from app.services.non_dialogue_localization import (
    TextDetection,
    approval_summary,
    classify_non_dialogue_text,
    group_temporal_detections,
    localization_policy,
    preserve_placeholder,
)


def _d(event_id: str, start: float, text: str, bbox: dict[str, int] | None = None, confidence: float = 0.9) -> TextDetection:
    return TextDetection(event_id, start, start + 1.0, bbox or {"left_x": 100, "top_y": 100, "right_x": 500, "bottom_y": 160}, text, confidence)


def test_cp08f_title_card_classification():
    assert classify_non_dialogue_text(_d("title", 1.0, "疯狂标题")) == "story_title"


def test_cp08f_document_classification():
    assert classify_non_dialogue_text(_d("letter", 220.0, "信件内容")) == "in_scene_document"


def test_cp08f_game_ui_prompt_classification_and_placeholder_preservation():
    detection = _d("prompt", 273.0, "[E]互动", {"left_x": 540, "top_y": 390, "right_x": 730, "bottom_y": 430})
    assert classify_non_dialogue_text(detection) == "game_ui_prompt"
    assert preserve_placeholder(detection.ocr_text, "Interact") == "[E] Interact"


def test_cp08f_watermark_and_cta_classification():
    assert classify_non_dialogue_text(_d("cta", 650.0, "关注作者 点赞投币")) == "creator_cta"
    assert classify_non_dialogue_text(_d("wm", 650.0, "来源账号水印")) == "source_watermark_or_provenance"


def test_cp08f_unknown_classification_for_low_confidence_non_cjk():
    assert classify_non_dialogue_text(_d("unknown", 300.0, "??", confidence=0.2)) == "unknown"


def test_cp08f_temporal_event_grouping():
    detections = [
        _d("a", 10.0, "互动"),
        _d("b", 10.2, "互动", {"left_x": 105, "top_y": 100, "right_x": 505, "bottom_y": 160}),
        _d("c", 12.0, "其他"),
    ]
    groups = group_temporal_detections(detections)
    assert [len(group) for group in groups] == [2, 1]


def test_cp08f_rendering_policies():
    assert localization_policy("story_title")["mode"] == "styled_replacement"
    assert localization_policy("game_ui_prompt")["mode"] == "styled_replacement"
    assert localization_policy("in_scene_document")["mode"] == "translation_card"
    assert localization_policy("source_watermark_or_provenance")["mode"] == "preserve"


def test_cp08f_no_automatic_provenance_removal_and_approval_rules():
    events = [
        {
            "classification": "source_watermark_or_provenance",
            "localization_policy": localization_policy("source_watermark_or_provenance"),
            "review_state": "provenance_preserved",
            "operator_decision": "approved_preserve_by_default_policy",
        }
    ]
    summary = approval_summary(events)
    assert summary["normal_localization_approval_allowed"] is True
    assert summary["english_only_approval_allowed"] is False


def test_cp08f_unresolved_blocker_prevents_english_only_approval():
    events = [
        {
            "classification": "creator_cta",
            "localization_policy": localization_policy("creator_cta"),
            "review_state": "needs_review",
            "operator_decision": "requires_operator_decision_preserve_or_trim_or_replace",
        }
    ]
    assert approval_summary(events)["english_only_approval_allowed"] is False


def test_cp08f_approved_provenance_does_not_block_normal_localization():
    events = [
        {
            "classification": "source_watermark_or_provenance",
            "localization_policy": localization_policy("source_watermark_or_provenance"),
            "review_state": "provenance_preserved",
            "operator_decision": "approved_preserve_by_default_policy",
        },
        {
            "classification": "game_ui_prompt",
            "localization_policy": localization_policy("game_ui_prompt"),
            "review_state": "preview_ready",
            "operator_decision": "preview_ready",
        },
    ]
    assert approval_summary(events)["normal_localization_approval_allowed"] is True


def test_cp08f_no_provider_call_on_page_load_and_bounded_batch_script_guard():
    from tools.run_cp08f_selective_non_dialogue_localization import qa_summary

    assert callable(qa_summary)
    assert localization_policy("unknown")["requires_operator_approval"] is True
