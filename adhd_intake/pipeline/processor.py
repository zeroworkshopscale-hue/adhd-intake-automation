"""End-to-end intake pipeline.

Orchestrates the full flow for a single dropped PDF.

    receive -> classify/extract (+OCR) -> check signature (advisory)
        |                                      |
        |                              (no signature) --> continue, flag for follow-up
        v
    find patient in OSCAR
        |
   (not found) --> stop: status = Patient Not Found (no upload, no Sheets)
        |
        v
    upload to OSCAR Documents --> update Google Sheets
        |
        +-- signature present: COMPLETED
        +-- signature missing: COMPLETED – Signature Missing on Consent Form

Every transition writes an audit entry and persists the record to SQLite, so the
dashboard and audit trail always reflect reality even if a later stage throws.

The OSCAR and Sheets clients are injected as factories so the heavy
Playwright / Google sessions are created lazily — only when a file actually
reaches those stages — and so tests can substitute fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, ContextManager, Optional, Protocol

from ..config import AppConfig
from ..database import AuditEvent, AuditLog, RecordRepository
from ..models import (
    Demographics,
    Discrepancy,
    PatientMatch,
    ProcessingRecord,
    ProcessingStatus,
)
from ..oscar import OscarError, PatientNotFoundError
from ..utils.files import safe_move, sha256_file
from ..utils.logging_config import get_logger

if TYPE_CHECKING:  # only needed for type hints; avoids importing PyMuPDF/numpy
    from ..extraction import Extractor
    from ..validation import CompletenessValidator, SignatureValidator

logger = get_logger(__name__)

# Description applied to every questionnaire uploaded into OSCAR Documents.
UPLOAD_DOCUMENT_DESCRIPTION = "ADHD Assessment Tool"


class _OscarSession(Protocol):
    def find_patient(self, demographics) -> PatientMatch: ...
    def upload_document(self, patient: PatientMatch, pdf_path: Path, description: str) -> str: ...


class _SheetsLike(Protocol):
    def append_record(self, record: ProcessingRecord) -> int: ...


# Factories let the pipeline create sessions lazily.
OscarFactory = Callable[[], ContextManager[_OscarSession]]
SheetsFactory = Callable[[], _SheetsLike]


@dataclass
class PipelineResult:
    record: ProcessingRecord
    status: ProcessingStatus
    message: str

    @property
    def success(self) -> bool:
        return self.status is ProcessingStatus.COMPLETED


class IntakeProcessor:
    """Processes one file at a time through the full intake workflow."""

    def __init__(
        self,
        config: AppConfig,
        repository: RecordRepository,
        audit: AuditLog,
        extractor: "Extractor",
        validator: "SignatureValidator",
        oscar_factory: OscarFactory,
        sheets_factory: SheetsFactory,
        confirm_update: Optional[Callable[[ProcessingRecord, list], bool]] = None,
        completeness_validator: "Optional[CompletenessValidator]" = None,
    ):
        self._config = config
        self._repo = repository
        self._audit = audit
        self._extractor = extractor
        self._validator = validator
        self._completeness = completeness_validator
        self._oscar_factory = oscar_factory
        self._sheets_factory = sheets_factory
        # Called when chart values differ from the tool; returns True to apply
        # the update. Default: never update (no GUI available / headless).
        self.confirm_update: Callable[[ProcessingRecord, list], bool] = (
            confirm_update or (lambda record, discrepancies: False)
        )
        # Called when the questionnaire has unanswered rows; returns True to
        # continue processing (Approve & Continue) or False to stop and return
        # the form to the patient. Default (headless): continue, since the check
        # is advisory and there is no operator to decide.
        self.confirm_incomplete: Callable[[ProcessingRecord, object], bool] = (
            lambda record, completeness: True
        )
        # Interactive match helpers (set by the GUI worker). Headless -> None,
        # so auto-matching alone is used.
        self.select_patient = None   # (candidates: list[dict]) -> demographic_no | None
        self.ask_email = None        # (patient_label: str) -> email | None

    # ------------------------------------------------------------------
    def process(self, pdf_path: Path) -> PipelineResult:
        """Run the full pipeline for ``pdf_path``.

        Always returns a :class:`PipelineResult`; unexpected exceptions are
        caught, recorded as ``ERROR`` and surfaced rather than propagated, so a
        single bad file never crashes the dashboard's worker thread.
        """
        record = ProcessingRecord(
            source_filename=pdf_path.name,
            stored_path=str(pdf_path),
            status=ProcessingStatus.PENDING,
        )

        try:
            record.file_hash = sha256_file(pdf_path)
        except OSError as exc:
            return self._fail(record, f"Cannot read file: {exc}")

        # Note (don't block) if we've seen this exact file before. Re-dropping a
        # file re-processes it; we just record the prior match for the audit log.
        existing = self._repo.find_by_hash(record.file_hash)

        self._repo.insert(record)
        if existing and existing.status is ProcessingStatus.COMPLETED:
            self._audit.record(
                AuditEvent.DUPLICATE_DETECTED,
                f"re-processing; hash matches earlier record {existing.id}",
                record_id=record.id,
            )
        self._audit.record(AuditEvent.FILE_RECEIVED, pdf_path.name, record_id=record.id)

        try:
            return self._run_stages(record, pdf_path)
        except Exception as exc:  # last-resort guard
            logger.exception("Unhandled error processing %s", pdf_path.name)
            return self._fail(record, f"Unexpected error: {exc}")

    # ------------------------------------------------------------------
    def _run_stages(self, record: ProcessingRecord, pdf_path: Path) -> PipelineResult:
        # ---- 1. Extraction (with OCR fallback) ----
        record.status = ProcessingStatus.EXTRACTING
        self._repo.update(record)

        extraction = self._extractor.extract(pdf_path)
        record.questionnaire_type = extraction.questionnaire_type
        record.pdf_kind = extraction.pdf_kind
        record.used_ocr = extraction.used_ocr
        record.demographics = extraction.demographics
        record.answers = extraction.answers  # for "form:<field>" sheet columns
        self._repo.update(record)

        self._audit.record(
            AuditEvent.CLASSIFIED,
            f"{extraction.pdf_kind.value}; type={extraction.questionnaire_type.value}",
            record_id=record.id,
        )
        if extraction.used_ocr:
            self._audit.record(AuditEvent.OCR_RUN, "scanned/handwritten input", record_id=record.id)
        self._audit.record(
            AuditEvent.EXTRACTED,
            f"identifiers: name={bool(record.demographics.full_name)}, "
            f"email={bool(record.demographics.email)}, dob={bool(record.demographics.dob)}; "
            f"warnings={'; '.join(extraction.warnings) or 'none'}",
            record_id=record.id,
        )

        # ---- 2. Signature / consent validation (advisory — never blocks upload) ----
        record.status = ProcessingStatus.VALIDATING
        self._repo.update(record)

        signature = self._validator.validate(pdf_path)
        record.signature_present = signature.signed
        self._repo.update(record)

        if not signature.signed:
            # No signature found: continue processing and upload the document.
            # Final status will be COMPLETED_NO_SIGNATURE so staff can follow up.
            self._audit.record(
                AuditEvent.SIGNATURE_MISSING,
                f"{signature.method}: {signature.detail}",
                record_id=record.id,
            )
        else:
            self._audit.record(
                AuditEvent.VALIDATION_PASSED,
                f"{signature.method}: {signature.detail}",
                record_id=record.id,
            )

        # ---- 2b. Response completeness (advisory gate with operator decision) ----
        if self._completeness is not None:
            completeness = self._completeness.validate(
                pdf_path, record.questionnaire_type
            )
            if completeness.checked:
                self._audit.record(
                    AuditEvent.COMPLETENESS_CHECKED, completeness.detail, record_id=record.id
                )
            if completeness.checked and not completeness.complete:
                self._audit.record(
                    AuditEvent.COMPLETENESS_INCOMPLETE,
                    f"unanswered on {completeness.pages_label}",
                    record_id=record.id,
                )
                approved = False
                try:
                    approved = bool(self.confirm_incomplete(record, completeness))
                except Exception:
                    logger.exception("Incomplete-form confirmation failed; returning to patient")
                    approved = False
                if not approved:
                    return self._incomplete_declined(record, pdf_path, completeness)
                self._audit.record(
                    AuditEvent.INCOMPLETE_APPROVED,
                    f"operator approved despite unanswered {completeness.pages_label}",
                    record_id=record.id,
                )

        # ---- 3. Find patient in OSCAR ----
        if not record.demographics.has_minimum_identifiers():
            return self._patient_not_found(
                record, "No usable identifiers extracted to search OSCAR."
            )

        self._audit.record(
            AuditEvent.PATIENT_SEARCH,
            "searching OSCAR by name/email/dob",
            record_id=record.id,
        )

        try:
            with self._oscar_factory() as oscar:
                try:
                    patient = oscar.find_patient(
                        record.demographics,
                        select_cb=self.select_patient,
                        email_cb=self.ask_email,
                    )
                except PatientNotFoundError as exc:
                    return self._patient_not_found(record, str(exc))

                record.demographic_no = patient.demographic_no
                record.status = ProcessingStatus.UPLOADING

                # OSCAR is the source of truth for contact info: pull the chart's
                # email and province for the sheet (never the form's clinic email).
                try:
                    chart = oscar.get_demographic_details(patient.demographic_no)
                    if chart.get("email"):
                        record.demographics.email = chart["email"]
                    record.demographics.province = chart.get("province") or None
                    # Column A (MSP/Private): classify from the chart Booking Alert.
                    record.demographics.program_status = (
                        self._config.program.classify(chart.get("booking_alert")) or None
                    )
                    logger.info(
                        "Chart program status=%r (booking alert present=%s)",
                        record.demographics.program_status, bool(chart.get("booking_alert")),
                    )
                except Exception:
                    logger.debug("Could not read chart contact info", exc_info=True)

                self._repo.update(record)
                self._audit.record(
                    AuditEvent.PATIENT_MATCHED,
                    f"demographic {patient.demographic_no} via {patient.matched_by}",
                    record_id=record.id,
                )

                # ---- 4. Upload document into OSCAR ----
                description = UPLOAD_DOCUMENT_DESCRIPTION
                document_id = oscar.upload_document(patient, pdf_path, description)
                record.oscar_document_id = document_id
                self._repo.update(record)
                self._audit.record(
                    AuditEvent.DOCUMENT_UPLOADED,
                    f"type={self._config.oscar.document_type}; desc={description}",
                    record_id=record.id,
                )

                # ---- 4b. Sync chart with the assessment tool (with approval) ----
                if self._config.oscar.update_chart:
                    self._sync_chart(record, patient, oscar)
        except OscarError as exc:
            return self._fail(record, f"OSCAR error: {exc}")

        # ---- 5. Update Google Sheets (PHI-safe) ----
        try:
            sheets = self._sheets_factory()
            row = sheets.append_record(record)
            record.sheets_row = row
            self._repo.update(record)
            self._audit.record(
                AuditEvent.SHEET_UPDATED, f"row {row}", record_id=record.id
            )
        except Exception as exc:
            # Upload succeeded but logging failed — surface as error for review,
            # but do NOT re-upload. Operator can re-run the Sheets sync.
            logger.exception("Sheets update failed after successful upload")
            return self._fail(
                record,
                f"Uploaded to OSCAR (doc {record.oscar_document_id}) but Sheets update failed: {exc}",
            )

        # ---- 6. Done ----
        if record.signature_present is False:
            record.status = ProcessingStatus.COMPLETED_NO_SIGNATURE
            record.message = "Uploaded to OSCAR — consent signature missing on form."
        else:
            record.status = ProcessingStatus.COMPLETED
            record.message = "Processed successfully."
        moved = safe_move(pdf_path, self._config.folders.processed)
        record.stored_path = str(moved)
        self._repo.update(record)
        self._audit.record(AuditEvent.COMPLETED, str(moved), record_id=record.id)
        logger.info("Completed %s -> demographic %s", record.source_filename, record.demographic_no)
        return PipelineResult(record, record.status, record.message)

    # ------------------------------------------------------------------
    # Chart sync (update OSCAR demographics from the assessment tool)
    # ------------------------------------------------------------------
    def _sync_chart(self, record: ProcessingRecord, patient: PatientMatch, oscar) -> None:
        """Detect name/preferred-name/address differences and, with the
        operator's per-case approval, update the OSCAR chart."""
        try:
            chart = oscar.get_demographic_details(patient.demographic_no)
        except Exception:
            logger.exception("Could not read chart for sync")
            return
        discrepancies = self._detect_discrepancies(record.demographics, chart)
        if not discrepancies:
            return

        summary = "; ".join(
            f"{d.field_label}: tool='{d.tool_value}' vs OSCAR='{d.oscar_value}'"
            for d in discrepancies
        )
        self._audit.record(
            AuditEvent.PATIENT_MATCHED,
            f"chart discrepancies found: {summary}", record_id=record.id,
        )
        logger.info("Chart discrepancies for %s: %s", patient.demographic_no, summary)

        approved = False
        try:
            approved = bool(self.confirm_update(record, discrepancies))
        except Exception:
            logger.exception("Discrepancy confirmation failed; not updating chart")
            approved = False

        if not approved:
            self._audit.record(
                AuditEvent.PATIENT_MATCHED,
                "operator declined chart update", record_id=record.id,
            )
            return

        changes = {d.oscar_field: d.tool_value for d in discrepancies}
        ok = oscar.update_demographic(patient.demographic_no, changes)
        self._audit.record(
            AuditEvent.PATIENT_MATCHED,
            f"chart updated: {summary}" if ok else "chart update failed",
            record_id=record.id,
        )

    @staticmethod
    def _detect_discrepancies(tool: Demographics, chart: dict) -> list[Discrepancy]:
        """Compare tool vs chart for first/last/preferred name and address."""
        def diff(label, field, tool_val, oscar_val, require_tool=True):
            t = (tool_val or "").strip()
            o = (oscar_val or "").strip()
            if require_tool and not t:
                return None
            if t and t.lower() != o.lower():
                return Discrepancy(label, field, t, o)
            return None

        out: list[Discrepancy] = []
        # Address is intentionally NOT compared/updated: OSCAR is the source of
        # truth for the patient's address (the form often carries the clinic
        # office address), so we never overwrite it from the form.
        for d in (
            diff("First Name", "first_name", tool.first_name, chart.get("first")),
            diff("Last Name", "last_name", tool.last_name, chart.get("last")),
            diff("Preferred Name", "pref_name", tool.pref_name, chart.get("pref")),
        ):
            if d is not None:
                out.append(d)

        # Date of birth: normalised compare. This surfaces when the operator
        # confirmed a name-matched chart despite a DOB difference, so they can
        # choose to correct OSCAR (Update) or keep it (the form DOB may be the
        # mistyped one).
        tdob = Demographics.normalise_dob(tool.dob) or ""
        cdob = Demographics.normalise_dob(chart.get("dob")) or ""
        if tdob and cdob and tdob != cdob:
            out.append(Discrepancy("Date of Birth", "dob", tdob, cdob))
        return out

    # ------------------------------------------------------------------
    # Terminal-state helpers
    # ------------------------------------------------------------------
    def _incomplete_declined(
        self, record: ProcessingRecord, pdf_path: Path, completeness
    ) -> PipelineResult:
        """Operator declined an incomplete form: stop, move to rejected/, NO
        OSCAR upload, NO Sheets write — the form goes back to the patient."""
        record.status = ProcessingStatus.INCOMPLETE_DECLINED
        record.message = (
            f"Returned to patient: unanswered questions on {completeness.pages_label}."
        )
        moved = safe_move(pdf_path, self._config.folders.rejected)
        record.stored_path = str(moved)
        self._repo.update(record)
        self._audit.record(
            AuditEvent.INCOMPLETE_DECLINED,
            f"{record.message}; moved to {moved}; no OSCAR upload, no Sheets write",
            record_id=record.id,
        )
        logger.warning("Incomplete (declined) %s", record.source_filename)
        return PipelineResult(record, record.status, record.message)

    def _patient_not_found(self, record: ProcessingRecord, detail: str) -> PipelineResult:
        """Patient not in OSCAR: stop. NO upload, NO Sheets."""
        record.status = ProcessingStatus.PATIENT_NOT_FOUND
        record.message = f"Patient not found in OSCAR: {detail}"
        self._repo.update(record)
        self._audit.record(AuditEvent.PATIENT_NOT_FOUND, detail, record_id=record.id)
        logger.warning("Patient not found for %s", record.source_filename)
        return PipelineResult(record, record.status, record.message)

    def _fail(self, record: ProcessingRecord, message: str) -> PipelineResult:
        record.status = ProcessingStatus.ERROR
        record.message = message
        if record.id is None:
            self._repo.insert(record)
        else:
            self._repo.update(record)
        self._audit.record(AuditEvent.ERROR, message, record_id=record.id)
        return PipelineResult(record, record.status, message)
