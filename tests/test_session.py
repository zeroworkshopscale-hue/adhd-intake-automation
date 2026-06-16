"""Tests for session continuity (resume a previous session after a close)."""

from __future__ import annotations

from datetime import datetime, timedelta

from adhd_intake.database import AuditLog
from adhd_intake.models import Demographics, ProcessingRecord, ProcessingStatus
from adhd_intake.services import AppServices

from .conftest import FakeOscar, FakeSheets, build_processor, make_extraction


def _services(config, db) -> AppServices:
    proc, repo = build_processor(
        config, db, extraction=make_extraction(), signed=True,
        oscar=FakeOscar(match=None), sheets=FakeSheets(),
    )
    audit = AuditLog(db, actor="test")
    return AppServices(config, db, repo, audit, proc)


def _add_completed(svc, when: datetime) -> None:
    rec = ProcessingRecord(
        source_filename="a.pdf",
        status=ProcessingStatus.COMPLETED,
        demographics=Demographics(first_name="Jane", last_name="Doe"),
        demographic_no="1",
    )
    rec.created_at = when
    svc.repository.insert(rec)


def test_no_previous_session(config, db):
    svc = _services(config, db)
    assert svc.previous_session_start() is None
    assert svc.resumable_completed_count() == 0


def test_new_session_writes_state_and_resets_sheet(config, db):
    csv = config.sheets.local_path
    csv.parent.mkdir(parents=True, exist_ok=True)
    csv.write_text("header\nrow1\n", encoding="utf-8")

    svc = _services(config, db)
    svc.begin_session(resume=False)

    assert not csv.exists()                       # fresh session clears the sheet
    assert svc._session_state_path().exists()     # boundary persisted
    assert abs((svc.session_start - datetime.now()).total_seconds()) < 5


def test_resume_reloads_previous_session(config, db):
    # First run: start a session, process a patient, then "close".
    svc1 = _services(config, db)
    svc1.begin_session(resume=False)
    t0 = svc1.previous_session_start()            # persisted (second precision)
    _add_completed(svc1, t0 + timedelta(seconds=1))

    # Next launch sees a resumable session.
    svc2 = _services(config, db)
    assert svc2.resumable_completed_count() == 1
    prev = svc2.previous_session_start()
    assert prev == t0

    # Resuming keeps the same start (so the patient reloads in the table).
    svc2.begin_session(resume=True)
    assert svc2.session_start == prev


def test_resume_keeps_copy_sheet(config, db):
    svc1 = _services(config, db)
    svc1.begin_session(resume=False)
    _add_completed(svc1, svc1.session_start + timedelta(seconds=1))

    csv = config.sheets.local_path
    csv.parent.mkdir(parents=True, exist_ok=True)
    csv.write_text("header\nrow1\n", encoding="utf-8")

    svc2 = _services(config, db)
    svc2.begin_session(resume=True)
    assert csv.exists()                           # resume must NOT clear the sheet
