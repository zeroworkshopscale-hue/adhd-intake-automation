"""OSCAR login dialog shown at application start-up.

The operator enters their own OSCAR Pro credentials so the questionnaire is
uploaded under the correct provider profile. Credentials are held only in
memory for the session and are never written to disk.

The "Verify" button attempts a real background login to confirm credentials
before the Sign in button is enabled.
"""

from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .widgets import AnimatedDialog


class _VerifySignals(QObject):
    """Worker signals (QObject required for cross-thread signals)."""
    result = Signal(bool, str)  # (success, message)


def _attempt_login(base_url: str, username: str, password: str, signals: _VerifySignals) -> None:
    """Background thread: attempt a real OSCAR login and emit the result."""
    try:
        from ..oscar.client import OscarClient, OscarLoginError, OscarError
        from ..config import OscarConfig

        config = OscarConfig(
            base_url=base_url,
            username=username,
            password=password,
            headless=True,
            timeout_ms=20000,
        )
        with OscarClient(config):
            signals.result.emit(True, "")
    except Exception as exc:  # noqa: BLE001
        signals.result.emit(False, str(exc))


class OscarLoginDialog(AnimatedDialog):
    """Modal dialog returning the operator's OSCAR username/password."""

    def __init__(self, base_url: str, default_username: str = "", parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._verified = False

        self.setWindowTitle("OSCAR Login")
        self.setModal(True)
        self.setMinimumWidth(440)

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

        # Password field with show/hide toggle.
        pwd_row = QHBoxLayout()
        pwd_row.setSpacing(4)
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("OSCAR password")
        self._eye_btn = QPushButton("👁")
        self._eye_btn.setCheckable(True)
        self._eye_btn.setFixedWidth(32)
        self._eye_btn.setToolTip("Show / hide password")
        self._eye_btn.setStyleSheet("QPushButton { border: none; font-size: 14px; }")
        self._eye_btn.toggled.connect(
            lambda checked: self._password.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        pwd_row.addWidget(self._password)
        pwd_row.addWidget(self._eye_btn)

        form.addRow("Username:", self._username)
        form.addRow("Password:", pwd_row)
        layout.addLayout(form)

        # Verify row.
        verify_row = QHBoxLayout()
        verify_row.setSpacing(10)
        self._verify_btn = QPushButton("Test Connection")
        self._verify_btn.clicked.connect(self._on_verify)
        verify_row.addWidget(self._verify_btn)
        self._verify_status = QLabel("")
        self._verify_status.setWordWrap(True)
        verify_row.addWidget(self._verify_status, 1)
        layout.addLayout(verify_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("Sign in")
        self._ok_btn.setEnabled(False)  # enabled only after successful verify
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #b30000;")
        self._error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._error)

        # When credentials change, reset verified state.
        self._username.textChanged.connect(self._on_creds_changed)
        self._password.textChanged.connect(self._on_creds_changed)

        if default_username:
            self._password.setFocus()
        else:
            self._username.setFocus()

    # ---- verification ---------------------------------------------------
    def _on_creds_changed(self) -> None:
        if self._verified:
            self._verified = False
            self._ok_btn.setEnabled(False)
            self._verify_status.setText("")

    def _on_verify(self) -> None:
        username = self._username.text().strip()
        password = self._password.text()
        if not username or not password:
            self._verify_status.setStyleSheet("color: #b30000;")
            self._verify_status.setText("Enter username and password first.")
            return

        self._verify_btn.setEnabled(False)
        self._verify_status.setStyleSheet("color: #555;")
        self._verify_status.setText("Connecting to OSCAR…")
        self._error.setText("")

        signals = _VerifySignals()
        signals.result.connect(self._on_verify_result)

        t = threading.Thread(
            target=_attempt_login,
            args=(self._base_url, username, password, signals),
            daemon=True,
        )
        t.start()

    def _on_verify_result(self, success: bool, message: str) -> None:
        self._verify_btn.setEnabled(True)
        if success:
            self._verified = True
            self._ok_btn.setEnabled(True)
            self._verify_status.setStyleSheet("color: #1a7f37; font-weight: 600;")
            self._verify_status.setText("✓ Login verified")
        else:
            self._verified = False
            self._ok_btn.setEnabled(False)
            short = message.split("\n")[0][:120] if message else "Connection failed"
            self._verify_status.setStyleSheet("color: #b30000;")
            self._verify_status.setText(f"✗ {short}")

    # ---- accept ---------------------------------------------------------
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
