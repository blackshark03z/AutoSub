from __future__ import annotations

from pathlib import Path

import pytest

from app.services.hy_mt2_offline_adapter import (
    CONTEXT_GLOSSARY_VERSIONS,
    CONTEXT_PROMPT_VERSION,
    GLOSSARY_VERSIONS,
    PROMPT_VERSION,
    SUPPORTED_LOCALES,
    HyMt2OfflineLocalizer,
    UnsupportedLocaleError,
    apply_rule_postedit,
    build_contextual_translation_prompt,
    build_grounded_translation_prompt,
    build_repair_translation_prompt,
    build_translation_prompt,
    detect_translation_defects,
    glossary_adherence,
    resolve_target_language,
    sanitize_translation,
    sha256_file,
    shares_neighbor_translation,
    significant_translation_divergence,
)

CUE = {"cue_id": "cue_002", "start_ms": 9600, "end_ms": 11167, "zh_text": "我大限已至"}


def test_supported_locales_map_to_full_language_names():
    assert resolve_target_language("en-US") == "English"
    assert resolve_target_language("vi-VN") == "Vietnamese"
    assert set(SUPPORTED_LOCALES) == {"en-US", "vi-VN"}


def test_unsupported_locale_fails_closed():
    with pytest.raises(UnsupportedLocaleError):
        resolve_target_language("fr-FR")


def test_prompt_uses_official_template_and_full_language_name():
    prompt = build_translation_prompt("我大限已至", "en-US")
    assert "Translate the following text into English." in prompt
    assert "only output the translated result" in prompt
    assert prompt.endswith("我大限已至")

    vi_prompt = build_translation_prompt("我大限已至", "vi-VN")
    assert "Translate the following text into Vietnamese." in vi_prompt


def test_cue_id_and_timing_preserved_with_injected_runtime():
    calls: list[str] = []

    def fake_infer(prompt: str) -> str:
        calls.append(prompt)
        return "My time has come"

    localizer = HyMt2OfflineLocalizer(infer_fn=fake_infer)
    out = localizer.translate_cue(CUE, "en-US")

    assert out.cue_id == "cue_002"
    assert out.start_ms == 9600
    assert out.end_ms == 11167
    assert out.source_text == "我大限已至"
    assert out.status == "TRANSLATED"
    assert out.translation == "My time has come"
    assert out.confidence == 0.95
    assert out.target_locale == "en-US"
    assert len(calls) == 1
    assert "我大限已至" in calls[0]


def test_runtime_unavailable_fails_closed_without_inference():
    localizer = HyMt2OfflineLocalizer(model_path=Path("missing_model.gguf"))
    assert localizer.is_runtime_available is False
    out = localizer.translate_cue(CUE, "vi-VN")
    assert out.status == "RUNTIME_UNAVAILABLE"
    assert out.translation is None
    assert "C4 remains BLOCKED" in out.error_message


def test_unsupported_locale_returns_status_without_inference():
    called = False

    def fake_infer(prompt: str) -> str:  # pragma: no cover - must never run
        nonlocal called
        called = True
        return "nope"

    localizer = HyMt2OfflineLocalizer(infer_fn=fake_infer)
    out = localizer.translate_cue(CUE, "de-DE")
    assert out.status == "UNSUPPORTED_LOCALE"
    assert out.translation is None
    assert called is False


def test_empty_source_returns_empty_translation():
    localizer = HyMt2OfflineLocalizer(infer_fn=lambda _p: "should not run")
    out = localizer.translate_cue({"cue_id": "c", "start_ms": 0, "end_ms": 5, "zh_text": "   "}, "en-US")
    assert out.status == "EMPTY_SOURCE"
    assert out.translation == ""


def test_inference_error_is_fail_closed():
    def boom(_prompt: str) -> str:
        raise ConnectionError("server down")

    localizer = HyMt2OfflineLocalizer(infer_fn=boom)
    out = localizer.translate_cue(CUE, "en-US")
    assert out.status == "INFERENCE_ERROR"
    assert out.translation is None
    assert "ConnectionError" in out.error_message


def test_detects_cjk_leakage():
    defective, reason = detect_translation_defects("我大限已至", "My time 已至")
    assert defective is True
    assert "CJK_LEAKAGE" in reason


def test_detects_repetition_loop():
    defective, reason = detect_translation_defects(
        "我走后你一定要守护好宗门",
        "protect the sect sect the sect sect the sect sect",
    )
    assert defective is True
    assert "REPETITION_LOOP" in reason


def test_detects_length_explosion():
    long_non_repeating = " ".join(f"token{i}" for i in range(120))
    defective, reason = detect_translation_defects("徒儿", long_non_repeating)
    assert defective is True
    assert "LENGTH_EXPLOSION" in reason


def test_short_cjk_cue_not_flagged_as_length_explosion():
    defective, reason = detect_translation_defects("我大限已至", "My time has come")
    assert defective is False
    assert reason is None


def test_quality_rejection_status_and_hashes():
    localizer = HyMt2OfflineLocalizer(infer_fn=lambda _p: "Đại hạn của ta 已至")
    out = localizer.translate_cue(CUE, "vi-VN")
    assert out.status.startswith("QUALITY_REJECTED:")
    assert out.translation is None
    assert out.prompt_sha256 and out.translation_sha256


def test_glossary_adherence_reports_match_and_unmatched():
    report = glossary_adherence("en-US", "拿着它壮壮大我万法宗", "Hold it—strengthen Wanfa Sect")
    assert report["万法宗"] == "MATCH"
    report2 = glossary_adherence("en-US", "壮大我万法宗", "strengthen Wana Fa Sect")
    assert report2["万法宗"] == "UNMATCHED"


def test_batch_preserves_order_and_ids(tmp_path):
    cues = [
        {"cue_id": "cue_001", "start_ms": 8533, "end_ms": 9033, "zh_text": "徒儿"},
        {"cue_id": "cue_002", "start_ms": 9600, "end_ms": 11167, "zh_text": "我大限已至"},
        {"cue_id": "cue_003", "start_ms": 12267, "end_ms": 15433, "zh_text": "我走后你一定要守护好宗门"},
    ]
    localizer = HyMt2OfflineLocalizer(infer_fn=lambda _p: "translated line")
    out = localizer.translate_cues(cues, "en-US")
    assert [c.cue_id for c in out] == ["cue_001", "cue_002", "cue_003"]
    assert [c.start_ms for c in out] == [8533, 9600, 12267]
    assert all(c.status == "TRANSLATED" for c in out)
    assert out[0].translation == "translated line"


def test_sanitize_translation_strips_quotes_and_whitespace():
    assert sanitize_translation('  "Hello there"  ') == "Hello there"
    assert sanitize_translation("'Xin chào'") == "Xin chào"
    assert sanitize_translation("a\n\n  b   c") == "a b c"


def test_provenance_includes_model_sha_and_prompt_version(tmp_path):
    model = tmp_path / "fake.gguf"
    model.write_bytes(b"GGUF-test-bytes")
    localizer = HyMt2OfflineLocalizer(
        model_path=model, infer_fn=lambda _p: "ok", runtime_version="b11669-620d2225c"
    )
    prov = localizer.provenance()
    assert prov.runtime == "llama.cpp"
    assert prov.runtime_version == "b11669-620d2225c"
    assert prov.model_sha256 == sha256_file(model)
    assert prov.prompt_version == PROMPT_VERSION


def test_glossary_versions_exist_per_locale():
    assert GLOSSARY_VERSIONS["en-US"] == "glossary-en-US-v1"
    assert GLOSSARY_VERSIONS["vi-VN"] == "glossary-vi-VN-v1"


def test_context_glossary_versions_exist_per_locale():
    assert CONTEXT_GLOSSARY_VERSIONS["en-US"] == "glossary-en-US-v6-context"
    assert CONTEXT_GLOSSARY_VERSIONS["vi-VN"] == "glossary-vi-VN-v2-context"
    assert "grounded" in CONTEXT_PROMPT_VERSION


def test_contextual_prompt_injects_only_relevant_terms_and_context():
    prompt = build_contextual_translation_prompt(
        "壮大我万法宗", "en-US", context_before="我走后你一定要守护好宗门"
    )
    assert "into English." in prompt
    assert "万法宗 = Wanfa Sect" in prompt
    assert "宗门 = sect" in prompt
    assert "Previous line: 我走后你一定要守护好宗门" in prompt
    assert "Line to translate:\n壮大我万法宗" in prompt
    # 房术大全 is unrelated to this cue and must not be injected.
    assert "房术大全" not in prompt


def test_contextual_prompt_without_context_has_no_story_block():
    prompt = build_contextual_translation_prompt("??", "vi-VN")
    assert "into Vietnamese." in prompt
    assert "Story context" not in prompt


def test_repair_prompt_forbids_han_and_grounds_on_source_line():
    prompt = build_repair_translation_prompt("看人只用重瞳术", "en-US")
    assert "into English." in prompt
    assert "Chinese characters" in prompt
    assert "看人只用重瞳术" in prompt  # grounded: same source line, no invented meaning


def test_rule_postedit_normalizes_en_terms_only_when_marker_present():
    text, applied = apply_rule_postedit(
        "壮大我万法宗", "en-US", "Hold it—strengthen Wana Fa Sect"
    )
    assert text == "Hold it—strengthen Wanfa Sect"
    assert applied and applied[0].startswith("万法宗:")

    untouched, no_rules = apply_rule_postedit("你好", "en-US", "Wana Fa Sect appears here")
    assert untouched == "Wana Fa Sect appears here"
    assert no_rules == []


def test_rule_postedit_fixes_vi_disciple_and_bedchamber():
    text, applied = apply_rule_postedit("徒儿", "vi-VN", "Tuếr")
    assert text == "đồ nhi"
    assert applied

    text2, _ = apply_rule_postedit(
        "怎么拿一本房术大全给我", "vi-VN", "sách về kiến trúc nhà ở"
    )
    assert "Phòng Thuật Đại Toàn" in text2


def test_rule_postedit_strips_trailing_explanation_for_ga():
    text, applied = apply_rule_postedit(
        "你真嘎了", "en-US", "You really died (in a colloquial sense, like 'croaked')"
    )
    assert text == "You really died"
    assert applied


def test_grounded_prompt_has_no_story_context_and_is_source_gated():
    prompt = build_grounded_translation_prompt("我是暗影楼副楼主", "en-US")
    assert "into English." in prompt
    assert "暗影楼 = Shadow Tower" in prompt
    assert "副楼主 = deputy tower master" in prompt
    assert "Previous line" not in prompt and "Next line" not in prompt
    assert "Line to translate:\n我是暗影楼副楼主" in prompt


def test_proper_noun_rules_only_fire_for_matching_source():
    text, applied = apply_rule_postedit(
        "是一点面子都不给我擎天圣地吗", "en-US",
        "Is this Tiantian Sacred Land not giving me a single speck of face?",
    )
    assert text == "Is this Qingtian Sacred Land not giving me a single speck of face?"
    assert any(rule.startswith("擎天圣地:") for rule in applied)

    untouched, no_rules = apply_rule_postedit("你好", "en-US", "Tiantian Sacred Land")
    assert untouched == "Tiantian Sacred Land"
    assert no_rules == []


def test_proper_noun_rules_normalize_shadow_tower_and_titles():
    text, applied = apply_rule_postedit(
        "我是暗影楼副楼主", "en-US",
        "I am the deputy leader of Shadow Building. The original poster sent me.",
    )
    assert "deputy tower master of the Shadow Tower" in text
    assert "tower master sent me" in text
    assert applied


def test_face_idiom_rules_repair_possessive_and_pride_drift():
    text, applied = apply_rule_postedit(
        "我为什么要给你们面子", "en-US", "Why should I give you your face?"
    )
    assert text == "Why should I give you face?"
    assert any(rule.startswith("面子:") for rule in applied)

    text2, _ = apply_rule_postedit(
        "你擎天圣地的面子很厉害吗", "en-US",
        "Do you really have such a strong face in the Qingtian Sacred Land?",
    )
    assert "such a strong reputation" in text2


def test_neighbor_bleed_is_detected_only_when_sources_differ():
    assert shares_neighbor_translation(
        "The system uses five spiritual veins. Plant these 50 peach tree seeds.",
        "The system uses five spiritual veins.",
        "系统", "系统使用五条灵脉",
    )
    assert not shares_neighbor_translation(
        "I will make the Wanfa Sect the strongest sect in the entire continent.",
        "Become the strongest sect in the entire continent.",
        "我会让万法宗成为整个大陆最强的宗门",
        "成为整个大陆最强的宗门",
    )


def test_ungrounded_extension_detects_imported_next_line():
    assert significant_translation_divergence(
        "Use 4 white character summon cards to plant these 50 peach seeds in the back mountain.",
        "Plant these 50 peach tree seeds in the back mountain.",
    )
    assert not significant_translation_divergence(
        "How many people are there in the Shadow Tower currently?",
        "How many people are currently in the Shadow Tower?",
    )
