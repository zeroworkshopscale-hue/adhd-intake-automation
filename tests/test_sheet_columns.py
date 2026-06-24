"""Copy-sheet column behaviour: NA-for-blank + the new computed columns."""

from __future__ import annotations

from adhd_intake.models import Demographics, ProcessingRecord, QuestionnaireType
from adhd_intake.sheets.local_sheet import LocalSheetWriter

_COLUMNS = (
    ("Program", "program_status"),
    ("Pronoun", "pronoun"),
    ("Alcohol", "form:substance_alcohol"),
    ("Cannabis", "form:substance_cannabis"),
    ("Other", "form:substance_other"),
    ("Spacer", "blank"),
    ("Hear1", "form:referral_1"),
    ("Hear2", "form:referral_2"),
    ("Hear3", "form:referral_3"),
    ("Initiatives", "form:future_initiatives"),
    ("Research", "form:future_research"),
)


def _row(record, placeholder="NA"):
    w = LocalSheetWriter(__import__("pathlib").Path("x.csv"), columns=_COLUMNS,
                         blank_placeholder=placeholder)
    return dict(zip([h for h, _ in _COLUMNS], w._row(record)))


def test_filled_values_pass_through():
    rec = ProcessingRecord(
        demographics=Demographics(pronoun="He/His", program_status="Private"),
        answers={
            "substance_alcohol": "Alcohol", "substance_cannabis": "Cannabis",
            "substance_other": "Other substance",
            "referral_1": "Family member", "referral_2": "Friend", "referral_3": "",
            "future_initiatives": "Yes", "future_research": "No",
        },
    )
    row = _row(rec)
    assert row["Program"] == "Private"
    assert row["Pronoun"] == "He/His"
    assert (row["Alcohol"], row["Cannabis"], row["Other"]) == ("Alcohol", "Cannabis", "Other substance")
    assert (row["Hear1"], row["Hear2"]) == ("Family member", "Friend")
    assert (row["Initiatives"], row["Research"]) == ("Yes", "No")


def test_blank_data_cells_become_placeholder_but_spacer_and_referral_stay_empty():
    rec = ProcessingRecord(demographics=Demographics(), answers={})
    row = _row(rec, placeholder="NA")
    # Data columns that resolve empty -> NA.
    assert row["Program"] == "NA"
    assert row["Pronoun"] == "NA"
    assert row["Alcohol"] == "NA" and row["Cannabis"] == "NA" and row["Other"] == "NA"
    # Spacer + how-did-you-hear option slots stay genuinely empty.
    assert row["Spacer"] == ""
    assert row["Hear1"] == "" and row["Hear2"] == "" and row["Hear3"] == ""


def test_no_placeholder_when_unset_keeps_blanks():
    rec = ProcessingRecord(demographics=Demographics(), answers={})
    row = _row(rec, placeholder="")
    assert row["Program"] == "" and row["Alcohol"] == ""


def test_partial_referral_fills_used_slots_only():
    rec = ProcessingRecord(
        demographics=Demographics(),
        answers={"referral_1": "Google", "referral_2": "", "referral_3": ""},
    )
    row = _row(rec)
    assert row["Hear1"] == "Google"
    assert row["Hear2"] == "" and row["Hear3"] == ""   # not NA
