from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from research_kb.errors import GROUNDING_MISMATCH, SCHEMA_VALIDATION_FAILED, Diagnostic


REVIEW_NOTE_OPERATIONS = (
    "continuous_text_evidence",
    "figure_table_evidence",
    "formula_layout_analysis",
    "supplementary_analysis",
)
REVIEW_OPERATIONS = ("basic_review_memory", *REVIEW_NOTE_OPERATIONS)


def review_candidate_diagnostics(candidate: Mapping[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    unit_count = 0
    for section_index, section in enumerate(candidate.get("sections", [])):
        section_id = section.get("section_id")
        for unit_index, unit in enumerate(section.get("units", [])):
            unit_count += 1
            base = f"/sections/{section_index}/units/{unit_index}"
            if unit.get("section_id") != section_id:
                diagnostics.append(_diagnostic(GROUNDING_MISMATCH, base + "/section_id", "Review Unit section does not match its parent section"))
            for note_index, note in enumerate(unit.get("source_notes", [])):
                note_path = f"{base}/source_notes/{note_index}"
                operation = note.get("requested_operation")
                figure = note.get("figure_or_table")
                if operation == "figure_table_evidence" and figure is None:
                    diagnostics.append(_diagnostic(GROUNDING_MISMATCH, note_path + "/figure_or_table", "figure/table Review provenance requires a figure_or_table label"))
                if operation == "continuous_text_evidence" and figure is not None:
                    diagnostics.append(_diagnostic(GROUNDING_MISMATCH, note_path + "/requested_operation", "continuous-text Review provenance cannot claim figure/table context"))
    status = candidate.get("memory_value", {}).get("status")
    if unit_count == 0 and status == "reusable":
        diagnostics.append(_diagnostic(SCHEMA_VALIDATION_FAILED, "/memory_value/status", "zero-Unit Review Memory cannot be marked reusable"))
    if unit_count > 0 and status != "reusable":
        diagnostics.append(_diagnostic(SCHEMA_VALIDATION_FAILED, "/memory_value/status", "Review Memory with retained Units must be marked reusable"))
    return diagnostics


def consumed_review_operations(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(note["requested_operation"])
                for section in candidate.get("sections", [])
                for unit in section.get("units", [])
                for note in unit.get("source_notes", [])
            }
        )
    )


def _diagnostic(code: str, path: str, message: str) -> Diagnostic:
    return Diagnostic(code, "review-semantic-candidate", None, path, message)


__all__ = [
    "REVIEW_NOTE_OPERATIONS",
    "REVIEW_OPERATIONS",
    "consumed_review_operations",
    "review_candidate_diagnostics",
]
