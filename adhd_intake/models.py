"""Domain models shared across the whole pipeline.

These are plain dataclasses / enums with no behaviour beyond light helpers, so
every module (extraction, ocr, validation, oscar, sheets, dashboard, database)
speaks the same vocabulary.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


def parse_dob_candidates(raw: Optional[str]) -> list[str]:
    """Return every plausible ``YYYY-MM-DD`` reading of a free-form date string.

    Patients write DOBs many ways (10/05/2001, May 10 2001, 2001-05-10, …).
    Numeric dates like 05/10/2001 are genuinely ambiguous, so BOTH day/month
    orders are returned; the caller matches against OSCAR's canonical DOB, which
    resolves the ambiguity. Years are constrained to a realistic range.
    """
    if not raw:
        return []
    s = str(raw).strip()
    out: set[str] = set()

    def add(y: int, mo: int, d: int) -> None:
        try:
            if 1900 <= y <= date.today().year and 1 <= mo <= 12 and 1 <= d <= 31:
                out.add(date(y, mo, d).isoformat())
        except ValueError:
            pass  # e.g. Feb 30

    # ISO-ish: YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
    for m in re.finditer(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", s):
        y, mo, d = (int(g) for g in m.groups())
        add(y, mo, d)
    # DD?/MM?/YYYY — try both day-first and month-first
    for m in re.finditer(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", s):
        a, b, y = (int(g) for g in m.groups())
        add(y, b, a)  # a = day,   b = month
        add(y, a, b)  # a = month, b = day
    # 8 contiguous digits (e.g. the form's "yyyymmdd" field): try YYYYMMDD,
    # then DDMMYYYY / MMDDYYYY. add() drops out-of-range combinations.
    for m in re.finditer(r"\b(\d{8})\b", s):
        g = m.group(1)
        add(int(g[0:4]), int(g[4:6]), int(g[6:8]))   # YYYYMMDD
        add(int(g[4:8]), int(g[2:4]), int(g[0:2]))   # DDMMYYYY
        add(int(g[4:8]), int(g[0:2]), int(g[2:4]))   # MMDDYYYY

    # Textual months (e.g. "10 May 2001") via dateutil — only when the string
    # contains letters, so numeric dates already handled above aren't re-guessed.
    if re.search(r"[A-Za-z]", s):
        try:
            from dateutil import parser as _dtp  # type: ignore

            for dayfirst in (True, False):
                try:
                    dt = _dtp.parse(s, dayfirst=dayfirst, fuzzy=True, default=datetime(1900, 1, 1))
                    if 1900 <= dt.year <= date.today().year:
                        out.add(dt.date().isoformat())
                except (ValueError, OverflowError):
                    pass
        except Exception:
            pass

    return sorted(out)


class QuestionnaireType(enum.Enum):
    """The two supported assessment tools."""

    ADULT_ADHD = "Adult ADHD Centre"
    ADHD_WOMEN = "ADHD Centre for Women"
    UNKNOWN = "Unknown"

    @property
    def document_description(self) -> str:
        """Description used when uploading into OSCAR Documents."""
        return {
            QuestionnaireType.ADULT_ADHD: "Adult ADHD Centre Assessment Questionnaire",
            QuestionnaireType.ADHD_WOMEN: "ADHD Centre for Women Assessment Questionnaire",
            QuestionnaireType.UNKNOWN: "ADHD Assessment Questionnaire",
        }[self]


class PdfKind(enum.Enum):
    """Whether a PDF has a usable text/form layer or must be OCR'd."""

    FILLABLE = "fillable"      # AcroForm fields and/or a real text layer
    SCANNED = "scanned"        # image-only, requires OCR
    UNKNOWN = "unknown"


class ProcessingStatus(enum.Enum):
    """Terminal and intermediate states of a single intake file.

    The ``*_*`` rejection states are terminal and explicitly block any OSCAR
    upload or Google Sheets write.
    """

    PENDING = "Pending"
    EXTRACTING = "Extracting"
    VALIDATING = "Validating"
    REJECTED_NO_SIGNATURE = "Rejected - No Signature"   # legacy; no longer produced
    INCOMPLETE_DECLINED = "Incomplete - Returned to Patient"  # legacy; no longer produced
    INCOMPLETE_PATIENT_INFORMED = "Incomplete Form - Patient Informed"
    PATIENT_NOT_FOUND = "Patient Not Found"
    UPLOADING = "Uploading"
    COMPLETED = "Completed"
    COMPLETED_NO_SIGNATURE = "Completed – Signature Missing on Consent Form"
    ERROR = "Error"

    @property
    def is_terminal(self) -> bool:
        return self in {
            ProcessingStatus.REJECTED_NO_SIGNATURE,
            ProcessingStatus.INCOMPLETE_DECLINED,
            ProcessingStatus.INCOMPLETE_PATIENT_INFORMED,
            ProcessingStatus.PATIENT_NOT_FOUND,
            ProcessingStatus.COMPLETED,
            ProcessingStatus.COMPLETED_NO_SIGNATURE,
            ProcessingStatus.ERROR,
        }

    @property
    def is_success(self) -> bool:
        return self in {
            ProcessingStatus.COMPLETED,
            ProcessingStatus.COMPLETED_NO_SIGNATURE,
            ProcessingStatus.INCOMPLETE_PATIENT_INFORMED,
        }


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DOB_RE = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")


@dataclass
class Demographics:
    """Patient identifying information extracted from the questionnaire."""

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    pref_name: Optional[str] = None    # preferred name
    email: Optional[str] = None
    dob: Optional[str] = None          # normalised primary, YYYY-MM-DD
    dob_raw: Optional[str] = None      # the original string as written on the form
    phone: Optional[str] = None
    address: Optional[str] = None
    province: Optional[str] = None     # from the OSCAR chart
    program_status: Optional[str] = None  # "Private" etc., from the chart Booking Alert
    health_card: Optional[str] = None
    pronoun: Optional[str] = None
    sex: Optional[str] = None          # M / F / etc., from the questionnaire

    def dob_candidates(self) -> list[str]:
        """All plausible YYYY-MM-DD readings of the form's DOB (for matching)."""
        return parse_dob_candidates(self.dob_raw or self.dob)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()

    @property
    def display_name(self) -> str:
        """'Last, First' — the format used in the dashboard and OSCAR."""
        parts = [p for p in (self.last_name, self.first_name) if p]
        return ", ".join(parts)

    def has_minimum_identifiers(self) -> bool:
        """True if we have enough to attempt an OSCAR patient search."""
        if self.last_name and self.first_name:
            return True
        if self.email or self.dob:
            return True
        return False

    @staticmethod
    def normalise_dob(value: str | None) -> Optional[str]:
        """Best-effort single YYYY-MM-DD value (for storage / Age / search).

        Prefers an unambiguous ISO date; otherwise returns the first plausible
        reading. For *matching*, use :meth:`dob_candidates` which keeps all
        readings so day/month ambiguity can be resolved against OSCAR.
        """
        if not value:
            return None
        # Unambiguous ISO form wins if present.
        m = _DOB_RE.search(str(value))
        if m:
            y, mo, d = (int(g) for g in m.groups())
            try:
                return f"{y:04d}-{mo:02d}-{d:02d}"
            except ValueError:
                pass
        candidates = parse_dob_candidates(value)
        return candidates[0] if candidates else None


@dataclass
class ExtractionResult:
    """Output of the extraction stage."""

    questionnaire_type: QuestionnaireType
    pdf_kind: PdfKind
    demographics: Demographics
    answers: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""
    used_ocr: bool = False
    confidence: float = 0.0           # 0..1, lower for OCR-derived data
    warnings: list[str] = field(default_factory=list)


@dataclass
class SignatureValidationResult:
    """Output of the consent/signature validation stage."""

    signed: bool
    consent_page_index: Optional[int] = None
    method: str = ""                  # "form-field", "ink-density", "text"
    ink_density: Optional[float] = None
    detail: str = ""


@dataclass
class CompletenessResult:
    """Output of the response-completeness check on the assessment pages.

    The questionnaire pages (6-11 for the Adult tool, 6-12 for the Women's tool)
    contain question rows that each require at least one response. This records
    whether every checked row has a response and, if not, which page numbers
    (1-based, as the operator sees them) carry unanswered questions.
    """

    complete: bool
    incomplete_pages: list[int] = field(default_factory=list)  # 1-based page numbers
    unanswered_count: int = 0
    # Human-readable description of each unanswered question, e.g.
    # "Page 10: 6 I am late for class." — surfaced to staff and the patient email.
    unanswered_questions: list[str] = field(default_factory=list)
    # Pages where EVERY question row is blank (a whole section left empty).
    blank_section_pages: list[int] = field(default_factory=list)
    checked: bool = True       # False if the page structure could not be parsed
    detail: str = ""

    @property
    def pages_label(self) -> str:
        """Human page list, e.g. 'Page 8 and Page 11' / 'Pages 8, 10 and 11'."""
        pages = sorted(set(self.incomplete_pages))
        if not pages:
            return ""
        labels = [str(p) for p in pages]
        if len(labels) == 1:
            return f"Page {labels[0]}"
        return "Pages " + ", ".join(labels[:-1]) + " and " + labels[-1]


@dataclass
class Discrepancy:
    """A field where the assessment tool and the OSCAR chart disagree."""

    field_label: str          # human label, e.g. "Last Name"
    oscar_field: str          # OSCAR form field name, e.g. "last_name"
    tool_value: str
    oscar_value: str


@dataclass
class PatientMatch:
    """A patient resolved in OSCAR Pro."""

    demographic_no: str
    last_name: str
    first_name: str
    email: Optional[str] = None
    dob: Optional[str] = None
    matched_by: str = ""              # which search strategy hit

    @property
    def full_name(self) -> str:
        return f"{self.last_name}, {self.first_name}".strip(", ")


@dataclass
class ProcessingRecord:
    """The full lifecycle record for one intake file. Persisted to SQLite."""

    id: Optional[int] = None
    source_filename: str = ""
    stored_path: str = ""
    file_hash: str = ""
    questionnaire_type: QuestionnaireType = QuestionnaireType.UNKNOWN
    pdf_kind: PdfKind = PdfKind.UNKNOWN
    status: ProcessingStatus = ProcessingStatus.PENDING
    used_ocr: bool = False

    # Patient linkage (names are kept locally only, NEVER sent to Sheets).
    demographics: Demographics = field(default_factory=Demographics)
    demographic_no: Optional[str] = None

    # Outcome bookkeeping.
    signature_present: Optional[bool] = None
    oscar_document_id: Optional[str] = None
    sheets_row: Optional[int] = None
    message: str = ""

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # In-memory only (not persisted): raw questionnaire field values, so the
    # copy-sheet can pull arbitrary answers via "form:<field name>" columns.
    answers: dict = field(default_factory=dict)
    # In-memory only: specific unanswered questions when a form is incomplete,
    # so the activity log and patient email can name exactly what to complete.
    incomplete_questions: list = field(default_factory=list)
    # In-memory only: True when this result was skipped as a duplicate of a
    # patient already processed this session (no new upload/row).
    skipped_duplicate: bool = False
    # In-memory only: outcome of an OSCAR chart update the operator approved.
    # ("ok"/"failed", the human field list) so the GUI can confirm or warn that
    # the chart was (not) changed. None when no update was attempted.
    chart_update_ok: Optional[bool] = None
    chart_update_fields: list = field(default_factory=list)

    def patient_email(self) -> str:
        return self.demographics.email or ""

    def patient_name(self) -> str:
        # "Last, First" for the dashboard.
        return self.demographics.display_name
