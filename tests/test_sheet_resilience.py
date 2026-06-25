"""Copy-sheet resilience: full province name + locked-file fallback."""

from __future__ import annotations

from adhd_intake.models import Demographics, ProcessingRecord
from adhd_intake.sheets.local_sheet import FIELD_RESOLVERS, LocalSheetWriter


def test_province_expanded_to_full_name():
    rec = ProcessingRecord(demographics=Demographics(province="BC"))
    assert FIELD_RESOLVERS["province"](rec) == "British Columbia"


def test_province_already_full_is_unchanged():
    rec = ProcessingRecord(demographics=Demographics(province="Ontario"))
    assert FIELD_RESOLVERS["province"](rec) == "Ontario"


def test_province_blank_stays_blank():
    rec = ProcessingRecord(demographics=Demographics())
    assert FIELD_RESOLVERS["province"](rec) == ""


def test_locked_sheet_falls_back_without_losing_row(tmp_path, monkeypatch):
    main = tmp_path / "copy.csv"
    writer = LocalSheetWriter(main, columns=(("Program", "program_status"),))
    real_write = writer._write_row

    def fake_write(path, record):
        if path == main:
            raise PermissionError(13, "Permission denied")
        return real_write(path, record)

    monkeypatch.setattr(writer, "_write_row", fake_write)

    rec = ProcessingRecord(demographics=Demographics(program_status="Private"))
    row = writer.append_record(rec)

    assert row == 1
    assert writer.last_write_path == writer._fallback_path()
    assert writer._fallback_path().exists()
    assert not main.exists()           # main stayed locked, nothing lost
