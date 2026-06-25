"""Manual review / correction dialog for low-confidence extractions.

Shown when a questionnaire had to be OCR'd (scanned / handwritten) or when not
enough identifiers could be read automatically. Every field is pre-filled with
whatever was extracted and remains editable, so the operator can confirm or fix
the patient details and the questionnaire sections before the form is matched in
OSCAR and written to the sheet.

``ask`` returns a dict ``{"demographics": {...}, "answers": {...}}`` of the
operator's values, or ``None`` if they cancel (process with what was extracted).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import AnimatedDialog

# (label, demographics attribute) — patient identity used for OSCAR matching.
_DEMOGRAPHIC_FIELDS = (
    ("First name", "first_name"),
    ("Last name", "last_name"),
    ("Date of birth (YYYY-MM-DD)", "dob"),
    ("Email", "email"),
    ("Phone", "phone"),
    ("Health card", "health_card"),
    ("Pronoun (He/His, She/Her, They/Them)", "pronoun"),
)

# (label, answer key) — questionnaire sections that feed the copy sheet.
_ANSWER_FIELDS = (
    ("Student — program", "Current Program"),
    ("Employment / occupation", "Current Occupation"),
    ("Substance — Alcohol (blank if none)", "substance_alcohol"),
    ("Substance — Cannabis", "substance_cannabis"),
    ("Substance — Other", "substance_other"),
    ("How did you hear (1)", "referral_1"),
    ("How did you hear (2)", "referral_2"),
    ("How did you hear (3)", "referral_3"),
    ("Consent — future initiatives (Yes/No)", "future_initiatives"),
    ("Consent — future research (Yes/No)", "future_research"),
)


class DetailsReviewDialog(AnimatedDialog):
    """Editable, pre-filled review of a low-confidence extraction."""

    def __init__(self, record, used_ocr: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review & Confirm Patient Details")
        self.setModal(True)
        self.setMinimumWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 18)
        outer.setSpacing(12)

        heading = QLabel("Review & Confirm")
        heading.setStyleSheet(
            f"background:{theme.BRAND_GRADIENT}; color:white; font-size:21px; "
            "font-weight:800; letter-spacing:1px; padding:14px 18px; border-radius:10px;"
        )
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(heading)

        why = (
            "This form was read with handwriting/scan recognition, so please "
            "double-check the details below."
            if used_ocr
            else "Some details could not be read automatically — please complete "
            "and confirm them below."
        )
        info = QLabel(
            f"{why}<br><br>Everything is editable. Correct anything that is wrong "
            f"or blank, then click <b>Confirm &amp; Continue</b>."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(info)

        # Scrollable form (there are many fields).
        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self._demo_edits: dict[str, QLineEdit] = {}
        demo = record.demographics
        for label, attr in _DEMOGRAPHIC_FIELDS:
            edit = QLineEdit(str(getattr(demo, attr, "") or ""))
            self._demo_edits[attr] = edit
            form.addRow(label, edit)

        sep = QLabel("Questionnaire sections")
        sep.setStyleSheet("font-weight:700; padding-top:10px;")
        form.addRow(sep)

        self._answer_edits: dict[str, QLineEdit] = {}
        answers = record.answers or {}
        for label, key in _ANSWER_FIELDS:
            edit = QLineEdit(str(answers.get(key, "") or ""))
            self._answer_edits[key] = edit
            form.addRow(label, edit)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)
        scroll.setMinimumHeight(360)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("SecondaryButton")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        confirm = QPushButton("Confirm && Continue")
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)
        buttons.addWidget(confirm)
        outer.addLayout(buttons)

    def _values(self) -> dict:
        return {
            "demographics": {k: e.text().strip() for k, e in self._demo_edits.items()},
            "answers": {k: e.text().strip() for k, e in self._answer_edits.items()},
        }

    @classmethod
    def ask(cls, record, used_ocr: bool, parent=None) -> Optional[dict]:
        dlg = cls(record, used_ocr, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg._values()
        return None
