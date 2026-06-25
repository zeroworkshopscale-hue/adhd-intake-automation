"""Manual-review step: operator corrections on low-confidence extractions."""

from __future__ import annotations

from contextlib import contextmanager

from adhd_intake.database import AuditLog, RecordRepository
from adhd_intake.models import PatientMatch, ProcessingStatus
from adhd_intake.pipeline import IntakeProcessor

from .conftest import FakeExtractor, FakeOscar, FakeSheets, FakeValidator, make_extraction


def _build(config, db, oscar, sheets, extraction, review):
    repo = RecordRepository(db)
    audit = AuditLog(db, actor="test")

    @contextmanager
    def oscar_factory():
        yield oscar

    proc = IntakeProcessor(
        config=config, repository=repo, audit=audit,
        extractor=FakeExtractor(extraction), validator=FakeValidator(True),
        oscar_factory=oscar_factory, sheets_factory=lambda: sheets,
    )
    proc.review_extraction = review
    return proc, repo


def test_review_triggers_on_ocr_and_applies_corrections(config, db, sample_pdf):
    # Handwritten form: nothing read automatically -> operator fills it in.
    extraction = make_extraction(first=None, last=None, email=None, dob=None, used_ocr=True)
    oscar = FakeOscar(match=PatientMatch("55", "Stuart", "Bryan"))
    sheets = FakeSheets()

    def review(record, used_ocr):
        assert used_ocr is True
        return {
            "demographics": {"first_name": "Bryan", "last_name": "Stuart", "dob": "1990-05-01"},
            "answers": {"referral_1": "Friend"},
        }

    proc, _ = _build(config, db, oscar, sheets, extraction, review)
    result = proc.process(sample_pdf)

    assert result.status is ProcessingStatus.COMPLETED
    assert result.record.demographics.first_name == "Bryan"
    assert result.record.demographics.last_name == "Stuart"
    assert result.record.answers.get("referral_1") == "Friend"
    assert len(oscar.find_calls) == 1     # searched after corrections


def test_review_not_triggered_for_clean_typed_form(config, db, sample_pdf):
    calls = {"n": 0}

    def review(record, used_ocr):
        calls["n"] += 1
        return None

    oscar = FakeOscar(match=PatientMatch("1", "Doe", "Jane"))
    sheets = FakeSheets()
    proc, _ = _build(config, db, oscar, sheets, make_extraction(), review)
    proc.process(sample_pdf)

    assert calls["n"] == 0                 # typed form with identifiers: no review
