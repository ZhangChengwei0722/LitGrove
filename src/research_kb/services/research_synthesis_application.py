from __future__ import annotations

from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.errors import SCHEMA_VALIDATION_FAILED, UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.services.workspace_session import WorkspaceSession
from research_kb.step7_support import STEP7_RECORD_KINDS, candidate_freshness


MAX_PAGE_SIZE = 100
CANDIDATE_TYPES = ("synthesis", "review_angle", "insight", "cross_view")


class ResearchSynthesisApplicationService:
    def limits(self, session: WorkspaceSession) -> dict[str, Any]:
        _layout(session)
        return _response(
            {
                "max_page_size": MAX_PAGE_SIZE,
                "candidate_types": list(CANDIDATE_TYPES),
                "maintenance_intents": ["append", "replace"],
                "ordinary_query_can_write": False,
                "review_background_can_support_fact": False,
            }
        )

    def list_candidates(
        self,
        session: WorkspaceSession,
        *,
        question_id: str | None = None,
        candidate_type: str | None = None,
        freshness: str | None = None,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        layout = _layout(session)
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        if question_id is not None:
            question_id = validate_id(question_id, Namespace.QUESTION)
        if candidate_type is not None and candidate_type not in CANDIDATE_TYPES:
            raise _error("/candidate_type", "unsupported Research Synthesis candidate type")
        if freshness is not None and freshness not in {"current", "stale"}:
            raise _error("/freshness", "freshness must be current or stale")
        candidates = []
        for kind, record in entries:
            if kind not in STEP7_RECORD_KINDS:
                continue
            if question_id is not None and record["question_id"] != question_id:
                continue
            if candidate_type is not None and record["type"] != candidate_type:
                continue
            projected = _candidate_projection(record, entries, detail=False)
            if freshness is None or projected["freshness"]["state"] == freshness:
                candidates.append(projected)
        page, next_cursor = _page(candidates, page_size, cursor)
        return _response({"candidates": page, "next_cursor": next_cursor})

    def show_candidate(self, session: WorkspaceSession, candidate_id: str) -> dict[str, Any]:
        layout = _layout(session)
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        record = next(
            (
                item
                for kind, item in entries
                if kind in STEP7_RECORD_KINDS and item["candidate_id"] == candidate_id
            ),
            None,
        )
        if record is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "research-synthesis-candidate",
                    candidate_id,
                    "/candidate_id",
                    "Research Synthesis candidate does not exist",
                )
            )
        return _response({"candidate": _candidate_projection(record, entries, detail=True)})

    def question_context(self, session: WorkspaceSession, question_id: str) -> dict[str, Any]:
        layout = _layout(session)
        question_id = validate_id(question_id, Namespace.QUESTION)
        question = ResearchOrganizationService(layout).read_question(question_id)
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        candidates = [
            item
            for kind, item in entries
            if kind in STEP7_RECORD_KINDS and item["question_id"] == question_id
        ]
        counts = {candidate_type: 0 for candidate_type in CANDIDATE_TYPES}
        stale_count = 0
        for item in candidates:
            counts[item["type"]] += 1
            stale_count += int(candidate_freshness(item, entries)["state"] == "stale")
        return _response(
            {
                "question": {
                    "question_id": question["question_id"],
                    "question_text": question["question_text"],
                    "scope": question["scope"],
                    "mapping_status": question["mapping_status"],
                    "revision_id": question.get("revision_id"),
                    "factual_link_count": len(question["paper_links"]),
                    "background_link_count": len(question.get("background_links", [])),
                },
                "candidate_counts": counts,
                "candidate_count": sum(counts.values()),
                "stale_candidate_count": stale_count,
                "candidates_truncated": False,
            }
        )


def _candidate_projection(record: dict[str, Any], entries, *, detail: bool) -> dict[str, Any]:
    result = {
        "candidate_id": record["candidate_id"],
        "candidate_type": record["type"],
        "question_id": record["question_id"],
        "title": record["title"],
        "candidate_status": record["candidate_status"],
        "analysis_operator": record["analysis_operator"],
        "trace_status": record["trace_status"],
        "not_fact": record["not_fact"],
        "review_status": record["review_status"],
        "automation_status": record["automation_status"],
        "primary_paper_count": len(record["paper_card_base"]),
        "evidence_count": len(record["evidence_base"]),
        "boundary_count": len(record["review_queue_refs"]),
        "review_background_count": sum(
            len(item["review_unit_ids"]) for item in record.get("review_background_base", [])
        ),
        "freshness": candidate_freshness(record, entries),
        "updated_at": record["updated_at"],
    }
    if detail:
        result.update(
            {
                "paper_card_base": record["paper_card_base"],
                "evidence_base": record["evidence_base"],
                "review_queue_refs": record["review_queue_refs"],
                "review_background_base": record.get("review_background_base", []),
                "missing_evidence": record["missing_evidence"],
                "assumptions": record["assumptions"],
                "risk": record["risk"],
                "testability": record["testability"],
                "next_action": record["next_action"],
                "type_content": {
                    key: value
                    for key, value in record.items()
                    if key in {
                        "claim", "scope", "agreement_pattern", "conflict_pattern", "boundary_statement",
                        "thesis", "organizing_axes", "included_clusters", "excluded_scope", "why_this_angle_adds_value",
                        "insight_type", "hypothesis_or_idea", "rationale", "falsification_condition", "minimum_test",
                        "source_views", "relation_type", "why_interesting", "shared_dimension", "non_equivalence_warning",
                    }
                },
            }
        )
    return result


def _page(items: list[dict[str, Any]], page_size: int, cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise _error("/page_size", f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    ordered = sorted(items, key=lambda item: item["candidate_id"])
    start = 0
    if cursor is not None:
        ids = [item["candidate_id"] for item in ordered]
        if cursor not in ids:
            raise _error("/cursor", "cursor is not present in the current result set")
        start = ids.index(cursor) + 1
    page = ordered[start : start + page_size]
    return page, page[-1]["candidate_id"] if start + page_size < len(ordered) else None


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
        Diagnostic(SCHEMA_VALIDATION_FAILED, "research-synthesis-application-request", None, path, message)
    )


__all__ = ["CANDIDATE_TYPES", "MAX_PAGE_SIZE", "ResearchSynthesisApplicationService"]
