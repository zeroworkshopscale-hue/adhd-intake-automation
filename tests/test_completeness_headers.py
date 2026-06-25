"""Completeness: section headers / instructions are not answerable rows.

Regression for a flattened form where the ink path flagged the sub-instruction
"For a period lasting one week or more:" on page 9 as unanswered.
"""

from __future__ import annotations

from adhd_intake.validation.completeness import CompletenessValidator

_is_header = CompletenessValidator._is_section_header


def test_colon_subinstruction_is_header():
    assert _is_header("For a period lasting one week or more:")


def test_all_caps_section_header():
    assert _is_header("QUESTIONS RELATED TO MANIA")
    assert _is_header("HOW DID YOU HEAR ABOUT US?")


def test_put_an_x_instruction():
    assert _is_header("Put an X in the box that best describes your behaviour")


def test_real_questions_are_not_headers():
    assert not _is_header("I have difficulty falling asleep.")
    assert not _is_header("I binge eat.")
    assert not _is_header("For no explainable reason, I have had an inflated sense of self.")
