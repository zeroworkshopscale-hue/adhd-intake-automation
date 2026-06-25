"""Validate that the consent page carries a signature or initials.

Hard gate: if no signature is detected the pipeline rejects the file and never
uploads to OSCAR or writes to the sheet.

Detection (first positive wins), all scoped to the **signature area** of the
consent page so printed body text can't masquerade as a signature:

  1. **Signature form field** — an AcroForm signature/initials field that is
     signed or carries a value.
  2. **Typed signature** — a "Signature:"/"Initials:" line followed by typed
     letters.
  3. **Handwritten / drawn / pasted signature** — ink measured specifically in
     the region beside the "Patient signature" label. A blank signature line
     scores low; an actual signature scores well above ``min_ink_density``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

from ..config import ValidationConfig
from ..models import SignatureValidationResult
from ..ocr.engine import TesseractOcrEngine
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# Label that marks the signature line on these consent forms.
_SIG_LABEL = "Patient signature"
_SIG_FALLBACK_LABELS = ("Signature", "Sign here", "Signed")
_SIG_TEXT_LABELS = ("patient signature", "signature", "signed by", "initials", "initial")
_MIN_TYPED_LETTERS = 2


class SignatureValidator:
    """Decides whether the consent page is signed."""

    def __init__(self, config: ValidationConfig, ocr_engine: Optional[TesseractOcrEngine] = None):
        self._config = config
        self._ocr = ocr_engine

    def validate(self, pdf_path: Path) -> SignatureValidationResult:
        with fitz.open(str(pdf_path)) as doc:
            page_index = self._locate_consent_page(doc)
            page = doc.load_page(page_index)

            # 1) Digital / form-field signature.
            result = self._check_form_field(page, page_index)
            if result is not None:
                return result

            # 2) Typed signature/initials in the text layer.
            result = self._check_typed_signature(page, page_index)
            if result is not None:
                return result

            # 3) Handwritten / drawn / pasted signature: ink in the signature
            #    region only (not the whole page).
            density = self._signature_region_density(page)
            if density is not None:
                threshold = self._config.min_ink_density
                signed = density >= threshold
                return SignatureValidationResult(
                    signed=bool(signed),
                    consent_page_index=page_index,
                    method="signature-region-ink",
                    ink_density=density,
                    detail=f"signature-region ink {density:.4f} "
                           f"({'>=' if signed else '<'} threshold {threshold})",
                )

            # 4) Scanned / image-only consent page (no text layer): OCR to find
            #    the signature label, then measure the ink beside it.
            result = self._check_scanned_signature(page, page_index)
            if result is not None:
                return result

            return SignatureValidationResult(
                signed=False,
                consent_page_index=page_index,
                method="signature-region-ink",
                ink_density=None,
                detail="no signature line found on the consent page",
            )

    # ------------------------------------------------------------------
    def _locate_consent_page(self, doc: "fitz.Document") -> int:
        """Prefer the last page with a 'Patient signature' line; else the last
        page mentioning a consent keyword; else the final page."""
        sig_page = None
        kw_page = None
        scanned_page = None
        for i in range(doc.page_count):
            page = doc.load_page(i)
            text = page.get_text("text").lower()
            if "patient signature" in text or "signature" in text:
                sig_page = i
            if any(kw in text for kw in self._config.consent_keywords):
                kw_page = i
            # An image-only page with almost no text layer is very likely a
            # scanned consent/signature page (the label is in the image, not text).
            if len(text.strip()) < 30 and page.get_images():
                scanned_page = i
        if sig_page is not None:
            return sig_page
        if scanned_page is not None:
            return scanned_page
        if kw_page is not None:
            return kw_page
        return doc.page_count - 1

    # --- 1) form / digital signature ----------------------------------
    @staticmethod
    def _check_form_field(page, page_index: int) -> Optional[SignatureValidationResult]:
        for widget in page.widgets() or []:
            name = (widget.field_name or "").lower()
            value = (widget.field_value or "").strip()
            is_sig_type = widget.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE
            is_named_sig = "sign" in name or "initial" in name
            if is_sig_type and bool(getattr(widget, "is_signed", False)):
                return SignatureValidationResult(
                    signed=True, consent_page_index=page_index, method="digital-signature",
                    detail=f"digital signature field '{widget.field_name}' is signed",
                )
            if (is_sig_type or is_named_sig) and value:
                return SignatureValidationResult(
                    signed=True, consent_page_index=page_index, method="form-field",
                    detail=f"signature field '{widget.field_name}' is populated",
                )
        return None

    # --- 2) typed signature / initials --------------------------------
    # "Patient signature or initials: <value>" — capture the value after the
    # colon; a trailing "Date:" label (same-line) is not a value.
    _SIG_LINE_RE = re.compile(
        r"(?:patient\s+)?signature(?:\s+or\s+initials)?\s*[:\-]\s*(?P<v>.*)",
        re.IGNORECASE,
    )

    def _check_typed_signature(self, page, page_index: int) -> Optional[SignatureValidationResult]:
        for raw_line in page.get_text("text").splitlines():
            m = self._SIG_LINE_RE.search(raw_line)
            if not m:
                continue
            value = re.split(r"\bdate\b", m.group("v"), flags=re.IGNORECASE)[0]
            value = value.strip().strip("_-–—").strip()
            letters = re.findall(r"[A-Za-z]", value)
            if len(letters) >= _MIN_TYPED_LETTERS and len(value) <= 40:
                return SignatureValidationResult(
                    signed=True, consent_page_index=page_index, method="typed-text",
                    detail=f"typed signature/initials: {value[:40]!r}",
                )
        return None

    # --- 3) ink in the signature region only --------------------------
    def _signature_region_density(self, page) -> Optional[float]:
        """Dark-pixel fraction in the band beside the signature label, stopping
        at a same-line 'Date:' label so an empty signature line stays empty."""
        rects = (
            page.search_for("Patient signature or initials")
            or page.search_for(_SIG_LABEL)
            or page.search_for("signature or initials")
        )
        if not rects:
            for lbl in _SIG_FALLBACK_LABELS:
                rects = page.search_for(lbl)
                if rects:
                    break
        if not rects:
            return None
        r = rects[-1]  # the actual signature line (last occurrence)

        # Right boundary: a "Date" label on the same line, else the page margin.
        right = page.rect.width - 36
        for dr in page.search_for("Date"):
            if abs(dr.y0 - r.y0) < 6 and dr.x0 > r.x1:
                right = min(right, dr.x0 - 2)

        region = fitz.Rect(r.x1 + 2, r.y0 - 10, right, r.y1 + 14)
        if region.width < 20 or region.height < 6:
            return None
        zoom = 300 / 72.0
        # annots=True so a signature drawn as an annotation/appearance (common on
        # these fillable consent PDFs) is counted, not just page-content ink.
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=region, alpha=False, annots=True)
        if pix.width == 0 or pix.height == 0:
            return None
        arr = np.asarray(
            Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        )
        density = float(np.count_nonzero(arr < 128) / arr.size)
        logger.debug("signature-region density = %.4f", density)
        return density

    # --- 4) scanned / image-only consent page: OCR the label, measure ink -----
    # Scanned cursive is thinner over a wider band than a fillable-field
    # signature, so an empty line reads ~0 and a real signature ~0.02-0.03.
    _SCANNED_SIG_THRESHOLD = 0.012

    def _check_scanned_signature(self, page, page_index: int) -> Optional[SignatureValidationResult]:
        """For an image-only consent page, OCR to locate the 'signature' label
        and measure the dark-pixel fraction in the band to its right."""
        if self._ocr is None:
            return None
        try:
            import pytesseract
            from pytesseract import Output
        except Exception:
            return None

        zoom = 200 / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, annots=True)
        if pix.width == 0 or pix.height == 0:
            return None
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        gray = np.asarray(img.convert("L"))
        try:
            data = pytesseract.image_to_data(img, output_type=Output.DICT)
        except Exception:
            logger.debug("Scanned-signature OCR failed", exc_info=True)
            return None

        sig = None
        date = None
        for i, word in enumerate(data["text"]):
            wl = (word or "").strip().lower()
            if not wl:
                continue
            if "signature" in wl and sig is None:
                sig = (data["left"][i], data["top"][i], data["width"][i], data["height"][i])
            elif wl.startswith("date") and sig is not None and date is None:
                if abs(data["top"][i] - sig[1]) <= 25:   # same line as the label
                    date = data["left"][i]
        if sig is None:
            return None

        x0 = sig[0] + sig[2] + 8
        y0 = max(0, sig[1] - 18)
        y1 = min(gray.shape[0], sig[1] + sig[3] + 18)
        x1 = (date - 8) if date else int(pix.width * 0.95)
        if x1 - x0 < 30:
            x1 = min(gray.shape[1], x0 + 200)
        region = gray[y0:y1, max(0, x0):min(gray.shape[1], x1)]
        if region.size == 0:
            return None
        density = float(np.count_nonzero(region < 128) / region.size)
        signed = density >= self._SCANNED_SIG_THRESHOLD
        logger.info("scanned-signature region ink = %.4f (page %d)", density, page_index + 1)
        return SignatureValidationResult(
            signed=signed,
            consent_page_index=page_index,
            method="scanned-ocr-ink",
            ink_density=density,
            detail=f"scanned signature-region ink {density:.4f} "
                   f"({'>=' if signed else '<'} {self._SCANNED_SIG_THRESHOLD})",
        )
