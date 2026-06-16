"""Start-up session prompt.

If the previous run left processed patients (e.g. the app was closed mid-batch),
offer to resume that session — restoring the Processed Patients list and keeping
the copy sheet — or start a fresh session.

``ask`` returns True to resume, False to start new. Closing the dialog defaults
to Resume, since resuming is non-destructive (only "Start New" clears the sheet).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import theme
from .widgets import AnimatedDialog


class SessionStartDialog(AnimatedDialog):
    def __init__(self, resumable_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resume Session?")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._resume = True  # safe default: resuming never deletes anything

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        heading = QLabel("Previous session found")
        heading.setStyleSheet(
            f"background:{theme.BRAND_GRADIENT}; color:white; font-size:20px; "
            "font-weight:800; padding:14px 18px; border-radius:10px;"
        )
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        plural = "patient" if resumable_count == 1 else "patients"
        info = QLabel(
            f"A previous session with <b>{resumable_count} processed {plural}</b> "
            f"was found (the app may have closed mid-batch).<br><br>"
            f"<b>Resume</b> to reload those patients into the list and keep the "
            f"existing copy sheet, or <b>Start New</b> to begin a fresh session "
            f"(the copy sheet will be cleared)."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)

        new_btn = QPushButton("Start New Session")
        new_btn.setObjectName("SecondaryButton")
        new_btn.clicked.connect(self._choose_new)
        buttons.addWidget(new_btn)

        resume_btn = QPushButton("Resume Previous Session")
        resume_btn.setDefault(True)
        resume_btn.clicked.connect(self._choose_resume)
        buttons.addWidget(resume_btn)
        layout.addLayout(buttons)

    def _choose_resume(self) -> None:
        self._resume = True
        self.accept()

    def _choose_new(self) -> None:
        self._resume = False
        self.accept()

    @classmethod
    def ask(cls, resumable_count: int, parent=None) -> bool:
        dlg = cls(resumable_count, parent)
        dlg.exec()
        return dlg._resume
