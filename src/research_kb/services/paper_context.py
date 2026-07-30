from __future__ import annotations

from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.errors import GROUNDING_MISMATCH, UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.source_resolution import observe_paper_source
from research_kb.workspace import WorkspaceLayout


class PaperContextService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def show(self, *, paper_id: str) -> dict[str, Any]:
        validate_id(paper_id, Namespace.PAPER)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        paper = next(
            (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == paper_id),
            None,
        )
        if paper is None:
            raise _unresolved(paper_id)

        before = observe_paper_source(self.layout, entries, paper)
        if before.state != "current":
            raise _stale(paper_id, "current source manifestation is not reusable")

        cards = [
            item for item in records_of_kind(entries, "paper-card") if item["paper_id"] == paper_id
        ]
        evidence = sorted(
            (item for item in records_of_kind(entries, "evidence") if item["paper_id"] == paper_id),
            key=lambda item: item["evidence_id"],
        )
        queue = sorted(
            (item for item in records_of_kind(entries, "review-queue") if item["paper_id"] == paper_id),
            key=lambda item: item["queue_id"],
        )
        result = {
            "status": "success",
            "interface_version": "1.0",
            "paper_id": paper_id,
            "paper_card": cards[0] if cards else None,
            "evidence": evidence,
            "review_queue": queue,
        }
        if observe_paper_source(self.layout, entries, paper) != before:
            raise _stale(paper_id, "current source manifestation changed during paper context read")
        return result


def _unresolved(paper_id: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(UNRESOLVED_REFERENCE, "registry-paper", paper_id, "/paper_id", "paper is not registered")
    )


def _stale(paper_id: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(GROUNDING_MISMATCH, "registry-paper", paper_id, "/source_fingerprint", message)
    )


__all__ = ["PaperContextService"]
