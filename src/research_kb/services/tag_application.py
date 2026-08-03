from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.process_events import timestamp, utc_now
from research_kb.services.tags import TARGET_NAMESPACES, TagService
from research_kb.services.workspace_session import WorkspaceSession


MAX_PAGE_SIZE = 100


class TagApplicationService:
    def limits(self, session: WorkspaceSession) -> dict[str, Any]:
        _layout(session)
        return _read_response({"max_page_size": MAX_PAGE_SIZE, "target_kinds": sorted(TARGET_NAMESPACES)})

    def list_tags(
        self,
        session: WorkspaceSession,
        *,
        include_archived: bool = False,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        items = TagService(_layout(session)).list_tags(include_archived=include_archived)
        page, next_cursor = _page(items, "tag_id", page_size, cursor)
        return _read_response({"tags": page, "next_cursor": next_cursor})

    def show_tag(self, session: WorkspaceSession, tag_id: str) -> dict[str, Any]:
        service = TagService(_layout(session))
        return _read_response({
            "tag": service.read_tag(tag_id),
            "assignments": service.list_assignments(tag_id=tag_id, include_removed=False),
        })

    def list_target_tags(
        self,
        session: WorkspaceSession,
        *,
        target_kind: str,
        target_id: str,
    ) -> dict[str, Any]:
        service = TagService(_layout(session))
        assignments = service.list_assignments(target_kind=target_kind, target_id=target_id)
        tags = {item["tag_id"]: service.read_tag(item["tag_id"]) for item in assignments}
        return _read_response({"target_kind": target_kind, "target_id": target_id, "tags": [tags[key] for key in sorted(tags)]})

    def promote_tag(self, session: WorkspaceSession, request: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"tag_id", "name", "description", "aliases", "status", "expected_revision_id", "receipt_id"}
        _closed_request(request, allowed, {"receipt_id"})
        payload = {key: request[key] for key in ("name", "description", "aliases", "status") if key in request}
        bundle, transaction = TagService(_layout(session)).promote_tag(
            payload,
            tag_id=request.get("tag_id"),
            expected_revision_id=request.get("expected_revision_id"),
            approval=_approval(request["receipt_id"]),
            actor="user",
        )
        tag = bundle["revisions"][-1]["tag"]
        return _write_response({"result": "no_change" if transaction is None else "committed", "tag": {**tag, "revision_id": bundle["active_revision_id"]}})

    def set_assignment(self, session: WorkspaceSession, request: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"tag_id", "target_kind", "target_id", "state", "expected_revision_id", "receipt_id"}
        _closed_request(request, allowed, {"tag_id", "target_kind", "target_id", "state", "receipt_id"})
        bundle, transaction = TagService(_layout(session)).set_assignment(
            tag_id=str(request["tag_id"]),
            target_kind=str(request["target_kind"]),
            target_id=str(request["target_id"]),
            state=str(request["state"]),
            expected_revision_id=request.get("expected_revision_id"),
            approval=_approval(request["receipt_id"]),
            actor="user",
        )
        assignment = None if bundle is None else {
            "tag_link_id": bundle["tag_link_id"],
            "tag_id": bundle["tag_id"],
            "target_kind": bundle["target_kind"],
            "target_id": bundle["target_id"],
            "state": bundle["revisions"][-1]["state"],
            "revision_id": bundle["active_revision_id"],
        }
        return _write_response({"result": "no_change" if transaction is None else "committed", "assignment": assignment})


def _approval(receipt_id: object) -> dict[str, str]:
    if not isinstance(receipt_id, str) or not receipt_id or len(receipt_id) > 200:
        raise _error("/receipt_id", "receipt_id must be non-empty and at most 200 characters")
    return {
        "receipt_id": receipt_id,
        "approved_by": "user",
        "approved_at": timestamp(utc_now),
        "origin": "user_authored",
    }


def _closed_request(request: Mapping[str, Any], allowed: set[str], required: set[str]) -> None:
    if not isinstance(request, Mapping) or set(request) - allowed or not required <= set(request):
        raise _error("", "request has missing or unexpected fields")


def _page(items: list[dict[str, Any]], id_field: str, page_size: int, cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
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
    return page, page[-1][id_field] if start + page_size < len(ordered) else None


def _layout(session: WorkspaceSession):
    if not isinstance(session, WorkspaceSession):
        raise _error("/session", "a Core-owned WorkspaceSession is required")
    return session._layout


def _read_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "success", "interface_version": "1.0", "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION, **payload, "persistent_writes": 0, "canonical_scientific_write": False}


def _write_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "success", "interface_version": "1.0", "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION, **payload, "persistent_writes": 1 if payload["result"] == "committed" else 0, "canonical_scientific_write": False}


def _error(path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(SCHEMA_VALIDATION_FAILED, "tag-application-request", None, path, message))


__all__ = ["MAX_PAGE_SIZE", "TagApplicationService"]
