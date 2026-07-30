from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    INVALID_AUTHORITY,
    PARSE_ADAPTER_UNAVAILABLE,
    PARSE_SOURCE_UNSUPPORTED,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, validate_id
from research_kb.pipeline_jobs import TERMINAL_STATUSES
from research_kb.services._pipeline_authority import require_job_authority
from research_kb.services.parse import ParseService
from research_kb.services.parse_application import ParseAdapterRegistry
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.source_adequacy import SourceAdequacyService
from research_kb.services.source_asset import SourceAssetService
from research_kb.source_assets import current_source_asset_heads
from research_kb.source_resolution import observe_paper_source
from research_kb.workspace import WorkspaceLayout


_TRUNK_AUTHORITIES = (
    "observe_source",
    "parse_run",
    "assess_source_adequacy",
    "advance_deterministic_trunk",
)
_SOURCE_WAIT_REASONS = {
    "missing": "source_missing",
    "inaccessible": "source_inaccessible",
    "not_regular_file": "source_inaccessible",
    "relink_required": "source_inaccessible",
    "fingerprint_mismatch": "source_changed",
    "stale_source": "source_changed",
}
_DOCUMENT_ROUTES = frozenset({"primary", "review"})
_REPARSE_WAIT_REASONS = frozenset(
    {"layout_parse_required", "ocr_required", "reparse_required"}
)
_PARSER_DOMAIN_ERRORS = frozenset(
    {PARSE_ADAPTER_UNAVAILABLE, PARSE_SOURCE_UNSUPPORTED}
)


class _AdapterExecutionError(RuntimeError):
    pass


class _TrunkParseAdapter:
    def __init__(self, adapter: Any):
        self._adapter = adapter
        self.name = adapter.name
        self.version = adapter.version

    def parse(self, source: Any, *, paper_id: str, parse_run_id: str) -> Any:
        try:
            return self._adapter.parse(
                source,
                paper_id=paper_id,
                parse_run_id=parse_run_id,
            )
        except ResearchKBError:
            raise
        except Exception as error:
            raise _AdapterExecutionError("parse adapter execution failed") from error


@dataclass(frozen=True, slots=True)
class DeterministicTrunkResult:
    state: dict[str, Any]
    paper_id: str
    requested_operation: str
    profile_id: str | None
    gate: dict[str, Any] | None
    document_route: str | None
    persistent_writes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "success",
            "interface_version": "1.0",
            "job_id": self.state["job_id"],
            "paper_id": self.paper_id,
            "requested_operation": self.requested_operation,
            "pipeline": PipelineJobService.summary(self.state),
            "profile_id": self.profile_id,
            "gate": self.gate,
            "document_route": self.document_route,
            "persistent_writes": self.persistent_writes,
        }


class DeterministicTrunkService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        jobs: PipelineJobService | None = None,
        parse_service: ParseService | None = None,
        parse_registry: ParseAdapterRegistry | None = None,
        adequacy: SourceAdequacyService | None = None,
        source_assets: SourceAssetService | None = None,
    ):
        self.layout = layout
        self.jobs = jobs or PipelineJobService(layout)
        self.parse_service = parse_service or ParseService(layout)
        self.parse_registry = parse_registry or ParseAdapterRegistry()
        self.adequacy = adequacy or SourceAdequacyService(layout)
        self.source_assets = source_assets or SourceAssetService(layout)

    def advance(
        self,
        *,
        job_id: str,
        paper_id: str,
        requested_operation: str,
        adapter_name: str,
        actor: str = "cli",
        document_route: str | None = None,
        route_reason: str | None = None,
    ) -> DeterministicTrunkResult:
        job_id = validate_id(job_id, Namespace.JOB)
        paper_id = validate_id(paper_id, Namespace.PAPER)
        self._validate_route(actor, document_route, route_reason)
        shown = self.jobs.show(job_id)
        head = shown["current_state"]
        force_reparse = (
            head["status"] == "waiting_user"
            and head["wait_reason"] in _REPARSE_WAIT_REASONS
        )
        profile = self._profile_from_outputs(head, paper_id, requested_operation)
        if head["status"] in TERMINAL_STATUSES:
            return self._terminal_replay(
                head,
                paper_id=paper_id,
                requested_operation=requested_operation,
                profile=profile,
                document_route=document_route,
            )
        self._validate_job_scope(head, paper_id)
        for operation in _TRUNK_AUTHORITIES:
            require_job_authority(self.layout, job_id, operation)

        if head["status"] == "waiting_user" and head["wait_reason"] == "route_ambiguous":
            if document_route is None:
                return self._result(
                    head,
                    paper_id,
                    requested_operation,
                    profile,
                    None,
                    None,
                    0,
                )
            if profile is not None:
                route_gate = self.adequacy.gate(
                    paper_id=paper_id,
                    requested_operation=requested_operation,
                )
                if (
                    route_gate["status"] == "allowed"
                    and route_gate["profile_id"] == profile["profile_id"]
                ):
                    return self._complete_route(
                        head,
                        paper_id=paper_id,
                        requested_operation=requested_operation,
                        profile=profile,
                        document_route=document_route,
                        route_reason=route_reason,
                    )

        writes = 0
        if head["status"] != "running":
            if head["status"] == "waiting_user" and actor != "user":
                raise _trunk_error(
                    INVALID_AUTHORITY,
                    job_id,
                    "/actor",
                    "resuming a user-owned wait requires actor user",
                )
            head, changed = self._transition(
                head,
                status="running",
                current_node="source_check",
                wait_reason=None,
                output_refs=[],
                actor="cli" if head["status"] != "waiting_user" else "user",
            )
            writes += changed

        entries, paper = self._paper(paper_id)
        source_outputs: list[str] = []
        observation = observe_paper_source(self.layout, entries, paper)
        if observation.source_asset_id is not None:
            source_state = next(
                item
                for item in current_source_asset_heads(records_of_kind(entries, "source-asset-state"))
                if item["source_asset_id"] == observation.source_asset_id
            )
            observed = self.source_assets.observe(
                source_asset_id=source_state["source_asset_id"],
                job_id=job_id,
                expected_state_id=source_state["source_asset_state_id"],
                expected_state_digest=canonical_digest(source_state),
                actor="cli",
            )
            writes += int(observed.transaction is not None)
            source_outputs.extend(
                [observed.state["source_asset_id"], observed.state["source_asset_state_id"]]
            )
            entries, paper = self._paper(paper_id)
            observation = observe_paper_source(self.layout, entries, paper)
        if observation.state != "current":
            wait_reason = _SOURCE_WAIT_REASONS.get(observation.state, "source_inaccessible")
            head, changed = self._transition(
                head,
                status="waiting_source",
                current_node="source_check",
                wait_reason=wait_reason,
                output_refs=source_outputs,
                actor="cli",
            )
            return self._result(
                head,
                paper_id,
                requested_operation,
                None,
                None,
                None,
                writes + changed,
            )

        pages = [
            item
            for item in records_of_kind(entries, "parsed-page")
            if item["paper_id"] == paper_id
        ]
        if force_reparse or not pages:
            try:
                adapter = _TrunkParseAdapter(self.parse_registry.create(adapter_name))
                pages, parse_transaction = self.parse_service.run(
                    paper_id=paper_id,
                    adapter=adapter,
                    actor="cli",
                    job_id=job_id,
                )
            except ResearchKBError as error:
                if error.diagnostic.code not in _PARSER_DOMAIN_ERRORS:
                    raise
                head, changed = self._parse_failure_wait(head, source_outputs)
                return self._result(
                    head,
                    paper_id,
                    requested_operation,
                    None,
                    None,
                    None,
                    writes + changed,
                )
            except _AdapterExecutionError:
                head, changed = self._parse_failure_wait(head, source_outputs)
                return self._result(
                    head,
                    paper_id,
                    requested_operation,
                    None,
                    None,
                    None,
                    writes + changed,
                )
            writes += 1
            source_outputs.append(parse_transaction.event_id)

        profile = self.adequacy.reusable_profile(
            paper_id=paper_id,
            requested_operation=requested_operation,
        )
        if profile is None:
            assessed = self.adequacy.assess(
                paper_id=paper_id,
                job_id=job_id,
                requested_operation=requested_operation,
                actor="cli",
            )
            profile = assessed.profile
            writes += int(assessed.transaction is not None)
        gate = self.adequacy.gate(
            paper_id=paper_id,
            requested_operation=requested_operation,
        )
        outputs = [*source_outputs, profile["profile_id"]]
        if gate["status"] != "allowed":
            head, changed = self._transition(
                head,
                status=gate["pipeline_status"],
                current_node="source_adequacy",
                wait_reason=gate["wait_reason"],
                output_refs=outputs,
                actor="cli",
            )
            return self._result(
                head,
                paper_id,
                requested_operation,
                profile,
                gate,
                None,
                writes + changed,
            )

        head, changed = self._transition(
            head,
            status="waiting_user",
            current_node="semantic_route",
            wait_reason="route_ambiguous",
            output_refs=outputs,
            actor="cli",
        )
        writes += changed
        if document_route is None:
            return self._result(
                head,
                paper_id,
                requested_operation,
                profile,
                gate,
                None,
                writes,
            )
        completed = self._complete_route(
            head,
            paper_id=paper_id,
            requested_operation=requested_operation,
            profile=profile,
            document_route=document_route,
            route_reason=route_reason,
        )
        return DeterministicTrunkResult(
            completed.state,
            completed.paper_id,
            completed.requested_operation,
            completed.profile_id,
            gate,
            completed.document_route,
            writes + completed.persistent_writes,
        )

    def _complete_route(
        self,
        head: dict[str, Any],
        *,
        paper_id: str,
        requested_operation: str,
        profile: dict[str, Any],
        document_route: str,
        route_reason: str | None,
    ) -> DeterministicTrunkResult:
        route_node = _route_node(document_route, route_reason)
        running, first_write = self._transition(
            head,
            status="running",
            current_node=route_node,
            wait_reason=None,
            output_refs=[profile["profile_id"]],
            actor="user",
        )
        completed, second_write = self._transition(
            running,
            status="completed",
            current_node=route_node,
            wait_reason=None,
            output_refs=[profile["profile_id"]],
            actor="cli",
        )
        return self._result(
            completed,
            paper_id,
            requested_operation,
            profile,
            None,
            document_route,
            first_write + second_write,
        )

    def _transition(
        self,
        head: dict[str, Any],
        *,
        status: str,
        current_node: str,
        wait_reason: str | None,
        output_refs: list[str],
        actor: str,
    ) -> tuple[dict[str, Any], int]:
        mutation = self.jobs.transition(
            head["job_id"],
            expected_state_id=head["state_id"],
            expected_state_digest=canonical_digest(head),
            status=status,
            current_node=current_node,
            wait_reason=wait_reason,
            output_refs=sorted(set(output_refs)),
            retry_increment=0,
            recovery_action=None,
            actor=actor,
        )
        return mutation.state, int(mutation.transaction is not None)

    def _parse_failure_wait(
        self,
        head: dict[str, Any],
        output_refs: list[str],
    ) -> tuple[dict[str, Any], int]:
        return self._transition(
            head,
            status="waiting_source",
            current_node="parse",
            wait_reason="parse_failed",
            output_refs=output_refs,
            actor="cli",
        )

    def _paper(self, paper_id: str) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        paper = next(
            (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == paper_id),
            None,
        )
        if paper is None:
            raise _trunk_error(
                UNRESOLVED_REFERENCE,
                paper_id,
                "/paper_id",
                "deterministic trunk requires a registered paper",
            )
        return entries, paper

    def _profile_from_outputs(
        self,
        state: dict[str, Any],
        paper_id: str,
        requested_operation: str,
    ) -> dict[str, Any] | None:
        entries = load_workspace_entries(self.layout)
        output_ids = set(state["output_refs"])
        return next(
            (
                item
                for item in records_of_kind(entries, "source-adequacy-profile")
                if item["profile_id"] in output_ids
                and item["paper_id"] == paper_id
                and item["requested_operation"] == requested_operation
            ),
            None,
        )

    @staticmethod
    def _validate_job_scope(state: dict[str, Any], paper_id: str) -> None:
        if state["requested_route"] != "local_source" or state["requested_depth"] != "semantic_gate":
            raise _trunk_error(
                SCHEMA_VALIDATION_FAILED,
                state["job_id"],
                "/requested_route",
                "deterministic trunk requires a local_source semantic_gate Job",
            )
        if paper_id not in set(state["input_refs"]) | set(state["output_refs"]):
            raise _trunk_error(
                INVALID_AUTHORITY,
                state["job_id"],
                "/input_refs",
                "Pipeline Job does not include the registered paper identity",
            )

    @staticmethod
    def _validate_route(actor: str, document_route: str | None, route_reason: str | None) -> None:
        if actor not in {"cli", "user"}:
            raise _trunk_error(INVALID_AUTHORITY, None, "/actor", "trunk actor must be cli or user")
        if document_route is not None and document_route not in _DOCUMENT_ROUTES:
            raise _trunk_error(
                SCHEMA_VALIDATION_FAILED,
                None,
                "/document_route",
                "document route must be primary or review",
            )
        if document_route is not None and actor != "user":
            raise _trunk_error(
                INVALID_AUTHORITY,
                None,
                "/document_route",
                "document route requires an explicit user decision",
            )
        if route_reason not in {None, "mixed_document"}:
            raise _trunk_error(
                SCHEMA_VALIDATION_FAILED,
                None,
                "/route_reason",
                "route reason is not registered",
            )
        if route_reason == "mixed_document" and document_route != "review":
            raise _trunk_error(
                SCHEMA_VALIDATION_FAILED,
                None,
                "/route_reason",
                "mixed_document must use the review route",
            )

    def _terminal_replay(
        self,
        head: dict[str, Any],
        *,
        paper_id: str,
        requested_operation: str,
        profile: dict[str, Any] | None,
        document_route: str | None,
    ) -> DeterministicTrunkResult:
        if head["status"] != "completed" or profile is None:
            raise _trunk_error(
                INVALID_AUTHORITY,
                head["job_id"],
                "/status",
                "terminal Pipeline Job cannot advance the deterministic trunk",
            )
        completed_route = "review" if head["current_node"].startswith("review_") else "primary"
        if document_route is not None and document_route != completed_route:
            raise _trunk_error(
                WRITE_CONFLICT,
                head["job_id"],
                "/document_route",
                "completed Job is bound to another document route",
            )
        return self._result(
            head,
            paper_id,
            requested_operation,
            profile,
            None,
            completed_route,
            0,
        )

    @staticmethod
    def _result(
        state: dict[str, Any],
        paper_id: str,
        requested_operation: str,
        profile: dict[str, Any] | None,
        gate: dict[str, Any] | None,
        document_route: str | None,
        writes: int,
    ) -> DeterministicTrunkResult:
        return DeterministicTrunkResult(
            state,
            paper_id,
            requested_operation,
            None if profile is None else profile["profile_id"],
            gate,
            document_route,
            writes,
        )


def _route_node(document_route: str, route_reason: str | None) -> str:
    if route_reason == "mixed_document":
        return "review_semantic_gate_mixed_document"
    return f"{document_route}_semantic_gate"


def _trunk_error(code: str, record_id: str | None, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(code, "deterministic-trunk", record_id, path, message)
    )


__all__ = ["DeterministicTrunkResult", "DeterministicTrunkService"]
