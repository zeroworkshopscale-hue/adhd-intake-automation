"""Consent-checkbox detection: any mark inside the box = YES, empty = NO.

Builds small PDFs that mimic the two Page-5 consent statements with checkboxes,
marking them in various ways (X, check, scribble) to confirm the logic doesn't
depend on a specific symbol.
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz")

from adhd_intake.extraction import templates  # noqa: E402
from adhd_intake.extraction.extractor import Extractor  # noqa: E402
from adhd_intake.models import QuestionnaireType  # noqa: E402

_TPL = templates.template_for(QuestionnaireType.ADULT_ADHD)


def _mark(page, box, kind):
    import fitz as _f
    x0, y0, x1, y1 = box
    if kind == "x":
        page.draw_line(_f.Point(x0 + 3, y0 + 3), _f.Point(x1 - 3, y1 - 3), width=1.5)
        page.draw_line(_f.Point(x1 - 3, y0 + 3), _f.Point(x0 + 3, y1 - 3), width=1.5)
    elif kind == "check":
        page.draw_line(_f.Point(x0 + 3, y0 + 9), _f.Point(x0 + 7, y1 - 3), width=1.6)
        page.draw_line(_f.Point(x0 + 7, y1 - 3), _f.Point(x1 - 2, y0 + 2), width=1.6)
    elif kind == "scribble":
        page.draw_line(_f.Point(x0 + 2, y0 + 5), _f.Point(x1 - 2, y0 + 7), width=1.5)
        page.draw_line(_f.Point(x0 + 2, y0 + 10), _f.Point(x1 - 2, y0 + 6), width=1.5)


def _make_pdf(path, mark1, mark2):
    doc = fitz.open()
    page = doc.new_page()
    box1 = (56, 505, 74, 523)
    page.draw_rect(fitz.Rect(*box1), width=1)
    page.insert_text((83, 512), "I consent for the Adult ADHD Centre to use my email address for", fontsize=11)
    page.insert_text((83, 527), "future ADHD initiatives and programs.", fontsize=11)
    if mark1:
        _mark(page, box1, mark1)
    box2 = (56, 560, 74, 578)
    page.draw_rect(fitz.Rect(*box2), width=1)
    page.insert_text((83, 567), "I consent for the Adult ADHD Centre to use my email address to", fontsize=11)
    page.insert_text((83, 582), "participate in future research opportunities.", fontsize=11)
    if mark2:
        _mark(page, box2, mark2)
    doc.save(str(path))
    doc.close()
    return path


def _detect(path):
    pairs = Extractor._collect_label_pairs(path, _TPL)
    return pairs.get("future_initiatives"), pairs.get("future_research")


def test_both_empty(tmp_path):
    assert _detect(_make_pdf(tmp_path / "a.pdf", None, None)) == ("NO", "NO")


def test_first_checked_second_empty(tmp_path):
    assert _detect(_make_pdf(tmp_path / "b.pdf", "x", None)) == ("YES", "NO")


def test_first_empty_second_checked(tmp_path):
    assert _detect(_make_pdf(tmp_path / "c.pdf", None, "check")) == ("NO", "YES")


def test_both_checked(tmp_path):
    assert _detect(_make_pdf(tmp_path / "d.pdf", "check", "x")) == ("YES", "YES")


def test_scribble_counts_as_yes(tmp_path):
    assert _detect(_make_pdf(tmp_path / "e.pdf", "scribble", "scribble")) == ("YES", "YES")
