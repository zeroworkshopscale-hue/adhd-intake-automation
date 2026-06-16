"""ADHD Intake Automation — application entry point.

Usage:
    python main.py                 # launch the desktop dashboard
    python main.py --config path   # use an alternate config file

On first run, copy ``config.example.yaml`` to ``config.yaml`` and fill in the
OSCAR credentials, Google service-account path and folder locations.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADHD Intake Automation")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (defaults to ./config.yaml)",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Load config + build services, then exit (used to verify a build).",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest(args.config)

    # Imports are deferred so that --help works without the heavy deps installed.
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from adhd_intake.config import AppConfig, ConfigError
    from adhd_intake.services import AppServices

    # Crisp scaling on 125%/150%/175% Windows display settings (fractional).
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Make Windows treat this as its own app (so the taskbar uses our icon,
    # not pythonw's). Must be set before any window is created.
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "AdultADHDCentre.IntakeAutomation"
        )
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("ADHD Intake Automation")

    # Application / window icon (works from source and from the frozen exe).
    from PySide6.QtGui import QIcon

    icon_path = _resolve_icon()
    if icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))

    # Apply the Adult ADHD Centre brand theme.
    from adhd_intake.dashboard.theme import app_stylesheet

    app.setStyleSheet(app_stylesheet())

    try:
        config = AppConfig.load(args.config)
    except ConfigError as exc:
        QMessageBox.critical(None, "Configuration error", str(exc))
        return 2

    try:
        services = AppServices.build(config)
    except Exception as exc:  # surface fatal wiring errors to the user
        QMessageBox.critical(None, "Startup error", f"Could not start application:\n\n{exc}")
        return 3

    # Prompt for the operator's OSCAR login. Credentials are validated on first
    # use; if they're wrong the dashboard re-prompts (see MainWindow).
    from adhd_intake.dashboard.login_dialog import OscarLoginDialog

    creds = OscarLoginDialog.prompt(
        base_url=config.oscar.base_url,
        default_username=config.oscar.username,
    )
    if creds is None:
        return 0  # user cancelled
    services.set_oscar_credentials(*creds)

    # Offer to resume a previous session (e.g. after a mid-batch close), which
    # reloads its processed patients and keeps the copy sheet. Only asked when
    # there is something to resume; otherwise a new session starts silently.
    from adhd_intake.dashboard.session_dialog import SessionStartDialog

    resumable = services.resumable_completed_count()
    resume = SessionStartDialog.ask(resumable) if resumable > 0 else False
    services.begin_session(resume=resume)

    from adhd_intake.dashboard import MainWindow

    window = MainWindow(services)
    window.show()
    return app.exec()


def _resolve_icon():
    """Locate app_icon.ico in both source and PyInstaller-frozen layouts."""
    import sys as _sys
    from pathlib import Path as _Path

    bases = []
    if getattr(_sys, "frozen", False):
        bases.append(_Path(getattr(_sys, "_MEIPASS", _Path(_sys.executable).parent)))
        bases.append(_Path(_sys.executable).parent)
    bases.append(_Path(__file__).resolve().parent)
    for base in bases:
        p = base / "resources" / "app_icon.ico"
        if p.exists():
            return p
    return None


def _selftest(config_path) -> int:
    """Import the heavy stack, load config, build services — verifies a build."""
    try:
        import fitz  # noqa: F401  PyMuPDF
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        import pytesseract  # noqa: F401
        from PySide6.QtWidgets import QApplication  # noqa: F401

        from adhd_intake.config import AppConfig
        from adhd_intake.oscar.browser import log_environment
        from adhd_intake.services import AppServices

        config = AppConfig.load(config_path)
        services = AppServices.build(config)
        log_environment()
        services.close()
        print("SELFTEST OK")
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"SELFTEST FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
