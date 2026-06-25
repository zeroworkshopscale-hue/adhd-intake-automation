"""Background processing worker.

Playwright's sync API and OCR are blocking, so files are processed off the GUI
thread. The worker consumes a queue of file paths and emits Qt signals for each
result. The OSCAR/Sheets clients are created inside ``run`` (this thread), which
is required because Playwright's sync API is bound to the thread that creates it.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..pipeline import IntakeProcessor, PipelineResult
from ..utils.files import safe_copy
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


class ProcessingWorker(QObject):
    """Runs in its own QThread, draining a queue of dropped PDFs."""

    started_file = Signal(str)               # filename
    finished_file = Signal(object)           # PipelineResult
    error_file = Signal(str, str)            # filename, message
    idle = Signal()
    # Emitted (to the GUI thread) when operator input is needed. The worker
    # thread blocks until the matching provide_* method is called.
    confirm_update_requested = Signal(object, object)   # (record, discrepancies)
    incomplete_requested = Signal(object, object)       # (record, CompletenessResult)
    review_requested = Signal(object, bool)             # (record, used_ocr)
    select_patient_requested = Signal(object)           # (candidates list[dict])
    email_requested = Signal(str)                       # (patient label)

    def __init__(self, processor: IntakeProcessor, incoming_dir: Path):
        super().__init__()
        self._processor = processor
        self._incoming_dir = incoming_dir
        self._queue: "queue.Queue[Path | None]" = queue.Queue()
        self._running = True
        # Cross-thread handshakes (one per interactive request type).
        self._confirm_event = threading.Event()
        self._confirm_result: list = []   # approved discrepancy rows (subset)
        self._incomplete_event = threading.Event()
        self._incomplete_result = True
        self._select_event = threading.Event()
        self._select_result = None
        self._email_event = threading.Event()
        self._email_result = None
        self._review_event = threading.Event()
        self._review_result = None
        # The pipeline calls these (in the worker thread) to ask the operator.
        self._processor.confirm_update = self._request_confirmation
        self._processor.confirm_incomplete = self._request_incomplete_decision
        self._processor.review_extraction = self._request_review
        self._processor.select_patient = self._request_patient_selection
        self._processor.ask_email = self._request_email

    def _request_review(self, record, used_ocr):
        """Blocking (worker-thread) request: operator reviews/edits a
        low-confidence extraction. Returns the edited values dict, or None."""
        self._review_result = None
        self._review_event.clear()
        self.review_requested.emit(record, bool(used_ocr))
        if not self._review_event.wait(timeout=900):
            logger.warning("Details review timed out; using the extracted values")
            return None
        return self._review_result

    def provide_review(self, result) -> None:
        """Called on the GUI thread with the operator's edited values (or None)."""
        self._review_result = result
        self._review_event.set()

    def _request_confirmation(self, record, discrepancies) -> list:
        """Blocking (worker-thread) request for operator approval via the GUI.

        Returns the list of approved discrepancy rows (a subset; empty = none),
        so the operator can update individual fields or all of them.
        """
        self._confirm_result = []
        self._confirm_event.clear()
        self.confirm_update_requested.emit(record, discrepancies)
        # Wait for the GUI thread to answer (generous timeout; default: decline).
        if not self._confirm_event.wait(timeout=300):
            logger.warning("Discrepancy confirmation timed out; not updating chart")
            return []
        return self._confirm_result

    def provide_confirmation(self, approved) -> None:
        """Called on the GUI thread with the operator's decision (list of rows)."""
        self._confirm_result = list(approved) if approved else []
        self._confirm_event.set()

    def _request_incomplete_decision(self, record, completeness) -> bool:
        """Blocking (worker-thread) request: continue despite unanswered rows?"""
        self._incomplete_result = False
        self._incomplete_event.clear()
        self.incomplete_requested.emit(record, completeness)
        if not self._incomplete_event.wait(timeout=600):
            logger.warning("Incomplete-form decision timed out; returning to patient")
            return False
        return self._incomplete_result

    def provide_incomplete_decision(self, approved: bool) -> None:
        """Called on the GUI thread: True = Approve & Continue, False = Decline."""
        self._incomplete_result = bool(approved)
        self._incomplete_event.set()

    def _request_patient_selection(self, candidates):
        """Blocking (worker-thread) request for the operator to pick a patient."""
        self._select_result = None
        self._select_event.clear()
        self.select_patient_requested.emit(candidates)
        if not self._select_event.wait(timeout=300):
            return None
        return self._select_result

    def provide_patient_selection(self, demographic_no) -> None:
        self._select_result = demographic_no
        self._select_event.set()

    def _request_email(self, patient_label: str):
        """Blocking (worker-thread) request for the patient's email."""
        self._email_result = None
        self._email_event.clear()
        self.email_requested.emit(patient_label or "")
        if not self._email_event.wait(timeout=300):
            return None
        return self._email_result

    def provide_email(self, email) -> None:
        self._email_result = email
        self._email_event.set()

    def enqueue(self, pdf_path: Path) -> None:
        self._queue.put(pdf_path)

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)  # unblock the queue

    def run(self) -> None:
        logger.info("Processing worker started")
        while self._running:
            item = self._queue.get()
            if item is None:
                break
            try:
                self._process_one(item)
            except Exception as exc:  # never let the worker thread die
                logger.exception("Worker failed on %s", item)
                self.error_file.emit(item.name, str(exc))
            finally:
                if self._queue.empty():
                    self.idle.emit()
        logger.info("Processing worker stopped")

    def _process_one(self, pdf_path: Path) -> None:
        self.started_file.emit(pdf_path.name)
        # Copy the dropped file into incoming/ so the original (e.g. an Outlook
        # temp file) is never mutated or moved out from under the user.
        staged = safe_copy(pdf_path, self._incoming_dir)
        result: PipelineResult = self._processor.process(staged)
        self.finished_file.emit(result)
