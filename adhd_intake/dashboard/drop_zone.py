"""Premium drag-and-drop upload zone.

A modern card-style component with the brand gradient (#E31D2B → #A31721), a
crisp DPI-aware upload icon, soft drop shadow, animated glow on drag-over, an
animated spinner while processing, and clear states: idle, drag-over,
processing, success, error.

Files dragged from the file system (or a saved Outlook attachment) work
directly. For an attachment dragged straight out of an open email (a Windows
"virtual file" with no path), it asks the user to save it first.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import Spinner, paint_icon

_ICON_SIZE = 56


class DropZone(QFrame):
    """A polished, animated upload card."""

    files_dropped = Signal(list)   # list[Path]
    drop_rejected = Signal(str)    # human message when a drop can't be used

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UploadCard")
        self.setAcceptDrops(True)
        self.setMinimumHeight(196)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(8)

        # Icon / spinner area (stacked so they share one slot).
        self._icon = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self._spinner = Spinner(46, "#ffffff")
        spin_page = QWidget()
        spl = QVBoxLayout(spin_page)
        spl.setContentsMargins(0, 0, 0, 0)
        spl.addWidget(self._spinner, alignment=Qt.AlignmentFlag.AlignCenter)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._icon)      # index 0
        self._stack.addWidget(spin_page)       # index 1
        self._stack.setFixedHeight(_ICON_SIZE + 4)
        layout.addWidget(self._stack)

        self._title = QLabel("Drag & drop questionnaire PDFs here")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("color: white; font-size: 17px; font-weight: 800;")
        layout.addWidget(self._title)

        self._subtitle = QLabel("or click to browse your files")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setStyleSheet("color: rgba(255,255,255,0.88); font-size: 13px;")
        layout.addWidget(self._subtitle)

        # Soft drop shadow (animated for the glow effect).
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(26)
        self._shadow.setOffset(0, 8)
        self._shadow.setColor(QColor(30, 24, 60, 95))   # indigo-tinted soft shadow
        self.setGraphicsEffect(self._shadow)

        self._glow = QPropertyAnimation(self._shadow, b"blurRadius", self)
        self._glow.setDuration(240)
        self._glow.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._revert_timer = QTimer(self)
        self._revert_timer.setSingleShot(True)
        self._revert_timer.timeout.connect(lambda: self.set_state("idle"))

        self._set_icon("upload")
        self._apply_style(theme.BRAND_GRADIENT, border="rgba(255,255,255,0.0)")

    # ---- helpers --------------------------------------------------------
    def _set_icon(self, kind: str) -> None:
        self._icon.setPixmap(paint_icon(kind, _ICON_SIZE, self.devicePixelRatioF()))

    def _apply_style(self, gradient: str, border: str) -> None:
        self.setStyleSheet(
            f"QFrame#UploadCard {{ background: {gradient};"
            f" border-radius: 18px; border: 2px solid {border}; }}"
        )

    def _animate_glow(self, to: int) -> None:
        self._glow.stop()
        self._glow.setStartValue(self._shadow.blurRadius())
        self._glow.setEndValue(to)
        self._shadow.setColor(QColor(71, 40, 94, 160) if to > 30 else QColor(30, 24, 60, 95))
        self._glow.start()

    def _show_icon(self) -> None:
        self._spinner.stop()
        self._stack.setCurrentIndex(0)

    def _show_spinner(self) -> None:
        self._stack.setCurrentIndex(1)
        self._spinner.start()

    # ---- public state machine ------------------------------------------
    def set_state(self, state: str, message: str = "") -> None:
        """state: idle | dragover | processing | success | error."""
        if state == "idle":
            self._show_icon(); self._set_icon("upload")
            self._title.setText("Drag & drop questionnaire PDFs here")
            self._subtitle.setText("or click to browse your files")
            self._apply_style(theme.BRAND_GRADIENT, "rgba(255,255,255,0.0)")
            self._animate_glow(26)
        elif state == "dragover":
            self._show_icon(); self._set_icon("upload")
            self._title.setText("Release to upload")
            self._subtitle.setText("Drop the PDF(s) to begin processing")
            self._apply_style(theme.BRAND_GRADIENT_HOVER, "rgba(255,255,255,0.85)")
            self._animate_glow(48)
        elif state == "processing":
            self._show_spinner()
            self._title.setText("Processing…")
            self._subtitle.setText(message or "Working through the questionnaire")
            self._apply_style(theme.BRAND_GRADIENT, "rgba(255,255,255,0.35)")
            self._animate_glow(40)
        elif state == "success":
            self._show_icon(); self._set_icon("check")
            self._title.setText("Completed")
            self._subtitle.setText(message or "Uploaded to OSCAR")
            self._apply_style(theme.BRAND_GRADIENT, "rgba(255,255,255,0.9)")
            self._animate_glow(34)
            self._revert_timer.start(3500)
        elif state == "error":
            self._show_icon(); self._set_icon("cross")
            self._title.setText("Not uploaded")
            self._subtitle.setText(message or "See the activity log")
            self._apply_style(theme.BRAND_GRADIENT, "rgba(255,255,255,0.9)")
            self._animate_glow(34)
            self._revert_timer.start(5000)

    # ---- drag & drop ----------------------------------------------------
    @staticmethod
    def _looks_droppable(mime) -> bool:
        return (
            mime.hasUrls()
            or mime.hasFormat("FileGroupDescriptorW")
            or mime.hasFormat("FileGroupDescriptor")
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._looks_droppable(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
            self.set_state("dragover")
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._looks_droppable(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.set_state("idle")

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        mime = event.mimeData()
        paths: list[Path] = []
        for url in mime.urls():
            local = url.toLocalFile()
            if local.lower().endswith(".pdf") and Path(local).exists():
                paths.append(Path(local))
        if not paths:
            paths = self._extract_virtual_pdfs(mime)

        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
            return

        event.ignore()
        self.set_state("idle")
        if mime.hasFormat("FileGroupDescriptorW") or mime.hasFormat("FileGroupDescriptor"):
            self.drop_rejected.emit(
                "That looks like an attachment dragged straight from Outlook, which "
                "Windows doesn't expose as a file. Please save the attachment to a "
                "folder first and drag it from there, or click to browse."
            )
        else:
            self.drop_rejected.emit("No PDF was found in what you dropped.")

    def _extract_virtual_pdfs(self, mime) -> list[Path]:
        out: list[Path] = []
        try:
            if not mime.hasFormat("FileContents"):
                return out
            data = bytes(mime.data("FileContents"))
            if data[:4] == b"%PDF":
                tmp = Path(tempfile.gettempdir()) / "outlook_dropped.pdf"
                tmp.write_bytes(data)
                out.append(tmp)
        except Exception:
            pass
        return out

    # ---- click-to-browse ------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select questionnaire PDF(s)", "", "PDF files (*.pdf)"
        )
        paths = [Path(f) for f in files if f.lower().endswith(".pdf")]
        if paths:
            self.files_dropped.emit(paths)
