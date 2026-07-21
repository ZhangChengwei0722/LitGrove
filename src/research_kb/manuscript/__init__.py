"""Bounded read-only manuscript projection adapters."""

from research_kb.manuscript.ooxml import OoxmlManuscriptAdapter
from research_kb.manuscript.pdf import PdfManuscriptAdapter

__all__ = ["OoxmlManuscriptAdapter", "PdfManuscriptAdapter"]
