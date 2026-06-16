"""Consent-page signature and questionnaire-completeness validation."""

from .completeness import CompletenessValidator
from .signature import SignatureValidator

__all__ = ["SignatureValidator", "CompletenessValidator"]
