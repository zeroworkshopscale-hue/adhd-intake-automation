"""Tests for the new features: Issue 1 (login verify), Issue 3 (incomplete upload),
Issue 4 (docx), Issue 5 (pronoun), Issue 6 (referral source).

No heavy deps (Playwright, PyMuPDF, python-docx) required — each heavy test is
guarded by importorskip or skips gracefully when the library is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adhd_intake.models import (
    Demographics,
    ProcessingStatus,
    QuestionnaireType,
)


# -------------------------------------------------------------------------
# Issue 3 — incomplete forms always upload
# -------------------------------------------------------------------------
def test_incomplete_patient_informed_is_terminal():
    assert ProcessingStatus.INCOMPLETE_PATIENT_INFORMED.is_terminal


def test_incomplete_patient_informed_is_success():
    """Incomplete uploads are 'success' for purposes of the patient table."""
    assert ProcessingStatus.INCOMPLETE_PATIENT_INFORMED.is_success


def test_incomplete_patient_informed_value():
    assert ProcessingStatus.INCOMPLETE_PATIENT_INFORMED.value == "Incomplete Form - Patient Informed"


# -------------------------------------------------------------------------
# Issue 5 — pronoun in demographics
# -------------------------------------------------------------------------
def test_demographics_pronoun_field():
    d = Demographics(pronoun="She/Her")
    assert d.pronoun == "She/Her"


def test_demographics_pronoun_default_none():
    d = Demographics()
    assert d.pronoun is None


def test_pronoun_in_template_demographic_fields():
    from adhd_intake.extraction.templates import template_for
    tmpl = template_for(QuestionnaireType.ADULT_ADHD)
    assert "pronoun" in tmpl.demographic_fields
    assert "pronoun" in tmpl.demographic_fields["pronoun"]


# -------------------------------------------------------------------------
# Issue 6 — referral_source in local_sheet
# -------------------------------------------------------------------------
def test_referral_source_resolver_exists():
    from adhd_intake.sheets.local_sheet import FIELD_RESOLVERS
    assert "referral_source" in FIELD_RESOLVERS


def test_referral_source_resolver_reads_answers():
    from adhd_intake.sheets.local_sheet import FIELD_RESOLVERS
    from adhd_intake.models import ProcessingRecord

    rec = ProcessingRecord(source_filename="x.pdf")
    rec.answers["referral_source"] = "Word of mouth"
    assert FIELD_RESOLVERS["referral_source"](rec) == "Word of mouth"


def test_referral_source_resolver_empty_when_absent():
    from adhd_intake.sheets.local_sheet import FIELD_RESOLVERS
    from adhd_intake.models import ProcessingRecord

    rec = ProcessingRecord(source_filename="x.pdf")
    assert FIELD_RESOLVERS["referral_source"](rec) == ""


# -------------------------------------------------------------------------
# Issue 4 — docx extraction (requires python-docx)
# -------------------------------------------------------------------------
docx = pytest.importorskip("docx", reason="python-docx not installed")


def _make_docx(tmp_path: Path, content: dict[str, str]) -> Path:
    """Create a minimal .docx with 'Label: Value' paragraphs."""
    from docx import Document

    doc = Document()
    for label, value in content.items():
        doc.add_paragraph(f"{label}: {value}")
    out = tmp_path / "test_form.docx"
    doc.save(str(out))
    return out


def test_docx_extract_demographics(tmp_path):
    from adhd_intake.extraction.docx_extractor import extract_docx

    path = _make_docx(tmp_path, {
        "Legal First Name": "Alice",
        "Legal Last Name": "Smith",
        "Email": "alice@example.com",
        "Date of Birth": "1995-03-15",
        "Pronoun": "She/Her",
        "Adult ADHD Centre": "assessment",  # triggers type identification
    })
    result = extract_docx(path)
    assert result.demographics.first_name == "Alice"
    assert result.demographics.last_name == "Smith"
    assert result.demographics.email == "alice@example.com"


def test_docx_identifies_questionnaire_type(tmp_path):
    from adhd_intake.extraction.docx_extractor import extract_docx

    path = _make_docx(tmp_path, {
        "Adult ADHD Centre Assessment": "tool",
        "Legal First Name": "Bob",
    })
    result = extract_docx(path)
    assert result.questionnaire_type is QuestionnaireType.ADULT_ADHD


def test_docx_unknown_type_when_no_identifier(tmp_path):
    from adhd_intake.extraction.docx_extractor import extract_docx

    path = _make_docx(tmp_path, {
        "Legal First Name": "Carol",
        "Email": "carol@example.com",
    })
    result = extract_docx(path)
    assert result.questionnaire_type is QuestionnaireType.UNKNOWN
    assert any("type" in w.lower() for w in result.warnings)


def test_docx_extractor_routes_via_extractor_extract(tmp_path):
    """Extractor.extract() must route .docx files to the docx extractor."""
    from adhd_intake.extraction.extractor import Extractor
    from adhd_intake.extraction.classifier import PdfClassifier

    path = _make_docx(tmp_path, {
        "Adult ADHD Centre Assessment": "tool",
        "Legal First Name": "Dave",
        "Email": "dave@example.com",
    })
    extractor = Extractor(classifier=PdfClassifier())
    result = extractor.extract(path)
    # Should not raise and should return an ExtractionResult
    assert result is not None
    assert result.demographics.first_name == "Dave"


def test_docx_referral_source_extracted(tmp_path):
    from adhd_intake.extraction.docx_extractor import extract_docx

    path = _make_docx(tmp_path, {
        "Adult ADHD Centre": "assessment",
        "How did you hear about us": "Social media",
        "Legal First Name": "Eve",
    })
    result = extract_docx(path)
    assert result.answers.get("referral_source") == "Social media"


# -------------------------------------------------------------------------
# Issue 1 — login dialog: verify worker signals (no Playwright required)
# -------------------------------------------------------------------------
def test_login_dialog_has_verify_button():
    """The login dialog module exposes _VerifySignals and _attempt_login."""
    from adhd_intake.dashboard import login_dialog
    assert hasattr(login_dialog, "_VerifySignals")
    assert hasattr(login_dialog, "_attempt_login")


def test_attempt_login_returns_false_on_bad_creds():
    """_attempt_login calls signals.result.emit(False, msg) when it cannot connect."""
    from adhd_intake.dashboard.login_dialog import _attempt_login

    results: list[tuple[bool, str]] = []

    class FakeResult:
        def emit(self, ok, msg):
            results.append((ok, msg))

    class FakeSignals:
        result = FakeResult()

    # Pass a fake signals object; call with a non-routable URL so it fails fast.
    _attempt_login("http://127.0.0.1:19999/kaiemr/#/", "x", "y", FakeSignals())
    assert len(results) == 1
    ok, msg = results[0]
    assert ok is False
    assert msg  # some error message present


# -------------------------------------------------------------------------
# OSCAR URL fix — _origin() and _classic_url()
# -------------------------------------------------------------------------
def test_oscar_client_origin_extraction():
    from adhd_intake.oscar.client import OscarClient, OscarSelectors
    from adhd_intake.config import OscarConfig

    config = OscarConfig(
        base_url="https://welcome.kai-oscar.com/kaiemr/#/",
        username="u", password="p",
    )
    client = OscarClient(config)
    assert client._origin() == "https://welcome.kai-oscar.com"
    assert client._classic_url("/demographic/demographiccontrol.jsp") == (
        "https://welcome.kai-oscar.com/oscar/demographic/demographiccontrol.jsp"
    )


def test_oscar_client_login_url():
    from adhd_intake.oscar.client import OscarClient
    from adhd_intake.config import OscarConfig

    config = OscarConfig(
        base_url="https://welcome.kai-oscar.com/kaiemr/#/",
        username="u", password="p",
    )
    client = OscarClient(config)
    # login_path is "" so the login URL is the base_url itself.
    assert client._login_url() == "https://welcome.kai-oscar.com/kaiemr/#/"
