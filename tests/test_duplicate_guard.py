"""Duplicate-upload guard tests.

Re-dropping a file that was already uploaded to OSCAR must NOT create a second
document. Instead:
  * if the earlier run also reached the sheet -> skip entirely (true duplicate)
  * if the earlier run uploaded but never wrote the sheet -> write the missing
    row now, still without touching OSCAR (automatic error recovery)

The guard keys off the file hash, so a renamed re-drop of the same content is
still caught.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adhd_intake.models import PatientMatch, ProcessingStatus

from .conftest import FakeOscar, FakeSheets, build_processor, make_extraction


@dataclass
class RaisingSheets:
    """Sheets stand-in whose append always fails (simulates a locked sheet)."""

    calls: int = 0

    def append_record(self, record):
        self.calls += 1
        raise RuntimeError("sheet is locked")


def _redrop(config, name: str = "redrop.pdf"):
    """Write a byte-identical copy of the sample file under a new name, as if the
    operator dragged the same questionnaire in again."""
    p = config.folders.incoming / name
    p.write_bytes(b"%PDF-1.4 fake content for hashing")
    return p


def test_redrop_of_completed_file_skips_oscar_and_sheet(config, db, sample_pdf):
    oscar = FakeOscar(match=PatientMatch("456", "Doe", "Jane"))
    sheets = FakeSheets()
    processor, repo = build_processor(
        config, db, extraction=make_extraction(), signed=True, oscar=oscar, sheets=sheets
    )

    first = processor.process(sample_pdf)
    assert first.status is ProcessingStatus.COMPLETED
    assert len(oscar.upload_calls) == 1
    assert len(sheets.rows) == 1

    # Same content, dropped again under a different name.
    second = processor.process(_redrop(config))

    assert second.status is ProcessingStatus.COMPLETED
    assert "already" in second.message.lower()
    # OSCAR untouched on the re-drop: no second find, no second upload.
    assert len(oscar.find_calls) == 1
    assert len(oscar.upload_calls) == 1
    # No duplicate sheet row.
    assert len(sheets.rows) == 1
    assert second.record.oscar_document_id == first.record.oscar_document_id


def test_redrop_after_failed_sheet_writes_missing_row_without_reuploading(config, db, sample_pdf):
    # --- First run: OSCAR upload succeeds, but the sheet write fails. ---
    oscar1 = FakeOscar(match=PatientMatch("789", "Doe", "Jane"))
    raising = RaisingSheets()
    processor1, repo = build_processor(
        config, db, extraction=make_extraction(), signed=True, oscar=oscar1, sheets=raising
    )
    first = processor1.process(sample_pdf)

    assert first.status is ProcessingStatus.ERROR
    assert first.record.oscar_document_id is not None      # uploaded
    assert first.record.sheets_row is None                 # but not on the sheet
    assert len(oscar1.upload_calls) == 1

    # --- Re-drop: a fresh processor with a working sheet. ---
    oscar2 = FakeOscar(match=PatientMatch("789", "Doe", "Jane"))
    sheets2 = FakeSheets()
    processor2, _ = build_processor(
        config, db, extraction=make_extraction(), signed=True, oscar=oscar2, sheets=sheets2
    )
    second = processor2.process(_redrop(config))

    # Recovered: sheet row written, OSCAR never touched again.
    assert second.status is ProcessingStatus.COMPLETED
    assert len(sheets2.rows) == 1
    assert oscar2.find_calls == []
    assert oscar2.upload_calls == []
    assert second.record.sheets_row is not None
    assert second.record.oscar_document_id == first.record.oscar_document_id


def test_redrop_preserves_incomplete_status(config, db, sample_pdf):
    """A re-drop of an incomplete-but-uploaded file keeps the incomplete status
    rather than silently promoting it to a clean Completed."""
    from adhd_intake.models import Demographics, ProcessingRecord
    from adhd_intake.database import RecordRepository
    from adhd_intake.utils.files import sha256_file

    # Seed a prior record that was uploaded as INCOMPLETE and made it to the sheet.
    repo = RecordRepository(db)
    prior = ProcessingRecord(
        source_filename="sample.pdf",
        file_hash=sha256_file(sample_pdf),
        status=ProcessingStatus.INCOMPLETE_PATIENT_INFORMED,
        demographics=Demographics(first_name="Jane", last_name="Doe", email="j@x.com"),
        demographic_no="321",
        oscar_document_id="321:doc",
        sheets_row=1,
    )
    repo.insert(prior)

    oscar = FakeOscar(match=PatientMatch("321", "Doe", "Jane"))
    sheets = FakeSheets()
    processor, _ = build_processor(
        config, db, extraction=make_extraction(), signed=True, oscar=oscar, sheets=sheets
    )
    result = processor.process(_redrop(config))

    assert result.status is ProcessingStatus.INCOMPLETE_PATIENT_INFORMED
    assert oscar.upload_calls == []
    assert sheets.rows == []
