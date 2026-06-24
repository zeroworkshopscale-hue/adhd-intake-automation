"""Dialog shown when the questionnaire has unanswered question rows.

Lists the exact unanswered question(s) — and flags any whole section left blank
— then asks the operator to either *Process as Complete* (upload and treat as a
finished form) or *Send Back to Patient* (flag incomplete and offer a follow-up
email).

``ask`` returns True for "Process as Complete", False for "Send Back".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
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
        self.setMinimumWidth(600)

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
        blank_sections = list(getattr(completeness, "blank_section_pages", []) or [])
        section_note = ""
        if blank_sections:
            pages = ", ".join(str(p) for p in blank_sections)
            section_note = (
                f"<br><br><b>Note:</b> an entire section is blank on "
                f"<b>Page {pages}</b>."
            )
        info = QLabel(
            f"The assessment form {who}has "
            f"<b>{completeness.unanswered_count}</b> question row(s) with no "
            f"response on <b>{completeness.pages_label}</b>.{section_note}"
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        questions = list(getattr(completeness, "unanswered_questions", []) or [])
        if questions:
            label = QLabel("Unanswered question(s):")
            label.setStyleSheet("font-weight:600;")
            layout.addWidget(label)
            box = QPlainTextEdit("\n".join(f"• {q}" for q in questions))
            box.setReadOnly(True)
            box.setMaximumHeight(150)
            layout.addWidget(box)

        question = QLabel(
            "Process this form as complete, or send it back to the patient to "
            "fill in the missing parts?"
        )
        question.setWordWrap(True)
        question.setStyleSheet("font-weight:600; padding-top:6px;")
        layout.addWidget(question)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)

        send_back_btn = QPushButton("Send Back to Patient")
        send_back_btn.setObjectName("SecondaryButton")
        send_back_btn.clicked.connect(self.reject)
        buttons.addWidget(send_back_btn)

        process_btn = QPushButton("Process as Complete")
        process_btn.setObjectName("DangerButton")
        process_btn.setDefault(True)
        process_btn.clicked.connect(self.accept)
        buttons.addWidget(process_btn)
        layout.addLayout(buttons)

    @classmethod
    def ask(cls, patient_name: str, completeness, parent=None) -> bool:
        """Return True to process as complete, False to send back to the patient."""
        dlg = cls(patient_name, completeness, parent)
        return dlg.exec() == QDialog.DialogCode.Accepted
