from __future__ import annotations

from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INPUT_TOO_LARGE,
    MANUSCRIPT_SOURCE_UNSUPPORTED,
    Diagnostic,
    ResearchKBError,
)
from research_kb.manuscript import OoxmlManuscriptAdapter, PdfManuscriptAdapter
from research_kb.services.intake_inspect import IntakeInspectService
from research_kb.storage.json_io import file_sha256
from research_kb.workspace import WorkspaceLayout


MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PROJECTED_UNITS = 20_000
MAX_EXTRACTED_CHARACTERS = 4 * 1024 * 1024


class ManuscriptProjectionService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def inspect(self, *, source: Path) -> dict[str, Any]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        resolved_source, root_id, relative_path = IntakeInspectService(self.layout).project_source(source)
        try:
            source_size = resolved_source.stat().st_size
        except OSError as error:
            raise _unsupported("manuscript source cannot be read") from error
        if source_size > MAX_SOURCE_BYTES:
            raise _too_large("/source", "manuscript source exceeds the 64 MiB limit")

        source_hash = file_sha256(resolved_source)
        if source_hash is None:
            raise _unsupported("manuscript source cannot be fingerprinted")
        suffix = resolved_source.suffix.lower()
        adapters = {
            ".docx": ("docx", OoxmlManuscriptAdapter()),
            ".pdf": ("pdf", PdfManuscriptAdapter()),
        }
        try:
            document_format, adapter = adapters[suffix]
        except KeyError as error:
            raise _unsupported("manuscript source must use the .docx or .pdf extension") from error

        projection = adapter.project(resolved_source)
        units = projection["units"]
        if len(units) > MAX_PROJECTED_UNITS:
            raise _too_large("/units", "manuscript projection exceeds the unit limit")
        extracted_character_count = sum(len(unit["text"]) for unit in units)
        if extracted_character_count > MAX_EXTRACTED_CHARACTERS:
            raise _too_large("/units", "manuscript projection exceeds the extracted-character limit")
        if file_sha256(resolved_source) != source_hash:
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    "manuscript-projection",
                    None,
                    "/source",
                    "source changed during manuscript projection",
                )
            )

        return {
            "status": "success",
            "interface_version": "1.0",
            "document": {
                "format": document_format,
                "source": {"root_id": root_id, "relative_path": relative_path},
                "source_fingerprint": {"algorithm": "sha256", "value": source_hash},
                "parser": projection["parser"],
                "unit_kind": projection["unit_kind"],
                "unit_count": len(units),
                "extracted_character_count": extracted_character_count,
                "coverage_limits": projection["coverage_limits"],
            },
            "units": units,
            "persistent_writes": 0,
        }


def _unsupported(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            MANUSCRIPT_SOURCE_UNSUPPORTED,
            "manuscript-projection",
            None,
            "/source",
            message,
        )
    )


def _too_large(json_path: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(INPUT_TOO_LARGE, "manuscript-projection", None, json_path, message)
    )


__all__ = ["ManuscriptProjectionService"]
