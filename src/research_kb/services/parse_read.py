from __future__ import annotations

from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.errors import (
    GROUNDING_MISMATCH,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, validate_id
from research_kb.source_resolution import observe_paper_source
from research_kb.workspace import WorkspaceLayout


class ParseReadService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def show(self, *, paper_id: str, page: object | None = None) -> dict[str, Any]:
        validate_id(paper_id, Namespace.PAPER)
        requested_page = _positive_page(page)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        paper = next(
            (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == paper_id),
            None,
        )
        if paper is None:
            raise _unresolved(paper_id, "/paper_id", "paper is not registered")
        before = observe_paper_source(self.layout, entries, paper)
        if before.state != "current":
            raise _stale(paper_id, "current source manifestation is not reusable")

        pages = sorted(
            (
                item
                for item in records_of_kind(entries, "parsed-page")
                if item["paper_id"] == paper_id
            ),
            key=lambda item: item["pdf_page"],
        )
        if not pages:
            raise _unresolved(paper_id, "/parse_run_id", "paper has no active parse")
        selected = pages
        if requested_page is not None:
            selected = [item for item in pages if item["pdf_page"] == requested_page]
            if not selected:
                raise _unresolved(paper_id, "/pdf_page", "requested parsed page does not exist")

        result = {
            "status": "success",
            "interface_version": "1.0",
            "paper_id": paper_id,
            "parse_run_id": pages[0]["parse_run_id"],
            "parser": dict(pages[0]["parser"]),
            "page_count": len(pages),
            "returned_page_count": len(selected),
            "pages": selected,
        }
        if observe_paper_source(self.layout, entries, paper) != before:
            raise _stale(paper_id, "current source manifestation changed during parsed-content read")
        return result


def _positive_page(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise _invalid_page()
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise _invalid_page() from error
    if parsed < 1 or str(parsed) != str(value):
        raise _invalid_page()
    return parsed


def _invalid_page() -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "parsed-page", None, "/pdf_page", "page must be a positive integer")
    )


def _unresolved(paper_id: str, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(UNRESOLVED_REFERENCE, "parsed-page", paper_id, path, message))


def _stale(paper_id: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(GROUNDING_MISMATCH, "parsed-page", paper_id, "/source_fingerprint", message))


__all__ = ["ParseReadService"]
