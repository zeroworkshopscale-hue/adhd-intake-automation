"""Reusable polished UI building blocks: an animated spinner, a fade-in dialog
base, and DPI-aware icon painting.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QWidget


class Spinner(QWidget):
    """A smooth rotating-arc spinner (vector-drawn, so it scales with DPI)."""

    def __init__(self, size: int = 48, color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self._size = size
        self._color = QColor(color)
        self._angle = 0
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(28)  # ~36 fps
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _rotate(self) -> None:
        self._angle = (self._angle + 11) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color)
        pen.setWidth(max(3, self._size // 12))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        margin = pen.width() + 1
        r = self._size / 2 - margin
        p.translate(self._size / 2, self._size / 2)
        p.rotate(self._angle)
        # 280-degree arc -> reads clearly as a spinner.
        p.drawArc(QRectF(-r, -r, 2 * r, 2 * r), 0, 280 * 16)
        p.end()


class AnimatedDialog(QDialog):
    """A QDialog that fades in when shown (subtle micro-animation)."""

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(160)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._fade_anim = anim  # keep a reference so it isn't GC'd


def paint_icon(kind: str, size: int, dpr: float = 1.0, color: str = "#ffffff") -> QPixmap:
    """Return a crisp, DPI-aware white line icon.

    kind: 'upload' | 'check' | 'cross'
    """
    px = max(1, int(round(size * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(max(3.0, size / 14) * dpr)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    s = size * dpr  # paint in device pixels

    if kind == "upload":
        cx = s / 2
        p.drawLine(int(cx), int(s * 0.22), int(cx), int(s * 0.60))
        head = s * 0.16
        p.drawLine(int(cx), int(s * 0.22), int(cx - head), int(s * 0.22 + head))
        p.drawLine(int(cx), int(s * 0.22), int(cx + head), int(s * 0.22 + head))
        p.drawLine(int(s * 0.26), int(s * 0.64), int(s * 0.26), int(s * 0.80))
        p.drawLine(int(s * 0.74), int(s * 0.64), int(s * 0.74), int(s * 0.80))
        p.drawLine(int(s * 0.26), int(s * 0.80), int(s * 0.74), int(s * 0.80))
    elif kind == "check":
        p.drawLine(int(s * 0.28), int(s * 0.52), int(s * 0.44), int(s * 0.68))
        p.drawLine(int(s * 0.44), int(s * 0.68), int(s * 0.74), int(s * 0.32))
    elif kind == "cross":
        p.drawLine(int(s * 0.32), int(s * 0.32), int(s * 0.68), int(s * 0.68))
        p.drawLine(int(s * 0.68), int(s * 0.32), int(s * 0.32), int(s * 0.68))
    p.end()
    return pm
