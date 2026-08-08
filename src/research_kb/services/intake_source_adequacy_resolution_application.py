from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INCOMPLETE_TRANSACTION,
    SCHEMA_VALIDATION_FAILED,
    SNAPSHOT_MISMATCH,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import utc_now
from research_kb.services.deterministic_trunk import DeterministicTrunkService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.reading_application import (
    OpenedEvidenceSource,
    _exact_source_observation,
    _open_validated_pdf,
    _source_candidates,
)
from research_kb.services.source_adequacy import SourceAdequacyService
from research_kb.services.trusted_parse_intake_support import (
    paper_for_job,
    semantic_intent,
    session_layout,
    validate_trusted_profile_lineage,
)
from research_kb.source_adequacy import profile_freshness
from research_kb.source_resolution import inspect_source_ref, observe_paper_source
from research_kb.storage.transactions import TransactionManager
from research_kb.workspace import WorkspaceLayout
from research_kb.services.workspace_session import WorkspaceSession


RESOLUTION_REGISTRY_VERSION = "intake-source-adequacy-resolution-v1"
ACCEPT_ACTION = "accept_uncertainty"
REMEDIATE_ACTION = "remediation_required"
ACCEPT_ATTESTATION = "basic_source_reviewed"
ALLOWED_ACTIONS = (ACCEPT_ACTION, REMEDIATE_ACTION)
SUPPORTED_OPERATIONS = ("basic_paper_card", "basic_review_memory")
REQUIRED_CAPABILITY = "basic_paper_understanding"
SOURCE_ADEQUACY_WAIT_REASONS = frozenset(
    {
        "source_adequacy_uncertain",
        "source_adequacy_inadequate",
        "source_adequacy_stale",
        "source_incomplete",
        "ocr_required",
        "reparse_required",
    }
)
RECONCILE_PREFIX = "trusted_parse_reconcile_"
SOURCE_ADEQUACY_NODE = "source_adequacy"
SEMANTIC_GATE_NODES = frozenset(
    {
        "primary_semantic_gate",
        "review_semantic_gate",
        "review_semantic_gate_mixed_document",
    }
)

IdAllocator = Callable[[Namespace], str]
Clock = Callable[[], datetime]
TrunkFactory = Callable[[WorkspaceLayout], Any]


@dataclass(frozen=True, slots=True)
class IntakeSourceReviewHandle:
    """Opaque, single-session source binding for the App reader handoff."""

    workspace_id: str = field(repr=False)
    job_id: str
    origin_state_id: str = field(repr=False)
    origin_state_digest: str = field(repr=False)
    paper_id: str
    basis_profile_id: str = field(repr=False)
    basis_profile_digest: str = field(repr=False)
    requested_operation: str
    required_capability: str
    expected_fingerprint: str = field(repr=False)
    source_root_id: str = field(repr=False)
    source_relative_path: str = field(repr=False)
    active_parse_id: str = field(repr=False)
    parse_output_digest: str = field(repr=False)
    parser_identity: tuple[str, str] = field(repr=False)
    authority_id: str = field(repr=False)
    authority_state_id: str = field(repr=False)
    authority_event_id: str = field(repr=False)
    parse_event_id: str = field(repr=False)
    document_route: str
    route_reason: str | None = None

    @property
    def evidence_id(self) -> str:
        return self.job_id

    @property
    def pdf_page(self) -> int:
        return 1

    @property
    def locator(self) -> str:
        return "document"

    @property
    def source_currentness(self) -> str:
        return "current"


@dataclass(frozen=True, slots=True)
class PreparedIntakeSourceReview:
    handle: IntakeSourceReviewHandle = field(repr=False)
    descriptor: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _ResolutionBinding:
    job: dict[str, Any]
    history: tuple[dict[str, Any], ...]
    origin_state: dict[str, Any]
    paper: dict[str, Any]
    basis_profile: dict[str, Any]
    successor_profile: dict[str, Any] | None
    freshness: dict[str, Any]
    successor_freshness: dict[str, Any] | None
    source_observation: Any
    authority: dict[str, Any]
    authority_event: dict[str, Any]
    parse_event: dict[str, Any]
    reconcile_state: dict[str, Any]
    route_suffix: str
    requested_operation: str
    document_route: str
    route_reason: str | None
    hard_failure: bool
    resolution_state: str


class IntakeSourceAdequacyResolutionApplicationService:
    """Resolve only the Job-bound basic-use Source Adequacy uncertainty.

    This service owns the source review and immutable user-decision successor. The
    deterministic trunk owns the actual same-Job continuation and is injected as a
    narrow dependency so this module cannot accidentally call a parser or assessment
    path itself.
    """

    def __init__(
        self,
        *,
        clock: Clock = utc_now,
        id_allocator: IdAllocator = allocate_id,
        trunk_factory: TrunkFactory = DeterministicTrunkService,
    ) -> None:
        self.clock = clock
        self.id_allocator = id_allocator
        self.trunk_factory = trunk_factory

    def show_context(self, session: WorkspaceSession, job_id: str) -> dict[str, Any]:
        layout = session_layout(session)
        binding = self._binding(layout, job_id)
        return self._context_projection(binding)

    def prepare_source_review(
        self,
        session: WorkspaceSession,
        job_id: str,
        expected_job_state: Mapping[str, Any],
    ) -> PreparedIntakeSourceReview:
        layout = session_layout(session)
        binding = self._binding(layout, job_id)
        _require_exact_state(binding.job, expected_job_state)
        if binding.job["state_id"] != binding.origin_state["state_id"]:
            raise _conflict(binding.job["state_id"], "source review requires the originating Job wait")
        if binding.resolution_state != "review_required":
            raise _request_error(
                binding.job["state_id"],
                "/resolution_state",
                "source review requires a current non-hard basic-use uncertainty",
            )

        entries = load_workspace_entries(layout)
        expected_fingerprint = _expected_fingerprint(binding.source_observation)
        observation = _exact_source_observation(
            layout,
            entries,
            binding.paper,
            expected_fingerprint,
        )
        parse_snapshot = binding.basis_profile["parse_snapshot"]
        parser_identity = parse_snapshot["parser_identity"]
        handle = IntakeSourceReviewHandle(
            workspace_id=session.workspace_id,
            job_id=binding.job["job_id"],
            origin_state_id=binding.origin_state["state_id"],
            origin_state_digest=canonical_digest(binding.origin_state),
            paper_id=binding.paper["paper_id"],
            basis_profile_id=binding.basis_profile["profile_id"],
            basis_profile_digest=canonical_digest(binding.basis_profile),
            requested_operation=binding.requested_operation,
            required_capability=REQUIRED_CAPABILITY,
            expected_fingerprint=expected_fingerprint,
            source_root_id=observation.source_ref.root_id,
            source_relative_path=observation.source_ref.relative_path,
            active_parse_id=parse_snapshot["active_parse_ref"],
            parse_output_digest=parse_snapshot["output_bundle_digest"],
            parser_identity=(parser_identity["adapter_id"], parser_identity["version"]),
            authority_id=binding.authority["authority_id"],
            authority_state_id=binding.authority["state_id"],
            authority_event_id=binding.authority_event["event_id"],
            parse_event_id=binding.parse_event["event_id"],
            document_route=binding.document_route,
            route_reason=binding.route_reason,
        )
        with self.open_source_review(session, handle) as opened:
            size_bytes = opened.size_bytes
        return PreparedIntakeSourceReview(
            handle=handle,
            descriptor={
                "status": "success",
                "interface_version": "1.0",
                "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
                "resolution_registry_version": RESOLUTION_REGISTRY_VERSION,
                "job_id": handle.job_id,
                "paper_id": handle.paper_id,
                "basis_profile_id": handle.basis_profile_id,
                "requested_operation": handle.requested_operation,
                "required_capability": handle.required_capability,
                "media_type": "application/pdf",
                "size_bytes": size_bytes,
                "persistent_writes": 0,
                "canonical_scientific_write": False,
            },
        )

    def open_source_review(
        self,
        session: WorkspaceSession,
        handle: IntakeSourceReviewHandle,
    ) -> OpenedEvidenceSource:
        if not isinstance(handle, IntakeSourceReviewHandle):
            raise _source_error(None, "source review handle is invalid")
        layout = session_layout(session)
        if handle.workspace_id != session.workspace_id:
            raise _source_error(handle.job_id, "source review handle belongs to another workspace")
        binding = self._binding(layout, handle.job_id)
        if binding.resolution_state != "review_required":
            raise _source_error(handle.job_id, "source review context is no longer current")
        _validate_handle(handle, binding)
        entries = load_workspace_entries(layout)
        candidates, _ = _source_candidates(
            entries,
            binding.paper,
            handle.expected_fingerprint,
        )
        if (handle.source_root_id, handle.source_relative_path) not in candidates:
            raise _source_error(handle.job_id, "source review lineage is no longer current")
        observation = inspect_source_ref(
            layout,
            root_id=handle.source_root_id,
            relative_path=handle.source_relative_path,
        )
        if observation.availability != "available":
            raise _source_error(handle.job_id, "source review document is unavailable")
        if observation.live_sha256 != handle.expected_fingerprint:
            raise _source_error(handle.job_id, "source review document changed before access")
        try:
            return _open_validated_pdf(observation.path, handle)
        except ResearchKBError:
            raise
        except OSError as error:
            raise _source_error(handle.job_id, "source review document could not be opened") from error

    def decide_and_continue(
        self,
        session: WorkspaceSession,
        job_id: str,
        expected_job_state: Mapping[str, Any],
        action: str,
        attestation: str | None = None,
    ) -> dict[str, Any]:
        layout = session_layout(session)
        binding = self._binding(layout, job_id)
        expected = _normalize_expected(expected_job_state)
        _require_origin_or_exact_descendant(binding, expected)
        if action not in ALLOWED_ACTIONS:
            raise _request_error(binding.job["state_id"], "/action", "source adequacy action is not registered")

        successor = binding.successor_profile
        if successor is not None:
            previous_action = successor["user_decision"]["decision"]
            if previous_action != action:
                raise _conflict(binding.job["state_id"], "source adequacy action conflicts with the existing successor")
            _validate_attestation(action, attestation, replay=True)
            if action == REMEDIATE_ACTION:
                return self._decision_projection(
                    layout,
                    binding,
                    successor,
                    resolution_state="remediation_required",
                    persistent_writes=0,
                )
            if binding.resolution_state in {"stale", "not_resolvable"}:
                raise _grounding_error(binding.job["state_id"], "accepted Source Adequacy successor is not current")
            return self._continue(
                layout,
                binding,
                successor,
                persistent_writes=0,
            )

        if binding.job["state_id"] != binding.origin_state["state_id"]:
            raise _conflict(binding.job["state_id"], "an accepted continuation has no recoverable successor")
        if binding.resolution_state != "review_required":
            raise _request_error(
                binding.job["state_id"],
                "/resolution_state",
                "source adequacy decision requires a current non-hard uncertainty",
            )
        _validate_attestation(action, attestation, replay=False)

        decision = {
            "decision": action,
            "capabilities": [REQUIRED_CAPABILITY],
            "reason": (
                "The user reviewed the current source for basic paper understanding."
                if action == ACCEPT_ACTION
                else "The user requires source or parse remediation before basic paper understanding."
            ),
        }
        mutation = SourceAdequacyService(
            layout,
            transaction_manager=TransactionManager(layout, clock=self.clock),
            id_allocator=self.id_allocator,
        ).assess(
            paper_id=binding.paper["paper_id"],
            job_id=binding.job["job_id"],
            requested_operation=binding.requested_operation,
            actor="user",
            basis_profile_id=binding.basis_profile["profile_id"],
            user_decision=decision,
        )
        refreshed = self._binding(layout, binding.job["job_id"])
        if refreshed.successor_profile is None:
            raise _conflict(binding.job["state_id"], "source adequacy decision successor was not recoverable")
        if action == REMEDIATE_ACTION:
            return self._decision_projection(
                layout,
                refreshed,
                refreshed.successor_profile,
                resolution_state="remediation_required",
                persistent_writes=int(mutation.transaction is not None),
            )
        return self._continue(
            layout,
            refreshed,
            refreshed.successor_profile,
            persistent_writes=int(mutation.transaction is not None),
        )

    def _continue(
        self,
        layout: WorkspaceLayout,
        binding: _ResolutionBinding,
        profile: Mapping[str, Any],
        *,
        persistent_writes: int,
    ) -> dict[str, Any]:
        trunk = self.trunk_factory(layout)
        continuation = getattr(trunk, "continue_with_profile", None)
        if not callable(continuation):
            raise _integration_error("deterministic trunk profile continuation is unavailable")
        continuation_result = continuation(
            job_id=binding.job["job_id"],
            paper_id=binding.paper["paper_id"],
            requested_operation=binding.requested_operation,
            expected_origin_state={
                "state_id": binding.origin_state["state_id"],
                "state_digest": canonical_digest(binding.origin_state),
            },
            profile=profile,
            document_route=binding.document_route,
            route_reason=binding.route_reason,
        )
        refreshed = self._binding(layout, binding.job["job_id"])
        resolution_state = "continued" if refreshed.resolution_state == "continued" else "continuation_in_progress"
        return self._decision_projection(
            layout,
            refreshed,
            profile,
            resolution_state=resolution_state,
            persistent_writes=persistent_writes + continuation_result.persistent_writes,
        )

    def _binding(self, layout: WorkspaceLayout, job_id: str) -> _ResolutionBinding:
        normalized_job_id = validate_id(job_id, Namespace.JOB)
        shown = PipelineJobService(layout).show(normalized_job_id)
        history = tuple(sorted(shown["history"], key=lambda item: item["revision"]))
        job = shown["current_state"]
        origin_candidates = [
            item
            for item in history
            if item["current_node"] == SOURCE_ADEQUACY_NODE
            and item["status"] in {"waiting_user", "waiting_source"}
            and item["wait_reason"] in SOURCE_ADEQUACY_WAIT_REASONS
        ]
        if len(origin_candidates) != 1:
            raise _lineage_error(normalized_job_id, "Job does not have exactly one Source Adequacy origin")
        origin_state = origin_candidates[0]

        reconcile_states = [
            item
            for item in history
            if isinstance(item.get("current_node"), str)
            and item["current_node"].startswith(RECONCILE_PREFIX)
            and item["current_node"].removeprefix(RECONCILE_PREFIX)
            in {"primary", "review", "review_mixed"}
        ]
        if len(reconcile_states) != 1:
            raise _lineage_error(normalized_job_id, "Job trusted Parse reconcile lineage is ambiguous")
        reconcile_state = reconcile_states[0]
        suffix = reconcile_state["current_node"].removeprefix(RECONCILE_PREFIX)
        requested_operation, document_route, route_reason = semantic_intent(suffix)
        if requested_operation not in SUPPORTED_OPERATIONS or document_route is None:
            raise _lineage_error(normalized_job_id, "Job route is not a supported basic-use route")
        if job["requested_route"] != "local_source" or job["requested_depth"] != "semantic_gate":
            raise _request_error(job["state_id"], "/requested_route", "Job is outside the local semantic-gate contract")
        if job["state_id"] != origin_state["state_id"] and not _is_descendant(history, origin_state, job):
            raise _conflict(job["state_id"], "Job is not an exact descendant of the Source Adequacy origin")

        paper = paper_for_job(layout, normalized_job_id)
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        source_observation = observe_paper_source(layout, entries, paper)

        profiles = records_of_kind(entries, "source-adequacy-profile")
        basis_candidates = _profiles_referenced_by_state(
            profiles,
            history,
            origin_state,
            job_id=normalized_job_id,
            paper_id=paper["paper_id"],
            requested_operation=requested_operation,
        )
        if len(basis_candidates) != 1:
            raise _lineage_error(normalized_job_id, "Job does not reference exactly one basic-use basis Profile")
        basis_profile = basis_candidates[0]
        if basis_profile.get("basis_profile") is not None or basis_profile.get("user_decision") is not None:
            raise _lineage_error(normalized_job_id, "Job basis Profile is not a machine assessment")
        freshness = profile_freshness(layout, entries, basis_profile)
        trusted_lineage = validate_trusted_profile_lineage(
            layout,
            job_id=normalized_job_id,
            paper_id=paper["paper_id"],
            profile=basis_profile,
            require_current=False,
        )
        if (
            trusted_lineage["reconcile_state"]["state_id"] != reconcile_state["state_id"]
            or trusted_lineage["route_suffix"] != suffix
        ):
            raise _lineage_error(normalized_job_id, "trusted Parse reconcile lineage changed")
        authority = trusted_lineage["authority"]
        authority_event = trusted_lineage["authority_event"]
        parse_event = trusted_lineage["parse_event"]

        successor_candidates = [
            item
            for item in profiles
            if item.get("basis_profile")
            == {
                "profile_id": basis_profile["profile_id"],
                "profile_digest": canonical_digest(basis_profile),
            }
            and item.get("job_id") == normalized_job_id
            and item.get("paper_id") == paper["paper_id"]
            and item.get("requested_operation") == requested_operation
            and item.get("user_decision") is not None
        ]
        if len(successor_candidates) > 1:
            raise _conflict(basis_profile["profile_id"], "Source Adequacy has multiple decision successors")
        successor = successor_candidates[0] if successor_candidates else None
        successor_freshness = None if successor is None else profile_freshness(layout, entries, successor)
        if job["state_id"] != origin_state["state_id"]:
            if (
                successor is None
                or successor["user_decision"]["decision"] != ACCEPT_ACTION
                or not _is_exact_continuation_descendant(
                    history,
                    origin_state,
                    job,
                    successor["profile_id"],
                    document_route,
                    route_reason,
                )
            ):
                raise _conflict(
                    job["state_id"],
                    "Job head is not the exact descendant of its accepted Source Adequacy decision",
                )
        hard_failure = _has_hard_failure(basis_profile)
        resolution_state = _resolution_state(
            job,
            origin_state,
            basis_profile,
            successor,
            freshness,
            successor_freshness,
            hard_failure,
        )
        return _ResolutionBinding(
            job=job,
            history=history,
            origin_state=origin_state,
            paper=paper,
            basis_profile=basis_profile,
            successor_profile=successor,
            freshness=freshness,
            successor_freshness=successor_freshness,
            source_observation=source_observation,
            authority=authority,
            authority_event=authority_event,
            parse_event=parse_event,
            reconcile_state=reconcile_state,
            route_suffix=suffix,
            requested_operation=requested_operation,
            document_route=document_route,
            route_reason=route_reason,
            hard_failure=hard_failure,
            resolution_state=resolution_state,
        )

    @staticmethod
    def _context_projection(binding: _ResolutionBinding) -> dict[str, Any]:
        capability = binding.basis_profile["capabilities"][REQUIRED_CAPABILITY]
        return {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "resolution_registry_version": RESOLUTION_REGISTRY_VERSION,
            "resolution_state": binding.resolution_state,
            "job": {
                "job_id": binding.job["job_id"],
                "state_id": binding.job["state_id"],
                "state_digest": canonical_digest(binding.job),
                "status": binding.job["status"],
                "current_node": binding.job["current_node"],
                "wait_reason": binding.job["wait_reason"],
            },
            "paper_id": binding.paper["paper_id"],
            "basis_profile_id": binding.basis_profile["profile_id"],
            "requested_operation": binding.requested_operation,
            "required_capability": REQUIRED_CAPABILITY,
            "document_route": binding.document_route,
            "route_reason": binding.route_reason,
            "machine_status": capability["status"],
            "hard_failure": binding.hard_failure,
            "freshness": binding.freshness["state"],
            "source_availability": _source_availability(binding.source_observation),
            "known_limitations": list(binding.basis_profile["known_limitations"]),
            "recommended_actions": list(binding.basis_profile["recommended_actions"]),
            "allowed_actions": list(ALLOWED_ACTIONS) if binding.resolution_state == "review_required" else [],
            "source_review_required": binding.resolution_state == "review_required",
            "persistent_writes": 0,
            "canonical_scientific_write": False,
            **(
                {}
                if binding.successor_profile is None
                else {
                    "successor_profile_id": binding.successor_profile["profile_id"],
                    "decision_action": binding.successor_profile["user_decision"]["decision"],
                }
            ),
        }

    @staticmethod
    def _decision_projection(
        layout: WorkspaceLayout,
        binding: _ResolutionBinding,
        profile: Mapping[str, Any],
        *,
        resolution_state: str,
        persistent_writes: int,
    ) -> dict[str, Any]:
        current = PipelineJobService(layout).show(binding.job["job_id"])["current_state"]
        return {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "resolution_registry_version": RESOLUTION_REGISTRY_VERSION,
            "resolution_state": resolution_state,
            "job": {
                "job_id": current["job_id"],
                "state_id": current["state_id"],
                "state_digest": canonical_digest(current),
                "status": current["status"],
                "current_node": current["current_node"],
                "wait_reason": current["wait_reason"],
            },
            "paper_id": binding.paper["paper_id"],
            "requested_operation": binding.requested_operation,
            "required_capability": REQUIRED_CAPABILITY,
            "basis_profile_id": binding.basis_profile["profile_id"],
            "successor_profile_id": profile["profile_id"],
            "decision_action": profile["user_decision"]["decision"],
            "document_route": binding.document_route,
            "route_reason": binding.route_reason,
            "refresh_required": resolution_state != "continued",
            "persistent_writes": persistent_writes,
            "canonical_scientific_write": False,
        }


def _profiles_referenced_by_state(
    profiles: list[dict[str, Any]],
    history: tuple[dict[str, Any], ...],
    origin: Mapping[str, Any],
    *,
    job_id: str,
    paper_id: str,
    requested_operation: str,
) -> list[dict[str, Any]]:
    refs = set(origin.get("output_refs", []))
    candidates = [
        item
        for item in profiles
        if item.get("profile_id") in refs
        and item.get("job_id") == job_id
        and item.get("paper_id") == paper_id
        and item.get("requested_operation") == requested_operation
    ]
    return _unique_by_id(candidates)


def _unique_by_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        result[item["profile_id"]] = item
    return list(result.values())


def _resolution_state(
    job: Mapping[str, Any],
    origin: Mapping[str, Any],
    basis: Mapping[str, Any],
    successor: Mapping[str, Any] | None,
    freshness: Mapping[str, Any],
    successor_freshness: Mapping[str, Any] | None,
    hard_failure: bool,
) -> str:
    if freshness["state"] != "current" or (
        successor_freshness is not None and successor_freshness["state"] != "current"
    ):
        return "stale"
    if job["state_id"] != origin["state_id"]:
        if successor is None:
            return "not_required"
        if job["current_node"] in SEMANTIC_GATE_NODES:
            return "continued"
        if successor["user_decision"]["decision"] == ACCEPT_ACTION:
            return "continuation_in_progress"
        return "not_required"
    if successor is not None:
        return (
            "accepted_continuation_required"
            if successor["user_decision"]["decision"] == ACCEPT_ACTION
            else "remediation_required"
        )
    capability_status = basis["capabilities"][REQUIRED_CAPABILITY]["status"]
    if hard_failure or capability_status == "no":
        return "not_resolvable"
    if capability_status == "uncertain":
        return "review_required"
    return "not_required"


def _has_hard_failure(profile: Mapping[str, Any]) -> bool:
    return any(
        item.get("hard_failure") is True
        and item.get("status") == "fail"
        and REQUIRED_CAPABILITY in item.get("affected_capabilities", [])
        for item in profile.get("machine_observations", [])
    )


def _profile_source_fingerprint(profile: Mapping[str, Any]) -> str:
    snapshots = [item for item in profile.get("source_snapshots", []) if item.get("role") == "main_pdf"]
    if len(snapshots) != 1:
        raise _lineage_error(profile.get("profile_id"), "basis Profile main source manifestation is ambiguous")
    manifestation = snapshots[0].get("manifestation_id")
    if not isinstance(manifestation, str) or not manifestation.startswith("sha256:"):
        raise _lineage_error(profile.get("profile_id"), "basis Profile source manifestation is invalid")
    value = manifestation.removeprefix("sha256:")
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise _lineage_error(profile.get("profile_id"), "basis Profile source manifestation is invalid")
    return value


def _expected_fingerprint(observation: Any) -> str:
    value = getattr(observation, "expected_sha256", None)
    if not isinstance(value, str) or len(value) != 64:
        raise _source_error(None, "source manifestation fingerprint is unavailable")
    return value


def _source_availability(observation: Any) -> str:
    state = getattr(observation, "state", None)
    return "available" if state == "current" else str(state or "inaccessible")


def _is_descendant(
    history: tuple[dict[str, Any], ...],
    origin: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    by_id = {item["state_id"]: item for item in history}
    cursor = current
    visited: set[str] = set()
    while cursor["state_id"] != origin["state_id"]:
        state_id = cursor["state_id"]
        if state_id in visited:
            return False
        visited.add(state_id)
        predecessor = cursor.get("predecessor")
        if not isinstance(predecessor, Mapping):
            return False
        previous = by_id.get(predecessor.get("state_id"))
        if previous is None or canonical_digest(previous) != predecessor.get("state_digest"):
            return False
        cursor = previous
    return True


def _is_exact_continuation_descendant(
    history: tuple[dict[str, Any], ...],
    origin: Mapping[str, Any],
    current: Mapping[str, Any],
    successor_profile_id: str,
    document_route: str,
    route_reason: str | None,
) -> bool:
    origin_index = next(
        (index for index, item in enumerate(history) if item["state_id"] == origin["state_id"]),
        None,
    )
    if origin_index is None:
        return False
    descendants = history[origin_index + 1 :]
    if not descendants or len(descendants) > 2 or descendants[-1]["state_id"] != current["state_id"]:
        return False
    route_node = (
        "review_semantic_gate_mixed_document"
        if route_reason == "mixed_document"
        else f"{document_route}_semantic_gate"
    )
    expected_statuses = ("running", "completed")
    continuation_outputs = sorted({*origin["output_refs"], successor_profile_id})
    return all(
        item["current_node"] == route_node
        and item["status"] == expected_statuses[index]
        and item["wait_reason"] is None
        and item["output_refs"] == continuation_outputs
        for index, item in enumerate(descendants)
    )


def _validate_handle(handle: IntakeSourceReviewHandle, binding: _ResolutionBinding) -> None:
    snapshot = binding.basis_profile["parse_snapshot"]
    parser = snapshot["parser_identity"]
    if (
        handle.job_id != binding.job["job_id"]
        or handle.origin_state_id != binding.origin_state["state_id"]
        or handle.origin_state_digest != canonical_digest(binding.origin_state)
        or handle.paper_id != binding.paper["paper_id"]
        or handle.basis_profile_id != binding.basis_profile["profile_id"]
        or handle.basis_profile_digest != canonical_digest(binding.basis_profile)
        or handle.requested_operation != binding.requested_operation
        or handle.required_capability != REQUIRED_CAPABILITY
        or handle.expected_fingerprint != _profile_source_fingerprint(binding.basis_profile)
        or handle.active_parse_id != snapshot["active_parse_ref"]
        or handle.parse_output_digest != snapshot["output_bundle_digest"]
        or handle.parser_identity != (parser["adapter_id"], parser["version"])
        or handle.authority_id != binding.authority["authority_id"]
        or handle.authority_state_id != binding.authority["state_id"]
        or handle.authority_event_id != binding.authority_event["event_id"]
        or handle.parse_event_id != binding.parse_event["event_id"]
        or handle.document_route != binding.document_route
        or handle.route_reason != binding.route_reason
    ):
        raise _source_error(handle.job_id, "source review handle lineage changed before access")


def _validate_attestation(action: str, attestation: str | None, *, replay: bool) -> None:
    if action == ACCEPT_ACTION:
        if replay:
            if attestation not in {None, ACCEPT_ATTESTATION}:
                raise _request_error(None, "/attestation", "source review attestation is invalid")
        elif attestation != ACCEPT_ATTESTATION:
            raise _request_error(None, "/attestation", "source review attestation is required")
    elif attestation is not None:
        raise _request_error(None, "/attestation", "remediation does not accept an attestation")


def _normalize_expected(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"state_id", "state_digest"}:
        raise _request_error(None, "/expected_job_state", "expected Job state requires state_id and state_digest")
    state_id = validate_id(value.get("state_id"), Namespace.JOB_STATE)
    digest = value.get("state_digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise _request_error(state_id, "/expected_job_state/state_digest", "expected Job state digest is invalid")
    return {"state_id": state_id, "state_digest": digest}


def _require_exact_state(state: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    normalized = _normalize_expected(expected)
    if state["state_id"] != normalized["state_id"] or canonical_digest(state) != normalized["state_digest"]:
        raise _conflict(normalized["state_id"], "Job state changed before source review")


def _require_origin_or_exact_descendant(binding: _ResolutionBinding, expected: Mapping[str, Any]) -> None:
    normalized = _normalize_expected(expected)
    current = {
        "state_id": binding.job["state_id"],
        "state_digest": canonical_digest(binding.job),
    }
    origin = {
        "state_id": binding.origin_state["state_id"],
        "state_digest": canonical_digest(binding.origin_state),
    }
    if normalized == current or normalized == origin:
        return
    raise _conflict(normalized["state_id"], "Job state changed before Source Adequacy resolution")


def _request_error(record_id: str | None, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(SCHEMA_VALIDATION_FAILED, "intake-source-adequacy-resolution", record_id, path, message))


def _conflict(record_id: str | None, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(WRITE_CONFLICT, "intake-source-adequacy-resolution", record_id, "/state", message))


def _lineage_error(record_id: str | None, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(INCOMPLETE_TRANSACTION, "intake-source-adequacy-resolution", record_id, "/lineage", message))


def _grounding_error(record_id: str | None, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(GROUNDING_MISMATCH, "intake-source-adequacy-resolution", record_id, "/source", message))


def _source_error(record_id: str | None, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(SNAPSHOT_MISMATCH, "intake-source-adequacy-resolution", record_id, "/source_review", message))


def _integration_error(message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(SCHEMA_VALIDATION_FAILED, "intake-source-adequacy-resolution", None, "/continuation", message))


SourceReviewHandle = IntakeSourceReviewHandle
PreparedSourceReview = PreparedIntakeSourceReview


__all__ = [
    "ACCEPT_ACTION",
    "ACCEPT_ATTESTATION",
    "ALLOWED_ACTIONS",
    "IntakeSourceAdequacyResolutionApplicationService",
    "IntakeSourceReviewHandle",
    "PreparedIntakeSourceReview",
    "PreparedSourceReview",
    "REMEDIATE_ACTION",
    "REQUIRED_CAPABILITY",
    "RESOLUTION_REGISTRY_VERSION",
    "SourceReviewHandle",
]
