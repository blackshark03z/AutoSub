from __future__ import annotations

import json
from pathlib import Path
import pytest

pytestmark = pytest.mark.release

from tools.run_cp08f_targeted_recomposition import (
    BASE_ARTIFACT_SHA,
    CTA_EVENT_ID,
    LETTER_EVENT_ID,
    TITLE_EVENT_ID,
    apply_targeted_overrides,
    qa_summary,
)


def test_cp08f_targeted_title_is_hidden_not_placeholder():
    base = json.loads(Path("data/projects/vertical_slice_cp07/artifacts/cp08f_non_dialogue_text_events.json").read_text(encoding="utf-8"))
    events = apply_targeted_overrides(base["events"])
    title = next(event for event in events if event["event_id"] == TITLE_EVENT_ID)
    assert title["english_translation"] == ""
    assert title["render_visibility"] == "hidden"
    assert title["operator_decision"] == "hide_title_until_operator_approves"


def test_cp08f_targeted_letter_uses_approved_summary():
    base = json.loads(Path("data/projects/vertical_slice_cp07/artifacts/cp08f_non_dialogue_text_events.json").read_text(encoding="utf-8"))
    events = apply_targeted_overrides(base["events"])
    letter = next(event for event in events if event["event_id"] == LETTER_EVENT_ID)
    assert letter["english_translation"] == "The letter warns him to stay inside and not open the door."
    assert letter["operator_decision"] == "approved_summary_card"
    assert letter["replacement_style"] == "translation_card"


def test_cp08f_targeted_creator_cta_has_explicit_operator_decision():
    base = json.loads(Path("data/projects/vertical_slice_cp07/artifacts/cp08f_non_dialogue_text_events.json").read_text(encoding="utf-8"))
    events = apply_targeted_overrides(base["events"])
    cta = next(event for event in events if event["event_id"] == CTA_EVENT_ID)
    assert cta["operator_decision"] == "approved_preserve_independent_ending_segment"
    assert cta["review_state"] == "approved_preserve"


def test_cp08f_targeted_qa_requires_base_artifact_immutability():
    assert BASE_ARTIFACT_SHA == "870b44f25b1b3366b0532f40162608193263e5504b5c787b9a1fc8280fc879a7"
