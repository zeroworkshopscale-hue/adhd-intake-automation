"""Main application window.

Layout:
  * top: drag-and-drop zone for questionnaire PDFs
  * middle: live activity / status log of the current batch
  * bottom: table of processed patients (Demographic Number, Patient Name, Email)

Processing happens on a background :class:`ProcessingWorker` thread; the window
only ever touches Qt widgets on the GUI thread, updating them from worker
signals.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import ProcessingStatus
from ..pipeline import PipelineResult
from ..services import AppServices
from ..utils.logging_config import get_logger
from .consent_email import ConsentEmailDialog
from .drop_zone import DropZone
from .login_dialog import OscarLoginDialog
from .worker import ProcessingWorker

logger = get_logger(__name__)

_STATUS_COLORS = {
    ProcessingStatus.COMPLETED: QColor("#1a7f37"),
    ProcessingStatus.COMPLETED_NO_SIGNATURE: QColor("#d97706"),       # amber
    ProcessingStatus.INCOMPLETE_PATIENT_INFORMED: QColor("#7c3aed"),  # purple
    ProcessingStatus.REJECTED_NO_SIGNATURE: QColor("#b30000"),        # legacy
    ProcessingStatus.INCOMPLETE_DECLINED: QColor("#b36b00"),          # legacy
    ProcessingStatus.PATIENT_NOT_FOUND: QColor("#b36b00"),
    ProcessingStatus.ERROR: QColor("#b30000"),
}


class MainWindow(QMainWindow):
    def __init__(self, services: AppServices):
        super().__init__()
        self._services = services
        self._session_start = services.session_start
        self._sig_missing_only = False   # filter toggle for "Signature Missing" view
        # Batch progress counters (drive the "Processing X of N" bar).
        self._batch_total = 0
        self._batch_done = 0
        self.setWindowTitle("ADHD Intake Automation")
        self.resize(900, 700)

        self._build_ui()
        self._start_worker()
        self._refresh_patient_table()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        # Brand header (gradient styled via theme).
        header = QLabel("Adult ADHD Centre  ·  Intake Automation")
        header.setObjectName("BrandHeader")
        layout.addWidget(header)

        # ---- Top row: upload (left)  |  activity + sheet button (right) ----
        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        # Left column: drag-and-drop / browse.
        self._drop_zone = DropZone()
        self._drop_zone.files_dropped.connect(self._on_files_dropped)
        self._drop_zone.drop_rejected.connect(
            lambda msg: QMessageBox.information(self, "Could not add file", msg)
        )
        top_row.addWidget(self._drop_zone, 1)

        # Right column: Activity log + "open Excel/copy sheet" button beneath it.
        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        activity_label = QLabel("Activity")
        activity_label.setObjectName("SectionLabel")
        right_col.addWidget(activity_label)
        self._activity = QPlainTextEdit()
        self._activity.setReadOnly(True)
        self._activity.setMaximumBlockCount(1000)
        right_col.addWidget(self._activity, 1)
        self._open_sheet_btn = QPushButton("Open Patients' Sheet")
        self._open_sheet_btn.clicked.connect(self._open_copy_sheet)
        right_col.addWidget(self._open_sheet_btn)
        top_row.addLayout(right_col, 1)

        layout.addLayout(top_row)

        # ---- Progress bar (visible while a batch is processing) ----
        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        self._progress_label = QLabel("")
        self._progress_label.setObjectName("SectionLabel")
        self._progress_label.hide()
        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m")
        self._progress.setFixedHeight(20)
        self._progress.hide()
        progress_row.addWidget(self._progress_label)
        progress_row.addWidget(self._progress, 1)
        layout.addLayout(progress_row)

        # ---- Processed Patients (full-width list) ----
        table_header_row = QHBoxLayout()
        table_label = QLabel("Processed Patients")
        table_label.setObjectName("SectionLabel")
        table_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        table_header_row.addWidget(table_label)
        table_header_row.addStretch(1)
        self._sig_filter_btn = QPushButton("Signature Missing")
        self._sig_filter_btn.setCheckable(True)
        self._sig_filter_btn.setToolTip(
            "Show only patients whose consent signature is missing"
        )
        self._sig_filter_btn.toggled.connect(self._on_sig_filter_toggled)
        table_header_row.addWidget(self._sig_filter_btn)
        layout.addLayout(table_header_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Demographic No", "Patient Name", "Email", "Status"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Copyable: let the operator select any single cell (e.g. just the email)
        # or drag across cells, and copy with Ctrl+C or the right-click menu.
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_table_menu)
        copy_sc = QShortcut(QKeySequence.StandardKey.Copy, self._table)
        copy_sc.activated.connect(self._copy_selection)
        layout.addWidget(self._table, 1)  # full width, takes remaining height

        self.setCentralWidget(central)
        self.resize(1040, 720)
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------------
    def _start_worker(self) -> None:
        self._thread = QThread(self)
        self._worker = ProcessingWorker(
            self._services.processor, self._services.config.folders.incoming
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)

        self._worker.started_file.connect(self._on_started_file)
        self._worker.finished_file.connect(self._on_finished_file)
        self._worker.error_file.connect(self._on_error_file)
        self._worker.confirm_update_requested.connect(self._on_confirm_update)
        self._worker.select_patient_requested.connect(self._on_select_patient)
        self._worker.email_requested.connect(self._on_ask_email)
        self._worker.idle.connect(lambda: self.statusBar().showMessage("Idle"))

        self._thread.start()

    def _on_select_patient(self, candidates) -> None:
        """Operator picks the correct patient from a DOB-matched list."""
        from .match_dialogs import PatientSelectDialog

        chosen = PatientSelectDialog.ask(candidates, parent=self)
        self._log(
            f"  Patient selection: {'chose demographic ' + chosen if chosen else 'none selected'}"
        )
        self._worker.provide_patient_selection(chosen)

    def _on_ask_email(self, patient_label: str) -> None:
        """Operator enters the patient's email to pin the exact chart."""
        from .match_dialogs import EmailPromptDialog

        email = EmailPromptDialog.ask(patient_label, parent=self)
        self._log(f"  Email match: {'entered' if email else 'skipped'}")
        self._worker.provide_email(email)

    def _show_incomplete_email(self, record, pages_label: str = "") -> None:
        from .consent_email import ConsentEmailDialog, build_incomplete_email

        first = record.demographics.first_name
        dlg = ConsentEmailDialog(
            first_name=first,
            patient_email=record.demographics.email,
            source_filename=record.source_filename,
            parent=self,
            title="Incomplete sections  --  request completion",
            heading_text="Incomplete sections  --  request completion",
            info_text=(
                f"The form '{record.source_filename}' had unanswered sections{' on ' + pages_label if pages_label else ''}.\n"
                "It was uploaded to OSCAR as 'ADHD Assessment Tool - Incomplete'.\n"
                "Copy the email below and send it to the patient from Outlook."
            ),
            body_text=build_incomplete_email(first, pages_label or "some pages"),
        )
        dlg.exec()

    def _on_confirm_update(self, record, discrepancies) -> None:
        """Show the red Alert dialog and return the decision to the worker.

        The dialog returns the list of approved rows (per-row or all), so the
        operator can overwrite individual fields or every field in OSCAR.
        """
        from .alert_dialog import DiscrepancyAlertDialog

        approved = DiscrepancyAlertDialog.ask(
            record.patient_name() or record.source_filename, discrepancies, parent=self
        )
        if approved:
            fields = ", ".join(d.field_label for d in approved)
            self._log(
                f"  Chart mismatch for {record.patient_name()}: updating "
                f"{len(approved)} of {len(discrepancies)} field(s) in OSCAR — {fields}"
            )
        else:
            self._log(
                f"  Chart mismatch for {record.patient_name()}: kept OSCAR unchanged"
            )
        self._worker.provide_confirmation(approved)

    # ------------------------------------------------------------------
    def _on_files_dropped(self, paths: list[Path]) -> None:
        # Start a fresh count if the previous batch had already finished.
        if self._batch_total > 0 and self._batch_done >= self._batch_total:
            self._batch_total = 0
            self._batch_done = 0
        self._batch_total += len(paths)
        for path in paths:
            self._log(f"Queued: {path.name}")
            self._worker.enqueue(path)
        self.statusBar().showMessage(f"Processing {len(paths)} file(s)…")
        self._update_progress()

    def _update_progress(self, current_name: str = "") -> None:
        """Refresh the 'Processing X of N' bar from the batch counters."""
        total, done = self._batch_total, self._batch_done
        if total <= 0:
            self._progress.hide()
            self._progress_label.hide()
            return
        self._progress.show()
        self._progress_label.show()
        self._progress.setRange(0, total)
        self._progress.setValue(min(done, total))
        if done >= total:
            self._progress_label.setText(f"Done — processed {total} file(s)")
        else:
            shown = min(done + 1, total)
            label = f"Processing {shown} of {total}"
            if current_name:
                label += f"  ·  {current_name}"
            self._progress_label.setText(label)

    def _on_started_file(self, filename: str) -> None:
        self._log(f"Processing: {filename}")
        self.statusBar().showMessage(f"Processing {filename}…")
        self._drop_zone.set_state("processing", f"Processing {filename}")
        self._update_progress(filename)

    def _on_sig_filter_toggled(self, checked: bool) -> None:
        self._sig_missing_only = checked
        self._sig_filter_btn.setText(
            "Signature Missing ✕" if checked else "Signature Missing"
        )
        self._refresh_patient_table()

    def _on_finished_file(self, result: PipelineResult) -> None:
        record = result.record
        self._batch_done += 1
        self._update_progress()
        self._log(f"  -> {record.status.value}: {record.message}")
        if record.status is ProcessingStatus.COMPLETED:
            self._drop_zone.set_state(
                "success", f"{record.patient_name() or record.source_filename}  --  uploaded to OSCAR"
            )
            self._refresh_patient_table()
        elif record.status is ProcessingStatus.COMPLETED_NO_SIGNATURE:
            self._drop_zone.set_state(
                "success",
                f"{record.patient_name() or record.source_filename}  --  uploaded; signature missing",
            )
            self._refresh_patient_table()
            self._show_consent_email(record)
        elif record.status is ProcessingStatus.INCOMPLETE_PATIENT_INFORMED:
            self._drop_zone.set_state(
                "success",
                f"{record.patient_name() or record.source_filename}  --  uploaded (incomplete)",
            )
            self._refresh_patient_table()
            self._show_incomplete_email(record)
        elif record.status is ProcessingStatus.REJECTED_NO_SIGNATURE:
            # Legacy status  --  kept for records in older databases.
            self._drop_zone.set_state("error", "Rejected  --  consent not signed")
            self._show_consent_email(record)
        elif record.status is ProcessingStatus.INCOMPLETE_DECLINED:
            # Legacy status  --  kept for records in older databases.
            self._drop_zone.set_state("error", "Incomplete  --  returned to patient")
        elif record.status is ProcessingStatus.PATIENT_NOT_FOUND:
            self._drop_zone.set_state("error", "Patient not found / not confirmed")
        elif (
            record.status is ProcessingStatus.ERROR
            and "login failed" in (record.message or "").lower()
        ):
            self._drop_zone.set_state("error", "OSCAR login failed")
            self._handle_login_failure(record)
        else:
            self._drop_zone.set_state("error", record.message[:60] or "Processing error")

    def _handle_login_failure(self, record) -> None:
        """Wrong OSCAR password: tell the operator, re-prompt, and retry the file."""
        QMessageBox.warning(
            self,
            "OSCAR login failed",
            "OSCAR rejected the login. Please re-enter your OSCAR username and password.",
        )
        creds = OscarLoginDialog.prompt(
            base_url=self._services.config.oscar.base_url,
            default_username=self._services.oscar_credentials.username,
            parent=self,
        )
        if creds is None:
            self._log("Login not updated  --  file not retried.")
            return
        self._services.set_oscar_credentials(*creds)
        self._log(f"OSCAR login updated for '{creds[0]}'. Retrying {record.source_filename}…")
        # The staged file is still in the incoming folder on error; retry it.
        if record.stored_path:
            self._worker.enqueue(Path(record.stored_path))

    def _on_error_file(self, filename: str, message: str) -> None:
        self._batch_done += 1
        self._update_progress()
        self._log(f"  -> ERROR {filename}: {message}")

    def _show_consent_email(self, record) -> None:
        """Offer the operator a ready-to-send consent reminder email."""
        dlg = ConsentEmailDialog(
            first_name=record.demographics.first_name,
            patient_email=record.demographics.email,
            source_filename=record.source_filename,
            parent=self,
        )
        dlg.exec()

    def _open_copy_sheet(self) -> None:
        path = self._services.config.sheets.local_path
        if not self._services.config.sheets.local_enabled:
            QMessageBox.information(
                self, "Copy sheet disabled",
                "The local copy sheet is turned off (sheets.mode in config.yaml).",
            )
            return
        if not path.exists():
            QMessageBox.information(
                self, "No rows yet",
                f"The copy sheet will be created here after the first completed file:\n\n{path}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------------
    def _log(self, text: str) -> None:
        self._activity.appendPlainText(text)

    def _refresh_patient_table(self) -> None:
        """Reload completed records into the table (respects Signature Missing filter)."""
        all_records = [
            r for r in self._services.repository.list_completed()
            if r.created_at >= self._session_start
        ]
        if self._sig_missing_only:
            records = [r for r in all_records
                       if r.status is ProcessingStatus.COMPLETED_NO_SIGNATURE]
        else:
            records = all_records
        self._table.setRowCount(len(records))
        for row, rec in enumerate(records):
            values = [
                rec.demographic_no or "",
                rec.patient_name(),
                rec.patient_email(),
                rec.status.value,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                color = _STATUS_COLORS.get(rec.status)
                if color and col == 3:
                    item.setForeground(color)
                self._table.setItem(row, col, item)

    # ------------------------------------------------------------------
    def _copy_selection(self) -> None:
        """Copy the selected cells (Demographic No / Name / Email …) to the
        clipboard  --  tab-separated within a row, newline between rows."""
        items = self._table.selectedItems()
        if not items:
            return
        cells: dict[int, dict[int, str]] = {}
        for it in items:
            cells.setdefault(it.row(), {})[it.column()] = it.text()
        lines = []
        for row in sorted(cells):
            cols = cells[row]
            lines.append("\t".join(cols[c] for c in sorted(cols)))
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.statusBar().showMessage("Copied to clipboard", 2000)

    def _show_table_menu(self, pos) -> None:
        if not self._table.selectedItems():
            return
        menu = QMenu(self._table)
        copy_action = menu.addAction("Copy")
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action is copy_action:
            self._copy_selection()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        logger.info("Shutting down")
        try:
            self._worker.stop()
            self._thread.quit()
            self._thread.wait(3000)
        finally:
            self._services.close()
        super().closeEvent(event)
