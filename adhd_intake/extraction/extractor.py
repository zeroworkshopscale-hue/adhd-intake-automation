"""Extract questionnaire type, demographics and answers from a PDF.

The extractor is OCR-aware but does **not** own OCR: if classification says the
file is scanned (or the text layer is too thin to find demographics), it calls
back into the injected ``ocr_engine`` to obtain text, then parses that text the
same way it parses a native text layer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Protocol

from ..models import (
    Demographics,
    ExtractionResult,
    PdfKind,
    QuestionnaireType,
    parse_dob_candidates,
)
from ..utils.logging_config import get_logger
from . import templates
from .classifier import ClassificationResult, PdfClassifier

logger = get_logger(__name__)


class OcrEngine(Protocol):
    """Minimal protocol the extractor needs from an OCR engine."""

    def extract_text(self, pdf_path: Path) -> str: ...


_LABELLED_VALUE = (
    # "Label: value" or "Label - value" up to end of line
    r"[:\-]\s*(?P<value>[^\n\r]+)"
)


class Extractor:
    """Turns a PDF into an :class:`ExtractionResult`."""

    def __init__(self, classifier: PdfClassifier, ocr_engine: Optional[OcrEngine] = None, clinic=None):
        self._classifier = classifier
        self._ocr = ocr_engine
        self._clinic = clinic  # ClinicConfig: clinic email/address to ignore

    def extract(self, pdf_path: Path) -> ExtractionResult:
        classification = self._classifier.classify(pdf_path)
        warnings: list[str] = []

        text = classification.text
        used_ocr = False
        confidence = 0.9 if classification.kind is PdfKind.FILLABLE else 0.0

        # Decide whether OCR is required.
        need_ocr = classification.kind is PdfKind.SCANNED or classification.text_char_count < 80
        if need_ocr:
            if self._ocr is None:
                warnings.append("OCR required but no OCR engine configured.")
                logger.warning("OCR needed for %s but no engine available", pdf_path.name)
            else:
                logger.info("Running OCR on %s", pdf_path.name)
                ocr_text = self._ocr.extract_text(pdf_path)
                if ocr_text.strip():
                    # Merge: form/text layer first, OCR appended.
                    text = f"{text}\n{ocr_text}".strip()
                    used_ocr = True
                    confidence = 0.6  # OCR-derived data is less certain
                else:
                    warnings.append("OCR produced no text.")

        qtype = templates.identify_type(text)
        if qtype is QuestionnaireType.UNKNOWN:
            warnings.append("Could not identify questionnaire type from content.")

        # Answers map for the copy-sheet "form:<key>" columns. AcroForm fields
        # are keyed by field name; flattened forms by their label text (read by
        # position). Both are merged so columns can reference either.
        answers = dict(classification.form_values)
        label_pairs: dict[str, str] = {}
        if not classification.form_values and not used_ocr:
            try:
                label_pairs = self._collect_label_pairs(
                    pdf_path, templates.template_for(qtype)
                )
                answers.update(label_pairs)
            except Exception:
                logger.debug("Label-pair collection failed", exc_info=True)

        demographics = self._extract_demographics(
            text, classification, qtype, label_pairs
        )
        if not demographics.has_minimum_identifiers():
            warnings.append("Insufficient demographic identifiers extracted.")

        if answers:
            logger.info("Extracted field keys (%d): %s",
                        len(answers), " | ".join(sorted(answers.keys()))[:600])

        return ExtractionResult(
            questionnaire_type=qtype,
            pdf_kind=classification.kind,
            demographics=demographics,
            answers=answers,
            raw_text=text,
            used_ocr=used_ocr,
            confidence=confidence,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    def _extract_demographics(
        self,
        text: str,
        classification: ClassificationResult,
        qtype: QuestionnaireType,
        label_pairs: Optional[dict] = None,
    ) -> Demographics:
        demo = Demographics()
        template = templates.template_for(qtype)

        # 1. Prefer AcroForm field values when present — most reliable.
        if classification.form_values and template:
            self._fill_from_form_fields(demo, classification.form_values, template)

        # 2. Position-based pairs (flattened/table forms): the value is in the
        #    column to the RIGHT of the label.
        if template and label_pairs:
            self._fill_from_pairs(demo, label_pairs, template)

        # 3. Fall back to label-based parsing of the text layer / OCR output.
        if template:
            self._fill_from_text(demo, text, template)

        # 3. Generic regex sweeps for email & DOB regardless of labels.
        if not demo.email:
            from ..models import _EMAIL_RE  # local import to avoid clutter

            m = _EMAIL_RE.search(text)
            if m:
                demo.email = m.group(0).strip()
        if not demo.dob:
            demo.dob = Demographics.normalise_dob(text)

        # Keep the original DOB string (patients write many formats) so the
        # matcher can try every plausible reading against OSCAR's canonical date;
        # store a normalised primary in `dob` for storage / Age / search.
        if demo.dob:
            if not demo.dob_raw:
                demo.dob_raw = demo.dob
            demo.dob = Demographics.normalise_dob(demo.dob) or demo.dob

        # Never treat the clinic's own email/address as the patient's.
        if self._clinic:
            if self._clinic.is_clinic_email(demo.email):
                logger.info("Ignoring clinic email found on the form.")
                demo.email = None
            if self._clinic.is_clinic_address(demo.address):
                logger.info("Ignoring clinic office address found on the form.")
                demo.address = None
        return demo

    @staticmethod
    def _fill_from_form_fields(
        demo: Demographics,
        form_values: dict[str, str],
        template: "templates.QuestionnaireTemplate",
    ) -> None:
        lowered = {k.lower(): v for k, v in form_values.items()}
        for canonical, candidates in template.demographic_fields.items():
            if getattr(demo, canonical, None):
                continue
            for label in candidates:
                for field_name, value in lowered.items():
                    if label in field_name:
                        setattr(demo, canonical, value.strip())
                        break
                if getattr(demo, canonical, None):
                    break

    @staticmethod
    def _collect_label_pairs(pdf_path: Path, template=None) -> dict[str, str]:
        """Position-based: pair each label line with the value in the column to
        its right (same row), across all pages. Also evaluates the template's
        checkbox questions -> "YES" when a mark is found, and derives a
        substance-use flag. Returns {key: value}.
        """
        import fitz  # PyMuPDF

        pairs: dict[str, str] = {}
        with fitz.open(str(pdf_path)) as doc:
            for page in doc:
                lines = Extractor._group_words_into_lines(page)
                for lab in lines:
                    if not lab["text"] or len(lab["text"]) > 60:
                        continue
                    rights = [
                        ln for ln in lines
                        if ln["x0"] > lab["x1"] + 3
                        and ln["y0"] < lab["y1"] and ln["y1"] > lab["y0"] and ln["text"]
                    ]
                    if rights:
                        rights.sort(key=lambda ln: ln["x0"])
                        pairs.setdefault(lab["text"].strip(), rights[0]["text"].strip())

                # Checkbox questions: any mark INSIDE the box -> "YES", else "NO".
                if template:
                    for key, anchor in template.checkbox_fields.items():
                        if key in pairs:
                            continue
                        rects = page.search_for(anchor)
                        if rects:
                            pairs[key] = "YES" if Extractor._checkbox_marked(page, rects[0]) else "NO"

                # Preferred pronoun (page 1): the selected option has a mark to
                # its left. Priority He/His if more than one is marked.
                if "pronoun" not in pairs:
                    pron = Extractor._detect_pronoun(page)
                    if pron:
                        pairs["pronoun"] = pron

        # Derived: substance use = YES if any substance row is marked.
        for sub in ("Alcohol", "Cannabis", "Other substances"):
            if pairs.get(sub, "").strip():
                pairs.setdefault("substance_use", "YES")
                break
        return pairs

    # Pronoun options in PRIORITY order (He/His wins if several are marked).
    _PRONOUN_OPTIONS = ("He/His", "She/Her", "They/Them")

    @staticmethod
    def _detect_pronoun(page) -> Optional[str]:
        """Return the selected pronoun (mark to the left of the option), giving
        priority to He/His when more than one is marked."""
        import re as _re

        words = page.get_text("words")  # (x0,y0,x1,y1,text,...)
        for option in Extractor._PRONOUN_OPTIONS:
            rects = page.search_for(option)
            if not rects:
                continue
            r = rects[0]
            cy = (r.y0 + r.y1) / 2
            # A mark token (x / X / ✓ / etc.) just left of the option, same row.
            for x0, y0, x1, y1, t, *_ in words:
                if x1 <= r.x0 and x0 >= r.x0 - 60 and abs((y0 + y1) / 2 - cy) <= 8:
                    s = t.strip()
                    if s and (_re.fullmatch(r"[xX✓√✔✗✘●•\*]+", s) or len(s) <= 2):
                        return option
            # Fallback: ink (a drawn mark) just left of the option.
            if Extractor._region_has_ink(page, r.x0 - 48, r.y0 - 2, r.x0 - 3, r.y1 + 2):
                return option
        return None

    @staticmethod
    def _region_has_ink(page, x0, y0, x1, y1, threshold: float = 0.02) -> bool:
        import fitz
        import numpy as np
        from PIL import Image

        if x1 - x0 < 4 or y1 - y0 < 4:
            return False
        region = fitz.Rect(max(page.rect.x0, x0), y0, x1, y1)
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72.0, 300 / 72.0), clip=region, alpha=False)
        if pix.width == 0 or pix.height == 0:
            return False
        arr = np.asarray(
            Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        )
        return float(np.count_nonzero(arr < 128) / arr.size) >= threshold

    @staticmethod
    def _checkbox_marked(page, anchor_rect) -> bool:
        """True if there is ANY user mark inside the checkbox for this consent
        statement. Finds the box rectangle near the statement's first line, then
        measures ink INSIDE it (inset to exclude the printed border) — so an
        empty box (outline only) reads as blank, and any check/X/scribble reads
        as marked, regardless of symbol.
        """
        import fitz
        import numpy as np
        from PIL import Image

        # Find the checkbox square: small rect in the left margin, on the line
        # just above the (2nd-line) anchor, entirely left of the anchor text.
        box = None
        best_area = 0.0
        for d in page.get_drawings():
            r = d.get("rect")
            if r is None:
                continue
            w, h = r.x1 - r.x0, r.y1 - r.y0
            if (
                8 <= w <= 34 and 8 <= h <= 34
                and r.x1 <= anchor_rect.x0
                and r.x0 < page.rect.x0 + 140
                and (anchor_rect.y0 - 34) <= r.y0 <= (anchor_rect.y0 + 8)
            ):
                area = w * h
                if area > best_area:
                    best_area, box = area, r
        if box is None:
            return False

        inset = 3.0  # exclude the printed border
        interior = fitz.Rect(box.x0 + inset, box.y0 + inset, box.x1 - inset, box.y1 - inset)
        if interior.width < 3 or interior.height < 3:
            return False
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72.0, 300 / 72.0), clip=interior, alpha=False)
        if pix.width == 0 or pix.height == 0:
            return False
        arr = np.asarray(
            Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        )
        density = float(np.count_nonzero(arr < 128) / arr.size)
        logger.debug("checkbox interior density=%.4f (anchor y=%.0f)", density, anchor_rect.y0)
        # Empty interior ~0; any mark is well above this.
        return density >= 0.02

    @staticmethod
    def _fill_from_pairs(
        demo: Demographics,
        pairs: dict[str, str],
        template: "templates.QuestionnaireTemplate",
    ) -> None:
        """Fill demographics from position-paired label->value entries."""
        items = list(pairs.items())
        for canonical, candidates in template.demographic_fields.items():
            if getattr(demo, canonical, None):
                continue
            for label in candidates:
                for key, value in items:
                    kl = key.lower()
                    if (kl == label or kl.startswith(label + " ") or kl.startswith(label + ":")) \
                            and Extractor._plausible_value(canonical, value):
                        setattr(demo, canonical, value)
                        break
                if getattr(demo, canonical, None):
                    break

    @staticmethod
    def _group_words_into_lines(page) -> list[dict]:
        from collections import defaultdict

        groups: dict = defaultdict(list)
        for x0, y0, x1, y1, word, block, line, _wno in page.get_text("words"):
            groups[(block, line)].append((x0, y0, x1, y1, word))
        lines = []
        for ws in groups.values():
            ws.sort(key=lambda r: r[0])
            lines.append(
                {
                    "x0": min(w[0] for w in ws),
                    "y0": min(w[1] for w in ws),
                    "x1": max(w[2] for w in ws),
                    "y1": max(w[3] for w in ws),
                    "text": " ".join(w[4] for w in ws).strip(),
                }
            )
        return lines

    @staticmethod
    def _value_right_of_label(lines: list[dict], label: str) -> Optional[str]:
        """Value line immediately to the right of a label line on the same row."""
        label_l = label.lower()
        for lab in lines:
            t = lab["text"].lower()
            if not (t == label_l or t.startswith(label_l + " ") or t.startswith(label_l + ":")):
                continue
            # Candidate value lines: vertically overlapping, to the right.
            rights = [
                ln for ln in lines
                if ln["x0"] > lab["x1"] + 3
                and ln["y0"] < lab["y1"] and ln["y1"] > lab["y0"]
                and ln["text"]
            ]
            if rights:
                rights.sort(key=lambda ln: ln["x0"])
                return rights[0]["text"].strip()
        return None

    @staticmethod
    def _fill_from_text(
        demo: Demographics,
        text: str,
        template: "templates.QuestionnaireTemplate",
    ) -> None:
        lines = [ln.strip() for ln in text.splitlines()]
        # Every label across all fields — used to avoid mistaking one label line
        # for another field's value in flattened forms.
        all_labels = {
            lbl.lower()
            for cands in template.demographic_fields.values()
            for lbl in cands
        }

        for canonical, candidates in template.demographic_fields.items():
            if getattr(demo, canonical, None):
                continue
            for label in candidates:
                # 1) Inline "Label: value" / "Label - value" on the same line.
                m = re.compile(re.escape(label) + _LABELLED_VALUE, re.IGNORECASE).search(text)
                if m:
                    value = re.split(r"\s{2,}", m.group("value").strip())[0].strip()
                    if value:
                        setattr(demo, canonical, value)
                        break
                # 2) Flattened forms: label on its own line, value on the next
                #    non-empty line (skip if that line is itself a field label,
                #    and only accept a value that's plausible for this field).
                value = Extractor._value_after_label(lines, label, all_labels)
                if value and Extractor._plausible_value(canonical, value):
                    setattr(demo, canonical, value)
                    break

    @staticmethod
    def _value_after_label(lines: list[str], label: str, all_labels: set[str]) -> str | None:
        """Find a value on the line following a standalone ``label`` line."""
        label_l = label.lower()
        for i, line in enumerate(lines):
            ll = line.lower()
            # Treat the line as the label only when it *is* the label (allowing a
            # trailing helper like "(yyyy/mm/dd)" or a colon), not a sentence.
            base = re.sub(r"\(.*?\)|:", "", ll).strip()
            if base != label_l:
                continue
            for j in range(i + 1, min(i + 3, len(lines))):
                nxt = lines[j].strip()
                if not nxt:
                    continue
                nxt_base = re.sub(r"\(.*?\)|:", "", nxt.lower()).strip()
                if nxt_base in all_labels:   # next line is another label -> blank field
                    return None
                if len(nxt) <= 60:           # plausible value, not a paragraph
                    return nxt
                return None
        return None

    # Words that appear as form text but are never a name value.
    _NAME_STOPWORDS = {
        "she", "her", "he", "his", "they", "them", "pronoun", "status",
        "program", "occupation", "weight", "height", "other", "homemaker",
        "unemployed", "retired", "disability", "current", "age", "physician",
        "practitioner", "student", "employment", "medication", "name", "email",
        "birthdate", "preferred", "legal", "identifying", "information",
        "part-time", "full-time", "doctor", "nurse", "counsellor", "coach",
        "psychiatrist", "social", "worker", "patient", "yes", "no",
    }

    @staticmethod
    def _plausible_value(canonical: str, value: str) -> bool:
        """Reject implausible next-line values (e.g. an adjacent label)."""
        v = value.strip()
        if not v:
            return False
        if canonical == "dob":
            return bool(parse_dob_candidates(v))
        if canonical == "email":
            from ..models import _EMAIL_RE

            return bool(_EMAIL_RE.search(v))
        if canonical in ("first_name", "last_name", "pref_name"):
            if not re.fullmatch(r"[A-Za-z][A-Za-z .'\-,]{0,38}", v):
                return False
            tokens = re.split(r"[\s/]+", v.lower())
            return not any(t in Extractor._NAME_STOPWORDS for t in tokens)
        # phone / address / health_card: accept short, non-empty text.
        return len(v) <= 60
