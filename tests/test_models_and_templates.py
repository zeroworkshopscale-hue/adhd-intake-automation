"""Unit tests for pure-logic helpers (no heavy deps)."""

from __future__ import annotations

import pytest

from adhd_intake.extraction.templates import identify_type
from adhd_intake.models import Demographics, ProcessingStatus, QuestionnaireType


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("DOB: 1990-05-01", "1990-05-01"),
        ("Born 1985/12/3", "1985-12-03"),
        ("2000-1-9 something", "2000-01-09"),
        ("no date here", None),
        (None, None),
    ],
)
def test_dob_normalisation(raw, expected):
    assert Demographics.normalise_dob(raw) == expected


def test_minimum_identifiers():
    assert Demographics(first_name="A", last_name="B").has_minimum_identifiers()
    assert Demographics(email="a@b.com").has_minimum_identifiers()
    assert Demographics(dob="1990-01-01").has_minimum_identifiers()
    assert not Demographics(first_name="OnlyFirst").has_minimum_identifiers()
    assert not Demographics().has_minimum_identifiers()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Welcome to the Adult ADHD Centre questionnaire", QuestionnaireType.ADULT_ADHD),
        ("ADHD Centre for Women — intake", QuestionnaireType.ADHD_WOMEN),
        ("Some unrelated document", QuestionnaireType.UNKNOWN),
    ],
)
def test_identify_questionnaire_type(text, expected):
    assert identify_type(text) is expected


def test_questionnaire_descriptions_distinct():
    desc_adult = QuestionnaireType.ADULT_ADHD.document_description
    desc_women = QuestionnaireType.ADHD_WOMEN.document_description
    assert desc_adult != desc_women
    assert "Adult" in desc_adult
    assert "Women" in desc_women


def test_terminal_states():
    assert ProcessingStatus.COMPLETED.is_terminal
    assert ProcessingStatus.REJECTED_NO_SIGNATURE.is_terminal
    assert ProcessingStatus.PATIENT_NOT_FOUND.is_terminal
    assert not ProcessingStatus.EXTRACTING.is_terminal
    assert ProcessingStatus.COMPLETED.is_success
    assert not ProcessingStatus.ERROR.is_success
