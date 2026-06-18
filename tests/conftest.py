"""Shared pytest fixtures and lightweight fakes.

These tests deliberately avoid the heavy third-party dependencies (PyMuPDF,
Tesseract, Playwright, gspread). They exercise the *business rules* of the
pipeline by injecting fakes for extraction, validation, OSCAR and Sheets — so
the rejection/upload gates can be verified on any machine.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from adhd_intake.config import (
    AppConfig,
    DatabaseConfig,
    FolderConfig,
    LoggingConfig,
    OcrConfig,
    OscarConfig,
    SheetsConfig,
    ValidationConfig,
)
from adhd_intake.database import AuditLog, Database, RecordRepository
from adhd_intake.models import (
    Demographics,
    ExtractionResult,
    PatientMatch,
    PdfKind,
    QuestionnaireType,
    SignatureValidationResult,
)
from adhd_intake.oscar import PatientNotFoundError
from adhd_intake.pipeline import IntakeProcessor


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
@dataclass
class FakeExtractor:
    result: ExtractionResult

    def extract(self, pdf_path: Path) -> ExtractionResult:
        return self.result


@dataclass
class FakeValidator:
    signed: bool

    def validate(self, pdf_path: Path) -> SignatureValidationResult:
        return SignatureValidationResult(
            signed=self.signed,
            consent_page_index=0,
            method="fake",
            detail="signed" if self.signed else "no ink detected",
        )


@dataclass
class FakeOscar:
    """Records calls so tests can assert no upload happens on the reject paths."""

    match: PatientMatch | None
    upload_calls: list = field(default_factory=list)
    find_calls: list = field(default_factory=list)

    def find_patient(self, demographics, select_cb=None, email_cb=None):
        self.find_calls.append(demographics)
        if self.match is None:
            raise PatientNotFoundError("not found")
        return self.match

    def upload_document(self, patient, pdf_path, description):
        self.upload_calls.append((patient.demographic_no, str(pdf_path), description))
        return f"{patient.demographic_no}:{description}"

    def get_demographic_details(self, demo: str) -> dict:
        if self.match:
            return {
                "last": self.match.last_name,
                "first": self.match.first_name,
                "dob": self.match.dob or "",
                "email": self.match.email or "",
            }
        return {}


@dataclass
class FakeSheets:
    rows: list = field(default_factory=list)

    def append_record(self, record):
        self.rows.append(record)
        return len(self.rows)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    folders = FolderConfig(
        incoming=tmp_path / "incoming",
        processed=tmp_path / "processed",
        rejected=tmp_path / "rejected",
    )
    folders.ensure_exist()
    return AppConfig(
        folders=folders,
        database=DatabaseConfig(path=tmp_path / "test.db"),
        logging=LoggingConfig(dir=tmp_path / "logs", level="WARNING"),
        ocr=OcrConfig(tesseract_cmd="tesseract"),
        validation=ValidationConfig(),
        oscar=OscarConfig(base_url="http://x", username="u", password="p"),
        sheets=SheetsConfig(
            local_path=tmp_path / "copy.csv",
            service_account_file=tmp_path / "sa.json",
            spreadsheet_id="x",
        ),
    )


@pytest.fixture
def db(config) -> Database:
    database = Database(config.database.path)
    yield database
    database.close()


@pytest.fixture
def sample_pdf(config) -> Path:
    """A throwaway file standing in for a dropped PDF (content is irrelevant to
    these fakes)."""
    p = config.folders.incoming / "sample.pdf"
    p.write_bytes(b"%PDF-1.4 fake content for hashing")
    return p


def build_processor(config, db, *, extraction, signed, oscar, sheets):
    repo = RecordRepository(db)
    audit = AuditLog(db, actor="test")

    @contextmanager
    def oscar_factory():
        yield oscar

    def sheets_factory():
        return sheets

    return IntakeProcessor(
        config=config,
        repository=repo,
        audit=audit,
        extractor=FakeExtractor(extraction),
        validator=FakeValidator(signed),
        oscar_factory=oscar_factory,
        sheets_factory=sheets_factory,
    ), repo


def make_extraction(
    *,
    qtype=QuestionnaireType.ADULT_ADHD,
    first="Jane",
    last="Doe",
    email="jane@example.com",
    dob="1990-05-01",
    used_ocr=False,
    pdf_kind=PdfKind.FILLABLE,
) -> ExtractionResult:
    return ExtractionResult(
        questionnaire_type=qtype,
        pdf_kind=pdf_kind,
        demographics=Demographics(
            first_name=first, last_name=last, email=email, dob=dob
        ),
        used_ocr=used_ocr,
    )
