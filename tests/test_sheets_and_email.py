"""Tests for the local copy-sheet, sheet routing, and consent email."""

from __future__ import annotations

import csv
from pathlib import Path

from adhd_intake.config import SheetsConfig
from adhd_intake.dashboard.consent_email import build_consent_email
from adhd_intake.models import (
    Demographics,
    ProcessingRecord,
    ProcessingStatus,
    QuestionnaireType,
)
from adhd_intake.sheets import CompositeSheetWriter, LOCAL_SHEET_HEADERS, LocalSheetWriter


def _record() -> ProcessingRecord:
    return ProcessingRecord(
        source_filename="jane.pdf",
        questionnaire_type=QuestionnaireType.ADHD_WOMEN,
        status=ProcessingStatus.COMPLETED,
        demographics=Demographics(
            first_name="Jane", last_name="Doe", email="jane@example.com", dob="1990-05-01"
        ),
        demographic_no="12345",
        signature_present=True,
        oscar_document_id="12345:ADHD Assessment Tool",
    )


def test_local_sheet_writes_header_and_demographics(tmp_path: Path):
    path = tmp_path / "copy.csv"
    writer = LocalSheetWriter(path)

    row_no = writer.append_record(_record())
    assert row_no == 1

    rows = list(csv.reader(path.open(encoding="utf-8-sig")))
    assert rows[0] == LOCAL_SHEET_HEADERS
    data = rows[1]
    # Local sheet DOES include demographics (it's a manual-copy file).
    assert "Doe" in data
    assert "Jane" in data
    assert "12345" in data
    assert "jane@example.com" in data

    # Second append does not repeat the header.
    writer.append_record(_record())
    rows = list(csv.reader(path.open(encoding="utf-8-sig")))
    assert len(rows) == 3  # header + 2 data rows


def test_composite_local_only_does_not_touch_google(tmp_path: Path):
    cfg = SheetsConfig(mode="local", local_path=tmp_path / "copy.csv")
    writer = CompositeSheetWriter(cfg)

    row = writer.append_record(_record())
    assert row == 1
    assert (tmp_path / "copy.csv").exists()
    assert writer._google is None  # never instantiated the Google client


def test_consent_email_uses_first_name_and_template():
    body = build_consent_email("Jane")
    assert body.startswith("Dear Jane,")
    assert "did not come through on the ADHD Assessment Tool" in body
    assert "Accept this email as my signed consent" in body
    assert body.strip().endswith("Adult ADHD Centre Manager")


def test_consent_email_defaults_when_no_name():
    body = build_consent_email(None)
    assert body.startswith("Dear Patient,")


def test_configurable_columns_align_and_compute_age(tmp_path):
    columns = [
        ("MRP/Private Status", "blank"),
        ("Date", "date"),
        ("Demographic number", "demographic_no"),
        ("Email Address", "email"),
        ("Age", "age"),
        ("Province", "blank"),
    ]
    writer = LocalSheetWriter(tmp_path / "master.csv", columns=columns)
    rec = _record()
    rec.demographics.dob = "2000-01-01"
    writer.append_record(rec)

    rows = list(csv.reader((tmp_path / "master.csv").open(encoding="utf-8-sig")))
    assert rows[0] == [h for h, _ in columns]
    data = rows[1]
    assert data[0] == ""                      # blank
    assert data[2] == "12345"                 # demographic_no
    assert data[3] == "jane@example.com"      # email
    assert data[4].isdigit() and int(data[4]) >= 25  # computed age
    assert data[5] == ""                      # blank
    # No patient name anywhere in the row.
    assert "Jane" not in data and "Doe" not in data


def test_unknown_column_field_raises(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        LocalSheetWriter(tmp_path / "x.csv", columns=[("X", "not_a_real_field")])


def test_form_column_alternatives_first_nonempty(tmp_path):
    columns = [
        ("Employment", "form:Current Occupation|PartTime FullTimeCurrent Occupation"),
        ("Consent", "form:future_research"),
    ]
    writer = LocalSheetWriter(tmp_path / "alt.csv", columns=columns)
    rec = _record()
    rec.answers = {"PartTime FullTimeCurrent Occupation": "Nurse", "future_research": "YES"}
    writer.append_record(rec)
    rows = list(csv.reader((tmp_path / "alt.csv").open(encoding="utf-8-sig")))
    assert rows[1][0] == "Nurse"   # fell back to the second key
    assert rows[1][1] == "YES"


def test_form_column_pulls_questionnaire_answer(tmp_path):
    columns = [
        ("Demographic number", "demographic_no"),
        ("List Employment", "form:PartTime FullTimeCurrent Occupation"),
        ("Unknown Field", "form:Nonexistent"),
    ]
    writer = LocalSheetWriter(tmp_path / "m.csv", columns=columns)
    rec = _record()
    rec.answers = {"PartTime FullTimeCurrent Occupation": "Full-time teacher"}
    writer.append_record(rec)

    rows = list(csv.reader((tmp_path / "m.csv").open(encoding="utf-8-sig")))
    assert rows[1][1] == "Full-time teacher"   # pulled from answers
    assert rows[1][2] == ""                      # missing answer -> blank
