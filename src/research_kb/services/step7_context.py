from __future__ import annotations

from typing import Any

from research_kb.bundle import BundleEntry, load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.errors import UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.step7_support import STEP7_RECORD_KINDS, STEP7_TYPE_ORDER, candidate_freshness
from research_kb.workspace import WorkspaceLayout


STATUS_ORDER = ("keep", "revise", "rejected", "needs_resolution")


class Step7ContextService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def show(self, *, question_id: str) -> dict[str, Any]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        return project_step7_context(entries, question_id)


def project_step7_context(
    entries: list[BundleEntry],
    question_id: str,
) -> dict[str, Any]:
    resolved_id = validate_id(question_id, Namespace.QUESTION)
    mapping = next(
        (
            item
            for item in records_of_kind(entries, "question-mapping")
            if item["question_id"] == resolved_id
        ),
        None,
    )
    if mapping is None:
        raise ResearchKBError(
            Diagnostic(
                UNRESOLVED_REFERENCE,
                "question-mapping",
                resolved_id,
                "/question_id",
                "question mapping does not exist",
            )
        )
    type_rank = {candidate_type: index for index, candidate_type in enumerate(STEP7_TYPE_ORDER)}
    candidates = [
        record
        for kind, record in entries
        if kind in STEP7_RECORD_KINDS and record["question_id"] == resolved_id
    ]
    candidates.sort(key=lambda item: (type_rank[item["type"]], item["candidate_id"]))
    projected = [
        {"candidate": item, "freshness": candidate_freshness(item, entries)}
        for item in candidates
    ]
    by_type = {
        candidate_type: sum(item["candidate"]["type"] == candidate_type for item in projected)
        for candidate_type in STEP7_TYPE_ORDER
    }
    by_status = {
        status: sum(item["candidate"]["candidate_status"] == status for item in projected)
        for status in STATUS_ORDER
    }
    return {
        "status": "success",
        "interface_version": "1.0",
        "question_id": resolved_id,
        "question_mapping": mapping,
        "candidates": projected,
        "summary": {
            "total": len(projected),
            "by_type": by_type,
            "by_status": by_status,
            "stale_count": sum(item["freshness"]["state"] != "current" for item in projected),
        },
    }


__all__ = ["Step7ContextService", "project_step7_context"]
