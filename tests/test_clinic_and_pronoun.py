"""Tests for clinic blocklist and pronoun detection/priority."""

from __future__ import annotations

import pytest

from adhd_intake.config import ClinicConfig

fitz = pytest.importorskip("fitz")
from adhd_intake.extraction.extractor import Extractor  # noqa: E402


def test_clinic_email_and_address_blocklist():
    c = ClinicConfig(
        email="ADHD@adultadhdcentre.com",
        address_markers=("7885 6th Street", "V3N 3N4"),
    )
    assert c.is_clinic_email("adhd@adultadhdcentre.com")
    assert not c.is_clinic_email("patient@gmail.com")
    assert c.is_clinic_address("Unit #202, 7885 6th Street, Burnaby, BC V3N 3N4")
    assert not c.is_clinic_address("10831 156th Surrey BC")
    assert not c.is_clinic_address(None)


def _pronoun_pdf(path, marks):
    """marks: dict option-> bool (place an 'x' left of marked options)."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((54, 212), "Preferred Pronoun", fontsize=11)
    ys = {"She/Her": 230, "He/His": 246, "They/Them": 262}
    for opt, y in ys.items():
        page.insert_text((307, y), opt, fontsize=11)
        if marks.get(opt):
            page.insert_text((291, y), "x", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def test_pronoun_single(tmp_path):
    doc = fitz.open(str(_pronoun_pdf(tmp_path / "p.pdf", {"She/Her": True})))
    assert Extractor._detect_pronoun(doc[0]) == "She/Her"
    doc.close()


def test_pronoun_priority_hehis(tmp_path):
    # Both He/His and They/Them marked -> He/His wins.
    doc = fitz.open(str(_pronoun_pdf(tmp_path / "q.pdf", {"He/His": True, "They/Them": True})))
    assert Extractor._detect_pronoun(doc[0]) == "He/His"
    doc.close()


def test_pronoun_none_marked(tmp_path):
    doc = fitz.open(str(_pronoun_pdf(tmp_path / "r.pdf", {})))
    assert Extractor._detect_pronoun(doc[0]) is None
    doc.close()


def test_program_classify_private_from_booking_alert():
    from adhd_intake.config import ProgramConfig

    p = ProgramConfig()
    assert p.classify("ADHD Private") == "Private"
    assert p.classify("private ADHD") == "Private"
    assert p.classify("Women private ADHD") == "Private"
    assert p.classify("Therapist Supported intake") == "Private"
    assert p.classify("MSP / publicly funded") == ""   # default (blank)
    assert p.classify(None) == ""
    assert p.classify("") == ""


def test_program_status_resolver():
    from adhd_intake.models import Demographics, ProcessingRecord
    from adhd_intake.sheets.local_sheet import FIELD_RESOLVERS

    private = ProcessingRecord(demographics=Demographics(program_status="Private"))
    assert FIELD_RESOLVERS["program_status"](private) == "Private"
    blank = ProcessingRecord(demographics=Demographics())
    assert FIELD_RESOLVERS["program_status"](blank) == ""
