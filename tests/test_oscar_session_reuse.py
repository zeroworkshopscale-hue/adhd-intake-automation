"""One OSCAR session (login) is reused across a batch in reuse mode."""

from __future__ import annotations

from contextlib import contextmanager

from adhd_intake.database import AuditLog, RecordRepository
from adhd_intake.models import PatientMatch
from adhd_intake.pipeline import IntakeProcessor

from .conftest import FakeExtractor, FakeOscar, FakeSheets, FakeValidator, make_extraction


def _processor(config, db, factory):
    repo = RecordRepository(db)
    audit = AuditLog(db, actor="test")
    return IntakeProcessor(
        config=config, repository=repo, audit=audit,
        extractor=FakeExtractor(make_extraction()), validator=FakeValidator(True),
        oscar_factory=factory, sheets_factory=lambda: FakeSheets(),
    )


def _counting_factory(oscar, calls):
    @contextmanager
    def factory():
        calls["n"] += 1            # one login per session opened
        yield oscar
    return factory


def _drop(config, name, content):
    p = config.folders.incoming / name
    p.write_bytes(content)
    return p


def test_session_reused_across_files(config, db, sample_pdf):
    calls = {"n": 0}
    oscar = FakeOscar(match=PatientMatch("1", "Doe", "Jane"))
    proc = _processor(config, db, _counting_factory(oscar, calls))
    proc.reuse_oscar_session = True

    proc.process(sample_pdf)
    proc.process(_drop(config, "second.pdf", b"%PDF-1.4 second"))
    assert calls["n"] == 1                     # ONE login for both files

    proc.close_oscar_session()
    proc.process(_drop(config, "third.pdf", b"%PDF-1.4 third"))
    assert calls["n"] == 2                     # re-opens after close


def test_session_per_file_when_not_reusing(config, db, sample_pdf):
    calls = {"n": 0}
    oscar = FakeOscar(match=PatientMatch("1", "Doe", "Jane"))
    proc = _processor(config, db, _counting_factory(oscar, calls))
    # reuse_oscar_session defaults False

    proc.process(sample_pdf)
    proc.process(_drop(config, "second.pdf", b"%PDF-1.4 second"))
    assert calls["n"] == 2                     # a session per file (legacy behaviour)
