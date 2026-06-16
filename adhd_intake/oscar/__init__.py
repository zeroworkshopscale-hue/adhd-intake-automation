"""OSCAR Pro browser automation (patient search + document upload)."""

from .client import OscarClient, OscarError, OscarLoginError, PatientNotFoundError

__all__ = ["OscarClient", "OscarError", "OscarLoginError", "PatientNotFoundError"]
