from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import timedelta

from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INCOMPLETE_TRANSACTION,
    INPUT_TOO_LARGE,
    INVALID_AUTHORITY,
    OPERATION_CANCELLED,
    PARSER_WORKER_FAILED,
    PARSE_SOURCE_UNSUPPORTED,
    PROTECTED_INPUT_CHANGED,
    SCHEMA_VALIDATION_FAILED,
    TRUST_AUTHORITY_INVALID,
    WRITE_CONFLICT,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, validate_id
from research_kb.parse.worker_protocol import ParserBudgetProfile, WorkerParseResult, run_parser_worker
from research_kb.pipeline_jobs import TERMINAL_STATUSES
from research_kb.process_events import Clock, utc_now
from research_kb.services.deterministic_intake_application import DeterministicIntakeApplicationService
from research_kb.services.deterministic_trunk import DeterministicTrunkService
from research_kb.services.parse_application import ParseAdapterRegistry
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.supervised_parse_application import SupervisedParseApplicationService
from research_kb.services.trusted_parse_authority import TrustedParseAuthorityService
from research_kb.services.trusted_parse_intake_support import (
    authority_event_for_job,
    authority_preparation,
    correlated_parse_event,
    paper_for_job,
    preparation_payload,
    require_expected_state,
    require_source_association,
    route_suffix,
    semantic_intent,
    service_error,
    session_layout,
    source_changed,
    transition_wait,
    validate_preparation_source_and_parser,
)
from research_kb.services.workspace_session import WorkspaceSession
from research_kb.source_resolution import observe_paper_source
from research_kb.storage.json_io import read_jsonl
from research_kb.trusted_parse_authority import TRUSTED_PARSE_POLICY
from research_kb.trusted_parse_intake import (
    ADAPTER_NAME,
    ALLOWED_OPERATION,
    AUTHORITY_PREFIX,
    EXECUTION_PREFIX,
    RECONCILE_PREFIX,
    TrustedParseIntakePreparation,
    TrustedParseIntakeResult,
)
from research_kb.workspace import WorkspaceLayout


CancelCheck = Callable[[], bool]
PhaseHook = Callable[[], None]
OperationHook = Callable[[str], None]
NonceFactory = Callable[[], str]


class TrustedParseIntakeApplicationService:
    def __init__(
        self,
        *,
        registry: ParseAdapterRegistry | None = None,
        worker_runner: Callable[..., WorkerParseResult] = run_parser_worker,
        budget: ParserBudgetProfile | None = None,
        clock: Clock = utc_now,
        preparation_ttl: timedelta = timedelta(minutes=10),
        nonce_factory: NonceFactory | None = None,
        operation_hook: OperationHook | None = None,
    ):
        if preparation_ttl <= timedelta(0) or preparation_ttl > timedelta(hours=1):
            raise service_error(
                SCHEMA_VALIDATION_FAILED,
                None,
                "/preparation_ttl",
                "trusted Parse preparation TTL is outside the bounded range",
            )
        self.registry = registry or ParseAdapterRegistry()
        self.worker_runner = worker_runner
        self.budget = budget or ParserBudgetProfile()
        self.clock = clock
        self.preparation_ttl = preparation_ttl
        self.nonce_factory = nonce_factory or (lambda: uuid.uuid4().hex)
        self.operation_hook = operation_hook

    def prepare(
        self,
        session: WorkspaceSession,
        job_id: str,
        expected_state: Mapping[str, object],
    ) -> TrustedParseIntakePreparation:
        layout = session_layout(session)
        job_id = validate_id(job_id, Namespace.JOB)
        state = require_expected_state(
            PipelineJobService(layout).show(job_id)["current_state"],
            expected_state,
        )
        suffix = route_suffix(state["current_node"])
        paper = paper_for_job(layout, job_id)
        require_source_association(layout, job_id, paper["paper_id"])
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        observation = observe_paper_source(layout, entries, paper)
        if observation.state != "current" or observation.live_sha256 is None:
            raise service_error(
                TRUST_AUTHORITY_INVALID,
                job_id,
                "/source",
                "trusted Parse source manifestation is not current",
            )
        source = observation.path
        try:
            source_size = source.stat().st_size
        except OSError as error:
            raise service_error(
                PARSE_SOURCE_UNSUPPORTED,
                job_id,
                "/source",
                "trusted Parse source cannot be inspected",
            ) from error
        parser = self.registry.identity(ADAPTER_NAME)
        authority_service = self._authority_service(layout)
        authority_preview, authority_committed, authority_event = authority_preparation(
            layout,
            job_id,
            paper["paper_id"],
            authority_service,
            parser,
            self.budget.profile_id,
            TRUSTED_PARSE_POLICY,
            self.clock,
            self.preparation_ttl,
            self.nonce_factory,
        )
        parse_event = correlated_parse_event(
            layout,
            job_id,
            paper["paper_id"],
            authority_preview.candidate,
            observation.live_sha256,
            require_current_source=True,
        )
        pages = read_jsonl(layout.parse_path(paper["paper_id"]), record_kind="parsed-page")
        if parse_event is not None:
            parsed_page_state = "same_job_recovery_output"
        elif pages:
            parsed_page_state = "supervised_reparse_required"
        else:
            parsed_page_state = "absent"
        if state["current_node"].startswith(RECONCILE_PREFIX) and parse_event is None:
            raise service_error(
                INCOMPLETE_TRANSACTION,
                job_id,
                "/current_node",
                "trusted Parse reconcile state has no matching Parse receipt",
            )
        payload = {
            "session_option_id": session.option_id,
            "workspace_id": session.workspace_id,
            "job_id": job_id,
            "job_state_id": state["state_id"],
            "job_state_digest": canonical_digest(state),
            "route_suffix": suffix,
            "paper_id": paper["paper_id"],
            "source_ref": observation.source_ref,
            "source_sha256": observation.live_sha256,
            "source_name": source.name,
            "source_size_bytes": source_size,
            "parser": parser,
            "parser_profile_id": self.budget.profile_id,
            "policy_version": TRUSTED_PARSE_POLICY,
            "allowed_operation": ALLOWED_OPERATION,
            "expires_at": authority_preview.candidate["expires_at"],
            "authority_preview_digest": authority_preview.preview_digest,
            "authority_committed": authority_committed,
            "authority_event_id": None if authority_event is None else authority_event["event_id"],
            "parsed_page_state": parsed_page_state,
            "correlated_parse_event_id": None if parse_event is None else parse_event["event_id"],
        }
        digest = canonical_digest(payload)
        return TrustedParseIntakePreparation(
            session.option_id,
            session.workspace_id,
            job_id,
            state["state_id"],
            canonical_digest(state),
            suffix,
            paper["paper_id"],
            dict(observation.source_ref),
            observation.live_sha256,
            source.name,
            source_size,
            parser,
            self.budget.profile_id,
            TRUSTED_PARSE_POLICY,
            ALLOWED_OPERATION,
            authority_preview.candidate["expires_at"],
            authority_preview,
            authority_committed,
            None if authority_event is None else authority_event["event_id"],
            parsed_page_state,
            None if parse_event is None else parse_event["event_id"],
            digest,
        )

    def approve(
        self,
        session: WorkspaceSession,
        preparation: TrustedParseIntakePreparation,
        *,
        aggregate_preview_digest: str,
        actor: str,
        cancel_check: CancelCheck | None = None,
        before_promotion: PhaseHook | None = None,
    ) -> TrustedParseIntakeResult:
        if actor != "user":
            raise service_error(
                INVALID_AUTHORITY,
                None,
                "/actor",
                "trusted Parse approval requires user authority",
            )
        if not isinstance(preparation, TrustedParseIntakePreparation):
            raise service_error(
                SCHEMA_VALIDATION_FAILED,
                None,
                "/preparation",
                "Core trusted Parse preparation is required",
            )
        if aggregate_preview_digest != preparation.preparation_digest:
            raise service_error(
                PROTECTED_INPUT_CHANGED,
                preparation.job_id,
                "/aggregate_preview_digest",
                "trusted Parse preparation digest changed",
            )
        if canonical_digest(preparation_payload(preparation)) != preparation.preparation_digest:
            raise service_error(
                PROTECTED_INPUT_CHANGED,
                preparation.job_id,
                "/preparation",
                "trusted Parse preparation content changed",
            )
        layout = session_layout(session)
        if (
            session.option_id != preparation.session_option_id
            or session.workspace_id != preparation.workspace_id
            or layout.workspace_id != preparation.workspace_id
        ):
            raise service_error(
                INVALID_AUTHORITY,
                preparation.job_id,
                "/session",
                "trusted Parse preparation belongs to another workspace session",
            )
        jobs = PipelineJobService(layout)
        state = require_expected_state(
            jobs.show(preparation.job_id)["current_state"],
            {
                "state_id": preparation.job_state_id,
                "state_digest": preparation.job_state_digest,
            },
        )
        if route_suffix(state["current_node"]) != preparation.route_suffix:
            raise service_error(
                WRITE_CONFLICT,
                preparation.job_id,
                "/current_node",
                "trusted Parse route changed after preparation",
            )
        authority_service = self._authority_service(layout)
        writes = 0
        parse_run_id: str | None = None
        try:
            validate_preparation_source_and_parser(layout, preparation, self.registry)
            authority = authority_service.commit(
                preparation.authority_preview,
                preview_digest=preparation.authority_preview.preview_digest,
                actor="user",
                job_id=preparation.job_id,
            )
            writes += int(authority.event_id is not None)
            self._hook("authority_commit")
            projection = authority_service.current(authority.authority_id)
            if projection.status != "current":
                raise service_error(
                    TRUST_AUTHORITY_INVALID,
                    preparation.job_id,
                    "/authority",
                    "trusted Parse authority is not current",
                )
            authority_event = authority_event_for_job(
                layout,
                preparation.job_id,
                authority.authority_id,
                authority.state_id,
            )
            if state["current_node"].startswith(AUTHORITY_PREFIX):
                mutation = jobs.transition(
                    preparation.job_id,
                    expected_state_id=state["state_id"],
                    expected_state_digest=canonical_digest(state),
                    status="running",
                    current_node=EXECUTION_PREFIX + preparation.route_suffix,
                    wait_reason=None,
                    output_refs=[
                        authority.authority_id,
                        authority.state_id,
                        authority_event["event_id"],
                    ],
                    retry_increment=0,
                    recovery_action=None,
                    actor="cli",
                )
                state = mutation.state
                writes += int(mutation.transaction is not None)
            self._hook("execution_transition")
            parse_event = correlated_parse_event(
                layout,
                preparation.job_id,
                preparation.paper_id,
                projection.record,
                preparation.source_sha256,
                require_current_source=True,
            )
            if parse_event is None:
                supervised = SupervisedParseApplicationService(
                    layout,
                    authority_service=authority_service,
                    registry=self.registry,
                    worker_runner=self.worker_runner,
                    budget=self.budget,
                    clock=self.clock,
                )
                parsed = supervised.run(
                    paper_id=preparation.paper_id,
                    authority_id=authority.authority_id,
                    actor="user",
                    job_id=preparation.job_id,
                    cancel_check=cancel_check,
                    before_promotion=before_promotion,
                )
                parse_run_id = parsed.parse_run_id
                writes += 1
                self._hook("parse_commit")
                parse_event = correlated_parse_event(
                    layout,
                    preparation.job_id,
                    preparation.paper_id,
                    projection.record,
                    preparation.source_sha256,
                    require_current_source=True,
                )
            else:
                parse_run_id = parse_event["event_id"]
            if parse_event is None:
                raise service_error(
                    INCOMPLETE_TRANSACTION,
                    preparation.job_id,
                    "/parse",
                    "trusted Parse receipt is missing after supervised execution",
                )
            current = jobs.show(preparation.job_id)["current_state"]
            if current["current_node"].startswith(EXECUTION_PREFIX):
                mutation = jobs.transition(
                    preparation.job_id,
                    expected_state_id=current["state_id"],
                    expected_state_digest=canonical_digest(current),
                    status="running",
                    current_node=RECONCILE_PREFIX + preparation.route_suffix,
                    wait_reason=None,
                    output_refs=[
                        authority.authority_id,
                        authority.state_id,
                        authority_event["event_id"],
                        parse_event["event_id"],
                    ],
                    retry_increment=0,
                    recovery_action=None,
                    actor="cli",
                )
                writes += int(mutation.transaction is not None)
            self._hook("reconcile_transition")
            operation, document_route, route_reason = semantic_intent(preparation.route_suffix)
            trunk = DeterministicTrunkService(layout).advance(
                job_id=preparation.job_id,
                paper_id=preparation.paper_id,
                requested_operation=operation,
                adapter_name=ADAPTER_NAME,
                actor="user",
                document_route=document_route,
                route_reason=route_reason,
            )
            writes += trunk.persistent_writes
            self._hook("trunk_continuation")
            result = DeterministicIntakeApplicationService(
                parse_policy="trusted_supervised_parse",
                clock=self.clock,
            ).show_job(session, preparation.job_id)
            result["persistent_writes"] = writes
            return TrustedParseIntakeResult("continued", parse_run_id, result)
        except ResearchKBError as error:
            routed = self._route_failure(layout, preparation, error)
            if routed is None:
                raise
            result = DeterministicIntakeApplicationService(
                parse_policy="trusted_supervised_parse",
                clock=self.clock,
            ).show_job(session, preparation.job_id)
            result["persistent_writes"] = writes + routed
            outcome = "cancelled" if error.diagnostic.code == OPERATION_CANCELLED else "waiting"
            return TrustedParseIntakeResult(outcome, None, result)

    def _authority_service(self, layout: WorkspaceLayout) -> TrustedParseAuthorityService:
        return TrustedParseAuthorityService(
            layout,
            clock=self.clock,
            parser_version_resolver=lambda name: self.registry.identity(name)["version"],
        )

    def _route_failure(
        self,
        layout: WorkspaceLayout,
        preparation: TrustedParseIntakePreparation,
        error: ResearchKBError,
    ) -> int | None:
        jobs = PipelineJobService(layout)
        head = jobs.show(preparation.job_id)["current_state"]
        if head["status"] in TERMINAL_STATUSES:
            return 0
        code = error.diagnostic.code
        if code == OPERATION_CANCELLED:
            mutation = jobs.cancel(
                preparation.job_id,
                expected_state_id=head["state_id"],
                expected_state_digest=canonical_digest(head),
                actor="user",
            )
            return int(mutation.transaction is not None)
        if source_changed(layout, preparation) or code in {
            GROUNDING_MISMATCH,
            PROTECTED_INPUT_CHANGED,
        }:
            return transition_wait(
                jobs,
                head,
                status="waiting_source",
                current_node="trusted_parse_source_" + preparation.route_suffix,
                wait_reason="source_changed",
            )
        if code == TRUST_AUTHORITY_INVALID:
            return transition_wait(
                jobs,
                head,
                status="waiting_user",
                current_node=AUTHORITY_PREFIX + preparation.route_suffix,
                wait_reason="authority_required",
            )
        if code in {PARSER_WORKER_FAILED, INPUT_TOO_LARGE, PARSE_SOURCE_UNSUPPORTED}:
            return transition_wait(
                jobs,
                head,
                status="waiting_source",
                current_node="trusted_parse_failed_" + preparation.route_suffix,
                wait_reason="parse_failed",
            )
        return None

    def _hook(self, phase: str) -> None:
        if self.operation_hook is not None:
            self.operation_hook(phase)


__all__ = [
    "TrustedParseIntakeApplicationService",
    "TrustedParseIntakePreparation",
    "TrustedParseIntakeResult",
]
