"""Response-completeness validation for the questionnaire pages.

The Adult ADHD Centre assessment tool (pages 6-11) and the ADHD Centre for
Women tool (pages 6-12) present their questions as rows, each with a set of
response columns (e.g. *Never or Rarely / Sometimes / Often or Very Often*, or
*Yes / No*). Every question row must carry at least one response mark.

Detection strategy:

  1. AcroForm field values are authoritative for fillable PDFs.
  2. For flattened/scanned forms, ink density is measured per cell.
  3. Thresholds are adaptive per page — later pages (8+) use a lower minimum
     to account for lighter printing or different fill patterns.
  4. As a final fallback the entire response-band strip of the row is checked
     for any ink: if anything dark appears anywhere in that strip the row is
     counted as answered.
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

_RENDER_ZOOM = 200.0 / 72.0  # ~200 DPI


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

        import fitz

        start, end = template.validation_pages
        incomplete_pages: list[int] = []
        unanswered_questions: list[str] = []
        blank_section_pages: list[int] = []
        parsed_any = False

        try:
            with fitz.open(str(pdf_path)) as doc:
                for page_no in range(start, end + 1):
                    idx = page_no - 1
                    if idx < 0 or idx >= doc.page_count:
                        continue
                    page = doc.load_page(idx)
                    try:
                        unanswered = self._check_page(page, template, page_no)
                    except Exception:
                        logger.debug("Completeness parse failed on page %d", page_no, exc_info=True)
                        unanswered = None
                    if unanswered is None:
                        continue
                    labels, total_rows = unanswered
                    parsed_any = True
                    if labels:
                        incomplete_pages.append(page_no)
                        unanswered_questions.extend(f"Page {page_no}: {q}" for q in labels)
                        if total_rows > 0 and len(labels) >= total_rows:
                            blank_section_pages.append(page_no)
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
            else f"{len(unanswered_questions)} unanswered question row(s) on "
                 f"{len(incomplete_pages)} page(s): " + "; ".join(unanswered_questions)
        )
        return CompletenessResult(
            complete=complete,
            incomplete_pages=incomplete_pages,
            unanswered_count=len(unanswered_questions),
            unanswered_questions=unanswered_questions,
            blank_section_pages=blank_section_pages,
            checked=True,
            detail=detail,
        )

    # ------------------------------------------------------------------
    def _check_page(self, page, template, page_no: int = 0) -> Optional[tuple[list[str], int]]:
        """Return (unanswered question labels, total question rows), or None if
        no grid is found on the page."""
        widget_result, any_filled = self._check_page_widgets(page)
        # Trust the AcroForm only when it actually carries answers. If the page
        # has widgets but NONE are filled, the form may be image-overlay (the
        # patient's marks are in a flattened page image over leftover empty
        # widgets) -- so let ink detection look first.
        if widget_result is not None and any_filled:
            return widget_result
        ink_result = self._check_page_ink(page, template, page_no)
        if ink_result is not None:
            return ink_result
        # Ink found no marks (e.g. a genuinely blank fillable template): fall back
        # to the widget read, which flags every empty row as unanswered.
        return widget_result

    # Optional questionnaire sub-sections: when EVERY row in the section is blank,
    # treat the whole section as not-applicable (e.g. School for a patient who is
    # not a student -- many adults are not in school) instead of flagging each
    # row. A partially-answered optional section is still validated normally.
    _OPTIONAL_SECTION_HEADERS = ("school",)
    # Recognised sub-section headers, used only to find where a section ends.
    _SECTION_HEADERS = (
        "family", "work", "school", "home", "social", "relationships",
    )

    @staticmethod
    def _section_header_lines(page) -> list[tuple[float, str]]:
        """(y0, lowercased exact line text) for every text line on the page, used
        to locate single-word section headers like 'School' or 'Work'."""
        groups: dict = defaultdict(list)
        for x0, y0, x1, y1, word, block, line, _wno in page.get_text("words"):
            groups[(block, line)].append((x0, y0, word))
        out: list[tuple[float, str]] = []
        for ws in groups.values():
            ws.sort(key=lambda r: r[0])
            text = " ".join(w[2] for w in ws).strip().lower()
            out.append((min(w[1] for w in ws), text))
        return out

    @classmethod
    def _optional_blank_rows(cls, page, row_geoms, answered) -> set[int]:
        """Indices of question rows inside an OPTIONAL section that has no answered
        rows at all -> exempt from the unanswered list (treat as not-applicable).

        row_geoms: [(y0, y1)] per row; answered: [bool] per row, same order.
        """
        lines = cls._section_header_lines(page)
        section_ys = sorted(y for y, t in lines if t in cls._SECTION_HEADERS)
        exempt: set[int] = set()
        for y, t in lines:
            if t not in cls._OPTIONAL_SECTION_HEADERS:
                continue
            below = [sy for sy in section_ys if sy > y + 1]
            end = min(below) if below else float("inf")
            idxs = [i for i, (ry0, _ry1) in enumerate(row_geoms) if y - 2 <= ry0 < end]
            if idxs and not any(answered[i] for i in idxs):
                exempt.update(idxs)
                logger.info(
                    "Completeness: optional section '%s' entirely blank -> "
                    "treating as not-applicable (%d row(s))", t, len(idxs),
                )
        return exempt

    @staticmethod
    def _row_label(page, y0: float, y1: float, x_max: float) -> str:
        """Read the question text (left of the response band) for a row, e.g.
        '6 I am late for class.' — used to name an unanswered question.

        Collects every word on the row's own text line (the number and the
        question text are separate PDF groups), so an adjacent question on the
        line below is never folded in.
        """
        near = [
            (wx0, wd)
            for wx0, wy0, wx1, wy1, wd, *_ in page.get_text("words")
            if wx1 <= x_max + 2 and (y0 - 6) <= wy0 <= (y0 + 8)
        ]
        near.sort(key=lambda t: t[0])
        return " ".join(w for _, w in near).strip()[:120]

    # --- 1) authoritative: AcroForm field values ----------------------
    def _check_page_widgets(self, page) -> tuple[Optional[tuple[list[str], int]], bool]:
        """Return ((unanswered labels, total rows) | None, any_widget_filled).

        ``any_widget_filled`` lets the caller decide whether to trust this read
        or defer to ink detection (image-overlay forms leave every widget empty).
        """
        import fitz

        widgets = list(page.widgets() or [])
        if not widgets:
            return None, False

        x_min = page.rect.width * 0.42
        cells: list[tuple[float, float, float, bool]] = []
        for w in widgets:
            r = w.rect
            if r.x0 < x_min:
                continue
            cells.append((r.y0, r.y1, r.x0, self._widget_filled(w)))
        if len(cells) < 4:
            return None, False

        cells.sort(key=lambda c: c[0])
        rows: list[list[tuple[float, float, float, bool]]] = [[cells[0]]]
        for c in cells[1:]:
            if c[0] - rows[-1][0][0] <= 7.0:
                rows[-1].append(c)
            else:
                rows.append([c])

        geom: list[tuple[float, float]] = []
        row_answered: list[bool] = []
        row_label: list[str] = []
        any_filled = False
        for row in rows:
            if len(row) < 2:
                continue
            ry0 = min(c[0] for c in row)
            ry1 = max(c[1] for c in row)
            filled = any(f for _, _, _, f in row)
            if filled:
                any_filled = True
                label = ""
            else:
                label = self._row_label(page, ry0, ry1, x_min) or f"question row at y={int(ry0)}"
                logger.debug("unanswered widget row y=%.0f (%d cols): %s", ry0, len(row), label)
            geom.append((ry0, ry1))
            row_answered.append(filled)
            row_label.append(label)
        real_rows = len(geom)
        if real_rows == 0:
            return None, False
        # Exempt a wholly-blank OPTIONAL section (e.g. School) from the flags.
        exempt = self._optional_blank_rows(page, geom, row_answered)
        unanswered = [
            row_label[i] for i in range(real_rows)
            if not row_answered[i] and i not in exempt
        ]
        if not any_filled:
            # No widget on the page carries a value: either a genuinely blank
            # fillable template, or an image-overlay form whose marks live in a
            # flattened page image over vestigial empty widgets. The caller tries
            # ink first (which catches the overlay marks) and only falls back to
            # this all-unanswered read when ink finds nothing.
            logger.info(
                "Completeness: %d widget rows, none filled; caller will try ink "
                "first (image-overlay/flattened form?)", real_rows,
            )
        return (unanswered, real_rows), any_filled

    @staticmethod
    def _widget_filled(w) -> bool:
        import fitz

        val = "" if w.field_value is None else str(w.field_value).strip()
        if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
            return val.lower() not in ("", "off", "no", "0", "false")
        return bool(val)

    # --- 2) fallback: ink measurement (scanned / flattened forms) -----
    def _check_page_ink(self, page, template, page_no: int = 0) -> Optional[tuple[list[str], int]]:
        """Return the count of unanswered question rows, or None if no grid found.

        Adaptive thresholds: pages 8 and beyond often have lighter printing, so
        both the minimum-ink requirement and the relative margin are scaled down.
        The final safety net is a row-wide ink check: if ANY dark pixel appears
        anywhere in the response band for a row it is counted as answered.
        """
        import fitz
        import numpy as np
        from PIL import Image

        centers = self._response_column_centers(page, template)
        if len(centers) < 2:
            return None

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

        n_rows, n_cols = len(rows), len(centers)
        ink = np.zeros((n_rows, n_cols), dtype=float)
        for ri, (ry0, ry1, _txt) in enumerate(rows):
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

        # Adaptive thresholds: pages 8+ have lighter printing.
        page_factor = 0.5 if page_no >= 8 else (0.7 if page_no >= 7 else 1.0)
        min_ink = self._cfg.response_min_ink * page_factor
        margin = self._cfg.response_rel_margin * page_factor
        # Very low threshold for the row-wide fallback check.
        row_wide_threshold = max(0.002, min_ink * 0.25)

        baseline = float(np.percentile(ink, 15)) if ink.size else 0.0

        is_later_page = page_no >= 7
        log_fn = logger.info if is_later_page else logger.debug

        log_fn(
            "Page %d completeness: %d rows, %d cols, baseline=%.4f, "
            "min_ink=%.4f (factor=%.1f), margin=%.4f",
            page_no, n_rows, n_cols, baseline, min_ink, page_factor, margin,
        )

        answered = [False] * n_rows
        for ri in range(n_rows):
            row_ink = ink[ri]

            # Per-column cell check.
            marked = (row_ink >= min_ink) & ((row_ink - baseline) >= margin)

            log_fn(
                "  Page %d row %d y=%.0f: ink=%s baseline=%.4f marked=%s",
                page_no, ri, rows[ri][0],
                "[" + " ".join(f"{v:.4f}" for v in row_ink) + "]",
                baseline, marked.any(),
            )

            if marked.any():
                answered[ri] = True
                continue

            # Row-wide fallback: any ink in the whole response band strip?
            py0 = max(0, to_px_y(rows[ri][0]))
            py1 = min(h_px, to_px_y(rows[ri][1]))
            if py1 > py0:
                row_strip = gray[py0:py1, :]
                row_density = float(np.count_nonzero(row_strip < 128)) / max(row_strip.size, 1)
                log_fn(
                    "    Page %d row %d fallback strip density=%.4f (threshold=%.4f)",
                    page_no, ri, row_density, row_wide_threshold,
                )
                if row_density >= row_wide_threshold:
                    answered[ri] = True
                    continue

        # Exempt a wholly-blank OPTIONAL section (e.g. School) before flagging.
        exempt = self._optional_blank_rows(
            page, [(rows[i][0], rows[i][1]) for i in range(n_rows)], answered
        )
        unanswered: list[str] = []
        for ri in range(n_rows):
            if answered[ri] or ri in exempt:
                continue
            label = (rows[ri][2] or "").strip()[:120] or f"question row at y={int(rows[ri][0])}"
            unanswered.append(label)
            logger.info(
                "  UNANSWERED: page %d row %d y=%.0f (%s) ink=%s",
                page_no, ri, rows[ri][0], label,
                "[" + " ".join(f"{v:.4f}" for v in ink[ri]) + "]",
            )
        return unanswered, n_rows

    # ------------------------------------------------------------------
    def _response_column_centers(self, page, template) -> list[float]:
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
    def _is_section_header(text: str) -> bool:
        """True for section headers / instructions that are NOT answerable rows,
        so the ink path doesn't flag them as 'unanswered' on flattened forms.

        Covers: lines ending in a colon (sub-instructions like 'For a period
        lasting one week or more:'), ALL-CAPS headers ('QUESTIONS RELATED TO …'),
        and the 'Put an X … best describes your behaviour' instruction.
        """
        t = text.strip()
        if t.endswith(":"):
            return True
        low = t.lower()
        if "put an x" in low or "best describes" in low or "describes your behaviour" in low:
            return True
        alpha = [c for c in t if c.isalpha()]
        if len(alpha) >= 6 and sum(c.isupper() for c in alpha) / len(alpha) >= 0.8:
            return True
        return False

    @staticmethod
    def _question_rows(page, band_left: float) -> list[tuple[float, float, str]]:
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
            letters = sum(ch.isalpha() for ch in text)
            if (
                lx0 < band_left - 6 and len(text) >= 12 and letters >= 8
                and not CompletenessValidator._is_section_header(text)
            ):
                lines.append((ly0, ly1, text))
        if not lines:
            return []
        lines.sort()

        heights = sorted(b - a for a, b, _t in lines if b > a)
        h = heights[len(heights) // 2] if heights else 12.0
        merge_gap = 0.5 * h

        merged: list[list] = [list(lines[0])]
        for y0, y1, text in lines[1:]:
            if y0 - merged[-1][1] <= merge_gap:
                merged[-1][1] = max(merged[-1][1], y1)
                merged[-1][2] = (merged[-1][2] + " " + text).strip()
            else:
                merged.append([y0, y1, text])
        return [(a, b, t) for a, b, t in merged]
