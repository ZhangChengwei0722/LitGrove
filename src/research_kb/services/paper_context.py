from __future__ import annotations

from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.errors import GROUNDING_MISMATCH, UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.storage.json_io import file_sha256
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

        _, source = self.layout.resolve_source(
            paper["source_ref"]["root_id"],
            paper["source_ref"]["relative_path"],
        )
        expected_hash = paper["source_fingerprint"]["value"]
        before_hash = _source_hash(source, paper_id)
        if before_hash != expected_hash:
            raise _stale(paper_id, "registered source fingerprint is stale")

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
        if _source_hash(source, paper_id) != before_hash:
            raise _stale(paper_id, "registered source changed during paper context read")
        return result


def _source_hash(source: Path, paper_id: str) -> str:
    if not source.exists() or not source.is_file():
        raise _stale(paper_id, "registered source is missing or is not a regular file")
    try:
        return file_sha256(source)
    except OSError as error:
        raise _stale(paper_id, "registered source could not be read") from error


def _unresolved(paper_id: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(UNRESOLVED_REFERENCE, "registry-paper", paper_id, "/paper_id", "paper is not registered")
    )


def _stale(paper_id: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(GROUNDING_MISMATCH, "registry-paper", paper_id, "/source_fingerprint", message)
    )


__all__ = ["PaperContextService"]
