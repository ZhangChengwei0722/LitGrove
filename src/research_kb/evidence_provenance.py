from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_kb.errors import GROUNDING_MISMATCH, UNRESOLVED_REFERENCE


_CHAR_LOCATOR = re.compile(r"^page:(?P<page>[1-9][0-9]*):char:(?P<start>[0-9]+)-(?P<end>[0-9]+)$")
_BLOCK_LOCATOR = re.compile(r"^page:(?P<page>[1-9][0-9]*):block:(?P<block>[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class EvidenceLocator:
    kind: str
    page: int
    start: int | None = None
    end: int | None = None
    block: int | None = None


@dataclass(frozen=True, slots=True)
class ProvenanceFailure:
    code: str
    record_kind: str
    record_id: str | None
    json_path: str
    message: str


def parse_locator(value: object) -> EvidenceLocator:
    if not isinstance(value, str):
        raise ValueError("unsupported evidence locator")
    character_match = _CHAR_LOCATOR.fullmatch(value)
    if character_match is not None:
        start = int(character_match.group("start"))
        end = int(character_match.group("end"))
        if start >= end:
            raise ValueError("unsupported evidence locator")
        return EvidenceLocator(
            kind="char",
            page=int(character_match.group("page")),
            start=start,
            end=end,
        )
    block_match = _BLOCK_LOCATOR.fullmatch(value)
    if block_match is not None:
        return EvidenceLocator(
            kind="block",
            page=int(block_match.group("page")),
            block=int(block_match.group("block")),
        )
    raise ValueError("unsupported evidence locator")


def normalize_legacy_block_text(value: str) -> str:
    return " ".join(value.split())


def index_active_pages(
    pages: Iterable[dict[str, Any]],
) -> tuple[dict[str, dict[int, dict[str, Any]]], list[ProvenanceFailure]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        grouped[page["paper_id"]].append(page)

    index: dict[str, dict[int, dict[str, Any]]] = {}
    failures: list[ProvenanceFailure] = []
    for paper_id, paper_pages in grouped.items():
        page_numbers = [page["pdf_page"] for page in paper_pages]
        parse_run_ids = {page["parse_run_id"] for page in paper_pages}
        parser_identities = {
            (page["parser"]["adapter"], page["parser"]["version"])
            for page in paper_pages
        }
        if len(page_numbers) != len(set(page_numbers)):
            failures.append(
                ProvenanceFailure(
                    GROUNDING_MISMATCH,
                    "parsed-page",
                    paper_id,
                    "/pdf_page",
                    "active parsed pages contain duplicate PDF page numbers",
                )
            )
        if len(parse_run_ids) != 1:
            failures.append(
                ProvenanceFailure(
                    GROUNDING_MISMATCH,
                    "parsed-page",
                    paper_id,
                    "/parse_run_id",
                    "active parsed pages do not share one parse run",
                )
            )
        if len(parser_identities) != 1:
            failures.append(
                ProvenanceFailure(
                    GROUNDING_MISMATCH,
                    "parsed-page",
                    paper_id,
                    "/parser",
                    "active parsed pages do not share one parser identity",
                )
            )
        if page_numbers != sorted(page_numbers):
            failures.append(
                ProvenanceFailure(
                    GROUNDING_MISMATCH,
                    "parsed-page",
                    paper_id,
                    "/pdf_page",
                    "active parsed pages are not stored in ascending PDF page order",
                )
            )
        index[paper_id] = {page["pdf_page"]: page for page in paper_pages}
    return index, failures


def validate_evidence_against_pages(
    evidence: Mapping[str, Any],
    pages_by_paper: Mapping[str, Mapping[int, dict[str, Any]]],
) -> list[ProvenanceFailure]:
    evidence_id = evidence.get("evidence_id")
    record_id = evidence_id if isinstance(evidence_id, str) else None
    paper_id = evidence.get("paper_id")
    paper_pages = pages_by_paper.get(paper_id, {}) if isinstance(paper_id, str) else {}
    try:
        locator = parse_locator(evidence.get("locator"))
    except ValueError:
        return [
            ProvenanceFailure(
                GROUNDING_MISMATCH,
                "evidence",
                record_id,
                "/locator",
                "evidence locator is not a supported character or synthetic block locator",
            )
        ]

    source_page = evidence.get("source_page")
    source_pdf_page = source_page.get("pdf_page") if isinstance(source_page, Mapping) else None
    if locator.page != source_pdf_page:
        return [
            ProvenanceFailure(
                GROUNDING_MISMATCH,
                "evidence",
                record_id,
                "/locator",
                "evidence locator page does not match source_page.pdf_page",
            )
        ]

    page = paper_pages.get(locator.page)
    if page is None:
        return [
            ProvenanceFailure(
                UNRESOLVED_REFERENCE,
                "evidence",
                record_id,
                "/source_page/pdf_page",
                "evidence source page does not resolve to an active parsed page for the same paper",
            )
        ]

    page_text = page["text"]
    quote = evidence.get("quote")
    if locator.kind == "char":
        assert locator.start is not None and locator.end is not None
        if locator.end > len(page_text):
            return [
                ProvenanceFailure(
                    GROUNDING_MISMATCH,
                    "evidence",
                    record_id,
                    "/locator",
                    "evidence character locator is outside the stored page text",
                )
            ]
        if quote != page_text[locator.start : locator.end]:
            return [
                ProvenanceFailure(
                    GROUNDING_MISMATCH,
                    "evidence",
                    record_id,
                    "/quote",
                    "evidence quote does not equal the exact stored page-text slice",
                )
            ]
        return []

    normalized_quote = normalize_legacy_block_text(quote) if isinstance(quote, str) else ""
    normalized_page = normalize_legacy_block_text(page_text)
    if not normalized_quote or normalized_quote not in normalized_page:
        return [
            ProvenanceFailure(
                GROUNDING_MISMATCH,
                "evidence",
                record_id,
                "/quote",
                "synthetic block evidence quote is absent from the linked stored page text",
            )
        ]
    return []
