from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    GROUNDING_MISMATCH,
    PARSE_SOURCE_UNSUPPORTED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.parse.base import ParseAdapter
from research_kb.process_events import build_process_event, timestamp
from research_kb.services._pipeline_authority import require_job_authority
from research_kb.source_resolution import observe_paper_source
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]


class ParseService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
        id_allocator: IdAllocator = allocate_id,
    ):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout)
        self.id_allocator = id_allocator

    def run(
        self,
        *,
        paper_id: str,
        adapter: ParseAdapter,
        actor: str = "cli",
        job_id: str | None = None,
        _trusted_provenance_refs: tuple[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], TransactionResult]:
        input_refs = [paper_id]
        if _trusted_provenance_refs is not None:
            if job_id is None or actor != "user" or len(_trusted_provenance_refs) != 2:
                raise ResearchKBError(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "parsed-page",
                        paper_id,
                        "/provenance",
                        "trusted Parse provenance requires a user-owned Pipeline Job",
                    )
                )
            authority_id, authority_state_id = _trusted_provenance_refs
            validate_id(authority_id, Namespace.PARSE_AUTHORITY)
            validate_id(authority_state_id, Namespace.PARSE_AUTHORITY_STATE)
            input_refs.extend((authority_id, authority_state_id))
        if job_id is not None:
            require_job_authority(self.layout, job_id, "parse_run")
        current_entries = load_workspace_entries(self.layout)
        validate_workspace_entries(current_entries)
        papers = {item["paper_id"]: item for item in records_of_kind(current_entries, "registry-paper")}
        if paper_id not in papers:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "parsed-page", paper_id, "/paper_id", "paper is not registered")
            )
        paper = papers[paper_id]
        source_observation = observe_paper_source(self.layout, current_entries, paper)
        source = source_observation.path
        if source_observation.state != "current":
            raise ResearchKBError(
                Diagnostic(GROUNDING_MISMATCH, "parsed-page", paper_id, "/paper_id", "current source manifestation is not reusable")
            )
        event_id = self.id_allocator(Namespace.PROCESS_EVENT)
        created_at = timestamp(self.transactions.clock)
        try:
            parsed = list(adapter.parse(source, paper_id=paper_id, parse_run_id=event_id))
            if not parsed:
                raise ResearchKBError(
                    Diagnostic(
                        PARSE_SOURCE_UNSUPPORTED,
                        "parsed-page",
                        paper_id,
                        "/source_ref",
                        "parse adapter returned no page records",
                    )
                )
        except Exception:
            self.transactions.record_failure(
                operation="parse_run",
                actor=actor,
                input_refs=input_refs,
                event_id=event_id,
                job_id=job_id,
            )
            raise
        pages = [
            {
                "schema_version": "1.0",
                "paper_id": paper_id,
                "parse_run_id": event_id,
                "parser": {"adapter": adapter.name, "version": adapter.version},
                "pdf_page": item["pdf_page"],
                "printed_page": item.get("printed_page"),
                "text": item["text"],
                "locator": item["locator"],
                "created_at": created_at,
            }
            for item in parsed
        ]
        for page in pages:
            diagnostics = validate_record("parsed-page", page, actor="cli")
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
        planned_event = build_process_event(
            event_id=event_id,
            operation="parse_run",
            actor=actor,
            result="success",
            input_refs=input_refs,
            output_refs=[paper_id],
            created_at=created_at,
            job_id=job_id,
        )
        target = self.layout.parse_path(paper_id)
        target_before = file_sha256(target)

        def validate_source_stability() -> None:
            if observe_paper_source(self.layout, current_entries, paper) != source_observation:
                raise ResearchKBError(
                    Diagnostic(GROUNDING_MISMATCH, "parsed-page", paper_id, "/paper_id", "source manifestation changed during parse")
                )

        def validate_temp(path: Path) -> None:
            validate_source_stability()
            temporary_pages = read_jsonl(path, record_kind="parsed-page", missing_ok=False)
            entries = load_workspace_entries(
                self.layout,
                overrides={target: [("parsed-page", item) for item in temporary_pages]},
                extra_entries=[("process-event", planned_event)],
            )
            validate_workspace_entries(entries)

        result = self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(pages),
            target_store="parsed_pages",
            operation="parse_run",
            actor=actor,
            input_refs=input_refs,
            output_refs=[paper_id],
            validator=validate_temp,
            post_replace_validator=validate_source_stability,
            expected_before_sha256=target_before,
            event_id=event_id,
            job_id=job_id,
        )
        return pages, result
