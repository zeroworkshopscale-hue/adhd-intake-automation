"""Red 'Alert' dialog shown when the assessment tool and OSCAR chart disagree.

Lists each differing field (tool value vs OSCAR value) and asks the operator
whether to update the OSCAR chart. Returns True only if they approve.
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
    """Modal alert for chart/tool discrepancies."""

    def __init__(self, patient_name: str, discrepancies: Sequence, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Alert")
        self.setModal(True)
        self.setMinimumWidth(580)

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
            f"Please review each field below — the value from the assessment tool "
            f"and the value currently in OSCAR are shown side by side."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        table = QTableWidget(len(discrepancies), 3)
        table.setHorizontalHeaderLabels(["Field", "Assessment Tool", "OSCAR (current)"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        for row, d in enumerate(discrepancies):
            table.setItem(row, 0, QTableWidgetItem(d.field_label))
            tool_item = QTableWidgetItem(d.tool_value or "(blank)")
            tool_item.setForeground(Qt.GlobalColor.darkGreen)
            table.setItem(row, 1, tool_item)
            oscar_item = QTableWidgetItem(d.oscar_value or "(blank)")
            oscar_item.setForeground(Qt.GlobalColor.red)
            table.setItem(row, 2, oscar_item)
        table.resizeRowsToContents()
        layout.addWidget(table)

        question = QLabel(
            "Would you like to update the OSCAR chart with the Assessment Tool "
            "information?"
        )
        question.setWordWrap(True)
        question.setStyleSheet("font-weight:600; padding-top:6px;")
        layout.addWidget(question)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        skip_btn = QPushButton("Keep Existing OSCAR Data")
        skip_btn.setObjectName("SecondaryButton")
        skip_btn.clicked.connect(self.reject)
        buttons.addWidget(skip_btn)

        update_btn = QPushButton("Update OSCAR")
        update_btn.setObjectName("DangerButton")
        update_btn.setDefault(True)
        update_btn.clicked.connect(self.accept)
        buttons.addWidget(update_btn)
        layout.addLayout(buttons)

    @classmethod
    def ask(cls, patient_name: str, discrepancies: Sequence, parent=None) -> bool:
        dlg = cls(patient_name, discrepancies, parent)
        return dlg.exec() == QDialog.DialogCode.Accepted
