"""Centralised logging setup.

A rotating file handler writes structured logs to ``logs/adhd_intake.log`` and a
console handler mirrors them to stderr. Call :func:`configure_logging` exactly
once at application start-up.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_CONFIGURED = False

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    """Configure root logging with a rotating file + console handler."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "adhd_intake.log"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    _CONFIGURED = True
    logging.getLogger(__name__).info("Logging configured -> %s", log_file)


def get_logger(name: str) -> logging.Logger:
    """Convenience accessor so callers don't import logging directly."""
    return logging.getLogger(name)
