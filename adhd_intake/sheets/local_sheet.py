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
    "province": lambda r: r.demographics.province or "",
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

    def __init__(
        self,
        path: Path,
        columns: Sequence[tuple[str, str]] | None = None,
    ):
        self._path = path
        self._columns = tuple(columns) if columns else DEFAULT_COLUMNS
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
                cells.append(value)
            else:
                cells.append(FIELD_RESOLVERS[field_key](record))
        return cells

    def append_record(self, record: ProcessingRecord) -> int:
        """Append a row; write the header first if the file is new. Returns the
        1-based data row number (excluding the header)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self._path.exists() or self._path.stat().st_size == 0

        with self._path.open("a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            if is_new:
                writer.writerow(self.headers)
            writer.writerow(self._row(record))

        with self._path.open("r", encoding="utf-8-sig") as fh:
            row_count = sum(1 for _ in fh) - 1  # minus header
        logger.info("Wrote local copy-sheet row %d -> %s", row_count, self._path)
        return row_count
