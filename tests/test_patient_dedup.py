"""Patient-level duplicate guard: same patient, different file, same session."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

from adhd_intake.database import AuditLog, RecordRepository
from adhd_intake.models import PatientMatch, ProcessingStatus
from adhd_intake.pipeline import IntakeProcessor

from .conftest import FakeExtractor, FakeOscar, FakeSheets, FakeValidator, make_extraction


def _processor(config, db, oscar, sheets):
    repo = RecordRepository(db)
    audit = AuditLog(db, actor="test")

    @contextmanager
    def oscar_factory():
        yield oscar

    return IntakeProcessor(
        config=config, repository=repo, audit=audit,
        extractor=FakeExtractor(make_extraction()), validator=FakeValidator(True),
        oscar_factory=oscar_factory, sheets_factory=lambda: sheets,
    ), repo


def _other_file(config, name, content):
    p = config.folders.incoming / name
    p.write_bytes(content)
    return p


def test_second_file_same_patient_is_skipped(config, db, sample_pdf):
    oscar = FakeOscar(match=PatientMatch("500", "Gregory", "Richard"))
    sheets = FakeSheets()
    proc, _ = _processor(config, db, oscar, sheets)
    proc.session_start = datetime.now() - timedelta(minutes=1)

    first = proc.process(sample_pdf)
    assert first.status is ProcessingStatus.COMPLETED
    assert len(oscar.upload_calls) == 1 and len(sheets.rows) == 1

    # A DIFFERENT file (different hash) for the SAME patient.
    second = proc.process(_other_file(config, "second.pdf", b"%PDF-1.4 different bytes"))

    assert second.record.skipped_duplicate is True
    assert len(oscar.upload_calls) == 1          # no second OSCAR document
    assert len(sheets.rows) == 1                 # no duplicate sheet row
    assert second.record.oscar_document_id == first.record.oscar_document_id


def test_no_patient_dedup_without_session_start(config, db, sample_pdf):
    oscar = FakeOscar(match=PatientMatch("500", "Gregory", "Richard"))
    sheets = FakeSheets()
    proc, _ = _processor(config, db, oscar, sheets)
    # session_start left None -> patient-level guard disabled (legacy behaviour)

    proc.process(sample_pdf)
    proc.process(_other_file(config, "second.pdf", b"%PDF-1.4 different bytes"))
    assert len(oscar.upload_calls) == 2          # no patient-level dedup
