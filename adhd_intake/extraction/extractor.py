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
        # Route Word documents to the dedicated docx extractor.
        if pdf_path.suffix.lower() == ".docx":
            from .docx_extractor import extract_docx
            return extract_docx(pdf_path)

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

        # Special multi-cell sections (substance use, how-did-you-hear, consent)
        # are spread across the form and need widget + ink detection, so compute
        # them here for fillable AND flattened PDFs and expose one answer key per
        # output column. Pronoun is promoted onto the answers map too so the sheet
        # can reference it by name.
        if demographics.pronoun and not answers.get("pronoun"):
            answers["pronoun"] = demographics.pronoun
        try:
            self._extract_special_sections(pdf_path, answers)
        except Exception:
            logger.debug("Special-section extraction failed", exc_info=True)

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

        # Ensure pronoun from the pairs dict is promoted to the Demographics object
        # (the _detect_pronoun result lives in pairs["pronoun"] and flows through
        # _fill_from_pairs only if "pronoun" is in demographic_fields; it is now,
        # but for robustness also pick it up here directly).
        if not demo.pronoun and label_pairs and label_pairs.get("pronoun"):
            demo.pronoun = label_pairs["pronoun"]
        if not demo.pronoun and classification.form_values.get("pronoun"):
            demo.pronoun = classification.form_values["pronoun"]

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
            if canonical == "pronoun":
                continue  # handled below from the dedicated option fields
            if getattr(demo, canonical, None):
                continue
            for label in candidates:
                for field_name, value in lowered.items():
                    if label in field_name:
                        setattr(demo, canonical, value.strip())
                        break
                if getattr(demo, canonical, None):
                    break

        # Pronoun is encoded as three separate option fields (She/Her, He/His,
        # They/Them); the marked one carries a value ('x', 'X', '✓', 'On', …).
        # Read it directly — the text layer lists EVERY option after the
        # "Pronoun" label, so a text scrape would always grab the first option
        # (She/Her) regardless of which the patient actually marked.
        if not demo.pronoun:
            pron = Extractor._pronoun_from_form_fields(form_values)
            if pron:
                demo.pronoun = pron

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

                # "How did you hear about us?" checkboxes (page 5).
                if "referral_source" not in pairs:
                    ref = Extractor._detect_referral_source(page)
                    if ref:
                        pairs["referral_source"] = ref

        # Derived: substance use = YES if any substance row is marked.
        for sub in ("Alcohol", "Cannabis", "Other substances"):
            if pairs.get(sub, "").strip():
                pairs.setdefault("substance_use", "YES")
                break
        return pairs

    # Common options on the "How did you hear about us?" section (page 5).
    _REFERRAL_OPTIONS = (
        "Word of mouth",
        "Social media",
        "Google",
        "Website",
        "Doctor",
        "Counsellor",
        "Coach",
        "Therapist",
        "Referral",
        "Advertisement",
        "Radio",
        "TV",
        "Newspaper",
        "Podcast",
        "Online",
        "Instagram",
        "Facebook",
        "Other",
    )

    @staticmethod
    def _detect_referral_source(page) -> Optional[str]:
        """Return a comma-separated list of checked referral source options, or
        None if the section is not found on this page.

        Strategy: search the page text for each known option label. When the
        label is found, check for either a mark token just to its LEFT (typed
        form) or ink just to the left of the label (handwritten/printed form).
        Also checks for a "How did you hear" header as a presence gate.
        """
        import re as _re

        # Only proceed if the page actually has this section.
        page_text = page.get_text("text") or ""
        if not _re.search(r"how did you hear", page_text, _re.IGNORECASE):
            return None

        words = page.get_text("words")  # (x0,y0,x1,y1,text,...)
        found: list[str] = []
        for option in Extractor._REFERRAL_OPTIONS:
            rects = page.search_for(option)
            if not rects:
                continue
            r = rects[0]
            cy = (r.y0 + r.y1) / 2
            # Check for a typed mark token immediately left of the option text.
            marked = False
            for x0, y0, x1, y1, t, *_ in words:
                if x1 <= r.x0 and x0 >= r.x0 - 70 and abs((y0 + y1) / 2 - cy) <= 10:
                    s = t.strip()
                    if s and (_re.fullmatch(r"[xX✓√✔✗✘●•\*\-]+", s) or len(s) <= 2):
                        marked = True
                        break
            if not marked:
                # Fallback: ink to the left of the option.
                if Extractor._region_has_ink(page, r.x0 - 52, r.y0 - 2, r.x0 - 3, r.y1 + 2, threshold=0.015):
                    marked = True
            if marked:
                found.append(option)
        return ", ".join(found) if found else None

    # Pronoun options in PRIORITY order (He/His wins if several are marked).
    _PRONOUN_OPTIONS = ("He/His", "She/Her", "They/Them")
    # AcroForm option-field name (letters only) -> canonical pronoun label.
    _PRONOUN_FIELD_MAP = {
        "sheher": "She/Her", "shehers": "She/Her",
        "hehis": "He/His", "hehim": "He/His",
        "theythem": "They/Them", "theythems": "They/Them",
    }

    @staticmethod
    def _pronoun_from_form_fields(form_values: dict) -> Optional[str]:
        """Pick the marked pronoun from the dedicated AcroForm option fields.

        Matches the field name EXACTLY (letters only) so compound fields such as
        'SheHer HeHis TheyThemBirthdate yyyymmdd' (a date field that merely starts
        with the option names) are never mistaken for a pronoun mark.
        """
        import re as _re

        marked: set[str] = set()
        for name, value in (form_values or {}).items():
            key = _re.sub(r"[^a-z]", "", str(name).lower())
            option = Extractor._PRONOUN_FIELD_MAP.get(key)
            if option is None:
                continue
            v = str(value or "").strip().lower()
            if v and v not in ("off", "no", "false", "0", "unchecked", "none"):
                marked.add(option)
        for option in Extractor._PRONOUN_OPTIONS:  # priority order
            if option in marked:
                return option
        return None

    @staticmethod
    def _detect_pronoun(page) -> Optional[str]:
        """Return the selected pronoun, giving priority to He/His when more than
        one is marked. Prefers the AcroForm option fields (exact, no guessing);
        falls back to a mark/ink to the LEFT of the option for flattened forms."""
        import re as _re

        # 1) AcroForm widgets are authoritative.
        widget_values = {
            (w.field_name or ""): (w.field_value or "")
            for w in (page.widgets() or [])
        }
        pron = Extractor._pronoun_from_form_fields(widget_values)
        if pron:
            return pron

        # 2) Flattened / scanned form. Prefer an explicit mark glyph (x/X/✓) left
        #    of exactly one option. Otherwise COMPARE the ink in each option's
        #    checkbox and pick the clear winner — the patient's mark adds ink well
        #    above an empty printed box, while every box has some outline ink, so
        #    "first box above a fixed threshold" wrongly always picked the top
        #    option. Never guess when nothing stands out.
        words = page.get_text("words")  # (x0,y0,x1,y1,text,...)
        token_hits: list[str] = []
        densities: list[tuple[str, float]] = []
        for option in Extractor._PRONOUN_OPTIONS:
            rects = page.search_for(option)
            if not rects:
                continue
            r = rects[0]
            cy = (r.y0 + r.y1) / 2
            has_token = any(
                x1 <= r.x0 and x0 >= r.x0 - 60 and abs((y0 + y1) / 2 - cy) <= 8
                and _re.fullmatch(r"[xX✓√✔✗✘●•\*]+", (t or "").strip())
                for x0, y0, x1, y1, t, *_ in words
            )
            if has_token:
                token_hits.append(option)
            densities.append(
                (option, Extractor._region_density(page, r.x0 - 32, r.y0 - 1, r.x0 - 3, r.y1 + 1))
            )
        # Explicit mark glyph(s): the highest-priority marked option wins.
        if token_hits:
            for option in Extractor._PRONOUN_OPTIONS:
                if option in token_hits:
                    return option
        # No glyph (e.g. a drawn ✓): pick the checkbox with clearly the most ink.
        if densities:
            densities.sort(key=lambda t: t[1], reverse=True)
            top_opt, top_d = densities[0]
            second = densities[1][1] if len(densities) > 1 else 0.0
            if top_d >= 0.05 and (top_d - second) >= 0.02:
                return top_opt
        return None

    @staticmethod
    def _region_density(page, x0, y0, x1, y1) -> float:
        """Fraction of dark pixels in a page region (0..1)."""
        import fitz
        import numpy as np
        from PIL import Image

        if x1 - x0 < 3 or y1 - y0 < 3:
            return 0.0
        region = fitz.Rect(max(page.rect.x0, x0), y0, x1, y1)
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72.0, 300 / 72.0), clip=region, alpha=False)
        if pix.width == 0 or pix.height == 0:
            return 0.0
        arr = np.asarray(
            Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        )
        return float(np.count_nonzero(arr < 128) / arr.size)

    # ------------------------------------------------------------------
    # Special multi-cell sections: substance use, how-did-you-hear, consent.
    # Each is exposed as one answer key per output column so the copy sheet can
    # place every selection in its own cell.
    # ------------------------------------------------------------------
    _REFERRAL_LABEL_CLEAN = {"Online search eg Google": "Online search"}

    @staticmethod
    def _is_marked(value) -> bool:
        v = str(value or "").strip().lower()
        return v not in ("", "off", "no", "0", "false", "unchecked", "none")

    def _extract_special_sections(self, pdf_path: Path, answers: dict) -> None:
        import fitz

        with fitz.open(str(pdf_path)) as doc:
            for page in doc:
                low = page.get_text("text").lower()
                if "alcohol" in low and "cannabis" in low and "substance_alcohol" not in answers:
                    self._detect_substances(page, answers)
                if "how did you hear" in low and "referral_1" not in answers:
                    self._detect_referral_columns(page, answers)
                if "future adhd initiatives" in low or "future research" in low:
                    # Always normalise to Yes/No (an older path may have set YES).
                    self._detect_consent_checkboxes(page, answers)

    @staticmethod
    def _detect_substances(page, answers: dict) -> None:
        """Per-substance Yes/No -> one column each: the substance name when the
        patient ticked 'Yes', blank otherwise."""
        mapping = [
            ("Alcohol", "substance_alcohol", "Alcohol"),
            ("Cannabis", "substance_cannabis", "Cannabis"),
            ("Other substances", "substance_other", "Other substance"),
        ]
        widgets = list(page.widgets() or [])
        selected: list[str] = []
        for label, key, value in mapping:
            rects = page.search_for(label)
            yes = False
            if rects:
                ry = rects[0].y0
                if widgets:
                    # Fillable: the Yes/No checkboxes are widgets on the label row.
                    row = [
                        w for w in widgets
                        if abs(w.rect.y0 - ry) <= 10 and w.rect.x0 < 360
                        and (w.rect.x1 - w.rect.x0) < 40
                    ]
                    row.sort(key=lambda w: w.rect.x0)   # [No (left), Yes (right)]
                    if len(row) >= 2:
                        yes = Extractor._is_marked(row[-1].field_value)
                else:
                    # Flattened: compare the ink in the mark area left of Yes vs No.
                    yes = Extractor._substance_yes_from_ink(page, ry)
            answers[key] = value if yes else ""
            if yes:
                selected.append(value)
        answers["substance_use"] = ", ".join(selected)

    @staticmethod
    def _substance_yes_from_ink(page, row_y: float) -> bool:
        """Flattened/scanned fallback for one substance row: 'Yes' only when the
        mark left of 'Yes' clearly exceeds the (empty) box left of 'No'."""
        def leftmost(word):
            rs = sorted(
                (r for r in page.search_for(word) if abs(r.y0 - row_y) <= 10),
                key=lambda r: r.x0,
            )
            return rs[0] if rs else None

        yr = leftmost("Yes")
        if yr is None:
            return False
        yes_ink = Extractor._region_density(page, yr.x0 - 28, yr.y0 - 1, yr.x0 - 2, yr.y1 + 1)
        nr = leftmost("No")
        no_ink = (
            Extractor._region_density(page, nr.x0 - 28, nr.y0 - 1, nr.x0 - 2, nr.y1 + 1)
            if nr is not None else 0.0
        )
        return yes_ink >= 0.10 and (yes_ink - no_ink) >= 0.03

    # Option labels on the "How did you hear about us?" page (used for the
    # flattened-form ink fallback; fillable forms read the widgets directly).
    _REFERRAL_OPTION_LABELS = (
        "Doctor", "Nurse practitioner", "Counsellor", "Coach", "Social worker",
        "Psychiatrist", "Family member", "Friend", "Online search", "Twitter",
        "TikTok", "Facebook", "Instagram", "LinkedIn", "CADDAC", "Brochure",
    )

    @staticmethod
    def _clean_referral_label(name: str) -> str:
        lbl = Extractor._REFERRAL_LABEL_CLEAN.get(name, name)
        if lbl.lower().endswith(" name"):
            lbl = lbl[:-5]
        return lbl.strip()

    # Field names that carry no meaning (must read the printed label instead).
    _GENERIC_FIELD_RE = re.compile(r"^(check\s*box|checkbox|undefined|field|text)\s*\d*$", re.I)

    @staticmethod
    def _text_right_of(page, rect, max_chars: int = 40) -> str:
        """The printed option label sitting to the right of a checkbox on its row
        (e.g. 'Friend'). Stops at a '(name)' / '(e.g.' / ':' marker."""
        cy = (rect.y0 + rect.y1) / 2.0
        words = [
            (wx0, wd)
            for wx0, wy0, wx1, wy1, wd, *_ in page.get_text("words")
            if wx0 >= rect.x1 - 2 and abs((wy0 + wy1) / 2.0 - cy) <= 6
        ]
        words.sort(key=lambda t: t[0])
        label = " ".join(w for _, w in words).strip()
        for cut in ("(name", "(e.g", "("):
            i = label.find(cut)
            if i > 0:
                label = label[:i]
        return label.strip().rstrip(":").strip()[:max_chars]

    @staticmethod
    def _referral_label_for(page, rect, name: str) -> str:
        """Prefer the printed label beside the checkbox; fall back to the field
        name (only useful when it is descriptive, not 'Check Box10')."""
        if Extractor._GENERIC_FIELD_RE.match((name or "").strip()):
            return Extractor._text_right_of(page, rect) or ""
        return Extractor._clean_referral_label(name) or Extractor._text_right_of(page, rect)

    @staticmethod
    def _detect_referral_columns(page, answers: dict) -> None:
        """Up to three selected 'How did you hear about us?' options, in order,
        one per column (referral_1/2/3).

        Fillable forms read the option checkboxes (widgets); flattened/scanned
        forms fall back to comparing the ink in the mark area left of each option
        label (the ticked ones stand clearly above the empty blanks).
        """
        sel: list = []
        for w in page.widgets() or []:
            r = w.rect
            if r.x0 < 100 and 6 < (r.x1 - r.x0) < 35 and Extractor._is_marked(w.field_value):
                sel.append((r.y0, r, w.field_name or ""))

        if sel:
            sel.sort(key=lambda t: t[0])
            labels = [Extractor._referral_label_for(page, rect, name) for _, rect, name in sel]
            labels = [lbl for lbl in labels if lbl]   # drop any that resolved empty
        else:
            labels = Extractor._referral_from_ink(page)

        for i in range(3):
            answers[f"referral_{i + 1}"] = labels[i] if i < len(labels) else ""
        if labels:
            answers["referral_source"] = ", ".join(labels)

    @staticmethod
    def _referral_from_ink(page) -> list[str]:
        """Flattened/scanned fallback: an option is selected when the ink in the
        mark area left of its label is clearly above the empty-blank baseline."""
        inks: list[tuple[float, str, float]] = []
        for opt in Extractor._REFERRAL_OPTION_LABELS:
            rects = page.search_for(opt)
            if not rects:
                continue
            r = rects[0]
            d = Extractor._region_density(page, r.x0 - 30, r.y0 - 1, r.x0 - 2, r.y1 + 1)
            inks.append((r.y0, opt, d))
        if not inks:
            return []
        baseline = sorted(d for _, _, d in inks)[len(inks) // 2]  # median ~ empty blank
        marked = [
            (y, opt) for y, opt, d in inks
            if d >= 0.05 and (d - baseline) >= 0.03
        ]
        marked.sort()
        return [opt for _, opt in marked]

    @staticmethod
    def _detect_consent_checkboxes(page, answers: dict) -> None:
        """The two e-mail consent checkboxes are drawn boxes (no form field), so
        detect a mark by ink. 'Yes' when ticked, otherwise 'No'."""
        for key, anchor in (
            ("future_initiatives", "future ADHD initiatives"),
            ("future_research", "future research"),
        ):
            rects = page.search_for(anchor)
            marked = False
            if rects:
                try:
                    marked = Extractor._checkbox_marked(page, rects[0])
                except Exception:
                    marked = False
            answers[key] = "Yes" if marked else "No"

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
