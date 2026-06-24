"""Operator decision on an incomplete form: process-as-complete vs send-back."""

from __future__ import annotations

from contextlib import contextmanager

from adhd_intake.database import AuditLog, RecordRepository
from adhd_intake.models import CompletenessResult, PatientMatch, ProcessingStatus
from adhd_intake.pipeline import IntakeProcessor
from adhd_intake.pipeline.processor import (
    UPLOAD_DOCUMENT_DESCRIPTION,
    UPLOAD_DOCUMENT_DESCRIPTION_INCOMPLETE,
)

from .conftest import FakeExtractor, FakeOscar, FakeSheets, FakeValidator, make_extraction


class FakeCompleteness:
    def __init__(self, result):
        self.result = result

    def validate(self, pdf_path, qtype):
        return self.result


def _incomplete():
    return CompletenessResult(
        complete=False, checked=True, incomplete_pages=[10], unanswered_count=1,
        unanswered_questions=["Page 10: 6 I am late for class."],
    )


def _build(config, db, oscar, sheets, confirm):
    repo = RecordRepository(db)
    audit = AuditLog(db, actor="test")

    @contextmanager
    def oscar_factory():
        yield oscar

    proc = IntakeProcessor(
        config=config, repository=repo, audit=audit,
        extractor=FakeExtractor(make_extraction()), validator=FakeValidator(True),
        oscar_factory=oscar_factory, sheets_factory=lambda: sheets,
        completeness_validator=FakeCompleteness(_incomplete()),
    )
    proc.confirm_incomplete = confirm
    return proc, repo


def test_process_as_complete_uploads_as_complete(config, db, sample_pdf):
    oscar = FakeOscar(match=PatientMatch("1", "Doe", "Jane"))
    sheets = FakeSheets()
    proc, _ = _build(config, db, oscar, sheets, confirm=lambda r, c: True)

    result = proc.process(sample_pdf)

    assert result.status is ProcessingStatus.COMPLETED
    assert len(oscar.upload_calls) == 1
    assert oscar.upload_calls[0][2] == UPLOAD_DOCUMENT_DESCRIPTION   # not the incomplete desc


def test_send_back_flags_incomplete_and_keeps_questions(config, db, sample_pdf):
    oscar = FakeOscar(match=PatientMatch("1", "Doe", "Jane"))
    sheets = FakeSheets()
    proc, _ = _build(config, db, oscar, sheets, confirm=lambda r, c: False)

    result = proc.process(sample_pdf)

    assert result.status is ProcessingStatus.INCOMPLETE_PATIENT_INFORMED
    assert result.record.incomplete_questions == ["Page 10: 6 I am late for class."]
    assert oscar.upload_calls[0][2] == UPLOAD_DOCUMENT_DESCRIPTION_INCOMPLETE


def test_default_headless_sends_back(config, db, sample_pdf):
    """With no GUI callback the safe default is to flag incomplete."""
    oscar = FakeOscar(match=PatientMatch("1", "Doe", "Jane"))
    sheets = FakeSheets()
    repo = RecordRepository(db)
    audit = AuditLog(db, actor="test")

    @contextmanager
    def oscar_factory():
        yield oscar

    proc = IntakeProcessor(
        config=config, repository=repo, audit=audit,
        extractor=FakeExtractor(make_extraction()), validator=FakeValidator(True),
        oscar_factory=oscar_factory, sheets_factory=lambda: sheets,
        completeness_validator=FakeCompleteness(_incomplete()),
    )
    # confirm_incomplete left at its default
    result = proc.process(sample_pdf)
    assert result.status is ProcessingStatus.INCOMPLETE_PATIENT_INFORMED
