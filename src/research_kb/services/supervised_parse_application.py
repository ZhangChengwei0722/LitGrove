from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INPUT_TOO_LARGE,
    INVALID_AUTHORITY,
    PARSE_SOURCE_UNSUPPORTED,
    PROTECTED_INPUT_CHANGED,
    TRUST_AUTHORITY_INVALID,
    Diagnostic,
    ResearchKBError,
)
from research_kb.parse.worker_protocol import (
    ParserBudgetProfile,
    WorkerParseRequest,
    WorkerParseResult,
    run_parser_worker,
)
from research_kb.parse.pdfplumber_adapter import PDF_SIGNATURE
from research_kb.process_events import Clock, utc_now
from research_kb.services.parse import ParseService
from research_kb.services.parse_application import ParseAdapterRegistry, ParseApplicationResult
from research_kb.services.trusted_parse_authority import TrustedParseAuthorityService
from research_kb.source_resolution import observe_paper_source
from research_kb.storage.json_io import file_sha256
from research_kb.workspace import WorkspaceLayout


WorkerRunner = Callable[[WorkerParseRequest], WorkerParseResult]


class SupervisedParseApplicationService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        authority_service: TrustedParseAuthorityService | None = None,
        registry: ParseAdapterRegistry | None = None,
        parse_service: ParseService | None = None,
        worker_runner: WorkerRunner = run_parser_worker,
        budget: ParserBudgetProfile | None = None,
        clock: Clock = utc_now,
    ):
        self.layout = layout
        self.registry = registry or ParseAdapterRegistry()
        self.authority_service = authority_service or TrustedParseAuthorityService(
            layout,
            clock=clock,
            parser_version_resolver=lambda name: self.registry.create(name).version,
        )
        self.parse_service = parse_service or ParseService(layout)
        self.worker_runner = worker_runner
        self.budget = budget or ParserBudgetProfile()

    def run(
        self,
        *,
        paper_id: str,
        authority_id: str,
        actor: str,
        job_id: str | None = None,
    ) -> ParseApplicationResult:
        if actor != "user":
            raise _error(INVALID_AUTHORITY, "/actor", "supervised Parse requires user authority")
        projection = self.authority_service.current(authority_id)
        if projection.status != "current" or projection.record["paper_id"] != paper_id:
            raise _error(TRUST_AUTHORITY_INVALID, "/authority_id", "trusted Parse authority is not current for this paper")
        authority = projection.record
        if authority["parser_profile_id"] != self.budget.profile_id:
            raise _error(TRUST_AUTHORITY_INVALID, "/parser_profile_id", "trusted Parse authority does not match the parser budget")

        # Adapter availability is probed only after source/policy authority is current.
        adapter = self.registry.create(authority["parser"]["adapter"])
        if adapter.version != authority["parser"]["version"]:
            raise _error(TRUST_AUTHORITY_INVALID, "/parser", "trusted Parse adapter version is stale")
        entries = load_workspace_entries(self.layout)
        papers = {item["paper_id"]: item for item in records_of_kind(entries, "registry-paper")}
        paper = papers.get(paper_id)
        if paper is None:
            raise _error(TRUST_AUTHORITY_INVALID, "/paper_id", "trusted Parse paper is not registered")
        observation = observe_paper_source(self.layout, entries, paper)
        source = observation.path
        expected_sha256 = authority["source_fingerprint"]["value"]
        if observation.state != "current" or observation.live_sha256 != expected_sha256:
            raise _error(TRUST_AUTHORITY_INVALID, "/source_fingerprint", "trusted Parse source is stale")
        try:
            source_bytes = source.stat().st_size
        except OSError as error:
            raise _error(PARSE_SOURCE_UNSUPPORTED, "/source_ref", "trusted Parse source cannot be inspected") from error
        if source_bytes > self.budget.max_source_bytes:
            raise _error(INPUT_TOO_LARGE, "/source_ref", "trusted Parse source exceeds the source-size budget")
        try:
            with source.open("rb") as stream:
                signature = stream.read(5)
        except OSError as error:
            raise _error(PARSE_SOURCE_UNSUPPORTED, "/source_ref", "trusted Parse source cannot be read") from error
        if source.suffix.lower() != ".pdf" or signature != PDF_SIGNATURE:
            raise _error(PARSE_SOURCE_UNSUPPORTED, "/source_ref", "supervised Parse accepts only a regular PDF source")

        operation_id = f"worker_{uuid.uuid4()}"
        temp_root = Path(tempfile.gettempdir()) / f"research-kb-{operation_id}"
        if temp_root.exists():
            raise _error(PROTECTED_INPUT_CHANGED, "/worker", "operation-owned parser temp root already exists")
        request = WorkerParseRequest(
            operation_id=operation_id,
            source_path=source,
            source_sha256=expected_sha256,
            paper_id=paper_id,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            parser_profile_id=self.budget.profile_id,
            temp_root=temp_root,
            budget=self.budget,
        )
        try:
            worker_result = self.worker_runner(request)
            if file_sha256(source) != expected_sha256:
                raise _error(GROUNDING_MISMATCH, "/source_fingerprint", "source manifestation changed after worker execution")
            latest = self.authority_service.current(authority_id)
            if latest.status != "current" or latest.record != authority:
                raise _error(TRUST_AUTHORITY_INVALID, "/authority_id", "trusted Parse authority changed during worker execution")
            if worker_result.source_sha256 != expected_sha256:
                raise _error(PROTECTED_INPUT_CHANGED, "/worker/source_sha256", "worker result does not match the trusted source")
            if worker_result.parser != authority["parser"]:
                raise _error(PROTECTED_INPUT_CHANGED, "/worker/parser", "worker result does not match the trusted parser")
            materialized = _MaterializedPagesAdapter(adapter.name, adapter.version, worker_result.pages)
            pages, transaction = self.parse_service.run(
                paper_id=paper_id,
                adapter=materialized,
                actor=actor,
                job_id=job_id,
            )
        finally:
            if temp_root.is_dir() and not temp_root.is_symlink():
                shutil.rmtree(temp_root)
        return ParseApplicationResult(
            paper_id,
            transaction.event_id,
            pages[0]["parser"],
            len(pages),
            self.layout.target_relative_path(transaction.target),
        )


class _MaterializedPagesAdapter:
    def __init__(self, name: str, version: str, pages: tuple[dict[str, Any], ...]):
        self.name = name
        self.version = version
        self.pages = pages

    def parse(self, source: Path, *, paper_id: str, parse_run_id: str):
        del source, paper_id, parse_run_id
        return list(self.pages)


def _error(code: str, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "supervised-parse", None, path, message))


__all__ = ["SupervisedParseApplicationService"]
