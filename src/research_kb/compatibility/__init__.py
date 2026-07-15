"""Read-only legacy compatibility contracts."""

from research_kb.compatibility.base import (
    CompatibilityContext,
    CompatibilitySourceRef,
    DifferenceCandidate,
    InventoryCandidate,
    LegacyReaderAdapter,
)

__all__ = [
    "CompatibilityContext",
    "CompatibilitySourceRef",
    "DifferenceCandidate",
    "InventoryCandidate",
    "LegacyReaderAdapter",
]
