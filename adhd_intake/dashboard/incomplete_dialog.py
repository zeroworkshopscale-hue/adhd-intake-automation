"""Dialog shown when the questionnaire has unanswered question rows.

Lists the page number(s) with missing responses and asks the operator to either
*Approve and Continue* (process and upload to OSCAR anyway) or *Decline &
Request Completion* (stop, do not upload, and send the patient a follow-up).

``ask`` returns True for Approve & Continue, False for Decline.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from . import theme
from .widgets import AnimatedDialog


class IncompleteFormDialog(AnimatedDialog):
    """Modal decision dialog for incomplete questionnaires."""

    def __init__(self, patient_name: str, completeness, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Incomplete Assessment")
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        heading = QLabel("⚠  Incomplete Sections")
        heading.setStyleSheet(
            f"background:{theme.BRAND_GRADIENT}; color:white; font-size:21px; "
            "font-weight:800; letter-spacing:1px; padding:14px 18px; border-radius:10px;"
        )
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        who = f"for <b>{patient_name}</b> " if patient_name else ""
        info = QLabel(
            f"The assessment form {who}contains unanswered questions on "
            f"<b>{completeness.pages_label}</b>.<br><br>"
            f"{completeness.unanswered_count} question row(s) have no response in "
            f"any of their answer columns. Please review the form before proceeding."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        question = QLabel(
            "Would you like to continue anyway, or request that the patient "
            "complete the missing sections?"
        )
        question.setWordWrap(True)
        question.setStyleSheet("font-weight:600; padding-top:6px;")
        layout.addWidget(question)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)

        decline_btn = QPushButton("Decline && Request Completion")
        decline_btn.setObjectName("SecondaryButton")
        decline_btn.clicked.connect(self.reject)
        buttons.addWidget(decline_btn)

        approve_btn = QPushButton("Approve and Continue")
        approve_btn.setObjectName("DangerButton")
        approve_btn.setDefault(True)
        approve_btn.clicked.connect(self.accept)
        buttons.addWidget(approve_btn)
        layout.addLayout(buttons)

    @classmethod
    def ask(cls, patient_name: str, completeness, parent=None) -> bool:
        dlg = cls(patient_name, completeness, parent)
        return dlg.exec() == QDialog.DialogCode.Accepted
