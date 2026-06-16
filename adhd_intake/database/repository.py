"""Repository for :class:`ProcessingRecord` persistence.

Maps the domain model to/from the ``processing_records`` table and keeps the
GUI's dashboard query (demographic number, name, email) in one place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .db import Database
from ..models import (
    Demographics,
    PdfKind,
    ProcessingRecord,
    ProcessingStatus,
    QuestionnaireType,
)


def _bool_to_int(value: Optional[bool]) -> Optional[int]:
    return None if value is None else int(value)


def _int_to_bool(value: Optional[int]) -> Optional[bool]:
    return None if value is None else bool(value)


class RecordRepository:
    """CRUD operations for processing records."""

    def __init__(self, db: Database):
        self._db = db

    # ---- writes ----------------------------------------------------------
    def insert(self, record: ProcessingRecord) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        record.created_at = record.created_at or datetime.now()
        with self._db.lock:
            cur = self._db.connection.execute(
                """
                INSERT INTO processing_records (
                    source_filename, stored_path, file_hash, questionnaire_type,
                    pdf_kind, status, used_ocr, first_name, last_name, email, dob,
                    demographic_no, signature_present, oscar_document_id, sheets_row,
                    message, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.source_filename,
                    record.stored_path,
                    record.file_hash,
                    record.questionnaire_type.value,
                    record.pdf_kind.value,
                    record.status.value,
                    int(record.used_ocr),
                    record.demographics.first_name,
                    record.demographics.last_name,
                    record.demographics.email,
                    record.demographics.dob,
                    record.demographic_no,
                    _bool_to_int(record.signature_present),
                    record.oscar_document_id,
                    record.sheets_row,
                    record.message,
                    record.created_at.isoformat(timespec="seconds"),
                    now,
                ),
            )
            self._db.connection.commit()
            record.id = int(cur.lastrowid)
        return record.id

    def update(self, record: ProcessingRecord) -> None:
        if record.id is None:
            raise ValueError("Cannot update a record without an id")
        now = datetime.now().isoformat(timespec="seconds")
        with self._db.lock:
            self._db.connection.execute(
                """
                UPDATE processing_records SET
                    source_filename=?, stored_path=?, file_hash=?, questionnaire_type=?,
                    pdf_kind=?, status=?, used_ocr=?, first_name=?, last_name=?, email=?,
                    dob=?, demographic_no=?, signature_present=?, oscar_document_id=?,
                    sheets_row=?, message=?, updated_at=?
                WHERE id=?
                """,
                (
                    record.source_filename,
                    record.stored_path,
                    record.file_hash,
                    record.questionnaire_type.value,
                    record.pdf_kind.value,
                    record.status.value,
                    int(record.used_ocr),
                    record.demographics.first_name,
                    record.demographics.last_name,
                    record.demographics.email,
                    record.demographics.dob,
                    record.demographic_no,
                    _bool_to_int(record.signature_present),
                    record.oscar_document_id,
                    record.sheets_row,
                    record.message,
                    now,
                    record.id,
                ),
            )
            self._db.connection.commit()

    # ---- reads -----------------------------------------------------------
    def _row_to_record(self, row) -> ProcessingRecord:
        return ProcessingRecord(
            id=row["id"],
            source_filename=row["source_filename"],
            stored_path=row["stored_path"] or "",
            file_hash=row["file_hash"] or "",
            questionnaire_type=QuestionnaireType(row["questionnaire_type"])
            if row["questionnaire_type"]
            else QuestionnaireType.UNKNOWN,
            pdf_kind=PdfKind(row["pdf_kind"]) if row["pdf_kind"] else PdfKind.UNKNOWN,
            status=ProcessingStatus(row["status"]),
            used_ocr=bool(row["used_ocr"]),
            demographics=Demographics(
                first_name=row["first_name"],
                last_name=row["last_name"],
                email=row["email"],
                dob=row["dob"],
            ),
            demographic_no=row["demographic_no"],
            signature_present=_int_to_bool(row["signature_present"]),
            oscar_document_id=row["oscar_document_id"],
            sheets_row=row["sheets_row"],
            message=row["message"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get(self, record_id: int) -> Optional[ProcessingRecord]:
        with self._db.lock:
            row = self._db.connection.execute(
                "SELECT * FROM processing_records WHERE id=?", (record_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def find_by_hash(self, file_hash: str) -> Optional[ProcessingRecord]:
        with self._db.lock:
            row = self._db.connection.execute(
                "SELECT * FROM processing_records WHERE file_hash=? ORDER BY id DESC LIMIT 1",
                (file_hash,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_all(self, limit: int = 500) -> list[ProcessingRecord]:
        with self._db.lock:
            rows = self._db.connection.execute(
                "SELECT * FROM processing_records ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_completed(self, limit: int = 500) -> list[ProcessingRecord]:
        """Records that completed (with or without a consent signature) — drives the dashboard."""
        completed_statuses = (
            ProcessingStatus.COMPLETED.value,
            ProcessingStatus.COMPLETED_NO_SIGNATURE.value,
        )
        with self._db.lock:
            rows = self._db.connection.execute(
                f"SELECT * FROM processing_records WHERE status IN ({','.join('?'*len(completed_statuses))})"
                " ORDER BY id DESC LIMIT ?",
                (*completed_statuses, limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]
