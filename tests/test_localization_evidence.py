from app.services.localization_evidence import (
    DIALOGUE_CAPTION,
    VISUAL_TEXT,
    classify_resolved_cue_role,
    resolved_dialogue_projection,
)


def test_lower_band_text_is_dialogue_candidate() -> None:
    cue = {"source_bbox": {"left_x": 400, "top_y": 860, "right_x": 1500, "bottom_y": 1010}}
    result = classify_resolved_cue_role(cue, frame_width=1920, frame_height=1080)
    assert result["role"] == DIALOGUE_CAPTION
    assert result["reason"] == "lower_caption_band_geometry"


def test_upper_ui_text_is_visual_not_dialogue() -> None:
    cue = {"source_bbox": {"left_x": 450, "top_y": 120, "right_x": 720, "bottom_y": 185}}
    result = classify_resolved_cue_role(cue, frame_width=1920, frame_height=1080)
    assert result["role"] == VISUAL_TEXT


def test_nonvisual_asr_cue_stays_dialogue_evidence() -> None:
    result = classify_resolved_cue_role({"resolved_text": "Hello"}, frame_width=1920, frame_height=1080)
    assert result["role"] == DIALOGUE_CAPTION


def test_projection_preserves_visual_candidates_without_putting_them_in_dialogue() -> None:
    resolved = {"subtitle_provenance": "source_caption_gemini_translation", "cues": [
        {"cue_id": "d", "start_ms": 0, "end_ms": 1000, "resolved_text": "Dialogue", "source_bbox": {"left_x": 300, "top_y": 850, "right_x": 1500, "bottom_y": 1000}},
        {"cue_id": "u", "start_ms": 100, "end_ms": 900, "resolved_text": "Menu", "source_bbox": {"left_x": 100, "top_y": 100, "right_x": 300, "bottom_y": 150}},
    ]}
    projected = resolved_dialogue_projection(resolved, frame_width=1920, frame_height=1080)
    assert [cue["cue_id"] for cue in projected["cues"]] == ["d"]
    assert [cue["cue_id"] for cue in projected["visual_text_candidates"]] == ["u"]
    assert projected["role_partition"] == {"input": 2, "dialogue": 1, "visual_text": 1}
