from __future__ import annotations

from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.errors import GROUNDING_MISMATCH, UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.review_memory_provenance import (
    build_active_parse_index,
    lead_registry_matches,
    review_memory_freshness,
)
from research_kb.storage.json_io import file_sha256
from research_kb.workspace import WorkspaceLayout


class ReviewContextService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def show(self, *, paper_id: str) -> dict[str, Any]:
        validate_id(paper_id, Namespace.PAPER)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        paper = next(
            (
                item
                for item in records_of_kind(entries, "registry-paper")
                if item["paper_id"] == paper_id
            ),
            None,
        )
        if paper is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "registry-paper",
                    paper_id,
                    "/paper_id",
                    "paper is not registered",
                )
            )
        _, source = self.layout.resolve_source(
            paper["source_ref"]["root_id"],
            paper["source_ref"]["relative_path"],
        )
        before_hash = _source_hash(source, paper_id)
        if before_hash != paper["source_fingerprint"]["value"]:
            raise _stale_source(paper_id, "registered source fingerprint is stale")

        memories = [
            item
            for item in records_of_kind(entries, "review-memory")
            if item["paper_id"] == paper_id
        ]
        memory = memories[0] if memories else None
        if memory is None:
            freshness = {"state": "absent", "reasons": []}
            matches: list[dict[str, Any]] = []
        else:
            active, failures = build_active_parse_index(records_of_kind(entries, "parsed-page"))
            if failures:
                failure = next((item for item in failures if item.record_id == paper_id), failures[0])
                raise ResearchKBError(
                    Diagnostic(
                        failure.code,
                        failure.record_kind,
                        failure.record_id,
                        failure.json_path,
                        failure.message,
                    )
                )
            state = review_memory_freshness(memory, active)
            if state == "missing_active_parse":
                raise ResearchKBError(
                    Diagnostic(
                        UNRESOLVED_REFERENCE,
                        "review-memory",
                        memory["review_memory_id"],
                        "/parse_snapshot",
                        "Review Memory has no active parse",
                    )
                )
            freshness = {
                "state": state,
                "reasons": [] if state == "current" else ["parse_snapshot_changed"],
            }
            matches = lead_registry_matches(memory, records_of_kind(entries, "registry-paper"))

        result = {
            "status": "success",
            "interface_version": "1.0",
            "paper_id": paper_id,
            "review_memory": memory,
            "freshness": freshness,
            "lead_registry_matches": matches,
        }
        if _source_hash(source, paper_id) != before_hash:
            raise _stale_source(paper_id, "registered source changed during Review Memory context read")
        return result


def _source_hash(source: Path, paper_id: str) -> str | None:
    try:
        value = file_sha256(source)
    except OSError as error:
        raise _stale_source(paper_id, "registered source could not be read") from error
    if value is None:
        raise _stale_source(paper_id, "registered source is missing or is not a regular file")
    return value


def _stale_source(paper_id: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            GROUNDING_MISMATCH,
            "registry-paper",
            paper_id,
            "/source_fingerprint",
            message,
        )
    )


__all__ = ["ReviewContextService"]
