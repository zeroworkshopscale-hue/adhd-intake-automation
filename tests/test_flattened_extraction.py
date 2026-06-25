"""Flattened/scanned forms (no AcroForm widgets) — ink-based fallbacks.

These cover the case where a questionnaire arrives flattened (printed/scanned or
saved flat), so the widget paths find nothing and detection must fall back to
comparing ink. The widget paths for fillable forms are covered elsewhere; these
verify the *added* fallback layer, not a replacement.
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz")
from adhd_intake.extraction.extractor import Extractor  # noqa: E402


def _pronoun_page(doc, mark=None):
    """Three pronoun options stacked vertically with an empty box left of each;
    `mark` fills one box with ink (a drawn ✓ has no text token)."""
    page = doc.new_page()
    ys = {"She/Her": 218, "He/His": 234, "They/Them": 250}
    for opt, y in ys.items():
        page.insert_text((307, y), opt, fontsize=11)
        page.draw_rect(fitz.Rect(281, y - 9, 300, y + 3), color=(0, 0, 0), width=0.6)
    if mark:
        y = ys[mark]
        page.draw_rect(fitz.Rect(282, y - 8, 299, y + 2), color=(0, 0, 0), fill=(0, 0, 0))
    return page


def test_pronoun_ink_fallback_picks_marked_option(tmp_path):
    doc = fitz.open()
    _pronoun_page(doc, mark="She/Her")   # the X/✓ is ink only, no text glyph
    assert Extractor._detect_pronoun(doc[0]) == "She/Her"
    doc.close()


def test_pronoun_ink_no_false_positive_when_unmarked(tmp_path):
    doc = fitz.open()
    _pronoun_page(doc, mark=None)        # only empty printed boxes
    assert Extractor._detect_pronoun(doc[0]) is None
    doc.close()


def test_referral_ink_fallback_picks_ticked_option(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((54, 150), "HOW DID YOU HEAR ABOUT US?", fontsize=12)
    opts = {"Family member": 271, "Friend": 288, "Online search": 304, "Twitter": 320}
    for opt, y in opts.items():
        page.insert_text((79, y), opt, fontsize=11)
        page.insert_text((50, y), "____", fontsize=11)   # empty blank
    # tick "Friend" with ink in the mark area left of the label
    page.draw_rect(fitz.Rect(52, 280, 72, 290), color=(0, 0, 0), fill=(0, 0, 0))
    assert Extractor._referral_from_ink(doc[0]) == ["Friend"]
    doc.close()


def test_referral_label_reads_printed_text_for_generic_field_name(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((79, 288), "Friend", fontsize=11)
    rect = fitz.Rect(53, 280, 74, 295)            # checkbox left of the label
    # A meaningless field name -> read the printed label beside it.
    assert Extractor._referral_label_for(page, rect, "Check Box10") == "Friend"
    # A descriptive field name still passes through unchanged.
    assert Extractor._referral_label_for(page, rect, "Family member") == "Family member"
    doc.close()


def _substance_row(doc, mark):
    """One substance row (Alcohol) with No/Yes; `mark` fills one of the boxes."""
    page = doc.new_page()
    page.insert_text((50, 120), "Alcohol", fontsize=11)
    page.insert_text((272, 120), "No", fontsize=11)
    page.insert_text((344, 120), "Yes", fontsize=11)
    page.draw_rect(fitz.Rect(244, 111, 264, 123), color=(0, 0, 0), width=0.6)   # No box
    page.draw_rect(fitz.Rect(316, 111, 336, 123), color=(0, 0, 0), width=0.6)   # Yes box
    if mark == "Yes":
        page.draw_rect(fitz.Rect(318, 113, 334, 121), color=(0, 0, 0), fill=(0, 0, 0))
    elif mark == "No":
        page.draw_rect(fitz.Rect(246, 113, 262, 121), color=(0, 0, 0), fill=(0, 0, 0))
    return page


def test_substance_ink_detects_yes(tmp_path):
    doc = fitz.open()
    page = _substance_row(doc, "Yes")
    ry = page.search_for("Alcohol")[0].y0   # same row anchor the real code uses
    assert Extractor._substance_yes_from_ink(page, ry) is True
    doc.close()


def test_substance_ink_no_when_no_is_marked(tmp_path):
    doc = fitz.open()
    page = _substance_row(doc, "No")
    ry = page.search_for("Alcohol")[0].y0
    assert Extractor._substance_yes_from_ink(page, ry) is False
    doc.close()
