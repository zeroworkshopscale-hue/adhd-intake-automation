"""Business-rule gate tests for the intake pipeline.

Verifies the non-negotiable rules:
  * missing signature -> COMPLETED_NO_SIGNATURE (still uploads + writes Sheets)
  * patient not found -> stop, NO upload, NO Sheets
  * happy path -> upload + sheet + moved to processed/ -> COMPLETED
  * Sheets rows never contain patient names
"""

from __future__ import annotations

from pathlib import Path

from adhd_intake.models import PatientMatch, ProcessingStatus, QuestionnaireType
from adhd_intake.pipeline.processor import UPLOAD_DOCUMENT_DESCRIPTION
from adhd_intake.sheets.client import SheetsClient

from .conftest import FakeOscar, FakeSheets, build_processor, make_extraction


def test_unsigned_still_uploads_as_sig_missing(config, db, sample_pdf):
    """A missing signature must NOT block upload — the form is processed and
    staff follow up with the patient to collect consent via email."""
    oscar = FakeOscar(match=PatientMatch("123", "Doe", "Jane"))
    sheets = FakeSheets()
    processor, repo = build_processor(
        config, db, extraction=make_extraction(), signed=False, oscar=oscar, sheets=sheets
    )

    result = processor.process(sample_pdf)

    assert result.status is ProcessingStatus.COMPLETED_NO_SIGNATURE
    assert result.record.signature_present is False
    assert len(oscar.find_calls) == 1      # patient searched
    assert len(oscar.upload_calls) == 1    # document uploaded
    assert len(sheets.rows) == 1           # written to Sheets
    # File moved into processed/ (not rejected/).
    assert list(config.folders.processed.glob("*.pdf"))
    assert not list(config.folders.rejected.glob("*.pdf"))


def test_sig_missing_appears_in_dashboard_query(config, db, sample_pdf):
    """list_completed() must include COMPLETED_NO_SIGNATURE records so the
    dashboard shows them without needing a special query."""
    oscar = FakeOscar(match=PatientMatch("123", "Doe", "Jane"))
    sheets = FakeSheets()
    processor, repo = build_processor(
        config, db, extraction=make_extraction(), signed=False, oscar=oscar, sheets=sheets
    )
    processor.process(sample_pdf)

    completed = repo.list_completed()
    assert len(completed) == 1
    assert completed[0].status is ProcessingStatus.COMPLETED_NO_SIGNATURE


def test_patient_not_found_stops_without_upload(config, db, sample_pdf):
    oscar = FakeOscar(match=None)           # search will raise PatientNotFound
    sheets = FakeSheets()
    processor, repo = build_processor(
        config, db, extraction=make_extraction(), signed=True, oscar=oscar, sheets=sheets
    )

    result = processor.process(sample_pdf)

    assert result.status is ProcessingStatus.PATIENT_NOT_FOUND
    assert oscar.upload_calls == []
    assert sheets.rows == []


def test_happy_path_uploads_and_logs(config, db, sample_pdf):
    oscar = FakeOscar(match=PatientMatch("456", "Doe", "Jane", matched_by="Email"))
    sheets = FakeSheets()
    processor, repo = build_processor(
        config, db, extraction=make_extraction(), signed=True, oscar=oscar, sheets=sheets
    )

    result = processor.process(sample_pdf)

    assert result.status is ProcessingStatus.COMPLETED
    assert oscar.upload_calls == [("456", UPLOAD_DOCUMENT_DESCRIPTION)]
    assert UPLOAD_DOCUMENT_DESCRIPTION == "ADHD Assessment Tool"
    assert len(sheets.rows) == 1
    assert result.record.demographic_no == "456"
    # File moved into processed/.
    assert list(config.folders.processed.glob("*.pdf"))


def test_insufficient_identifiers_is_patient_not_found(config, db, sample_pdf):
    extraction = make_extraction(first=None, last=None, email=None, dob=None)
    oscar = FakeOscar(match=PatientMatch("1", "X", "Y"))
    sheets = FakeSheets()
    processor, repo = build_processor(
        config, db, extraction=extraction, signed=True, oscar=oscar, sheets=sheets
    )

    result = processor.process(sample_pdf)

    assert result.status is ProcessingStatus.PATIENT_NOT_FOUND
    assert oscar.find_calls == []
    assert oscar.upload_calls == []


def test_dashboard_query_returns_completed_records(config, db, sample_pdf):
    oscar = FakeOscar(match=PatientMatch("789", "Doe", "Jane"))
    sheets = FakeSheets()
    processor, repo = build_processor(
        config, db, extraction=make_extraction(), signed=True, oscar=oscar, sheets=sheets
    )
    processor.process(sample_pdf)

    completed = repo.list_completed()
    assert len(completed) == 1
    rec = completed[0]
    assert rec.status is ProcessingStatus.COMPLETED
    assert rec.demographic_no == "789"
    assert rec.patient_name() == "Doe, Jane"
    assert rec.patient_email() == "jane@example.com"


def test_sheets_row_never_contains_patient_name(config, db):
    """The SheetsClient PHI guard must reject any row containing a name."""
    from adhd_intake.models import Demographics, ProcessingRecord

    record = ProcessingRecord(
        source_filename="x.pdf",
        questionnaire_type=QuestionnaireType.ADULT_ADHD,
        demographics=Demographics(first_name="Jane", last_name="Doe", email="j@x.com"),
        demographic_no="999",
    )
    client = SheetsClient(config.sheets)
    row = client._build_row(record)

    # The built row must not contain either name...
    joined = " ".join(row).lower()
    assert "jane" not in joined
    assert "doe" not in joined
    # ...and the guard must pass for a clean row.
    client._assert_no_names(row, record)  # should not raise
