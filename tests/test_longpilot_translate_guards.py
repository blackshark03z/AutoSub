"""Guards that keep an unresolved cue from ever being rendered as if translated.

A cue whose translation is empty, still contains Han characters, or merely echoes the
prompt scaffolding is defective: it must never be marked TRANSLATED, and the renderer
must give it a source-covering plate flagged NEEDS_REVIEW instead of leaking the
Chinese caption or fabricating English.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from tools import run_longpilot_translate as t  # noqa: E402


def test_empty_translation_is_defective():
    assert t.is_defective_translation("")
    assert t.is_defective_translation("   ")


def test_residual_han_is_defective():
    assert t.is_defective_translation("Use the 重瞳 technique")
    assert t.is_defective_translation("江霸护法")


def test_prompt_echo_without_han_is_defective():
    # Han-free scaffolding echo is still not a translation.
    echo = "Use only the specified terminology: - Double Pupils Line to translate: I judge"
    assert t.is_prompt_echo(echo)
    assert t.is_defective_translation(echo)
    assert t.is_defective_translation("Line to translate: hello")


def test_clean_translation_is_accepted():
    assert not t.is_defective_translation("Use only the heavy pupil technique to judge people")


def test_source_equivalent_fit_accepts_grounded_closures():
    expected = [
        ("System.", 54),
        ("Plant these 50 peach tree seeds in the back mountain.", 41),
        ("I am the deputy tower master of the Shadow Tower.", 42),
        ("How many people are currently in the Shadow Tower?", 40),
        ("Is this Qingtian Sacred Land not giving me any face at all?", 39),
    ]
    for text, size in expected:
        assert t.translation_fits(text, size), (text, size)


def test_verbose_translation_is_rejected_at_source_size():
    assert not t.translation_fits("word " * 80, 54)
