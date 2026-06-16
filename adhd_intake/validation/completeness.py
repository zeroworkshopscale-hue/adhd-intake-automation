"""Response-completeness validation for the questionnaire pages.

The Adult ADHD Centre assessment tool (pages 6-11) and the ADHD Centre for
Women tool (pages 6-12) present their questions as rows, each with a set of
response columns (e.g. *Never or Rarely / Sometimes / Often or Very Often*, or
*Yes / No*). Every question row must carry at least one response mark — an X, a
check, a slash, a dot, a circle, or any other visible mark.

This validator flags rows that have **no** mark in any response column and
reports which page(s) the gaps fall on. It is intentionally tolerant: if a page
cannot be parsed into a response grid it is skipped rather than mis-flagged, and
the operator always gets the final say (Approve & Continue vs Decline).

Detection strategy (robust to forms that print the response labels inside every
cell, and to empty checkbox outlines):

  1. Locate the response columns by searching for the known option labels and
     clustering their x-centres.
  2. For every question row, measure the ink fraction in each column cell.
  3. Compare each cell to a per-column *baseline* (a low percentile of that
     column's cells, i.e. the look of an un-marked cell). A cell stands out as
     marked only when its ink exceeds that baseline by a margin — so constant
     printed labels / box outlines cancel out and only a real mark registers.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

from ..config import ValidationConfig
from ..models import CompletenessResult, QuestionnaireType
from ..utils.logging_config import get_logger
from ..extraction import templates

logger = get_logger(__name__)

_RENDER_ZOOM = 200.0 / 72.0  # ~200 DPI is plenty for mark detection


class CompletenessValidator:
    """Checks that every question row on the assessment pages has a response."""

    def __init__(self, config: ValidationConfig):
        self._cfg = config

    # ------------------------------------------------------------------
    def validate(self, pdf_path: Path, qtype: QuestionnaireType) -> CompletenessResult:
        template = templates.template_for(qtype)
        if not self._cfg.check_completeness or not template or not template.validation_pages:
            return CompletenessResult(
                complete=True, checked=False,
                detail="completeness check not applicable for this document",
            )

        import fitz  # PyMuPDF (heavy import kept local)

        start, end = template.validation_pages
        incomplete_pages: list[int] = []
        total_unanswered = 0
        parsed_any = False

        try:
            with fitz.open(str(pdf_path)) as doc:
                for page_no in range(start, end + 1):  # 1-based, inclusive
                    idx = page_no - 1
                    if idx < 0 or idx >= doc.page_count:
                        continue
                    page = doc.load_page(idx)
                    try:
                        unanswered = self._check_page(page, template)
                    except Exception:
                        logger.debug("Completeness parse failed on page %d", page_no, exc_info=True)
                        unanswered = None
                    if unanswered is None:
                        continue  # page had no recognisable response grid
                    parsed_any = True
                    if unanswered > 0:
                        incomplete_pages.append(page_no)
                        total_unanswered += unanswered
        except Exception:
            logger.exception("Completeness validation could not open %s", pdf_path)
            return CompletenessResult(
                complete=True, checked=False, detail="could not open document for completeness check"
            )

        if not parsed_any:
            return CompletenessResult(
                complete=True, checked=False,
                detail="no response grid recognised on the questionnaire pages",
            )

        complete = not incomplete_pages
        detail = (
            "all question rows have a response"
            if complete
            else f"{total_unanswered} unanswered question row(s) on {len(incomplete_pages)} page(s)"
        )
        return CompletenessResult(
            complete=complete,
            incomplete_pages=incomplete_pages,
            unanswered_count=total_unanswered,
            checked=True,
            detail=detail,
        )

    # ------------------------------------------------------------------
    def _check_page(self, page, template) -> Optional[int]:
        """Return the count of unanswered question rows, or None if the page has
        no recognisable response grid (so it should be skipped, not flagged).

        These questionnaires are fillable PDFs: each response cell is an AcroForm
        field, so the authoritative signal is the field VALUE (a patient marks one
        per row). We read those directly; only when a page has no form fields
        (e.g. a flattened scan) do we fall back to measuring ink.
        """
        result = self._check_page_widgets(page)
        if result is not None:
            return result
        return self._check_page_ink(page, template)

    # --- 1) authoritative: AcroForm field values ----------------------
    def _check_page_widgets(self, page) -> Optional[int]:
        """Read the response fields. A question row is answered when ANY of its
        response cells holds a value; a row with all cells empty is unanswered.
        Returns None if the page has no response-grid fields."""
        import fitz

        widgets = list(page.widgets() or [])
        if not widgets:
            return None

        # Response fields live in the right portion of the page; the question
        # text on the left carries no fields. Keep only fields in that band.
        x_min = page.rect.width * 0.42
        cells: list[tuple[float, float, bool]] = []  # (y0, x0, filled)
        for w in widgets:
            r = w.rect
            if r.x0 < x_min:
                continue
            cells.append((r.y0, r.x0, self._widget_filled(w)))
        if len(cells) < 4:  # too few to be a response grid
            return None

        # Group fields into rows by y (cluster within ~7pt).
        cells.sort(key=lambda c: c[0])
        rows: list[list[tuple[float, float, bool]]] = [[cells[0]]]
        for c in cells[1:]:
            if c[0] - rows[-1][0][0] <= 7.0:
                rows[-1].append(c)
            else:
                rows.append([c])

        unanswered = 0
        real_rows = 0
        for row in rows:
            if len(row) < 2:  # a response row has >= 2 option columns
                continue
            real_rows += 1
            if not any(filled for _, _, filled in row):
                unanswered += 1
                logger.debug("unanswered widget row y=%.0f (%d cols)", row[0][0], len(row))
        if real_rows == 0:
            return None
        return unanswered

    @staticmethod
    def _widget_filled(w) -> bool:
        """True if a response field carries the patient's mark."""
        import fitz

        val = "" if w.field_value is None else str(w.field_value).strip()
        if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
            return val.lower() not in ("", "off", "no", "0", "false")
        # Text / combo / radio: any non-empty value is a response.
        return bool(val)

    # --- 2) fallback: ink in the response grid (scanned forms) --------
    def _check_page_ink(self, page, template) -> Optional[int]:
        """Return the count of unanswered question rows, or None if the page has
        no recognisable response grid (so it should be skipped, not flagged)."""
        import fitz
        import numpy as np
        from PIL import Image

        centers = self._response_column_centers(page, template)
        if len(centers) < 2:
            return None

        # Column cell width: the median spacing between adjacent columns.
        gaps = [b - a for a, b in zip(centers, centers[1:]) if b > a]
        cell_w = (sorted(gaps)[len(gaps) // 2] if gaps else 60.0)
        cell_w = max(18.0, min(cell_w, 120.0))
        half = cell_w / 2.0

        band_left = centers[0] - half
        band_right = min(centers[-1] + half, page.rect.width - 2)

        rows = self._question_rows(page, band_left)
        if not rows:
            return None

        top = max(page.rect.y0, rows[0][0] - 2)
        bottom = min(page.rect.height, rows[-1][1] + 2)
        clip = fitz.Rect(max(page.rect.x0, band_left), top, band_right, bottom)
        if clip.width < 10 or clip.height < 10:
            return None

        pix = page.get_pixmap(matrix=fitz.Matrix(_RENDER_ZOOM, _RENDER_ZOOM), clip=clip, alpha=False)
        if pix.width == 0 or pix.height == 0:
            return None
        gray = np.asarray(
            Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        )
        h_px, w_px = gray.shape

        def to_px_x(x: float) -> int:
            return int(round((x - clip.x0) * _RENDER_ZOOM))

        def to_px_y(y: float) -> int:
            return int(round((y - clip.y0) * _RENDER_ZOOM))

        # Ink fraction for every (row, column) cell.
        n_rows, n_cols = len(rows), len(centers)
        ink = np.zeros((n_rows, n_cols), dtype=float)
        for ri, (ry0, ry1) in enumerate(rows):
            py0 = max(0, to_px_y(ry0))
            py1 = min(h_px, to_px_y(ry1))
            if py1 - py0 < 2:
                py1 = min(h_px, py0 + 2)
            for ci, cx in enumerate(centers):
                px0 = max(0, to_px_x(cx - half))
                px1 = min(w_px, to_px_x(cx + half))
                if px1 - px0 < 2 or py1 - py0 < 2:
                    continue
                cell = gray[py0:py1, px0:px1]
                if cell.size:
                    ink[ri, ci] = float(np.count_nonzero(cell < 128)) / cell.size

        # Global baseline = the look of an un-marked cell. Each row carries at
        # most one mark, so most cells on the page are blank; a low percentile of
        # all cells therefore reflects an empty cell (whether that's ~0 for open
        # cells or the constant ink of a uniform empty checkbox/box outline).
        # This stays correct even when many rows pick the *same* column.
        baseline = float(np.percentile(ink, 15)) if ink.size else 0.0
        min_ink = self._cfg.response_min_ink
        margin = self._cfg.response_rel_margin

        unanswered = 0
        for ri in range(n_rows):
            row_ink = ink[ri]
            marked = (row_ink >= min_ink) & ((row_ink - baseline) >= margin)
            if not marked.any():
                unanswered += 1
                logger.debug(
                    "unanswered row y=%.0f ink=%s baseline=%.4f",
                    rows[ri][0], np.round(row_ink, 4), baseline,
                )
        return unanswered

    # ------------------------------------------------------------------
    def _response_column_centers(self, page, template) -> list[float]:
        """X-centres of the response columns. Tries each label set in order and
        returns the first that locates >= 2 distinct columns, so whole-phrase
        headers are preferred over their constituent words."""
        for label_set in template.response_label_sets:
            xs: list[float] = []
            for label in label_set:
                for r in page.search_for(label) or []:
                    xs.append((r.x0 + r.x1) / 2.0)
            centers = self._cluster(xs)
            if len(centers) >= 2:
                return centers
        return []

    @staticmethod
    def _cluster(xs: list[float], tol: float = 28.0) -> list[float]:
        """Collapse x-centres within ``tol`` points into single columns."""
        if not xs:
            return []
        xs = sorted(xs)
        clusters: list[list[float]] = [[xs[0]]]
        for x in xs[1:]:
            if x - clusters[-1][-1] <= tol:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        return [sum(c) / len(c) for c in clusters]

    @staticmethod
    def _question_rows(page, band_left: float) -> list[tuple[float, float]]:
        """Y-spans of question rows: text lines that begin in the left (question)
        column. Tightly-spaced lines (wrapped questions) are merged so the
        response mark on the first line still counts for the whole question."""
        groups: dict = defaultdict(list)
        for x0, y0, x1, y1, word, block, line, _wno in page.get_text("words"):
            groups[(block, line)].append((x0, y0, x1, y1, word))

        lines = []
        for ws in groups.values():
            ws.sort(key=lambda r: r[0])
            text = " ".join(w[4] for w in ws).strip()
            lx0 = min(w[0] for w in ws)
            ly0 = min(w[1] for w in ws)
            ly1 = max(w[3] for w in ws)
            # A question line starts left of the response band and is a real
            # statement (enough letters), not a stray column header.
            letters = sum(ch.isalpha() for ch in text)
            if lx0 < band_left - 6 and len(text) >= 12 and letters >= 8:
                lines.append((ly0, ly1))
        if not lines:
            return []
        lines.sort()

        heights = sorted(b - a for a, b in lines if b > a)
        h = heights[len(heights) // 2] if heights else 12.0
        merge_gap = 0.5 * h

        merged: list[list[float]] = [list(lines[0])]
        for y0, y1 in lines[1:]:
            if y0 - merged[-1][1] <= merge_gap:
                merged[-1][1] = max(merged[-1][1], y1)
            else:
                merged.append([y0, y1])
        return [(a, b) for a, b in merged]
