from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from research_kb.errors import GROUNDING_MISMATCH, SCHEMA_VALIDATION_FAILED, UNRESOLVED_REFERENCE, Diagnostic


EVIDENCE_OPERATIONS = (
    "continuous_text_evidence",
    "figure_table_evidence",
    "formula_layout_analysis",
    "supplementary_analysis",
)
PRIMARY_OPERATIONS = ("basic_paper_card", *EVIDENCE_OPERATIONS)


def primary_candidate_diagnostics(
    candidate: Mapping[str, Any],
    *,
    expected_sections: Sequence[str],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    evidence_aliases = [item.get("alias") for item in candidate.get("evidence", [])]
    boundary_aliases = [item.get("alias") for item in candidate.get("review_boundaries", [])]
    diagnostics.extend(_duplicate_aliases(evidence_aliases, "/evidence"))
    diagnostics.extend(_duplicate_aliases(boundary_aliases, "/review_boundaries"))
    overlap = set(evidence_aliases) & set(boundary_aliases)
    if overlap:
        diagnostics.append(_diagnostic(SCHEMA_VALIDATION_FAILED, "/review_boundaries", "Evidence and boundary aliases must be globally distinct"))
    sections = candidate.get("sections", [])
    actual_sections = [item.get("section_id") for item in sections]
    if actual_sections != list(expected_sections):
        diagnostics.append(_diagnostic(SCHEMA_VALIDATION_FAILED, "/sections", "candidate sections must exactly match the ordered domain profile sections"))
    evidence_set = set(evidence_aliases)
    boundary_set = set(boundary_aliases)
    for section_index, section in enumerate(sections):
        for unit_index, unit in enumerate(section.get("units", [])):
            base = f"/sections/{section_index}/units/{unit_index}"
            selected_evidence = unit.get("evidence_aliases", [])
            selected_boundaries = unit.get("boundary_aliases", [])
            for value in selected_evidence:
                if value not in evidence_set:
                    diagnostics.append(_diagnostic(UNRESOLVED_REFERENCE, base + "/evidence_aliases", f"unresolved Evidence alias: {value}"))
            for value in selected_boundaries:
                if value not in boundary_set:
                    diagnostics.append(_diagnostic(UNRESOLVED_REFERENCE, base + "/boundary_aliases", f"unresolved boundary alias: {value}"))
            status = unit.get("grounding_status")
            if status in {"grounded", "revised"} and not selected_evidence:
                diagnostics.append(_diagnostic(GROUNDING_MISMATCH, base + "/evidence_aliases", "grounded/revised Unit requires Evidence"))
            if status in {"interpretive", "background_only", "needs_resolution"} and selected_evidence:
                diagnostics.append(_diagnostic(GROUNDING_MISMATCH, base + "/evidence_aliases", "non-supporting Unit cannot cite Evidence"))
            if status == "needs_resolution" and not selected_boundaries:
                diagnostics.append(_diagnostic(GROUNDING_MISMATCH, base + "/boundary_aliases", "needs_resolution Unit requires a scientific boundary"))
    return diagnostics


def consumed_evidence_operations(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted({str(item["requested_operation"]) for item in candidate.get("evidence", [])}))


def _duplicate_aliases(values: list[object], path: str) -> list[Diagnostic]:
    return [] if len(values) == len(set(values)) else [_diagnostic(SCHEMA_VALIDATION_FAILED, path, "candidate aliases must be unique")]


def _diagnostic(code: str, path: str, message: str) -> Diagnostic:
    return Diagnostic(code, "primary-semantic-candidate", None, path, message)


__all__ = [
    "EVIDENCE_OPERATIONS",
    "PRIMARY_OPERATIONS",
    "consumed_evidence_operations",
    "primary_candidate_diagnostics",
]
