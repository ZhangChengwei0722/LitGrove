from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.process_events import timestamp, utc_now
from research_kb.services.question_screening import QuestionScreeningService
from research_kb.services.workspace_session import WorkspaceSession


MAX_PAGE_SIZE = 100


class QuestionScreeningApplicationService:
    def limits(self, session: WorkspaceSession) -> dict[str, Any]:
        _layout(session)
        return _read_response({"max_page_size": MAX_PAGE_SIZE, "outcomes": ["included", "excluded"], "basis_scopes": ["metadata", "available_abstract", "paper_card", "user_full_text_review", "mixed"]})

    def list_criteria(self, session: WorkspaceSession, *, question_id: str | None = None, include_archived: bool = False, page_size: int = 20, cursor: str | None = None) -> dict[str, Any]:
        items = QuestionScreeningService(_layout(session)).list_criteria(question_id=question_id, include_archived=include_archived)
        page, next_cursor = _page(items, "criteria_id", page_size, cursor)
        return _read_response({"criteria": page, "next_cursor": next_cursor})

    def show_criteria(self, session: WorkspaceSession, criteria_id: str) -> dict[str, Any]:
        return _read_response({"criteria": QuestionScreeningService(_layout(session)).read_criteria(criteria_id)})

    def promote_criteria(self, session: WorkspaceSession, request: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"criteria_id", "question_id", "title", "scope", "inclusion_criteria", "exclusion_criteria", "notes", "status", "expected_revision_id", "receipt_id"}
        _closed_request(request, allowed, {"receipt_id"})
        payload = {key: request[key] for key in ("question_id", "title", "scope", "inclusion_criteria", "exclusion_criteria", "notes", "status") if key in request}
        bundle, transaction = QuestionScreeningService(_layout(session)).promote_criteria(payload, criteria_id=request.get("criteria_id"), expected_revision_id=request.get("expected_revision_id"), approval=_approval(request["receipt_id"]), actor="user")
        record = bundle["revisions"][-1]["criteria"]
        return _write_response({"result": "no_change" if transaction is None else "committed", "criteria": {**record, "revision_id": bundle["active_revision_id"], "criteria_digest": bundle["revisions"][-1]["content_digest"]}})

    def list_decisions(self, session: WorkspaceSession, *, question_id: str | None = None, paper_id: str | None = None, outcome: str | None = None, freshness: str | None = None, page_size: int = 20, cursor: str | None = None) -> dict[str, Any]:
        items = QuestionScreeningService(_layout(session)).list_decisions(question_id=question_id, paper_id=paper_id)
        if outcome is not None:
            items = [item for item in items if item["outcome"] == outcome]
        if freshness is not None:
            items = [item for item in items if item["freshness"]["state"] == freshness]
        page, next_cursor = _page(items, "decision_id", page_size, cursor)
        return _read_response({"decisions": page, "next_cursor": next_cursor})

    def show_decision(self, session: WorkspaceSession, decision_id: str) -> dict[str, Any]:
        return _read_response({"decision": QuestionScreeningService(_layout(session)).read_decision(decision_id)})

    def promote_decision(self, session: WorkspaceSession, request: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"decision_id", "question_id", "paper_id", "outcome", "criteria_revision_id", "criteria_digest", "criterion_dispositions", "basis_scope", "rationale", "known_limitations", "expected_revision_id", "receipt_id"}
        _closed_request(request, allowed, {"question_id", "paper_id", "outcome", "criteria_revision_id", "criteria_digest", "criterion_dispositions", "basis_scope", "rationale", "known_limitations", "receipt_id"})
        payload = {key: request[key] for key in allowed if key not in {"decision_id", "expected_revision_id", "receipt_id"}}
        bundle, transaction = QuestionScreeningService(_layout(session)).promote_decision(payload, decision_id=request.get("decision_id"), expected_revision_id=request.get("expected_revision_id"), approval=_approval(request["receipt_id"]), actor="user")
        decision = bundle["revisions"][-1]["decision"]
        return _write_response({"result": "no_change" if transaction is None else "committed", "decision": {**decision, "revision_id": bundle["active_revision_id"]}})


def _approval(receipt_id: object) -> dict[str, str]:
    if not isinstance(receipt_id, str) or not receipt_id or len(receipt_id) > 200:
        raise _error("/receipt_id", "receipt_id must be non-empty and at most 200 characters")
    return {"receipt_id": receipt_id, "approved_by": "user", "approved_at": timestamp(utc_now), "origin": "user_authored"}


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
    return ResearchKBError(Diagnostic(SCHEMA_VALIDATION_FAILED, "question-screening-application-request", None, path, message))


__all__ = ["MAX_PAGE_SIZE", "QuestionScreeningApplicationService"]
