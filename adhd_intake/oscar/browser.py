"""Browser bootstrap helpers for the OSCAR Playwright automation.

Handles the two common first-run problems:
  * Playwright's bundled Chromium isn't downloaded yet -> install it on demand.
  * Diagnostics so logs show exactly which Python / browser path is in use.
"""

from __future__ import annotations

import subprocess
import sys

from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def log_environment() -> None:
    """Log the interpreter and Playwright browser path for diagnostics."""
    logger.info("Python executable: %s", sys.executable)
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        try:
            logger.info("Playwright chromium path: %s", pw.chromium.executable_path)
        finally:
            pw.stop()
    except Exception:
        logger.debug("Could not query Playwright browser path", exc_info=True)


def ensure_chromium() -> bool:
    """Ensure Playwright's bundled Chromium is installed.

    Returns True if the browser is present (already, or after installing).
    Safe to call repeatedly; it only downloads when missing.
    """
    import os

    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        try:
            exe = pw.chromium.executable_path
        finally:
            pw.stop()
        if exe and os.path.exists(exe):
            return True
    except Exception:
        logger.debug("Browser path check failed; will attempt install", exc_info=True)

    logger.warning("Bundled Chromium missing — installing (one-time, may take a minute)…")
    try:
        # Use THIS interpreter's Playwright so the browser lands where the app
        # looks for it, regardless of how the app was launched.
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            logger.info("Chromium installed successfully.")
            return True
        logger.error("Chromium install failed: %s", (result.stderr or result.stdout)[:500])
        return False
    except Exception:
        logger.exception("Chromium install raised")
        return False
