"""Application configuration.

Loads ``config.yaml`` (falling back to ``config.example.yaml`` for first-run
discovery) into a set of typed, frozen dataclasses so the rest of the codebase
never touches raw dictionaries or environment lookups directly.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

# Project root: the folder where config.yaml / data / logs live.
# - running from source: the repo root (parent of this package)
# - frozen (.exe via PyInstaller): the folder containing the executable
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.yaml"


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


def _resolve(path_str: str) -> Path:
    """Resolve a possibly-relative config path against the project root."""
    p = Path(os.path.expanduser(path_str))
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


def _parse_columns(raw_columns: Any) -> tuple[tuple[str, str], ...]:
    """Parse the optional ``sheets.columns`` config into (header, field) pairs.

    Each YAML entry may be:
        - {header: "Email Address", field: email}
        - {"Email Address": email}      # single-key mapping shorthand
        - "email"                        # header == field
    """
    if not raw_columns:
        return ()
    parsed: list[tuple[str, str]] = []
    for entry in raw_columns:
        if isinstance(entry, str):
            parsed.append((entry, entry))
        elif isinstance(entry, dict):
            if "header" in entry and "field" in entry:
                parsed.append((str(entry["header"]), str(entry["field"])))
            elif len(entry) == 1:
                header, field_key = next(iter(entry.items()))
                parsed.append((str(header), str(field_key)))
            else:
                raise ConfigError(f"Invalid sheets.columns entry: {entry!r}")
        else:
            raise ConfigError(f"Invalid sheets.columns entry: {entry!r}")
    return tuple(parsed)


@dataclass(frozen=True)
class FolderConfig:
    incoming: Path
    processed: Path
    rejected: Path

    def ensure_exist(self) -> None:
        for p in (self.incoming, self.processed, self.rejected):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path


@dataclass(frozen=True)
class LoggingConfig:
    dir: Path
    level: str = "INFO"


@dataclass(frozen=True)
class OcrConfig:
    tesseract_cmd: str
    language: str = "eng"
    render_dpi: int = 300


@dataclass(frozen=True)
class ValidationConfig:
    min_ink_density: float = 0.01
    consent_keywords: tuple[str, ...] = ("consent", "signature")
    # Response-completeness check on the questionnaire pages (6-11 / 6-12).
    check_completeness: bool = True
    # A response cell counts as marked when its ink fraction exceeds this AND
    # stands out from the other cells of the same row by `response_rel_margin`.
    response_min_ink: float = 0.012
    response_rel_margin: float = 0.010


@dataclass(frozen=True)
class OscarConfig:
    base_url: str
    username: str
    password: str
    headless: bool = False
    timeout_ms: int = 30000
    document_type: str = "Form"
    # Which browser Playwright drives. "chrome" uses the system-installed Google
    # Chrome (no bundled download needed); "" / "chromium" uses Playwright's
    # bundled Chromium (auto-installed on first use).
    browser_channel: str = "chrome"
    # When True, after upload the app compares the chart's name/preferred-name/
    # address with the assessment tool and, on any difference, asks the operator
    # (red "Alert" dialog) before updating OSCAR.
    update_chart: bool = True


@dataclass(frozen=True)
class SheetsConfig:
    # mode: "local"  -> only maintain a local copy sheet (CSV) to paste from
    #       "google" -> only write to the cloud Google Sheet (PHI-safe, no names)
    #       "both"   -> do both
    mode: str = "local"
    local_path: Path = field(default=PROJECT_ROOT / "data" / "intake_master_copy.csv")
    # Start the local copy sheet empty on each app launch (previous session's
    # rows are cleared). The dashboard table is already per-session.
    reset_each_session: bool = True
    # Ordered (header, field_key) pairs for the local copy sheet. Empty -> the
    # default schema in adhd_intake.sheets.local_sheet.DEFAULT_COLUMNS.
    columns: tuple[tuple[str, str], ...] = ()
    service_account_file: Optional[Path] = None
    spreadsheet_id: str = ""
    worksheet_name: str = "Intake Log"

    @property
    def google_enabled(self) -> bool:
        return self.mode in ("google", "both")

    @property
    def local_enabled(self) -> bool:
        return self.mode in ("local", "both")


@dataclass(frozen=True)
class ClinicConfig:
    """The clinic's own contact details — never treated as a patient's."""

    email: str = ""
    address_markers: tuple[str, ...] = ()

    def is_clinic_email(self, value: str | None) -> bool:
        return bool(value) and value.strip().lower() == self.email.strip().lower()

    def is_clinic_address(self, value: str | None) -> bool:
        if not value:
            return False
        v = value.lower()
        return any(m.lower() in v for m in self.address_markers if m)


@dataclass(frozen=True)
class ProgramConfig:
    """Classify a patient's program (MSP vs Private) from the chart's Booking
    Alert text. If any private keyword appears, column A becomes ``private_label``
    ("Private"); otherwise ``default_label`` (blank by default)."""

    private_keywords: tuple[str, ...] = ("private", "therapist supported")
    private_label: str = "Private"
    default_label: str = ""

    def classify(self, alert_text: str | None) -> str:
        t = (alert_text or "").lower()
        if any(k.lower() in t for k in self.private_keywords if k):
            return self.private_label
        return self.default_label


@dataclass(frozen=True)
class AppConfig:
    folders: FolderConfig
    database: DatabaseConfig
    logging: LoggingConfig
    ocr: OcrConfig
    validation: ValidationConfig
    oscar: OscarConfig
    sheets: SheetsConfig
    clinic: ClinicConfig = field(default_factory=ClinicConfig)
    program: ProgramConfig = field(default_factory=ProgramConfig)
    source_path: Path = field(default=DEFAULT_CONFIG_PATH)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """Load configuration from ``path`` (default: ``config.yaml``)."""
        cfg_path = path or DEFAULT_CONFIG_PATH
        if not cfg_path.exists():
            if EXAMPLE_CONFIG_PATH.exists():
                raise ConfigError(
                    f"No config file at {cfg_path}. Copy "
                    f"'{EXAMPLE_CONFIG_PATH.name}' to 'config.yaml' and fill it in."
                )
            raise ConfigError(f"No config file found at {cfg_path}.")

        try:
            raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - defensive
            raise ConfigError(f"Could not parse {cfg_path}: {exc}") from exc

        try:
            folders = FolderConfig(
                incoming=_resolve(raw["folders"]["incoming"]),
                processed=_resolve(raw["folders"]["processed"]),
                rejected=_resolve(raw["folders"]["rejected"]),
            )
            database = DatabaseConfig(path=_resolve(raw["database"]["path"]))
            log_raw = raw.get("logging", {})
            logging_cfg = LoggingConfig(
                dir=_resolve(log_raw.get("dir", "./logs")),
                level=str(log_raw.get("level", "INFO")).upper(),
            )
            ocr_raw = raw.get("ocr", {})
            ocr = OcrConfig(
                tesseract_cmd=ocr_raw.get("tesseract_cmd", "tesseract"),
                language=ocr_raw.get("language", "eng"),
                render_dpi=int(ocr_raw.get("render_dpi", 300)),
            )
            val_raw = raw.get("validation", {})
            validation = ValidationConfig(
                min_ink_density=float(val_raw.get("min_ink_density", 0.01)),
                consent_keywords=tuple(
                    str(k).lower() for k in val_raw.get("consent_keywords", ["consent", "signature"])
                ),
                check_completeness=bool(val_raw.get("check_completeness", True)),
                response_min_ink=float(val_raw.get("response_min_ink", 0.012)),
                response_rel_margin=float(val_raw.get("response_rel_margin", 0.010)),
            )
            oscar_raw = raw["oscar"]
            oscar = OscarConfig(
                base_url=oscar_raw["base_url"].rstrip("/"),
                # Credentials are optional here: the app prompts for the
                # operator's own OSCAR login at start-up so the correct
                # provider profile is connected.
                username=oscar_raw.get("username", ""),
                password=oscar_raw.get("password", ""),
                headless=bool(oscar_raw.get("headless", False)),
                timeout_ms=int(oscar_raw.get("timeout_ms", 30000)),
                document_type=oscar_raw.get("document_type", "Form"),
                browser_channel=str(oscar_raw.get("browser_channel", "chrome")),
                update_chart=bool(oscar_raw.get("update_chart", True)),
            )
            sheets_raw = raw.get("sheets", {})
            sa_file = sheets_raw.get("service_account_file")
            columns = _parse_columns(sheets_raw.get("columns"))
            sheets = SheetsConfig(
                mode=str(sheets_raw.get("mode", "local")).lower(),
                local_path=_resolve(
                    sheets_raw.get("local_path", "./data/intake_master_copy.csv")
                ),
                reset_each_session=bool(sheets_raw.get("reset_each_session", True)),
                columns=columns,
                service_account_file=_resolve(sa_file) if sa_file else None,
                spreadsheet_id=sheets_raw.get("spreadsheet_id", ""),
                worksheet_name=sheets_raw.get("worksheet_name", "Intake Log"),
            )
            clinic_raw = raw.get("clinic", {})
            clinic = ClinicConfig(
                email=str(clinic_raw.get("email", "")),
                address_markers=tuple(str(m) for m in clinic_raw.get("address_markers", [])),
            )
            prog_raw = raw.get("program", {})
            program = ProgramConfig(
                private_keywords=tuple(
                    str(k) for k in prog_raw.get(
                        "private_keywords", ["private", "therapist supported"]
                    )
                ),
                private_label=str(prog_raw.get("private_label", "Private")),
                default_label=str(prog_raw.get("default_label", "")),
            )
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"Missing/invalid key in {cfg_path}: {exc}") from exc

        return cls(
            folders=folders,
            database=database,
            logging=logging_cfg,
            ocr=ocr,
            validation=validation,
            oscar=oscar,
            sheets=sheets,
            clinic=clinic,
            program=program,
            source_path=cfg_path,
        )
