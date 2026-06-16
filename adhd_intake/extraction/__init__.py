"""PDF classification and data extraction.

``PdfClassifier`` and ``Extractor`` depend on PyMuPDF, so they are imported
lazily (PEP 562) — importing lightweight submodules such as ``templates`` does
not pull in the heavy native dependency.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .classifier import PdfClassifier
    from .extractor import Extractor

__all__ = ["PdfClassifier", "Extractor"]


def __getattr__(name: str):
    if name == "PdfClassifier":
        from .classifier import PdfClassifier

        return PdfClassifier
    if name == "Extractor":
        from .extractor import Extractor

        return Extractor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
