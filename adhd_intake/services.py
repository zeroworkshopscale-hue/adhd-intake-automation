"""Composition root.

Builds and wires every component from an :class:`AppConfig` into a single
:class:`AppServices` container. The GUI and any headless/CLI entry point both
go through here, so wiring lives in exactly one place.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import AppConfig
from .database import AuditLog, Database, RecordRepository
from .extraction import Extractor, PdfClassifier
from .ocr import TesseractOcrEngine
from .oscar import OscarClient
from .pipeline import IntakeProcessor
from .sheets import CompositeSheetWriter
from .utils.logging_config import configure_logging, get_logger
from .validation import CompletenessValidator, SignatureValidator

logger = get_logger(__name__)


@dataclass
class _OscarCredentials:
    """Mutable holder so the OSCAR factory can pick up the operator's login
    that is entered at start-up, overriding anything in config.yaml."""

    username: str = ""
    password: str = ""


@dataclass
class AppServices:
    config: AppConfig
    database: Database
    repository: RecordRepository
    audit: AuditLog
    processor: IntakeProcessor
    oscar_credentials: _OscarCredentials = field(default_factory=_OscarCredentials)
    # Start time of the current session. Patients/sheet rows from this instant
    # onward belong to "this session". Set by begin_session(); defaults to now
    # so non-GUI callers (selftest/tests) still behave sanely.
    session_start: datetime = field(default_factory=datetime.now)

    # ------------------------------------------------------------------
    # Session continuity (resume a previous session after a mid-batch close)
    # ------------------------------------------------------------------
    def _session_state_path(self) -> Path:
        return self.config.database.path.parent / "session_state.json"

    def previous_session_start(self) -> Optional[datetime]:
        """The session-start timestamp persisted by the last run, if any."""
        p = self._session_state_path()
        try:
            if p.exists():
                raw = json.loads(p.read_text(encoding="utf-8")).get("session_start")
                return datetime.fromisoformat(raw) if raw else None
        except Exception:
            logger.warning("Could not read session state %s", p, exc_info=True)
        return None

    def resumable_completed_count(self) -> int:
        """How many completed patients belong to the previous session (0 = no
        resumable session, so the start-up prompt is skipped)."""
        prev = self.previous_session_start()
        if prev is None:
            return 0
        return sum(1 for r in self.repository.list_completed() if r.created_at >= prev)

    def begin_session(self, resume: bool) -> None:
        """Start the session. ``resume`` keeps the previous session's patients and
        copy sheet; otherwise a fresh session starts (and the copy sheet is reset
        when configured to). The effective start is persisted for the next run."""
        prev = self.previous_session_start()
        if resume and prev is not None:
            self.session_start = prev
            logger.info("Resuming previous session started %s", prev.isoformat())
        else:
            self.session_start = datetime.now()
            if self.config.sheets.local_enabled and self.config.sheets.reset_each_session:
                self.reset_copy_sheet()
            logger.info("Starting new session at %s", self.session_start.isoformat())
        self._write_session_state(self.session_start)

    def _write_session_state(self, started: datetime) -> None:
        p = self._session_state_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps({"session_start": started.isoformat(timespec="seconds")}),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("Could not write session state %s", p, exc_info=True)

    def reset_copy_sheet(self) -> None:
        p = self.config.sheets.local_path
        try:
            if p.exists():
                p.unlink()
                logger.info("Cleared previous copy sheet %s", p)
        except OSError:
            logger.warning("Could not clear copy sheet %s", p)

    def set_oscar_credentials(self, username: str, password: str) -> None:
        """Set the OSCAR login used for the rest of this session."""
        self.oscar_credentials.username = username
        self.oscar_credentials.password = password
        logger.info("OSCAR credentials set for operator '%s'", username)

    def has_oscar_credentials(self) -> bool:
        return bool(self.oscar_credentials.username and self.oscar_credentials.password)

    def verify_oscar_login(self) -> tuple[bool, str]:
        """Attempt a headless OSCAR login with the current credentials.

        Returns (ok, message). Used at start-up so a wrong password is caught
        immediately rather than on the first file.
        """
        from .oscar import OscarClient, OscarError, OscarLoginError

        oscar_config = dataclasses.replace(
            self.config.oscar,
            username=self.oscar_credentials.username,
            password=self.oscar_credentials.password,
            headless=True,
        )
        try:
            with OscarClient(oscar_config):
                return True, ""
        except OscarLoginError as exc:
            return False, str(exc)
        except OscarError as exc:
            return False, f"Could not reach OSCAR: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"Login check failed: {exc}"

    def close(self) -> None:
        self.database.close()

    @classmethod
    def build(cls, config: AppConfig) -> "AppServices":
        configure_logging(config.logging.dir, config.logging.level)
        config.folders.ensure_exist()
        logger.info("Building application services")

        # NOTE: the copy sheet is no longer reset here. The session choice at
        # start-up (begin_session) decides: a new session resets it; resuming a
        # previous session keeps it. This lets a mid-batch close be recovered.

        # Log interpreter / browser path so first-run issues are diagnosable.
        from .oscar.browser import log_environment

        log_environment()

        database = Database(config.database.path)
        repository = RecordRepository(database)
        audit = AuditLog(database, actor="intake-app")

        ocr_engine = TesseractOcrEngine(config.ocr)
        try:
            ocr_engine._ensure_available()
        except Exception:
            logger.warning(
                "Tesseract OCR not found at %r — scanned/handwritten PDFs will not "
                "be OCR'd until Tesseract-OCR is installed. Typed/fillable PDFs are "
                "unaffected.",
                config.ocr.tesseract_cmd,
            )
        classifier = PdfClassifier()
        extractor = Extractor(classifier, ocr_engine=ocr_engine, clinic=config.clinic)
        validator = SignatureValidator(config.validation, ocr_engine=ocr_engine)
        completeness_validator = CompletenessValidator(config.validation)

        # Operator's OSCAR login, captured at start-up. Seeded from config only
        # as a fallback for unattended/testing setups.
        credentials = _OscarCredentials(
            username=config.oscar.username, password=config.oscar.password
        )

        # Lazy factories: sessions are created only when a file reaches the
        # OSCAR / Sheets stages, and the OSCAR session uses the operator's own
        # login so the correct provider profile is connected.
        def oscar_factory() -> OscarClient:
            oscar_config = dataclasses.replace(
                config.oscar,
                username=credentials.username,
                password=credentials.password,
            )
            return OscarClient(oscar_config)

        def sheets_factory() -> CompositeSheetWriter:
            return CompositeSheetWriter(config.sheets)

        processor = IntakeProcessor(
            config=config,
            repository=repository,
            audit=audit,
            extractor=extractor,
            validator=validator,
            completeness_validator=completeness_validator,
            oscar_factory=oscar_factory,
            sheets_factory=sheets_factory,
        )

        return cls(
            config=config,
            database=database,
            repository=repository,
            audit=audit,
            processor=processor,
            oscar_credentials=credentials,
        )
