"""Consent-reminder email helper.

When a questionnaire is rejected because the consent page is unsigned, the app
offers the operator a ready-to-send email to the patient. The operator copies it
with one click and pastes it into Outlook (the app does not send mail itself).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .widgets import AnimatedDialog

_EMAIL_TEMPLATE = (
    "Dear {name},\n\n"
    "Your signature on the last page did not come through on the ADHD "
    "Assessment Tool you submitted.\n\n"
    "Please just send us an email back that says, Accept this email as my "
    "signed consent.\n\n"
    "Best regards,\n"
    "Adult ADHD Centre Manager"
)


_INCOMPLETE_TEMPLATE = (
    "Dear {name},\n\n"
    "Thank you for submitting your ADHD Assessment Tool.\n\n"
    "While reviewing it we noticed that some questions were left unanswered{pages}. "
    "Each question needs a response so that your assessment can be completed "
    "accurately.{questions}\n\n"
    "Please review the form, complete the missing sections, and send the updated "
    "copy back to us at your earliest convenience.\n\n"
    "Best regards,\n"
    "Adult ADHD Centre Manager"
)


def build_consent_email(first_name: Optional[str]) -> str:
    """Return the consent-reminder email body, personalised when possible."""
    name = (first_name or "").strip() or "Patient"
    return _EMAIL_TEMPLATE.format(name=name)


def build_incomplete_email(
    first_name: Optional[str],
    pages_label: str = "",
    questions: Optional[list] = None,
) -> str:
    """Return the incomplete-sections follow-up email body.

    When the specific unanswered questions are known they are listed so the
    patient knows exactly what to complete.
    """
    name = (first_name or "").strip() or "Patient"
    pages = f" (on {pages_label})" if pages_label else ""
    questions_block = ""
    if questions:
        bullets = "\n".join(f"  - {q}" for q in questions)
        questions_block = (
            "\n\nThe following still need an answer:\n" + bullets
        )
    return _INCOMPLETE_TEMPLATE.format(name=name, pages=pages, questions=questions_block)


class ConsentEmailDialog(AnimatedDialog):
    """Shows the reminder email with copy-to-clipboard buttons."""

    def __init__(
        self,
        first_name: Optional[str],
        patient_email: Optional[str],
        source_filename: str = "",
        parent=None,
        *,
        title: Optional[str] = None,
        heading_text: Optional[str] = None,
        info_text: Optional[str] = None,
        body_text: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title or "Missing signature — send consent reminder")
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        heading = QLabel(heading_text or "Consent reminder required")
        heading.setObjectName("BrandHeader")
        layout.addWidget(heading)

        info = QLabel(
            info_text
            or (
                f'No signature/initials were found on "{source_filename}".\n'
                "The document was uploaded to OSCAR and logged, but the patient's consent\n"
                "signature is missing. Copy the email below and send it to the patient from Outlook."
            )
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Patient email row (so the operator knows who to send to).
        if patient_email:
            row = QHBoxLayout()
            row.addWidget(QLabel("Patient email:"))
            email_field = QLineEdit(patient_email)
            email_field.setReadOnly(True)
            row.addWidget(email_field)
            copy_email_btn = QPushButton("Copy address")
            copy_email_btn.clicked.connect(lambda: self._copy(patient_email))
            row.addWidget(copy_email_btn)
            layout.addLayout(row)

        # Email body.
        self._body = QPlainTextEdit(body_text or build_consent_email(first_name))
        self._body.setMinimumHeight(220)
        layout.addWidget(self._body)

        buttons = QHBoxLayout()
        copy_btn = QPushButton("Copy email text")
        copy_btn.clicked.connect(self._copy_body)
        buttons.addWidget(copy_btn)

        self._copied_label = QLabel("")
        buttons.addWidget(self._copied_label)
        buttons.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _copy_body(self) -> None:
        self._copy(self._body.toPlainText())

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self._copied_label.setText("Copied ✓")
