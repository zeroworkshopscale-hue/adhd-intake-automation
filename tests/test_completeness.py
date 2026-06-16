"""Tests for questionnaire response-completeness validation and its pipeline gate.

Covers:
  * CompletenessResult.pages_label formatting
  * the validator on synthetic rating-grid PDFs (all answered / a blank row)
  * the validator skipping (checked=False) when there is no response grid
  * the pipeline gate: decline -> INCOMPLETE_DECLINED (no upload, no sheets,
    moved to rejected); approve -> COMPLETED
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from adhd_intake.config import ValidationConfig
from adhd_intake.models import (
    CompletenessResult,
    PatientMatch,
    ProcessingStatus,
    QuestionnaireType,
)
from adhd_intake.database import AuditLog, RecordRepository
from adhd_intake.pipeline import IntakeProcessor

from .conftest import FakeOscar, FakeSheets, FakeExtractor, FakeValidator, make_extraction


# ----------------------------------------------------------------------
# pages_label formatting
# ----------------------------------------------------------------------
def test_pages_label_single():
    assert CompletenessResult(complete=False, incomplete_pages=[8]).pages_label == "Page 8"


def test_pages_label_two():
    r = CompletenessResult(complete=False, incomplete_pages=[8, 11])
    assert r.pages_label == "Pages 8 and 11"


def test_pages_label_many_sorted_deduped():
    r = CompletenessResult(complete=False, incomplete_pages=[11, 8, 8, 10])
    assert r.pages_label == "Pages 8, 10 and 11"


# ----------------------------------------------------------------------
# Validator on synthetic PDFs
# ----------------------------------------------------------------------
fitz = pytest.importorskip("fitz")
from adhd_intake.validation import CompletenessValidator  # noqa: E402

# Response columns (3-point rating scale).
_COLS = {
    "Never or Rarely": 290.0,
    "Sometimes": 385.0,
    "Often or Very Often": 455.0,
}
_COL_MARK_X = {  # x to drop an "X" so it lands inside that column's cell
    "Never or Rarely": 312.0,
    "Sometimes": 400.0,
    "Often or Very Often": 480.0,
}
_QUESTIONS = [
    "I have difficulty falling asleep at night.",
    "I get into arguments with other people.",
    "I feel bad about myself most days.",
    "I use the internet and electronic devices in excess.",
    "I have problems keeping an acceptable appearance.",
]


def _grid_page(page, answered: list[bool | str]) -> None:
    """Draw a rating grid. answered[i]: True/col-name to mark, False to leave blank."""
    for label, x in _COLS.items():
        page.insert_text((x, 100), label, fontsize=10)
    y = 150
    for i, question in enumerate(_QUESTIONS):
        page.insert_text((54, y), question, fontsize=10)
        mark = answered[i] if i < len(answered) else True
        if mark:
            col = mark if isinstance(mark, str) else "Sometimes"
            page.insert_text((_COL_MARK_X[col], y), "X", fontsize=13)
        y += 26


def _build_pdf(tmp_path: Path, name: str, n_pages: int, grids: dict[int, list]) -> Path:
    """grids: {0-based page index -> answered list}. Other pages are left blank."""
    doc = fitz.open()
    for idx in range(n_pages):
        page = doc.new_page()
        if idx in grids:
            _grid_page(page, grids[idx])
    out = tmp_path / name
    doc.save(str(out))
    doc.close()
    return out


def _validator() -> CompletenessValidator:
    return CompletenessValidator(ValidationConfig())


def test_all_answered_is_complete(tmp_path):
    # Adult tool -> pages 6..11 checked; fill them all with answered rows.
    grids = {idx: [True] * len(_QUESTIONS) for idx in range(5, 11)}
    pdf = _build_pdf(tmp_path, "ok.pdf", 11, grids)
    res = _validator().validate(pdf, QuestionnaireType.ADULT_ADHD)
    assert res.checked is True
    assert res.complete is True
    assert res.incomplete_pages == []


def test_blank_row_is_flagged_with_page_number(tmp_path):
    grids = {idx: [True] * len(_QUESTIONS) for idx in range(5, 11)}
    # Leave one row blank on page index 7 (== page 8) and index 10 (== page 11).
    grids[7] = [True, False, True, True, True]
    grids[10] = [True, True, True, False, True]
    pdf = _build_pdf(tmp_path, "gaps.pdf", 11, grids)
    res = _validator().validate(pdf, QuestionnaireType.ADULT_ADHD)
    assert res.checked is True
    assert res.complete is False
    assert res.incomplete_pages == [8, 11]
    assert res.pages_label == "Pages 8 and 11"
    assert res.unanswered_count == 2


def test_marks_in_different_columns_all_count(tmp_path):
    grids = {idx: [True] * len(_QUESTIONS) for idx in range(5, 11)}
    grids[6] = ["Never or Rarely", "Sometimes", "Often or Very Often",
                "Never or Rarely", "Sometimes"]
    pdf = _build_pdf(tmp_path, "cols.pdf", 11, grids)
    res = _validator().validate(pdf, QuestionnaireType.ADULT_ADHD)
    assert res.complete is True


def test_no_grid_is_not_flagged(tmp_path):
    # A document with no response grid on the pages -> checked False, complete.
    doc = fitz.open()
    for _ in range(11):
        p = doc.new_page()
        p.insert_text((54, 120), "Some narrative text with no rating columns.", fontsize=11)
    out = tmp_path / "nogrid.pdf"
    doc.save(str(out))
    doc.close()
    res = _validator().validate(out, QuestionnaireType.ADULT_ADHD)
    assert res.checked is False
    assert res.complete is True


def test_unknown_type_skips(tmp_path):
    pdf = _build_pdf(tmp_path, "u.pdf", 11, {7: [True, False, True, True, True]})
    res = _validator().validate(pdf, QuestionnaireType.UNKNOWN)
    assert res.checked is False
    assert res.complete is True


def test_women_tool_checks_page_12(tmp_path):
    grids = {idx: [True] * len(_QUESTIONS) for idx in range(5, 12)}
    grids[11] = [True, True, False, True, True]  # page 12 has a gap
    pdf = _build_pdf(tmp_path, "women.pdf", 12, grids)
    res = _validator().validate(pdf, QuestionnaireType.ADHD_WOMEN)
    assert res.complete is False
    assert res.incomplete_pages == [12]


# --- Fillable PDFs (AcroForm text fields, like the real questionnaire) -------
_WIDGET_COLS = (370.0, 430.0, 510.0)  # Never or Rarely / Sometimes / Often


def _build_fillable(tmp_path: Path, name: str, n_pages: int, grids: dict[int, list]) -> Path:
    """grids: {0-based page idx -> answered list}. Each answered row gets an 'X'
    in one of its three text-field response cells; blanks stay empty."""
    doc = fitz.open()
    for idx in range(n_pages):
        page = doc.new_page()
        if idx not in grids:
            continue
        y = 150.0
        for ri, answered in enumerate(grids[idx]):
            for ci, x in enumerate(_WIDGET_COLS):
                w = fitz.Widget()
                w.field_name = f"p{idx}_r{ri}_c{ci}"
                w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
                w.rect = fitz.Rect(x, y, x + 14, y + 12)
                w.field_value = "X" if (answered and ci == 1) else ""
                page.add_widget(w)
            y += 26
    out = tmp_path / name
    doc.save(str(out))
    doc.close()
    return out


def test_fillable_all_answered_complete(tmp_path):
    grids = {idx: [True] * 8 for idx in range(5, 11)}
    pdf = _build_fillable(tmp_path, "f_ok.pdf", 11, grids)
    res = _validator().validate(pdf, QuestionnaireType.ADULT_ADHD)
    assert res.checked is True and res.complete is True


def test_fillable_missing_one_row_flags_that_page(tmp_path):
    grids = {idx: [True] * 8 for idx in range(5, 11)}
    grids[7][3] = False  # page index 7 == page 8, one blank row
    pdf = _build_fillable(tmp_path, "f_gap.pdf", 11, grids)
    res = _validator().validate(pdf, QuestionnaireType.ADULT_ADHD)
    assert res.complete is False
    assert res.incomplete_pages == [8]
    assert res.unanswered_count == 1


def test_fillable_blank_form_flags_all_pages(tmp_path):
    # A blank template has every row empty -> every question page is unanswered.
    grids = {idx: [False] * 5 for idx in range(5, 11)}
    pdf = _build_fillable(tmp_path, "f_blank.pdf", 11, grids)
    res = _validator().validate(pdf, QuestionnaireType.ADULT_ADHD)
    assert res.complete is False
    assert res.incomplete_pages == [6, 7, 8, 9, 10, 11]


def test_disabled_check_skips(tmp_path):
    pdf = _build_pdf(tmp_path, "d.pdf", 11, {7: [True, False, True, True, True]})
    v = CompletenessValidator(ValidationConfig(check_completeness=False))
    res = v.validate(pdf, QuestionnaireType.ADULT_ADHD)
    assert res.checked is False
    assert res.complete is True


# ----------------------------------------------------------------------
# Pipeline gate
# ----------------------------------------------------------------------
class _FakeCompleteness:
    def __init__(self, result: CompletenessResult):
        self._result = result

    def validate(self, pdf_path, qtype) -> CompletenessResult:
        return self._result


def _build_gate_processor(config, db, *, completeness, decision, oscar, sheets):
    repo = RecordRepository(db)
    audit = AuditLog(db, actor="test")

    @contextmanager
    def oscar_factory():
        yield oscar

    proc = IntakeProcessor(
        config=config,
        repository=repo,
        audit=audit,
        extractor=FakeExtractor(make_extraction()),
        validator=FakeValidator(True),
        completeness_validator=_FakeCompleteness(completeness),
        oscar_factory=oscar_factory,
        sheets_factory=lambda: sheets,
    )
    proc.confirm_incomplete = lambda record, comp: decision
    return proc, repo


def _incomplete() -> CompletenessResult:
    return CompletenessResult(
        complete=False, incomplete_pages=[8], unanswered_count=1, checked=True,
        detail="1 unanswered",
    )


def test_decline_incomplete_stops_without_upload(config, db, sample_pdf):
    oscar = FakeOscar(match=PatientMatch("321", "Doe", "Jane"))
    sheets = FakeSheets()
    proc, _ = _build_gate_processor(
        config, db, completeness=_incomplete(), decision=False, oscar=oscar, sheets=sheets
    )
    result = proc.process(sample_pdf)
    assert result.status is ProcessingStatus.INCOMPLETE_DECLINED
    assert oscar.find_calls == []
    assert oscar.upload_calls == []
    assert sheets.rows == []
    assert list(config.folders.rejected.glob("*.pdf"))
    assert not sample_pdf.exists()


def test_approve_incomplete_continues_to_upload(config, db, sample_pdf):
    oscar = FakeOscar(match=PatientMatch("654", "Doe", "Jane"))
    sheets = FakeSheets()
    proc, _ = _build_gate_processor(
        config, db, completeness=_incomplete(), decision=True, oscar=oscar, sheets=sheets
    )
    result = proc.process(sample_pdf)
    assert result.status is ProcessingStatus.COMPLETED
    assert oscar.upload_calls and oscar.upload_calls[0][0] == "654"
    assert len(sheets.rows) == 1


def test_complete_form_never_asks(config, db, sample_pdf):
    asked = {"n": 0}
    oscar = FakeOscar(match=PatientMatch("777", "Doe", "Jane"))
    sheets = FakeSheets()
    complete = CompletenessResult(complete=True, checked=True, detail="ok")
    proc, _ = _build_gate_processor(
        config, db, completeness=complete, decision=False, oscar=oscar, sheets=sheets
    )

    def _count(record, comp):
        asked["n"] += 1
        return False

    proc.confirm_incomplete = _count
    result = proc.process(sample_pdf)
    assert result.status is ProcessingStatus.COMPLETED
    assert asked["n"] == 0  # complete form must not prompt
