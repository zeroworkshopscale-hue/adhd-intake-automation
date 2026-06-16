"""Sheet logging.

* Cloud Google Sheet — PHI-safe, never writes patient names.
* Local copy-sheet (CSV) — full demographics, for manual paste into the master.
* CompositeSheetWriter — routes to either/both per configuration.
"""

from .composite import CompositeSheetWriter
from .local_sheet import LocalSheetWriter, LOCAL_SHEET_HEADERS

__all__ = [
    "CompositeSheetWriter",
    "LocalSheetWriter",
    "LOCAL_SHEET_HEADERS",
]
