"""Tesseract-based OCR engine.

Renders each PDF page to a high-DPI image with PyMuPDF (no external Poppler
dependency) and runs Tesseract over it. Handwritten content is inherently
lower-confidence; callers should treat the output as best-effort and rely on
the validation stage for the consent/signature decision.
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from ..config import OcrConfig
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class OcrError(RuntimeError):
    """Raised when OCR cannot be performed (e.g. Tesseract not installed)."""


class TesseractOcrEngine:
    """Wraps pytesseract with PDF page rendering."""

    def __init__(self, config: OcrConfig):
        self._config = config
        if config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = config.tesseract_cmd
        self._zoom = config.render_dpi / 72.0  # 72 dpi is the PDF base resolution

    def _ensure_available(self) -> None:
        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:  # EnvironmentError / pytesseract errors
            raise OcrError(
                f"Tesseract is not available at '{self._config.tesseract_cmd}'. "
                "Install Tesseract-OCR and set ocr.tesseract_cmd in config.yaml."
            ) from exc

    def render_page(self, doc: "fitz.Document", page_index: int) -> Image.Image:
        """Render a single PDF page to a Pillow image."""
        page = doc.load_page(page_index)
        matrix = fitz.Matrix(self._zoom, self._zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return Image.open(io.BytesIO(pix.tobytes("png")))

    def extract_text(self, pdf_path: Path) -> str:
        """OCR every page and return the concatenated text."""
        self._ensure_available()
        parts: list[str] = []
        with fitz.open(str(pdf_path)) as doc:
            for i in range(doc.page_count):
                image = self.render_page(doc, i)
                try:
                    page_text = pytesseract.image_to_string(
                        image, lang=self._config.language
                    )
                except Exception:
                    logger.exception("OCR failed on page %d of %s", i, pdf_path.name)
                    page_text = ""
                parts.append(page_text)
        text = "\n".join(parts)
        logger.info("OCR extracted %d chars from %s", len(text), pdf_path.name)
        return text
