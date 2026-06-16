"""Google Sheets intake log.

The schema is deliberately PHI-light. **Patient names are never written.** A
defensive guard (:func:`_assert_no_names`) inspects every outgoing row against
the known first/last name of the record and refuses to write if a name leaks
into any cell — so a future schema change cannot silently expose PHI.

Identification on the sheet is via the OSCAR demographic number and (optionally)
email, which the clinic already controls in OSCAR.

Schema (one row per processed file):

    timestamp | demographic_no | email | questionnaire_type |
    pdf_kind | used_ocr | signature_present | status |
    oscar_document_id | source_filename
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # imported lazily in _connect so the guard logic stays testable
    import gspread

from ..config import SheetsConfig
from ..models import ProcessingRecord
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_HEADERS = [
    "Timestamp",
    "Demographic No",
    "Email",
    "Questionnaire Type",
    "PDF Kind",
    "Used OCR",
    "Signature Present",
    "Status",
    "OSCAR Document ID",
    "Source Filename",
]


class SheetsError(RuntimeError):
    """Raised on any Google Sheets API failure."""


class PhiLeakError(SheetsError):
    """Raised when a patient name would be written to the sheet."""


class SheetsClient:
    """Appends PHI-safe rows to the configured worksheet."""

    def __init__(self, config: SheetsConfig):
        self._config = config
        self._worksheet: Optional[gspread.Worksheet] = None

    def _connect(self) -> "gspread.Worksheet":
        if self._worksheet is not None:
            return self._worksheet
        if not self._config.service_account_file or not self._config.service_account_file.exists():
            raise SheetsError(
                "Google Sheets is enabled but the service-account file is missing: "
                f"{self._config.service_account_file}"
            )
        try:
            import gspread  # noqa: PLC0415 - lazy import
            from google.oauth2.service_account import Credentials  # noqa: PLC0415

            creds = Credentials.from_service_account_file(
                str(self._config.service_account_file), scopes=_SCOPES
            )
            client = gspread.authorize(creds)
            spreadsheet = client.open_by_key(self._config.spreadsheet_id)
            worksheet = self._get_or_create_worksheet(spreadsheet)
        except Exception as exc:  # gspread/google raise a variety of types
            raise SheetsError(f"Could not open Google Sheet: {exc}") from exc
        self._worksheet = worksheet
        return worksheet

    def _get_or_create_worksheet(self, spreadsheet) -> "gspread.Worksheet":
        import gspread  # noqa: PLC0415 - lazy import

        try:
            ws = spreadsheet.worksheet(self._config.worksheet_name)
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(
                title=self._config.worksheet_name, rows=1000, cols=len(SHEET_HEADERS)
            )
            ws.append_row(SHEET_HEADERS, value_input_option="RAW")
            logger.info("Created worksheet '%s' with headers", self._config.worksheet_name)
            return ws
        # Ensure headers exist on a pre-existing sheet.
        first_row = ws.row_values(1)
        if first_row[: len(SHEET_HEADERS)] != SHEET_HEADERS:
            ws.update("A1", [SHEET_HEADERS])
        return ws

    # ------------------------------------------------------------------
    @staticmethod
    def _assert_no_names(row: list[str], record: ProcessingRecord) -> None:
        """Hard guard: refuse to write if a patient name appears in the row."""
        names = {
            (record.demographics.first_name or "").strip().lower(),
            (record.demographics.last_name or "").strip().lower(),
        }
        names.discard("")
        for cell in row:
            cell_l = str(cell).strip().lower()
            for name in names:
                if name and name in cell_l:
                    raise PhiLeakError(
                        "Refusing Sheets write: patient name detected in outgoing row."
                    )

    def _build_row(self, record: ProcessingRecord) -> list[str]:
        return [
            datetime.now().isoformat(timespec="seconds"),
            record.demographic_no or "",
            record.demographics.email or "",
            record.questionnaire_type.value,
            record.pdf_kind.value,
            "Yes" if record.used_ocr else "No",
            "" if record.signature_present is None else ("Yes" if record.signature_present else "No"),
            record.status.value,
            record.oscar_document_id or "",
            record.source_filename,
        ]

    def append_record(self, record: ProcessingRecord) -> int:
        """Append one PHI-safe row. Returns the 1-based row index written."""
        row = self._build_row(record)
        self._assert_no_names(row, record)  # raises before any network call

        worksheet = self._connect()
        try:
            result = worksheet.append_row(row, value_input_option="USER_ENTERED")
        except Exception as exc:
            raise SheetsError(f"Failed to append row to Google Sheet: {exc}") from exc

        # gspread returns an update range like "Intake Log!A5:J5"; parse the row.
        row_index = self._parse_appended_row(result)
        logger.info("Appended intake row %s for demographic %s", row_index, record.demographic_no)
        return row_index

    @staticmethod
    def _parse_appended_row(result: dict) -> int:
        try:
            updated_range = result["updates"]["updatedRange"]  # e.g. "Sheet!A5:J5"
            cell = updated_range.split("!")[-1].split(":")[0]   # "A5"
            digits = "".join(ch for ch in cell if ch.isdigit())
            return int(digits) if digits else -1
        except (KeyError, ValueError, TypeError):
            return -1
