"""OSCAR login dialog shown at application start-up.

The operator enters their own OSCAR Pro credentials so the questionnaire is
uploaded under the correct provider profile. Credentials are held only in
memory for the session and are never written to disk.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from .widgets import AnimatedDialog


class OscarLoginDialog(AnimatedDialog):
    """Modal dialog returning the operator's OSCAR username/password."""

    def __init__(self, base_url: str, default_username: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("OSCAR Login")
        self.setModal(True)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        header = QLabel("Sign in to OSCAR")
        header.setObjectName("BrandHeader")
        layout.addWidget(header)

        intro = QLabel(
            "Enter your OSCAR Pro account. Uploads are made under your provider "
            "profile, and your credentials are kept only for this session."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        server = QLabel(f"Server: {base_url}")
        server.setStyleSheet("color: #8a8f99; font-size: 11px;")
        layout.addWidget(server)

        form = QFormLayout()
        self._username = QLineEdit(default_username)
        self._username.setPlaceholderText("OSCAR username")
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("OSCAR password")
        form.addRow("Username:", self._username)
        form.addRow("Password:", self._password)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Sign in")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #b30000;")
        self._error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._error)

        if default_username:
            self._password.setFocus()
        else:
            self._username.setFocus()

    def _on_accept(self) -> None:
        if not self._username.text().strip() or not self._password.text():
            self._error.setText("Both username and password are required.")
            return
        self.accept()

    @property
    def username(self) -> str:
        return self._username.text().strip()

    @property
    def password(self) -> str:
        return self._password.text()

    @classmethod
    def prompt(
        cls, base_url: str, default_username: str = "", parent=None
    ) -> Optional[tuple[str, str]]:
        """Show the dialog; return (username, password) or None if cancelled."""
        dlg = cls(base_url, default_username, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.username, dlg.password
        return None
