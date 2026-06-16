"""Append-only audit log.

Every meaningful action (file received, classified, validated, rejected,
patient matched, uploaded, sheet updated, error) is written here with a
timestamp and actor. The log is never updated or deleted in normal operation.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from .db import Database
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class AuditEvent(str, enum.Enum):
    FILE_RECEIVED = "file_received"
    DUPLICATE_DETECTED = "duplicate_detected"
    CLASSIFIED = "classified"
    EXTRACTED = "extracted"
    OCR_RUN = "ocr_run"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    SIGNATURE_MISSING = "signature_missing"
    COMPLETENESS_CHECKED = "completeness_checked"
    COMPLETENESS_INCOMPLETE = "completeness_incomplete"
    INCOMPLETE_APPROVED = "incomplete_approved"
    INCOMPLETE_DECLINED = "incomplete_declined"
    REJECTED = "rejected"
    PATIENT_SEARCH = "patient_search"
    PATIENT_MATCHED = "patient_matched"
    PATIENT_NOT_FOUND = "patient_not_found"
    DOCUMENT_UPLOADED = "document_uploaded"
    SHEET_UPDATED = "sheet_updated"
    COMPLETED = "completed"
    ERROR = "error"


class AuditLog:
    """Thin writer/reader over the ``audit_log`` table."""

    def __init__(self, db: Database, actor: str = "system"):
        self._db = db
        self._actor = actor

    def record(
        self,
        event: AuditEvent,
        detail: str = "",
        record_id: Optional[int] = None,
        actor: Optional[str] = None,
    ) -> None:
        """Append one audit entry. Never raises into the caller's flow."""
        try:
            with self._db.lock:
                self._db.connection.execute(
                    "INSERT INTO audit_log (record_id, timestamp, actor, event, detail) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        record_id,
                        datetime.now().isoformat(timespec="seconds"),
                        actor or self._actor,
                        event.value,
                        detail,
                    ),
                )
                self._db.connection.commit()
            logger.debug("audit: %s (record=%s) %s", event.value, record_id, detail)
        except Exception:  # pragma: no cover - auditing must not break the pipeline
            logger.exception("Failed to write audit entry for %s", event)

    def for_record(self, record_id: int) -> list[dict]:
        with self._db.lock:
            rows = self._db.connection.execute(
                "SELECT timestamp, actor, event, detail FROM audit_log "
                "WHERE record_id = ? ORDER BY id ASC",
                (record_id,),
            ).fetchall()
        return [dict(r) for r in rows]
