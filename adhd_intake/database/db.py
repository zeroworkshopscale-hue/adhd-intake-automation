"""SQLite connection management and schema migration.

A single :class:`Database` owns the connection. SQLite is used in WAL mode so
the GUI thread can read while a worker thread writes. All schema creation is
idempotent so the app is safe to start repeatedly.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from ..utils.logging_config import get_logger

logger = get_logger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_filename     TEXT    NOT NULL,
    stored_path         TEXT,
    file_hash           TEXT,
    questionnaire_type  TEXT,
    pdf_kind            TEXT,
    status              TEXT    NOT NULL,
    used_ocr            INTEGER NOT NULL DEFAULT 0,

    -- Local-only patient detail. Names live here but are NEVER exported to Sheets.
    first_name          TEXT,
    last_name           TEXT,
    email               TEXT,
    dob                 TEXT,
    demographic_no      TEXT,

    signature_present   INTEGER,
    oscar_document_id   TEXT,
    sheets_row          INTEGER,
    message             TEXT,

    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_status ON processing_records(status);
CREATE INDEX IF NOT EXISTS idx_records_hash   ON processing_records(file_hash);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id   INTEGER,
    timestamp   TEXT    NOT NULL,
    actor       TEXT    NOT NULL,
    event       TEXT    NOT NULL,
    detail      TEXT,
    FOREIGN KEY (record_id) REFERENCES processing_records(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_record ON audit_log(record_id);
"""


class Database:
    """Owns the SQLite connection and applies the schema on construction."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + an explicit lock lets the Qt worker thread
        # share the connection with the GUI thread safely.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._configure()
        self._migrate()
        logger.info("SQLite database ready at %s", path)

    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.commit()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def close(self) -> None:
        with self._lock:
            self._conn.close()
