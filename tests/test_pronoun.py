"""Pronoun extraction from AcroForm option fields.

The form encodes pronoun as three separate fields (She/Her, He/His, They/Them);
the marked one carries a value. Regression: the text layer lists every option
after the "Pronoun" label, so a text scrape always grabbed the first option
(She/Her) regardless of the actual mark.
"""

from __future__ import annotations

from adhd_intake.extraction.extractor import Extractor


def test_marked_hehis_wins():
    fv = {"SheHer": "", "HeHis": "x", "TheyThem": ""}
    assert Extractor._pronoun_from_form_fields(fv) == "He/His"


def test_marked_sheher():
    fv = {"SheHer": "X", "HeHis": "", "TheyThem": ""}
    assert Extractor._pronoun_from_form_fields(fv) == "She/Her"


def test_marked_theythem():
    fv = {"SheHer": "", "HeHis": "", "TheyThem": "✓"}
    assert Extractor._pronoun_from_form_fields(fv) == "They/Them"


def test_compound_field_names_are_ignored():
    """Wide fields whose names merely START with the option words (e.g. the
    birthdate field) must not be read as a pronoun mark."""
    fv = {
        "SheHer": "",
        "HeHis": "x",
        "TheyThem": "",
        "SheHer HeHis TheyThemBirthdate yyyymmdd": "1995/02/28",
        "SheHer HeHis TheyThemCurrent Age": "31",
    }
    assert Extractor._pronoun_from_form_fields(fv) == "He/His"


def test_none_marked_returns_none():
    fv = {"SheHer": "", "HeHis": "", "TheyThem": "", "Legal First Name": "James"}
    assert Extractor._pronoun_from_form_fields(fv) is None


def test_off_values_are_not_marks():
    fv = {"SheHer": "Off", "HeHis": "off", "TheyThem": "0"}
    assert Extractor._pronoun_from_form_fields(fv) is None


def test_priority_when_multiple_marked():
    fv = {"SheHer": "x", "HeHis": "x", "TheyThem": "x"}
    assert Extractor._pronoun_from_form_fields(fv) == "He/His"
