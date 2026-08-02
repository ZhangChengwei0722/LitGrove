from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Callable

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.discovery import DiscoveryConnector
from research_kb.discovery.acquisition import DiscoveryAcquisitionTransport
from research_kb.discovery.europe_pmc import EuropePmcConnector, EuropePmcResolver
from research_kb.discovery.europe_pmc_pdf import EuropePmcPdfTransport
from research_kb.discovery.resolution import DiscoveryResolver
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.services.acquired_candidate_intake import AcquiredCandidateIntakeService
from research_kb.services.discovery import DiscoveryConnectorRegistry, DiscoveryService
from research_kb.services.discovery_acquisition import (
    DiscoveryAcquisitionService,
    DiscoveryAcquisitionTransportRegistry,
)
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from research_kb.services.discovery_resolution import (
    DiscoveryResolutionService,
    DiscoveryResolverRegistry,
)
from research_kb.services.workspace_session import WorkspaceSession
from research_kb.workspace import WorkspaceLayout


MAX_PAGE_SIZE = 100
PdfPreflight = Callable[[Path, str], None]


class DiscoveryApplicationService:
    def __init__(
        self,
        *,
        connectors: Iterable[DiscoveryConnector] | None = None,
        resolvers: Iterable[DiscoveryResolver] | None = None,
        transports: Iterable[DiscoveryAcquisitionTransport] | None = None,
        pdf_preflight: PdfPreflight | None = None,
    ):
        self.connectors = DiscoveryConnectorRegistry(
            (EuropePmcConnector(),) if connectors is None else connectors
        )
        self.resolvers = DiscoveryResolverRegistry(
            (EuropePmcResolver(),) if resolvers is None else resolvers
        )
        self.transports = DiscoveryAcquisitionTransportRegistry(
            (EuropePmcPdfTransport(),) if transports is None else transports
        )
        self.pdf_preflight = pdf_preflight

    def limits(self) -> dict[str, Any]:
        return {
            "status": "success",
            "interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "provider": "europe-pmc",
            "max_results": 15,
            "max_date_span_days": 31,
            "max_page_size": MAX_PAGE_SIZE,
        }

    def search(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return DiscoveryService(self.connectors).search("europe-pmc", request)

    def select(
        self,
        session: WorkspaceSession,
        report: Mapping[str, Any],
        result_keys: Iterable[str],
        *,
        actor: str,
    ) -> dict[str, Any]:
        keys = list(result_keys)
        request = {
            "request_version": "1.0",
            "report": dict(report),
            "selections": [
                {"result_key": result_key, "target_question_ids": []}
                for result_key in keys
            ],
        }
        result = DiscoveryCandidateService(_session_layout(session)).select(
            request,
            actor=actor,
        )
        return result.to_dict(_session_layout(session))

    def list_candidates(
        self,
        session: WorkspaceSession,
        *,
        page_size: int,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise _request_error("candidate page size must be from 1 through 100", "/page_size")
        if cursor is not None:
            validate_id(cursor, Namespace.DISCOVERY)
        report = DiscoveryCandidateService(_session_layout(session)).list()
        candidates = report["candidates"]
        if cursor is not None:
            candidates = [item for item in candidates if item["candidate_id"] > cursor]
        page = candidates[:page_size]
        return {
            "status": "success",
            "interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "candidate_count": report["candidate_count"],
            "page_size": page_size,
            "candidates": page,
            "next_cursor": page[-1]["candidate_id"] if len(candidates) > page_size else None,
            "persistent_writes": 0,
        }

    def show_candidate(
        self,
        session: WorkspaceSession,
        candidate_id: str,
    ) -> dict[str, Any]:
        return DiscoveryCandidateService(_session_layout(session)).show(candidate_id)

    def resolve(
        self,
        session: WorkspaceSession,
        candidate_id: str,
    ) -> dict[str, Any]:
        return DiscoveryResolutionService(
            _session_layout(session),
            self.resolvers,
        ).resolve(candidate_id, provider="europe-pmc")

    def acquire(
        self,
        session: WorkspaceSession,
        candidate_id: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "resolver_registry": self.resolvers,
            "transport_registry": self.transports,
        }
        if self.pdf_preflight is not None:
            arguments["pdf_preflight"] = self.pdf_preflight
        return DiscoveryAcquisitionService(
            _session_layout(session),
            **arguments,
        ).acquire(candidate_id, provider="europe-pmc", actor=actor)

    def inspect_acquired(
        self,
        session: WorkspaceSession,
        candidate_id: str,
    ) -> dict[str, Any]:
        return AcquiredCandidateIntakeService(_session_layout(session)).inspect(candidate_id)


def _session_layout(session: WorkspaceSession) -> WorkspaceLayout:
    if not isinstance(session, WorkspaceSession):
        raise _request_error("a Core-owned WorkspaceSession is required", "/session")
    return session._layout


def _request_error(message: str, path: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            SCHEMA_VALIDATION_FAILED,
            "discovery-application-request",
            None,
            path,
            message,
        )
    )


__all__ = ["DiscoveryApplicationService", "MAX_PAGE_SIZE"]
