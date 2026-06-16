"""Text-layer (flattened form) demographic extraction tests."""

from __future__ import annotations

from adhd_intake.extraction import templates
from adhd_intake.extraction.extractor import Extractor
from adhd_intake.models import Demographics, QuestionnaireType

_TPL = templates.template_for(QuestionnaireType.ADULT_ADHD)


def _extract(text: str) -> Demographics:
    demo = Demographics()
    Extractor._fill_from_text(demo, text, _TPL)
    return demo


def test_filled_flattened_form_extracts_name_dob():
    # Label on one line, value on the next (typical flattened/printed form).
    text = (
        "IDENTIFYING INFORMATION\n"
        "Legal Last Name\nSmith\n"
        "Legal First Name\nJohn\n"
        "Preferred Name\nJack\n"
        "Birthdate (yyyy/mm/dd)\n1990-05-01\n"
        "Current Age\n34\n"
    )
    d = _extract(text)
    assert d.last_name == "Smith"
    assert d.first_name == "John"
    assert d.pref_name == "Jack"
    assert d.dob == "1990-05-01"


def test_blank_stacked_labels_extract_no_name():
    # Labels stacked with no values -> must NOT invent names from adjacent labels.
    text = (
        "Legal Last Name\n"
        "Legal First Name\n"
        "Preferred Name\n"
        "Preferred Pronoun\n"
        "She/Her\n"
        "Birthdate (yyyy/mm/dd)\n"
        "Current Age\n"
    )
    d = _extract(text)
    assert d.last_name is None
    assert d.first_name is None
    assert d.pref_name is None


def test_inline_label_value_still_works():
    text = "Legal Last Name: Doe\nLegal First Name: Jane\n"
    d = _extract(text)
    assert d.last_name == "Doe"
    assert d.first_name == "Jane"
