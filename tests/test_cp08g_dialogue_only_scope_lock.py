from __future__ import annotations

from pathlib import Path

from app.core.hashing import sha256_file
from app.services.non_dialogue_localization import (
    PRODUCTION_PLACEHOLDER_BLOCKERS,
    dialogue_only_scope_config,
    contains_production_placeholder,
    manual_non_dialogue_review_policy,
)


def test_cp08g_dialogue_only_scope_disables_non_dialogue_automation():
    policy = dialogue_only_scope_config()
    assert policy["scope"] == "dialogue_subtitles_only"
    assert policy["auto_dialogue_subtitle_cleanup"] is True
    assert policy["auto_english_dialogue_render"] is True
    assert policy["auto_non_dialogue_text_localization"] is False
    assert policy["manual_non_dialogue_text_review"] == "optional"
    assert policy["provenance_preservation"] is True
    assert policy["creator_cta_preservation"] is True
    assert policy["watermark_preservation"] is True


def test_cp08g_placeholder_blockers_are_caught_before_render():
    for marker in PRODUCTION_PLACEHOLDER_BLOCKERS:
        assert contains_production_placeholder(marker.upper())
    assert contains_production_placeholder("Opening Title")
    assert contains_production_placeholder("summary pending operator review")
    assert contains_production_placeholder("operator review required")
    assert contains_production_placeholder("TODO")
    assert contains_production_placeholder("tbd")


def test_cp08g_manual_non_dialogue_review_stays_optional_or_preserve():
    assert manual_non_dialogue_review_policy("story_title")["mode"] == "manual_optional"
    assert manual_non_dialogue_review_policy("in_scene_document")["mode"] == "manual_optional"
    assert manual_non_dialogue_review_policy("game_ui_prompt")["mode"] == "manual_optional"
    assert manual_non_dialogue_review_policy("creator_cta")["mode"] == "preserve"
    assert manual_non_dialogue_review_policy("creator_cta")["review_mode"] == "manual_required"
    assert manual_non_dialogue_review_policy("source_watermark_or_provenance")["mode"] == "preserve"
    assert manual_non_dialogue_review_policy("source_watermark_or_provenance")["requires_operator_approval"] is False


def test_cp08g_promoted_artifact_is_byte_identical():
    input_path = Path("data/projects/vertical_slice_cp07/renders/cp08e2_decoupled_suppression_english_plate_720p.mp4")
    output_path = Path("data/projects/vertical_slice_cp07/renders/cp08g_dialogue_subtitle_only_final_720p.mp4")
    assert input_path.exists()
    assert output_path.exists()
    assert input_path.read_bytes() == output_path.read_bytes()
    assert sha256_file(input_path) == sha256_file(output_path)
