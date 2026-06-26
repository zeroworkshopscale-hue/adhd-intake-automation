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

from contextlib import contextmanager
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

# Description applied to complete questionnaires uploaded into OSCAR Documents.
UPLOAD_DOCUMENT_DESCRIPTION = "ADHD Assessment Tool"
# Description used when the form has unanswered sections but is uploaded anyway.
UPLOAD_DOCUMENT_DESCRIPTION_INCOMPLETE = "ADHD Assessment Tool - Incomplete"


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
        # OSCAR session reuse: when True (set by the GUI worker), ONE logged-in
        # browser session is kept open and reused for the whole batch instead of
        # launching the browser and logging in again for every file.
        self.reuse_oscar_session = False
        self.oscar_session = None       # the live, logged-in session object
        self._oscar_cm = None           # its context manager (to close it later)
        # Current session start; when set (by the GUI), a patient already uploaded
        # THIS session is skipped to avoid duplicate documents/rows even if the
        # form arrived as a different file. None -> patient-level guard disabled.
        self.session_start = None
        # Called when chart values differ from the tool; returns True to apply
        # the update. Default: never update (no GUI available / headless).
        self.confirm_update: Callable[[ProcessingRecord, list], bool] = (
            confirm_update or (lambda record, discrepancies: False)
        )
        # Called when a form has unanswered rows; returns True to process it as a
        # COMPLETE form anyway, False to flag it incomplete and email the patient.
        # Default (headless): False -> flag incomplete (the safe choice).
        self.confirm_incomplete: Callable[[ProcessingRecord, object], bool] = (
            lambda record, completeness: False
        )
        # Called for low-confidence extractions (handwritten/scanned, or too few
        # identifiers) so the operator can review/correct the details. Returns a
        # dict {"demographics": {...}, "answers": {...}} or None. Default: no-op.
        self.review_extraction: Callable[[ProcessingRecord, bool], object] = (
            lambda record, used_ocr: None
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

        # Have we already uploaded this exact file to OSCAR before? If so, do NOT
        # create a second document. Re-dropping the file becomes the recovery
        # action: skip OSCAR entirely and just write the sheet row if the earlier
        # run never got that far (e.g. it errored after the upload).
        existing = self._repo.find_by_hash(record.file_hash)

        self._repo.insert(record)
        self._audit.record(AuditEvent.FILE_RECEIVED, pdf_path.name, record_id=record.id)

        if existing is not None and existing.oscar_document_id:
            self._audit.record(
                AuditEvent.DUPLICATE_DETECTED,
                f"hash matches record {existing.id}, already uploaded as OSCAR "
                f"document {existing.oscar_document_id}; skipping OSCAR upload",
                record_id=record.id,
            )
            try:
                return self._handle_existing_upload(record, existing, pdf_path)
            except Exception as exc:  # last-resort guard
                logger.exception("Duplicate handling failed for %s", pdf_path.name)
                return self._fail(record, f"Unexpected error: {exc}")

        try:
            return self._run_stages(record, pdf_path)
        except Exception as exc:  # last-resort guard
            logger.exception("Unhandled error processing %s", pdf_path.name)
            return self._fail(record, f"Unexpected error: {exc}")

    # ------------------------------------------------------------------
    def _handle_existing_upload(
        self, record: ProcessingRecord, existing: ProcessingRecord, pdf_path: Path
    ) -> PipelineResult:
        """Re-drop of a file that was already uploaded to OSCAR.

        The document is already in the chart, so we must not upload a second
        copy. Reuse the prior OSCAR linkage and, if the earlier run never reached
        the sheet (for example it errored at the Sheets step), write the missing
        row now. This makes simply re-dropping the file the way to recover a
        half-finished upload — no OSCAR contact required.
        """
        record.demographic_no = existing.demographic_no
        record.oscar_document_id = existing.oscar_document_id
        record.signature_present = existing.signature_present

        # Re-extract locally (no OSCAR) so the sheet row carries the names and
        # answers; ``answers`` are in-memory only and can't come from the DB.
        try:
            extraction = self._extractor.extract(pdf_path)
            record.questionnaire_type = extraction.questionnaire_type
            record.pdf_kind = extraction.pdf_kind
            record.used_ocr = extraction.used_ocr
            record.demographics = extraction.demographics
            record.answers = extraction.answers
        except Exception:
            logger.exception("Re-extraction failed on duplicate; using stored demographics")
            record.demographics = existing.demographics
        # The OSCAR chart email captured on the first run beats a blank form.
        if not record.demographics.email and existing.demographics.email:
            record.demographics.email = existing.demographics.email

        # Preserve the earlier completion meaning (incomplete / signature missing).
        final_status = (
            existing.status if existing.status.is_success else ProcessingStatus.COMPLETED
        )

        if existing.sheets_row is not None:
            # Already uploaded AND already on the sheet: genuinely nothing to do.
            record.sheets_row = existing.sheets_row
            record.status = final_status
            record.message = (
                f"Already uploaded to OSCAR (document {existing.oscar_document_id}) and "
                "already recorded on the sheet — skipped to avoid a duplicate."
            )
            self._repo.update(record)
            self._audit.record(
                AuditEvent.COMPLETED,
                "duplicate skipped (already uploaded and on sheet)",
                record_id=record.id,
            )
        else:
            # Uploaded before but the sheet row is missing — write it now.
            record.status = ProcessingStatus.UPLOADING
            self._repo.update(record)
            try:
                sheets = self._sheets_factory()
                row = sheets.append_record(record)
                record.sheets_row = row
                self._repo.update(record)
                self._audit.record(
                    AuditEvent.SHEET_UPDATED,
                    f"row {row} (recovery; OSCAR upload skipped — already uploaded)",
                    record_id=record.id,
                )
            except Exception as exc:
                logger.exception("Sheets recovery write failed")
                return self._fail(
                    record,
                    f"Already in OSCAR (document {existing.oscar_document_id}) but the "
                    f"sheet row still could not be written: {exc}",
                )
            record.status = final_status
            record.message = (
                f"Already in OSCAR (document {existing.oscar_document_id}); skipped the "
                "duplicate upload and added the missing sheet row."
            )

        moved = safe_move(pdf_path, self._config.folders.processed)
        record.stored_path = str(moved)
        self._repo.update(record)
        self._audit.record(AuditEvent.COMPLETED, str(moved), record_id=record.id)
        logger.info(
            "Duplicate of %s -> reused OSCAR document %s",
            record.source_filename, record.oscar_document_id,
        )
        return PipelineResult(record, record.status, record.message)

    # ------------------------------------------------------------------
    def _skip_duplicate_patient(self, record, prior, pdf_path) -> PipelineResult:
        """This patient was already uploaded this session — reuse the existing
        OSCAR document and skip the duplicate upload + sheet row."""
        record.oscar_document_id = prior.oscar_document_id
        record.demographic_no = prior.demographic_no
        record.sheets_row = prior.sheets_row
        record.skipped_duplicate = True
        record.status = (
            prior.status if prior.status.is_success else ProcessingStatus.COMPLETED
        )
        record.message = (
            f"Already processed this session (OSCAR document "
            f"{prior.oscar_document_id}) — skipped to avoid a duplicate upload and row."
        )
        self._audit.record(
            AuditEvent.DUPLICATE_DETECTED,
            f"same patient (demographic {prior.demographic_no}) already uploaded this "
            f"session as record {prior.id}; skipped",
            record_id=record.id,
        )
        moved = safe_move(pdf_path, self._config.folders.processed)
        record.stored_path = str(moved)
        self._repo.update(record)
        self._audit.record(AuditEvent.COMPLETED, str(moved), record_id=record.id)
        logger.info(
            "Skipped duplicate patient %s (reused OSCAR document %s)",
            record.demographic_no, record.oscar_document_id,
        )
        return PipelineResult(record, record.status, record.message)

    # ------------------------------------------------------------------
    @contextmanager
    def _oscar_session(self):
        """Yield an OSCAR session.

        Reuse mode (GUI batch): keep ONE logged-in session open and reuse it for
        every file — the browser launch + OSCAR login happen only once. Otherwise
        (headless / tests): open a fresh per-file session and close it.
        """
        if not self.reuse_oscar_session:
            with self._oscar_factory() as oscar:
                yield oscar
            return
        if self.oscar_session is None:
            cm = self._oscar_factory()
            oscar = cm.__enter__()      # launch browser + log in (once per batch)
            self._oscar_cm = cm
            self.oscar_session = oscar
        yield self.oscar_session        # reused — left open for the next file

    def close_oscar_session(self) -> None:
        """Close the reused OSCAR session (called by the worker when it stops, or
        after an error so the next file logs in fresh). No-op if none is open."""
        if self._oscar_cm is not None:
            try:
                self._oscar_cm.__exit__(None, None, None)
            except Exception:
                logger.debug("Error closing reused OSCAR session", exc_info=True)
            finally:
                self._oscar_cm = None
                self.oscar_session = None

    # ------------------------------------------------------------------
    def _apply_review(self, record: ProcessingRecord, used_ocr: bool) -> None:
        """Ask the operator to review/correct a low-confidence extraction and
        apply whatever they confirm. No-op when there is no GUI callback."""
        try:
            result = self.review_extraction(record, used_ocr)
        except Exception:
            logger.exception("Details review failed; using the extracted values")
            return
        if not result:
            return
        demo_edits = result.get("demographics") or {}
        for field, value in demo_edits.items():
            setattr(record.demographics, field, value or None)
        if "dob" in demo_edits:
            # Keep the raw string in sync so DOB matching uses the corrected value.
            record.demographics.dob_raw = demo_edits["dob"] or None
        for key, value in (result.get("answers") or {}).items():
            record.answers[key] = value
        self._repo.update(record)
        self._audit.record(
            AuditEvent.EXTRACTED, "operator reviewed/corrected details", record_id=record.id
        )

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

        # ---- 1b. Manual review for low-confidence extractions ----
        # Handwritten/scanned forms (OCR) and forms we couldn't pull enough
        # identifiers from are unreliable, so let the operator confirm/correct the
        # details (pre-filled, fully editable) before we search OSCAR.
        if extraction.used_ocr or not record.demographics.has_minimum_identifiers():
            self._apply_review(record, extraction.used_ocr)

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

        # ---- 2b. Response completeness check (always upload; incomplete = different description) ----
        _form_incomplete = False
        if self._completeness is not None:
            completeness = self._completeness.validate(
                pdf_path, record.questionnaire_type
            )
            if completeness.checked:
                self._audit.record(
                    AuditEvent.COMPLETENESS_CHECKED, completeness.detail, record_id=record.id
                )
            if completeness.checked and not completeness.complete:
                questions_label = (
                    "; ".join(completeness.unanswered_questions)
                    or completeness.pages_label
                )
                # Ask the operator: process as a complete form, or send it back to
                # the patient? Default (headless) is to flag it incomplete.
                process_as_complete = False
                try:
                    process_as_complete = bool(self.confirm_incomplete(record, completeness))
                except Exception:
                    logger.exception("Incomplete-form decision failed; flagging incomplete")
                    process_as_complete = False

                if process_as_complete:
                    self._audit.record(
                        AuditEvent.COMPLETENESS_CHECKED,
                        f"operator processed as COMPLETE despite unanswered: {questions_label}",
                        record_id=record.id,
                    )
                else:
                    _form_incomplete = True
                    record.incomplete_questions = list(completeness.unanswered_questions)
                    self._audit.record(
                        AuditEvent.COMPLETENESS_INCOMPLETE,
                        f"operator requested patient completion; unanswered: {questions_label}",
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
            with self._oscar_session() as oscar:
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

                # Patient-level duplicate guard: if THIS patient was already
                # uploaded in this session (e.g. the same questionnaire arrived as
                # a different file, so the file-hash guard didn't catch it), do not
                # create a second OSCAR document or a duplicate sheet row.
                if self.session_start is not None:
                    prior = self._repo.find_uploaded_by_demographic(
                        patient.demographic_no,
                        since=self.session_start,
                        exclude_id=record.id,
                    )
                    if prior is not None:
                        return self._skip_duplicate_patient(record, prior, pdf_path)

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
                description = (
                    UPLOAD_DOCUMENT_DESCRIPTION_INCOMPLETE
                    if _form_incomplete
                    else UPLOAD_DOCUMENT_DESCRIPTION
                )
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
            # A reused session may now be in a bad state (login expired, browser
            # gone). Drop it so the next file logs in fresh.
            self.close_oscar_session()
            return self._fail(record, f"OSCAR error: {exc}")

        # ---- 5. Update Google Sheets / copy sheet (PHI-safe) ----
        sheet_note = ""
        try:
            sheets = self._sheets_factory()
            row = sheets.append_record(record)
            record.sheets_row = row
            self._repo.update(record)
            self._audit.record(
                AuditEvent.SHEET_UPDATED, f"row {row}", record_id=record.id
            )
        except Exception as exc:
            # OSCAR already has the document — never drop the patient over a copy-
            # sheet write (e.g. the CSV is open in Excel). Keep the record
            # completed so it still shows in Processed Patients; the data is in the
            # database and the row can be re-synced once the sheet is closed.
            logger.exception("Copy-sheet update failed after successful upload")
            self._audit.record(
                AuditEvent.ERROR,
                f"copy-sheet write failed (kept as completed): {exc}",
                record_id=record.id,
            )
            sheet_note = (
                " (copy sheet was unavailable — close Excel; this row is saved in "
                "the app and can be re-synced.)"
            )

        # ---- 6. Done ----
        if _form_incomplete:
            record.status = ProcessingStatus.INCOMPLETE_PATIENT_INFORMED
            missing = "; ".join(record.incomplete_questions)
            record.message = (
                "Uploaded to OSCAR as incomplete — email patient to complete missing sections."
                + (f" Unanswered: {missing}" if missing else "")
            )
        elif record.signature_present is False:
            record.status = ProcessingStatus.COMPLETED_NO_SIGNATURE
            record.message = "Uploaded to OSCAR — consent signature missing on form."
        else:
            record.status = ProcessingStatus.COMPLETED
            record.message = "Processed successfully."
        record.message += sheet_note
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

        try:
            decision = self.confirm_update(record, discrepancies)
        except Exception:
            logger.exception("Discrepancy confirmation failed; not updating chart")
            decision = []

        # Back-compat: a bool True means "update all"; False/None means none.
        # Otherwise the operator returns the subset of rows to apply.
        if decision is True:
            approved = list(discrepancies)
        elif not decision:
            approved = []
        else:
            approved = list(decision)

        if not approved:
            self._audit.record(
                AuditEvent.PATIENT_MATCHED,
                "operator declined chart update", record_id=record.id,
            )
            return

        applied_summary = "; ".join(
            f"{d.field_label}: '{d.oscar_value}' -> '{d.tool_value}'" for d in approved
        )
        def _oscar_value(d: Discrepancy) -> str:
            if d.oscar_field == "pronoun":
                return self._PRONOUN_NORM.get(d.tool_value.strip().lower(), d.tool_value)
            return d.tool_value

        changes = {d.oscar_field: _oscar_value(d) for d in approved}
        ok = oscar.update_demographic(patient.demographic_no, changes)
        self._audit.record(
            AuditEvent.PATIENT_MATCHED,
            f"chart updated ({len(approved)}/{len(discrepancies)} field(s)): {applied_summary}"
            if ok else f"chart update failed for: {applied_summary}",
            record_id=record.id,
        )

    # Form labels → OSCAR-canonical pronoun values.
    # "He/His" on the form is stored as "He/Him" in OSCAR — treat as equivalent.
    _PRONOUN_NORM: dict[str, str] = {
        "he/his":   "he/him",
        "she/hers": "she/her",
    }

    @classmethod
    def _normalise_pronoun(cls, value: str) -> str:
        return cls._PRONOUN_NORM.get(value.strip().lower(), value.strip().lower())

    @classmethod
    def _detect_discrepancies(cls, tool: Demographics, chart: dict) -> list[Discrepancy]:
        """Compare tool vs chart for first/last/preferred name and address."""
        def diff(label, field, tool_val, oscar_val, require_tool=True):
            t = (tool_val or "").strip()
            o = (oscar_val or "").strip()
            if require_tool and not t:
                return None
            if t and t.lower() != o.lower():
                return Discrepancy(label, field, t, o)
            return None

        def diff_pronoun(tool_val, oscar_val):
            t = (tool_val or "").strip()
            o = (oscar_val or "").strip()
            if not t:
                return None
            if cls._normalise_pronoun(t) != cls._normalise_pronoun(o):
                return Discrepancy("Pronoun", "pronoun", t, o)
            return None

        out: list[Discrepancy] = []
        # Address is intentionally NOT compared/updated: OSCAR is the source of
        # truth for the patient's address (the form often carries the clinic
        # office address), so we never overwrite it from the form.
        for d in (
            diff("First Name", "first_name", tool.first_name, chart.get("first")),
            diff("Last Name", "last_name", tool.last_name, chart.get("last")),
            diff("Preferred Name", "pref_name", tool.pref_name, chart.get("pref")),
            diff_pronoun(tool.pronoun, chart.get("pronoun")),
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
