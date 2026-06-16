"""Composite sheet writer.

Routes each processed record to the local copy-sheet, the cloud Google Sheet,
or both, according to ``SheetsConfig.mode``. The pipeline only knows the small
``append_record(record) -> int`` interface, so it is unaffected by the choice.
"""

from __future__ import annotations

from typing import Optional

from ..config import SheetsConfig
from ..models import ProcessingRecord
from ..utils.logging_config import get_logger
from .local_sheet import LocalSheetWriter

logger = get_logger(__name__)


class CompositeSheetWriter:
    """Writes to local CSV and/or Google Sheets based on configuration."""

    def __init__(self, config: SheetsConfig):
        self._config = config
        self._local: Optional[LocalSheetWriter] = (
            LocalSheetWriter(config.local_path, columns=config.columns or None)
            if config.local_enabled
            else None
        )
        # The Google client is created lazily on first use to avoid importing
        # gspread unless the cloud sheet is actually enabled.
        self._google = None

    def _google_client(self):
        if self._google is None:
            from .client import SheetsClient  # noqa: PLC0415 - lazy

            self._google = SheetsClient(self._config)
        return self._google

    def append_record(self, record: ProcessingRecord) -> int:
        """Append the record everywhere enabled. Returns the local row number
        when local is enabled, otherwise the Google row number."""
        local_row = -1
        google_row = -1

        if self._local is not None:
            local_row = self._local.append_record(record)

        if self._config.google_enabled:
            google_row = self._google_client().append_record(record)

        return local_row if local_row >= 0 else google_row

    @property
    def local_path(self):
        return self._config.local_path if self._config.local_enabled else None
