"""Application theme.

Primary brand gradient (header, upload zone, buttons, table headers, dialogs):
    Deep Indigo #26266E  →  Rich Purple #47285E  →  Deep Burgundy #7A1D3E
"""

from __future__ import annotations

# --- Brand palette ---------------------------------------------------------
INDIGO = "#26266E"       # start
PURPLE = "#47285E"       # center
BURGUNDY = "#7A1D3E"     # end
ACCENT = PURPLE          # solid accent for borders / focus / status

# Hover (slightly lighter) and pressed (slightly darker) stops.
INDIGO_H, PURPLE_H, BURGUNDY_H = "#313184", "#563371", "#94264C"
INDIGO_P, PURPLE_P, BURGUNDY_P = "#1D1D57", "#371F4A", "#5F1630"

INK = "#23252b"
SUBTLE_INK = "#5b6470"
LIGHT = "#f5f6f8"
SUCCESS = "#1a7f37"


def _grad(c1: str, c2: str, c3: str, vertical: bool = False) -> str:
    """3-stop brand gradient — horizontal (default) or vertical."""
    coords = "x1:0, y1:0, x2:0, y2:1" if vertical else "x1:0, y1:0, x2:1, y2:0"
    return f"qlineargradient({coords}, stop:0 {c1}, stop:0.5 {c2}, stop:1 {c3})"


BRAND_GRADIENT = _grad(INDIGO, PURPLE, BURGUNDY)
BRAND_GRADIENT_HOVER = _grad(INDIGO_H, PURPLE_H, BURGUNDY_H)
BRAND_GRADIENT_PRESSED = _grad(INDIGO_P, PURPLE_P, BURGUNDY_P)
BRAND_GRADIENT_VERTICAL = _grad(INDIGO, PURPLE, BURGUNDY, vertical=True)

WINDOW_GRADIENT = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fbfbfd, stop:1 #eceef3)"


def app_stylesheet() -> str:
    """Return the global Qt style sheet for the application."""
    return f"""
    QMainWindow, QDialog {{
        background: {WINDOW_GRADIENT};
    }}
    QWidget {{
        color: {INK};
        font-family: "Segoe UI", "Inter", Arial, sans-serif;
        font-size: 13px;
    }}
    QLabel#BrandHeader {{
        background: {BRAND_GRADIENT};
        color: white;
        font-size: 20px;
        font-weight: 800;
        letter-spacing: 0.3px;
        padding: 16px 20px;
        border-radius: 14px;
    }}
    QLabel#SectionLabel {{
        color: {PURPLE};
        font-weight: 700;
        font-size: 13px;
        padding-top: 4px;
    }}

    /* Primary action buttons use the brand gradient. */
    QPushButton {{
        background: {BRAND_GRADIENT};
        color: white;
        border: none;
        border-radius: 9px;
        padding: 9px 18px;
        font-weight: 700;
        min-height: 18px;
    }}
    QPushButton:hover {{ background: {BRAND_GRADIENT_HOVER}; }}
    QPushButton:pressed {{ background: {BRAND_GRADIENT_PRESSED}; }}
    QPushButton:disabled {{ background: #c9ccd2; color: #f3f4f6; }}
    QPushButton#DangerButton {{ background: {BRAND_GRADIENT}; }}

    /* Secondary / outline button (e.g. "Keep existing"). */
    QPushButton#SecondaryButton {{
        background: white;
        color: {PURPLE};
        border: 1.5px solid {PURPLE};
        border-radius: 9px;
        padding: 9px 18px;
        font-weight: 700;
    }}
    QPushButton#SecondaryButton:hover {{ background: #efecf5; }}
    QPushButton#SecondaryButton:pressed {{ background: #e3def0; }}

    QLineEdit, QPlainTextEdit, QTextEdit {{
        background: white;
        border: 1px solid #d4d7de;
        border-radius: 8px;
        padding: 7px;
        selection-background-color: {PURPLE};
        selection-color: white;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
        border: 1px solid {PURPLE};
    }}

    QTableWidget {{
        background: white;
        border: 1px solid #d4d7de;
        border-radius: 10px;
        gridline-color: #eceef2;
        selection-background-color: #e7e3f3;
        selection-color: {INK};
    }}
    QTableWidget::item {{ padding: 6px; }}
    QHeaderView::section {{
        background: {BRAND_GRADIENT_VERTICAL};
        color: white;
        padding: 8px;
        border: none;
        font-weight: 700;
    }}
    QHeaderView::section:first {{ border-top-left-radius: 8px; }}
    QHeaderView::section:last {{ border-top-right-radius: 8px; }}
    QStatusBar {{ color: {PURPLE}; font-weight: 600; }}

    /* Message boxes / popups match the app (not the OS default). */
    QMessageBox {{ background: white; }}
    QMessageBox QLabel {{ color: {INK}; font-size: 13px; }}
    QMessageBox QPushButton {{ min-width: 88px; }}

    /* Modern thin scrollbars. */
    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #c4c8d0; border-radius: 5px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {PURPLE}; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: #c4c8d0; border-radius: 5px; min-width: 28px; }}
    QScrollBar::handle:horizontal:hover {{ background: {PURPLE}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QToolTip {{
        background: {INK}; color: white; border: none;
        padding: 6px 9px; border-radius: 6px; font-size: 12px;
    }}
    """
