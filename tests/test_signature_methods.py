"""Integration tests for multi-method signature detection.

These generate small PDFs with PyMuPDF, so they require fitz/numpy/Pillow to be
installed (unlike the dependency-free gate tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from adhd_intake.config import ValidationConfig  # noqa: E402
from adhd_intake.validation import SignatureValidator  # noqa: E402


def _consent_pdf(tmp_path: Path, name: str, extra_lines: list[tuple[str, tuple[int, int]]]) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Adult ADHD Centre - Consent and Declaration")
    page.insert_text((72, 400), "I agree and give my consent.")
    for text, (x, y) in extra_lines:
        page.insert_text((x, y), text)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def validator() -> SignatureValidator:
    # Signature-region ink threshold (calibrated default).
    return SignatureValidator(ValidationConfig(min_ink_density=0.04))


def test_typed_signature_detected(validator, tmp_path):
    pdf = _consent_pdf(tmp_path, "typed.pdf", [("Signature: Jane Doe", (72, 520))])
    result = validator.validate(pdf)
    assert result.signed
    assert result.method == "typed-text"


def test_typed_initials_detected(validator, tmp_path):
    pdf = _consent_pdf(
        tmp_path, "initials.pdf", [("Patient signature or initials: D.K.", (72, 520))]
    )
    result = validator.validate(pdf)
    assert result.signed
    assert result.method == "typed-text"


def test_forgot_signature_is_unsigned(validator, tmp_path):
    # The label is present but only "Date:" follows — the patient forgot to sign.
    pdf = _consent_pdf(
        tmp_path, "forgot.pdf", [("Patient signature or initials:            Date:", (72, 520))]
    )
    result = validator.validate(pdf)
    assert not result.signed


def test_empty_signature_line_is_unsigned(validator, tmp_path):
    pdf = _consent_pdf(tmp_path, "blank.pdf", [("Signature: ______________________", (72, 520))])
    result = validator.validate(pdf)
    assert not result.signed


def test_sentence_with_sign_word_is_unsigned(validator, tmp_path):
    pdf = _consent_pdf(tmp_path, "sentence.pdf", [("Please sign below this line", (72, 520))])
    result = validator.validate(pdf)
    assert not result.signed
