from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_kb.errors import GROUNDING_MISMATCH, UNRESOLVED_REFERENCE
from research_kb.evidence_provenance import (
    ProvenanceFailure,
    index_active_pages,
    parse_locator,
)


@dataclass(frozen=True, slots=True)
class ActiveReviewParse:
    paper_id: str
    parse_run_id: str
    adapter: str
    version: str
    pages: Mapping[int, dict[str, Any]]

    @property
    def snapshot(self) -> dict[str, str]:
        return {
            "parse_run_id": self.parse_run_id,
            "adapter": self.adapter,
            "version": self.version,
        }


def build_active_parse_index(
    pages: Iterable[dict[str, Any]],
) -> tuple[dict[str, ActiveReviewParse], list[ProvenanceFailure]]:
    pages_by_paper, failures = index_active_pages(pages)
    invalid_papers = {
        failure.record_id
        for failure in failures
        if failure.record_kind == "parsed-page" and failure.record_id is not None
    }
    active: dict[str, ActiveReviewParse] = {}
    for paper_id, paper_pages in pages_by_paper.items():
        if paper_id in invalid_papers or not paper_pages:
            continue
        first_page = paper_pages[min(paper_pages)]
        active[paper_id] = ActiveReviewParse(
            paper_id=paper_id,
            parse_run_id=first_page["parse_run_id"],
            adapter=first_page["parser"]["adapter"],
            version=first_page["parser"]["version"],
            pages=paper_pages,
        )
    return active, failures


def review_memory_freshness(
    memory: Mapping[str, Any],
    active_by_paper: Mapping[str, ActiveReviewParse],
) -> str:
    paper_id = memory.get("paper_id")
    active = active_by_paper.get(paper_id) if isinstance(paper_id, str) else None
    if active is None:
        return "missing_active_parse"
    return "current" if memory.get("parse_snapshot") == active.snapshot else "stale_parse"


def validate_review_memory_provenance(
    memory: Mapping[str, Any],
    active_by_paper: Mapping[str, ActiveReviewParse],
) -> list[ProvenanceFailure]:
    memory_id = memory.get("review_memory_id")
    record_id = memory_id if isinstance(memory_id, str) else None
    paper_id = memory.get("paper_id")
    active = active_by_paper.get(paper_id) if isinstance(paper_id, str) else None
    if active is None:
        return [
            ProvenanceFailure(
                UNRESOLVED_REFERENCE,
                "review-memory",
                record_id,
                "/parse_snapshot",
                "Review Memory has no active parsed pages for the same paper",
            )
        ]
    if memory.get("parse_snapshot") != active.snapshot:
        return []

    failures: list[ProvenanceFailure] = []
    for section_index, section in enumerate(memory.get("sections", [])):
        if not isinstance(section, Mapping):
            continue
        for unit_index, unit in enumerate(section.get("units", [])):
            if not isinstance(unit, Mapping):
                continue
            for note_index, note in enumerate(unit.get("source_notes", [])):
                if not isinstance(note, Mapping):
                    continue
                base = f"/sections/{section_index}/units/{unit_index}/source_notes/{note_index}"
                failures.extend(_validate_source_note(note, active, record_id, base))
    return failures


def _validate_source_note(
    note: Mapping[str, Any],
    active: ActiveReviewParse,
    record_id: str | None,
    base: str,
) -> list[ProvenanceFailure]:
    note_type = note.get("note_type")
    locator_value = note.get("locator")
    if note_type == "paraphrase":
        if locator_value is not None:
            return [
                ProvenanceFailure(
                    GROUNDING_MISMATCH,
                    "review-memory",
                    record_id,
                    base + "/locator",
                    "Review Memory paraphrase source note must not contain a locator",
                )
            ]
        return _require_source_page(note, active, record_id, base)

    try:
        locator = parse_locator(locator_value)
    except ValueError:
        return [
            ProvenanceFailure(
                GROUNDING_MISMATCH,
                "review-memory",
                record_id,
                base + "/locator",
                "Review Memory quote excerpt requires a supported character locator",
            )
        ]
    if locator.kind != "char":
        return [
            ProvenanceFailure(
                GROUNDING_MISMATCH,
                "review-memory",
                record_id,
                base + "/locator",
                "Review Memory quote excerpt requires a character locator",
            )
        ]
    if locator.page != note.get("pdf_page"):
        return [
            ProvenanceFailure(
                GROUNDING_MISMATCH,
                "review-memory",
                record_id,
                base + "/locator",
                "Review Memory locator page does not match source-note PDF page",
            )
        ]
    page_failures = _require_source_page(note, active, record_id, base)
    if page_failures:
        return page_failures
    page_text = active.pages[locator.page]["text"]
    assert locator.start is not None and locator.end is not None
    if locator.end > len(page_text):
        return [
            ProvenanceFailure(
                GROUNDING_MISMATCH,
                "review-memory",
                record_id,
                base + "/locator",
                "Review Memory character locator is outside the stored page text",
            )
        ]
    if note.get("text") != page_text[locator.start : locator.end]:
        return [
            ProvenanceFailure(
                GROUNDING_MISMATCH,
                "review-memory",
                record_id,
                base + "/text",
                "Review Memory quote excerpt does not equal the exact stored page-text slice",
            )
        ]
    return []


def _require_source_page(
    note: Mapping[str, Any],
    active: ActiveReviewParse,
    record_id: str | None,
    base: str,
) -> list[ProvenanceFailure]:
    pdf_page = note.get("pdf_page")
    if not isinstance(pdf_page, int) or pdf_page not in active.pages:
        return [
            ProvenanceFailure(
                UNRESOLVED_REFERENCE,
                "review-memory",
                record_id,
                base + "/pdf_page",
                "Review Memory source-note page does not resolve to the active parse for the same paper",
            )
        ]
    return []


def normalize_doi(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    return normalized or None


def lead_registry_matches(
    memory: Mapping[str, Any],
    registry_papers: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    papers_by_doi: dict[str, list[str]] = {}
    for paper in registry_papers:
        bibliography = paper.get("bibliography")
        doi = normalize_doi(bibliography.get("doi")) if isinstance(bibliography, Mapping) else None
        paper_id = paper.get("paper_id")
        if doi is not None and isinstance(paper_id, str):
            papers_by_doi.setdefault(doi, []).append(paper_id)

    matches: list[dict[str, Any]] = []
    for section in memory.get("sections", []):
        if not isinstance(section, Mapping):
            continue
        for unit in section.get("units", []):
            if not isinstance(unit, Mapping) or unit.get("unit_type") != "primary_paper_lead":
                continue
            lead = unit.get("primary_paper_lead")
            doi = normalize_doi(lead.get("doi")) if isinstance(lead, Mapping) else None
            matched_ids = sorted(set(papers_by_doi.get(doi, []))) if doi is not None else []
            if doi is None:
                status = "not_evaluable_no_doi"
            elif len(matched_ids) == 0:
                status = "no_registered_doi_match"
            elif len(matched_ids) == 1:
                status = "exact_single_match"
            else:
                status = "exact_multiple_matches"
            matches.append(
                {
                    "review_unit_id": unit.get("review_unit_id"),
                    "status": status,
                    "matched_paper_ids": matched_ids,
                }
            )
    return sorted(matches, key=lambda item: item["review_unit_id"])


__all__ = [
    "ActiveReviewParse",
    "build_active_parse_index",
    "lead_registry_matches",
    "normalize_doi",
    "review_memory_freshness",
    "validate_review_memory_provenance",
]
