"""Interactive patient-matching dialogs.

* PatientSelectDialog — when several patients share the date of birth, the
  operator picks the correct one from a list.
* EmailPromptDialog — ask the operator for the patient's email to pin the
  exact chart when name/DOB can't resolve it.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .widgets import AnimatedDialog


class PatientSelectDialog(AnimatedDialog):
    """Pick one patient from a list (e.g. all patients with a given DOB)."""

    def __init__(self, candidates: Sequence[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Patient")
        self.setModal(True)
        self.setMinimumWidth(600)
        self._candidates = list(candidates)
        self._chosen: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        header = QLabel("Select the correct patient")
        header.setObjectName("BrandHeader")
        layout.addWidget(header)

        has_notes = any(c.get("note") for c in self._candidates)
        if has_notes:
            info = QLabel(
                "A patient with a <b>matching name</b> was found, but the date of "
                "birth on the form does not match the chart (see the <b>Match</b> "
                "column). Confirm this is the same person and click "
                "<b>Use Selected Patient</b>, or choose <b>None of these</b>."
            )
        else:
            info = QLabel(
                "More than one patient matches this date of birth. Please select the "
                "chart that belongs to this assessment tool, then click "
                "<b>Use Selected Patient</b>. If none is correct, choose "
                "<b>None of these</b>."
            )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        cols = ["Demographic #", "Name (Last, First)", "DOB", "Email", "Sex"]
        if has_notes:
            cols.append("Match")
        self._table = QTableWidget(len(self._candidates), len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        for row, c in enumerate(self._candidates):
            name = ", ".join(p for p in (c.get("last"), c.get("first")) if p)
            cells = [
                c.get("demographic_no", ""), name, c.get("dob", ""),
                c.get("email", ""), c.get("sex", ""),
            ]
            if has_notes:
                cells.append(c.get("note", ""))
            for col, val in enumerate(cells):
                self._table.setItem(row, col, QTableWidgetItem(str(val)))
        self._table.doubleClicked.connect(self._use_selected)
        if self._candidates:
            self._table.selectRow(0)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        none_btn = QPushButton("None of these")
        none_btn.setObjectName("SecondaryButton")
        none_btn.clicked.connect(self.reject)
        buttons.addWidget(none_btn)
        use_btn = QPushButton("Use Selected Patient")
        use_btn.setDefault(True)
        use_btn.clicked.connect(self._use_selected)
        buttons.addWidget(use_btn)
        layout.addLayout(buttons)

    def _use_selected(self) -> None:
        row = self._table.currentRow()
        if 0 <= row < len(self._candidates):
            self._chosen = str(self._candidates[row].get("demographic_no") or "")
            self.accept()

    @classmethod
    def ask(cls, candidates: Sequence[dict], parent=None) -> Optional[str]:
        dlg = cls(candidates, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg._chosen:
            return dlg._chosen
        return None


class EmailPromptDialog(AnimatedDialog):
    """Ask the operator for the patient's email to find the exact chart."""

    def __init__(self, patient_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Match by Email")
        self.setModal(True)
        self.setMinimumWidth(440)
        self._email: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        header = QLabel("Match by email")
        header.setObjectName("BrandHeader")
        layout.addWidget(header)

        info = QLabel(
            f"We couldn't confidently match <b>{patient_label or 'this patient'}</b> "
            f"by name or date of birth.<br>If you have the patient's email, enter it "
            f"to find the exact chart — or skip to leave it unmatched."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        self._field = QLineEdit()
        self._field.setPlaceholderText("patient@example.com")
        self._field.returnPressed.connect(self._search)
        layout.addWidget(self._field)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        skip_btn = QPushButton("Skip")
        skip_btn.setObjectName("SecondaryButton")
        skip_btn.clicked.connect(self.reject)
        buttons.addWidget(skip_btn)
        search_btn = QPushButton("Search by Email")
        search_btn.setDefault(True)
        search_btn.clicked.connect(self._search)
        buttons.addWidget(search_btn)
        layout.addLayout(buttons)

    def _search(self) -> None:
        text = self._field.text().strip()
        if text:
            self._email = text
            self.accept()

    @classmethod
    def ask(cls, patient_label: str, parent=None) -> Optional[str]:
        dlg = cls(patient_label, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg._email
        return None
