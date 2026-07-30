from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from research_kb.errors import PARSE_ADAPTER_UNAVAILABLE, Diagnostic, ResearchKBError
from research_kb.parse.base import ParseAdapter
from research_kb.parse.pdfplumber_adapter import PdfPlumberAdapter, PdfPlumberTextFlowAdapter
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.workspace import WorkspaceLayout


ParseAdapterFactory = Callable[[], ParseAdapter]


@dataclass(frozen=True, slots=True)
class ParseApplicationResult:
    paper_id: str
    parse_run_id: str
    parser: Mapping[str, str]
    page_count: int
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "success",
            "paper_id": self.paper_id,
            "parse_run_id": self.parse_run_id,
            "parser": dict(self.parser),
            "pages": self.page_count,
            "target": self.target,
        }


class ParseAdapterRegistry:
    def __init__(self, factories: Mapping[str, ParseAdapterFactory] | None = None):
        self.factories = dict(
            {
                "synthetic-text": SyntheticTextAdapter,
                "pdfplumber": PdfPlumberAdapter,
                "pdfplumber-text-flow": PdfPlumberTextFlowAdapter,
            }
            if factories is None
            else factories
        )

    def create(self, name: str) -> ParseAdapter:
        factory = self.factories.get(name)
        if factory is None:
            raise ResearchKBError(
                Diagnostic(
                    PARSE_ADAPTER_UNAVAILABLE,
                    "parse-adapter",
                    None,
                    "/adapter",
                    "parse adapter is not explicitly registered",
                )
            )
        return factory()


class ParseApplicationService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        registry: ParseAdapterRegistry | None = None,
        parse_service: ParseService | None = None,
    ):
        self.layout = layout
        self.registry = registry or ParseAdapterRegistry()
        self.parse_service = parse_service or ParseService(layout)

    def run(
        self,
        *,
        paper_id: str,
        adapter_name: str,
        actor: str,
        job_id: str | None = None,
    ) -> ParseApplicationResult:
        adapter = self.registry.create(adapter_name)
        pages, transaction = self.parse_service.run(
            paper_id=paper_id,
            adapter=adapter,
            actor=actor,
            job_id=job_id,
        )
        return ParseApplicationResult(
            paper_id,
            transaction.event_id,
            pages[0]["parser"],
            len(pages),
            self.layout.target_relative_path(transaction.target),
        )
