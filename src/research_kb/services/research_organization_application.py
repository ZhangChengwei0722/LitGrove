from __future__ import annotations

from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.services.workspace_session import WorkspaceSession


MAX_PAGE_SIZE = 100
MAX_LINKS_PER_DETAIL = 100
MAX_RELATED_TARGETS = 100


class ResearchOrganizationApplicationService:
    def limits(self, session: WorkspaceSession) -> dict[str, Any]:
        _layout(session)
        return _response({"max_page_size": MAX_PAGE_SIZE})

    def list_directions(
        self,
        session: WorkspaceSession,
        *,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        service = ResearchOrganizationService(_layout(session))
        items, next_cursor = _page(service.list_directions(), "direction_id", page_size, cursor)
        return _response({"directions": [_safe_direction(item, include_links=False) for item in items], "next_cursor": next_cursor})

    def show_direction(self, session: WorkspaceSession, direction_id: str) -> dict[str, Any]:
        item = ResearchOrganizationService(_layout(session)).read_direction(direction_id)
        return _response({"direction": _safe_direction(item, include_links=True)})

    def list_field_map_entries(
        self,
        session: WorkspaceSession,
        *,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        service = ResearchOrganizationService(_layout(session))
        items, next_cursor = _page(
            service.list_field_map_entries(), "field_map_entry_id", page_size, cursor
        )
        return _response({"field_map_entries": [_safe_field(item, include_links=False) for item in items], "next_cursor": next_cursor})

    def show_field_map_entry(
        self,
        session: WorkspaceSession,
        field_map_entry_id: str,
    ) -> dict[str, Any]:
        item = ResearchOrganizationService(_layout(session)).read_field_map_entry(field_map_entry_id)
        return _response({"field_map_entry": _safe_field(item, include_links=True)})

    def list_questions(
        self,
        session: WorkspaceSession,
        *,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        layout = _layout(session)
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        questions = [
            {
                **item,
                "compatibility_source": (
                    "p7_revision"
                    if layout.question_revision_bundle_path(item["question_id"]).is_file()
                    else "legacy"
                ),
                "background_links": [],
            }
            for item in records_of_kind(entries, "question-mapping")
        ]
        items, next_cursor = _page(questions, "question_id", page_size, cursor)
        return _response({"questions": [_safe_question(item, include_links=False) for item in items], "next_cursor": next_cursor})

    def show_question(self, session: WorkspaceSession, question_id: str) -> dict[str, Any]:
        item = ResearchOrganizationService(_layout(session)).read_question(question_id)
        return _response({"question": _safe_question(item, include_links=True)})

    def show_paper_context(self, session: WorkspaceSession, paper_id: str) -> dict[str, Any]:
        validate_id(paper_id, Namespace.PAPER)
        layout = _layout(session)
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        service = ResearchOrganizationService(layout)
        directions = [_safe_direction(item, include_links=True) for item in service.list_directions() if _links_paper(item, paper_id)]
        fields = [_safe_field(item, include_links=True) for item in service.list_field_map_entries() if _links_paper(item, paper_id)]
        questions = []
        for mapping in records_of_kind(entries, "question-mapping"):
            if any(link["paper_id"] == paper_id for link in mapping["paper_links"]):
                questions.append(_safe_question(service.read_question(mapping["question_id"]), include_links=True))
        return _response(
            {
                "paper_id": paper_id,
                "directions": directions[:MAX_RELATED_TARGETS],
                "direction_count": len(directions),
                "directions_truncated": len(directions) > MAX_RELATED_TARGETS,
                "field_map_entries": fields[:MAX_RELATED_TARGETS],
                "field_map_entry_count": len(fields),
                "field_map_entries_truncated": len(fields) > MAX_RELATED_TARGETS,
                "questions": questions[:MAX_RELATED_TARGETS],
                "question_count": len(questions),
                "questions_truncated": len(questions) > MAX_RELATED_TARGETS,
            }
        )


def _safe_direction(item: dict[str, Any], *, include_links: bool) -> dict[str, Any]:
    result = {key: item[key] for key in ("direction_id", "name", "scope", "status", "gap_notes", "revision_id")}
    return _with_links(result, item["links"], include_links)


def _safe_field(item: dict[str, Any], *, include_links: bool) -> dict[str, Any]:
    result = {
        key: item[key]
        for key in (
            "field_map_entry_id",
            "title",
            "entry_type",
            "definition",
            "status",
            "consensus_level",
            "direction_refs",
            "aspect_notes",
            "revision_id",
        )
    }
    return _with_links(result, item["links"], include_links)


def _safe_question(item: dict[str, Any], *, include_links: bool) -> dict[str, Any]:
    result = {
        key: item[key]
        for key in (
            "question_id",
            "question_text",
            "scope",
            "mapping_status",
            "compatibility_source",
        )
    }
    if "revision_id" in item:
        result["revision_id"] = item["revision_id"]
    result = _with_links(result, item["paper_links"], include_links, key="paper_links")
    return _with_links(
        result,
        item.get("background_links", []),
        include_links,
        key="background_links",
    )


def _with_links(
    result: dict[str, Any],
    links: list[dict[str, Any]],
    include_links: bool,
    *,
    key: str = "links",
) -> dict[str, Any]:
    result[f"{key}_count"] = len(links)
    if include_links:
        result[key] = links[:MAX_LINKS_PER_DETAIL]
        result[f"{key}_truncated"] = len(links) > MAX_LINKS_PER_DETAIL
    return result


def _links_paper(item: dict[str, Any], paper_id: str) -> bool:
    return any(link["paper_id"] == paper_id for link in item.get("links", []))


def _page(
    items: list[dict[str, Any]],
    id_field: str,
    page_size: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise _error("/page_size", f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    ordered = sorted(items, key=lambda item: item[id_field])
    start = 0
    if cursor is not None:
        ids = [item[id_field] for item in ordered]
        if cursor not in ids:
            raise _error("/cursor", "cursor is not present in the current result set")
        start = ids.index(cursor) + 1
    page = ordered[start : start + page_size]
    next_cursor = page[-1][id_field] if start + page_size < len(ordered) else None
    return page, next_cursor


def _layout(session: WorkspaceSession):
    if not isinstance(session, WorkspaceSession):
        raise _error("/session", "a Core-owned WorkspaceSession is required")
    return session._layout


def _response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "interface_version": "1.0",
        "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
        **payload,
        "persistent_writes": 0,
        "canonical_scientific_write": False,
    }


def _error(path: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "research-organization-request", None, path, message)
    )


__all__ = ["MAX_PAGE_SIZE", "ResearchOrganizationApplicationService"]
