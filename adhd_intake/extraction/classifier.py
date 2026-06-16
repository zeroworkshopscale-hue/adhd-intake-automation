"""Classify a PDF as *fillable* or *scanned*.

Heuristics, in order:

  1. If the PDF exposes AcroForm fields with values, it is **fillable**.
  2. If pages contain a meaningful extractable text layer, it is **fillable**
     (a typed/flattened form we can still read without OCR).
  3. Otherwise (image-only pages, negligible text) it is **scanned** and will
     require OCR downstream.

Uses PyMuPDF (``fitz``) which is fast and exposes both text and image inventory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from ..models import PdfKind
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# Below this many extractable characters across the document, we treat the text
# layer as unusable and fall back to "scanned".
_MIN_TEXT_CHARS = 80


@dataclass
class ClassificationResult:
    kind: PdfKind
    has_form_fields: bool
    text_char_count: int
    page_count: int
    text: str            # concatenated text layer (may be empty for scanned)
    form_values: dict[str, str]
    detail: str = ""


class PdfClassifier:
    """Determine whether a PDF can be read directly or needs OCR."""

    def classify(self, pdf_path: Path) -> ClassificationResult:
        with fitz.open(str(pdf_path)) as doc:
            form_values = self._extract_form_values(doc)
            text = self._extract_text(doc)
            page_count = doc.page_count

        text_chars = len(text.strip())
        has_fields = bool(form_values)

        if has_fields:
            kind = PdfKind.FILLABLE
            detail = f"{len(form_values)} AcroForm field(s) with values"
        elif text_chars >= _MIN_TEXT_CHARS:
            kind = PdfKind.FILLABLE
            detail = f"text layer present ({text_chars} chars)"
        else:
            kind = PdfKind.SCANNED
            detail = f"no form fields, only {text_chars} chars of text"

        logger.info("Classified %s as %s (%s)", pdf_path.name, kind.value, detail)
        return ClassificationResult(
            kind=kind,
            has_form_fields=has_fields,
            text_char_count=text_chars,
            page_count=page_count,
            text=text,
            form_values=form_values,
            detail=detail,
        )

    @staticmethod
    def _extract_form_values(doc: "fitz.Document") -> dict[str, str]:
        values: dict[str, str] = {}
        for page in doc:
            for widget in page.widgets() or []:
                name = (widget.field_name or "").strip()
                value = (widget.field_value or "")
                if name and str(value).strip():
                    values[name] = str(value).strip()
        return values

    @staticmethod
    def _extract_text(doc: "fitz.Document") -> str:
        parts: list[str] = []
        for page in doc:
            parts.append(page.get_text("text"))
        return "\n".join(parts)
