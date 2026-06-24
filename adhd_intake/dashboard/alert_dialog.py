"""Red 'Alert' dialog shown when the assessment tool and OSCAR chart disagree.

Lists each differing field (tool value vs OSCAR value) with a per-row checkbox,
so the operator can update **every** field at once or pick **individual** rows.
``ask`` returns the list of approved :class:`Discrepancy` rows (empty = keep
OSCAR unchanged).
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import theme
from .widgets import AnimatedDialog


class DiscrepancyAlertDialog(AnimatedDialog):
    """Modal alert for chart/tool discrepancies with per-row selection."""

    def __init__(self, patient_name: str, discrepancies: Sequence, parent=None):
        super().__init__(parent)
        self._discrepancies = list(discrepancies)
        self._result: list = []          # approved rows (empty = none)

        self.setWindowTitle("Alert")
        self.setModal(True)
        self.setMinimumWidth(620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        heading = QLabel("⚠  Alert")
        heading.setStyleSheet(
            f"background:{theme.BRAND_GRADIENT}; color:white; font-size:21px; "
            "font-weight:800; letter-spacing:1px; padding:14px 18px; border-radius:10px;"
        )
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        info = QLabel(
            f"The patient information in the <b>Assessment Tool</b> is different "
            f"from the information currently stored in <b>OSCAR</b> for "
            f"<b>{patient_name}</b>.<br><br>"
            f"Tick the rows you want to write into OSCAR, then choose "
            f"<b>Update Selected</b> — or <b>Update All</b> to overwrite every "
            f"field below."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        table = QTableWidget(len(self._discrepancies), 4)
        table.setHorizontalHeaderLabels(
            ["Update?", "Field", "Assessment Tool", "OSCAR (current)"]
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        for row, d in enumerate(self._discrepancies):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked)
            check.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 0, check)
            table.setItem(row, 1, QTableWidgetItem(d.field_label))
            tool_item = QTableWidgetItem(d.tool_value or "(blank)")
            tool_item.setForeground(Qt.GlobalColor.darkGreen)
            table.setItem(row, 2, tool_item)
            oscar_item = QTableWidgetItem(d.oscar_value or "(blank)")
            oscar_item.setForeground(Qt.GlobalColor.red)
            table.setItem(row, 3, oscar_item)
        table.resizeRowsToContents()
        self._table = table
        layout.addWidget(table)

        question = QLabel(
            "Updating OSCAR overwrites the existing chart value with the "
            "Assessment Tool value for each selected field."
        )
        question.setWordWrap(True)
        question.setStyleSheet("font-weight:600; padding-top:6px;")
        layout.addWidget(question)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)

        keep_btn = QPushButton("Keep Existing OSCAR Data")
        keep_btn.setObjectName("SecondaryButton")
        keep_btn.clicked.connect(self._keep)
        buttons.addWidget(keep_btn)

        update_sel_btn = QPushButton("Update Selected")
        update_sel_btn.clicked.connect(self._update_selected)
        buttons.addWidget(update_sel_btn)

        update_all_btn = QPushButton("Update All")
        update_all_btn.setObjectName("DangerButton")
        update_all_btn.setDefault(True)
        update_all_btn.clicked.connect(self._update_all)
        buttons.addWidget(update_all_btn)

        layout.addLayout(buttons)

    # -- button handlers ------------------------------------------------
    def _keep(self) -> None:
        self._result = []
        self.reject()

    def _update_selected(self) -> None:
        self._result = [
            d for i, d in enumerate(self._discrepancies)
            if self._table.item(i, 0).checkState() == Qt.CheckState.Checked
        ]
        self.accept()

    def _update_all(self) -> None:
        self._result = list(self._discrepancies)
        self.accept()

    @classmethod
    def ask(cls, patient_name: str, discrepancies: Sequence, parent=None) -> list:
        """Return the list of approved discrepancy rows (empty = keep OSCAR)."""
        dlg = cls(patient_name, discrepancies, parent)
        dlg.exec()
        return dlg._result
