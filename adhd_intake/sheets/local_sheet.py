"""Local "copy sheet".

Maintains a CSV the operator can open and copy/paste, in one go, into the
clinic's master Google Sheet. The column ORDER and which app field feeds each
column are fully configurable (``sheets.columns`` in config.yaml) so the file
lines up exactly with the master sheet for pasting.

Columns the app cannot fill (e.g. "Pronoun", "Province") are written blank so
the operator fills them in after pasting, and the alignment is preserved.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Sequence

from ..models import ProcessingRecord
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def _age_from_dob(record: ProcessingRecord) -> str:
    dob = record.demographics.dob
    if not dob:
        return ""
    try:
        born = datetime.strptime(dob, "%Y-%m-%d").date()
    except ValueError:
        return ""
    today = date.today()
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return str(years) if years >= 0 else ""


def _signature(record: ProcessingRecord) -> str:
    if record.signature_present is None:
        return ""
    return "Yes" if record.signature_present else "No"


# Canadian province/territory codes -> full names (the sheet shows the full name).
_PROVINCE_NAMES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}


def _province_full(record: ProcessingRecord) -> str:
    p = (record.demographics.province or "").strip()
    if not p:
        return ""
    return _PROVINCE_NAMES.get(p.upper().replace(".", ""), p)


# field key -> function(record) -> cell string.
# Add new keys here to expose more data to the copy sheet.
FIELD_RESOLVERS: dict[str, Callable[[ProcessingRecord], str]] = {
    "blank": lambda r: "",
    "date": lambda r: date.today().isoformat(),
    "datetime": lambda r: datetime.now().isoformat(timespec="seconds"),
    "demographic_no": lambda r: r.demographic_no or "",
    "email": lambda r: r.demographics.email or "",
    "first_name": lambda r: r.demographics.first_name or "",
    "last_name": lambda r: r.demographics.last_name or "",
    "full_name": lambda r: r.demographics.full_name,
    "dob": lambda r: r.demographics.dob or "",
    "age": _age_from_dob,
    "phone": lambda r: r.demographics.phone or "",
    "health_card": lambda r: r.demographics.health_card or "",
    "pronoun": lambda r: r.demographics.pronoun or "",
    "province": _province_full,
    "referral_source": lambda r: str(r.answers.get("referral_source", "")),
    "program_status": lambda r: r.demographics.program_status or "",
    "questionnaire_type": lambda r: r.questionnaire_type.value,
    "signature": _signature,
    "status": lambda r: r.status.value,
    "oscar_document_id": lambda r: r.oscar_document_id or "",
    "source_filename": lambda r: r.source_filename,
}

# Default schema, used when sheets.columns is not configured. Each entry is
# (header text, field key).
DEFAULT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Date Processed", "datetime"),
    ("Demographic No", "demographic_no"),
    ("Last Name", "last_name"),
    ("First Name", "first_name"),
    ("Email", "email"),
    ("DOB", "dob"),
    ("Questionnaire Type", "questionnaire_type"),
    ("Signature", "signature"),
    ("Status", "status"),
    ("OSCAR Document ID", "oscar_document_id"),
    ("Source File", "source_filename"),
)

# Back-compat alias used elsewhere/tests.
LOCAL_SHEET_HEADERS = [h for h, _ in DEFAULT_COLUMNS]


class LocalSheetWriter:
    """Append-only CSV writer with a configurable column schema."""

    # Columns never filled with the blank placeholder: pure spacers, and the
    # how-did-you-hear option slots (which legitimately stay empty when the
    # patient selected fewer than three options).
    _NO_PLACEHOLDER_FIELDS = ("blank",)
    _NO_PLACEHOLDER_PREFIXES = ("form:referral",)

    def __init__(
        self,
        path: Path,
        columns: Sequence[tuple[str, str]] | None = None,
        blank_placeholder: str = "",
        force_text: bool = False,
    ):
        self._path = path
        self._columns = tuple(columns) if columns else DEFAULT_COLUMNS
        self._blank_placeholder = blank_placeholder or ""
        self._force_text = bool(force_text)
        # Where the most recent row actually landed (main path, or fallback if
        # the main sheet was locked). Lets callers tell the operator.
        self.last_write_path = path
        # Validate field keys early so a config typo fails loudly at start-up.
        # A "form:<field name>" key pulls a raw questionnaire answer by name.
        for _, field_key in self._columns:
            if field_key.startswith("form:"):
                continue
            if field_key not in FIELD_RESOLVERS:
                raise ValueError(
                    f"Unknown sheet column field '{field_key}'. Use one of "
                    f"{', '.join(sorted(FIELD_RESOLVERS))} or 'form:<PDF field name>'."
                )

    @property
    def path(self) -> Path:
        return self._path

    @property
    def headers(self) -> list[str]:
        return [h for h, _ in self._columns]

    def _placeholder_for(self, field_key: str) -> str:
        """The text used when a column resolves empty (blank for exempt cols)."""
        if not self._blank_placeholder:
            return ""
        if field_key in self._NO_PLACEHOLDER_FIELDS:
            return ""
        if any(field_key.startswith(p) for p in self._NO_PLACEHOLDER_PREFIXES):
            return ""
        return self._blank_placeholder

    def _as_text(self, value: str) -> str:
        """Wrap a non-empty value as an ="..." cell so Excel and Google Sheets
        treat it as text and LEFT-ALIGN it (numbers/dates stop right-aligning),
        matching the master sheet. Empty cells are left empty."""
        if not self._force_text or value == "":
            return value
        return '="' + value.replace('"', '""') + '"'

    def _row(self, record: ProcessingRecord) -> list[str]:
        cells: list[str] = []
        for _, field_key in self._columns:
            if field_key.startswith("form:"):
                # Pull a questionnaire answer by key. "A|B|C" tries each in order
                # and uses the first non-empty value (handles AcroForm vs label
                # naming, and several possible source fields).
                value = ""
                for key in field_key[5:].split("|"):
                    v = str(record.answers.get(key.strip(), "")).strip()
                    if v:
                        value = v
                        break
                cells.append(self._as_text(value or self._placeholder_for(field_key)))
            else:
                value = FIELD_RESOLVERS[field_key](record)
                cells.append(self._as_text(value or self._placeholder_for(field_key)))
        return cells

    def _fallback_path(self) -> Path:
        """Where rows go when the main sheet is locked (open in Excel)."""
        return self._path.with_name(self._path.stem + "_LOCKED_ROWS" + self._path.suffix)

    def _write_row(self, path: Path, record: ProcessingRecord) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            if is_new:
                writer.writerow(self.headers)
            writer.writerow(self._row(record))
        with path.open("r", encoding="utf-8-sig") as fh:
            return sum(1 for _ in fh) - 1  # minus header

    def append_record(self, record: ProcessingRecord) -> int:
        """Append a row; write the header first if the file is new. Returns the
        1-based data row number.

        If the main sheet is locked (typically because it is open in Excel), the
        row is written to a sibling ``*_LOCKED_ROWS.csv`` file instead of being
        lost, so the patient is never dropped over a file lock.
        """
        self.last_write_path = self._path
        try:
            row = self._write_row(self._path, record)
            logger.info("Wrote local copy-sheet row %d -> %s", row, self._path)
            return row
        except PermissionError:
            fb = self._fallback_path()
            self.last_write_path = fb
            row = self._write_row(fb, record)
            logger.warning(
                "Copy sheet '%s' was locked (open in Excel?) — wrote row %d to "
                "fallback '%s' instead.", self._path, row, fb,
            )
            return row
