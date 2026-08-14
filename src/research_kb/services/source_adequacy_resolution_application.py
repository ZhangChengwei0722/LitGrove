from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    SCHEMA_VALIDATION_FAILED,
    SNAPSHOT_MISMATCH,
    UNRESOLVED_REFERENCE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import utc_now
from research_kb.services.agent_task_application import AgentTaskApplicationService
from research_kb.services.reading_application import (
    OpenedEvidenceSource,
    _exact_source_observation,
    _open_validated_pdf,
    _source_candidates,
)
from research_kb.services.source_adequacy import SourceAdequacyService
from research_kb.services.workspace_session import WorkspaceSession
from research_kb.source_adequacy import profile_freshness
from research_kb.source_resolution import inspect_source_ref
from research_kb.storage.transactions import TransactionManager
from research_kb.workspace import WorkspaceLayout


RESOLUTION_REGISTRY_VERSION = "source-adequacy-resolution-v1"
REQUESTED_OPERATION = "continuous_text_evidence"
REQUIRED_CAPABILITY = "continuous_text_citation"
ACCEPT_ACTION = "accept_uncertainty"
REMEDIATE_ACTION = "remediation_required"
READING_ORDER_ATTESTATION = "reading_order_reviewed"
SUPPORTED_TASK_KINDS = frozenset({"primary_semantic_processing", "review_semantic_processing"})
ALLOWED_ACTIONS = (ACCEPT_ACTION, REMEDIATE_ACTION)

IdAllocator = Callable[[Namespace], str]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class SourceReviewHandle:
    workspace_id: str = field(repr=False)
    task_id: str
    task_state_id: str = field(repr=False)
    task_state_digest: str = field(repr=False)
    task_kind: str
    job_id: str = field(repr=False)
    paper_id: str
    basis_profile_id: str
    basis_profile_digest: str = field(repr=False)
    expected_fingerprint: str = field(repr=False)
    source_root_id: str = field(repr=False)
    source_relative_path: str = field(repr=False)
    parse_snapshot_digest: str = field(repr=False)
    requested_operation: str = REQUESTED_OPERATION
    required_capability: str = REQUIRED_CAPABILITY

    @property
    def evidence_id(self) -> str:
        return self.task_id

    @property
    def pdf_page(self) -> int:
        return 1

    @property
    def locator(self) -> str:
        return "document"


@dataclass(frozen=True, slots=True)
class PreparedSourceReview:
    handle: SourceReviewHandle = field(repr=False)
    descriptor: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _ResolutionBinding:
    task: dict[str, Any]
    paper: dict[str, Any]
    basis_profile: dict[str, Any]
    successor_profile: dict[str, Any] | None
    freshness: dict[str, Any]
    hard_failure: bool
    resolution_state: str


class SourceAdequacyResolutionApplicationService:
    def __init__(
        self,
        *,
        clock: Clock = utc_now,
        id_allocator: IdAllocator = allocate_id,
    ):
        self.clock = clock
        self.id_allocator = id_allocator

    def show_context(self, session: WorkspaceSession, task_id: str) -> dict[str, Any]:
        layout = _session_layout(session)
        binding = self._binding(layout, task_id)
        return self._context_projection(binding)

    def prepare_source_review(
        self,
        session: WorkspaceSession,
        task_id: str,
        expected_task_state: Mapping[str, Any],
    ) -> PreparedSourceReview:
        layout = _session_layout(session)
        binding = self._binding(layout, task_id)
        _require_expected(binding.task, expected_task_state)
        if binding.resolution_state != "review_required":
            raise _request_error(
                binding.task["state_id"],
                "/resolution_state",
                "Source review requires a current non-hard uncertainty",
            )
        entries = load_workspace_entries(layout)
        expected_fingerprint = _task_source_digest(binding.task)
        observation = _exact_source_observation(
            layout,
            entries,
            binding.paper,
            expected_fingerprint,
        )
        handle = SourceReviewHandle(
            workspace_id=session.workspace_id,
            task_id=binding.task["task_id"],
            task_state_id=binding.task["state_id"],
            task_state_digest=canonical_digest(binding.task),
            task_kind=binding.task["task_kind"],
            job_id=binding.task["input_basis"]["job_id"],
            paper_id=binding.task["input_basis"]["paper_id"],
            basis_profile_id=binding.basis_profile["profile_id"],
            basis_profile_digest=canonical_digest(binding.basis_profile),
            expected_fingerprint=expected_fingerprint,
            source_root_id=observation.source_ref.root_id,
            source_relative_path=observation.source_ref.relative_path,
            parse_snapshot_digest=canonical_digest(binding.basis_profile["parse_snapshot"]),
        )
        with self.open_source_review(session, handle) as opened:
            size_bytes = opened.size_bytes
        return PreparedSourceReview(
            handle=handle,
            descriptor={
                "status": "success",
                "interface_version": "1.0",
                "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
                "task_id": handle.task_id,
                "paper_id": handle.paper_id,
                "basis_profile_id": handle.basis_profile_id,
                "media_type": "application/pdf",
                "size_bytes": size_bytes,
                "persistent_writes": 0,
                "canonical_scientific_write": False,
            },
        )

    def open_source_review(
        self,
        session: WorkspaceSession,
        handle: SourceReviewHandle,
    ) -> OpenedEvidenceSource:
        if not isinstance(handle, SourceReviewHandle):
            raise _source_error(None, "Source review handle is invalid")
        if handle.workspace_id != session.workspace_id:
            raise _source_error(handle.task_id, "Source review handle belongs to a different workspace")
        layout = _session_layout(session)
        binding = self._binding(layout, handle.task_id)
        if binding.resolution_state != "review_required":
            raise _source_error(handle.task_id, "Source review context is no longer current")
        _validate_handle_binding(handle, binding)
        entries = load_workspace_entries(layout)
        candidates, _ = _source_candidates(entries, binding.paper, handle.expected_fingerprint)
        if (handle.source_root_id, handle.source_relative_path) not in candidates:
            raise _source_error(handle.task_id, "Source review ref is no longer part of the paper lineage")
        observation = inspect_source_ref(
            layout,
            root_id=handle.source_root_id,
            relative_path=handle.source_relative_path,
        )
        if observation.availability != "available":
            raise _source_error(handle.task_id, "Source review document is unavailable")
        if observation.live_sha256 != handle.expected_fingerprint:
            raise _source_error(handle.task_id, "Source review document changed before access")
        return _open_validated_pdf(observation.path, handle)

    def decide(
        self,
        session: WorkspaceSession,
        task_id: str,
        expected_task_state: Mapping[str, Any],
        basis_profile_id: str,
        action: str,
        user_attestation_code: str | None = None,
    ) -> dict[str, Any]:
        layout = _session_layout(session)
        binding = self._binding(layout, task_id)
        expected = _normalize_expected(expected_task_state)
        _require_expected_or_refresh_replay(binding.task, expected)
        normalized_basis_id = validate_id(basis_profile_id, Namespace.SOURCE_ADEQUACY)
        if normalized_basis_id != binding.basis_profile["profile_id"]:
            raise _conflict(binding.task["state_id"], "Source Adequacy basis Profile changed")
        if action not in ALLOWED_ACTIONS:
            raise _request_error(binding.task["state_id"], "/action", "Source Adequacy action is not registered")
        _validate_attestation(action, user_attestation_code, binding.task["state_id"])

        if binding.successor_profile is not None:
            previous_action = binding.successor_profile["user_decision"]["decision"]
            if previous_action != action:
                raise _conflict(binding.task["state_id"], "Source Adequacy decision conflicts with the existing successor")
            return self._decision_projection(binding, binding.successor_profile, persistent_writes=0)
        if binding.resolution_state != "review_required":
            raise _request_error(
                binding.task["state_id"],
                "/resolution_state",
                "Source Adequacy decision requires a current non-hard uncertainty",
            )

        reason = (
            "The user reviewed the current source and accepted bounded continuous-text reading-order uncertainty."
            if action == ACCEPT_ACTION
            else "The user requires source or parse remediation before continuous-text citation."
        )
        mutation = SourceAdequacyService(
            layout,
            transaction_manager=TransactionManager(layout, clock=self.clock),
            id_allocator=self.id_allocator,
        ).assess(
            paper_id=binding.task["input_basis"]["paper_id"],
            job_id=binding.task["input_basis"]["job_id"],
            requested_operation=REQUESTED_OPERATION,
            actor="user",
            basis_profile_id=binding.basis_profile["profile_id"],
            user_decision={
                "decision": action,
                "capabilities": [REQUIRED_CAPABILITY],
                "reason": reason,
            },
        )
        refreshed_binding = self._binding(layout, task_id)
        if refreshed_binding.successor_profile is None:
            raise _conflict(binding.task["state_id"], "Source Adequacy successor Profile was not recoverable")
        return self._decision_projection(
            refreshed_binding,
            mutation.profile,
            persistent_writes=int(mutation.transaction is not None),
        )

    def _binding(self, layout: WorkspaceLayout, task_id: str) -> _ResolutionBinding:
        normalized_task_id = validate_id(task_id, Namespace.AGENT_TASK)
        states = AgentTaskApplicationService._read_states(layout)
        task = AgentTaskApplicationService._head(states, normalized_task_id)
        if task["task_kind"] not in SUPPORTED_TASK_KINDS:
            raise _request_error(task["state_id"], "/task_kind", "Agent Task does not support Source Adequacy resolution")
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        paper_id = task["input_basis"]["paper_id"]
        paper = next(
            (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == paper_id),
            None,
        )
        if paper is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "registry-paper", paper_id, "/paper_id", "paper is not registered")
            )
        basis_ref = next(
            (
                item
                for item in task["input_basis"].get("adequacy_profiles", [])
                if item.get("requested_operation") == REQUESTED_OPERATION
            ),
            None,
        )
        if basis_ref is None:
            raise _conflict(task["state_id"], "Agent Task lacks the continuous-text Source Adequacy basis")
        profiles = records_of_kind(entries, "source-adequacy-profile")
        basis_profile = next(
            (item for item in profiles if item["profile_id"] == basis_ref["profile_id"]),
            None,
        )
        if basis_profile is None or canonical_digest(basis_profile) != basis_ref["profile_digest"]:
            raise _conflict(task["state_id"], "Agent Task Source Adequacy basis is unresolved or changed")
        if (
            basis_profile["paper_id"] != paper_id
            or basis_profile["job_id"] != task["input_basis"]["job_id"]
            or basis_profile["requested_operation"] != REQUESTED_OPERATION
        ):
            raise _conflict(task["state_id"], "Agent Task Source Adequacy basis has invalid ownership")

        freshness = profile_freshness(layout, entries, basis_profile)
        successors = [
            item
            for item in profiles
            if item.get("basis_profile")
            == {
                "profile_id": basis_profile["profile_id"],
                "profile_digest": canonical_digest(basis_profile),
            }
            and item["paper_id"] == paper_id
            and item["job_id"] == task["input_basis"]["job_id"]
            and item["requested_operation"] == REQUESTED_OPERATION
            and item.get("user_decision") is not None
        ]
        successor_profile = _single_successor(successors, task["state_id"])
        if successor_profile is not None:
            successor_freshness = profile_freshness(layout, entries, successor_profile)
            if successor_freshness["state"] != "current":
                successor_profile = None
                freshness = successor_freshness

        hard_failure = _has_hard_failure(basis_profile)
        state = _resolution_state(task, basis_profile, successor_profile, freshness, hard_failure)
        return _ResolutionBinding(
            task=task,
            paper=paper,
            basis_profile=basis_profile,
            successor_profile=successor_profile,
            freshness=freshness,
            hard_failure=hard_failure,
            resolution_state=state,
        )

    @staticmethod
    def _context_projection(binding: _ResolutionBinding) -> dict[str, Any]:
        capability = binding.basis_profile["capabilities"][REQUIRED_CAPABILITY]
        result = {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "resolution_registry_version": RESOLUTION_REGISTRY_VERSION,
            "resolution_state": binding.resolution_state,
            "task": {
                "task_id": binding.task["task_id"],
                "state_id": binding.task["state_id"],
                "state_digest": canonical_digest(binding.task),
                "task_kind": binding.task["task_kind"],
                "status": binding.task["status"],
            },
            "paper_id": binding.task["input_basis"]["paper_id"],
            "job_id": binding.task["input_basis"]["job_id"],
            "basis_profile_id": binding.basis_profile["profile_id"],
            "requested_operation": REQUESTED_OPERATION,
            "required_capability": REQUIRED_CAPABILITY,
            "machine_status": capability["status"],
            "hard_failure": binding.hard_failure,
            "freshness": binding.freshness["state"],
            "known_limitations": list(binding.basis_profile["known_limitations"]),
            "recommended_actions": list(binding.basis_profile["recommended_actions"]),
            "allowed_actions": list(ALLOWED_ACTIONS) if binding.resolution_state == "review_required" else [],
            "source_review_required": binding.resolution_state == "review_required",
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }
        if binding.successor_profile is not None:
            result["successor_profile_id"] = binding.successor_profile["profile_id"]
            result["decision_action"] = binding.successor_profile["user_decision"]["decision"]
        return result

    def _decision_projection(
        self,
        binding: _ResolutionBinding,
        profile: Mapping[str, Any],
        *,
        persistent_writes: int,
    ) -> dict[str, Any]:
        action = profile["user_decision"]["decision"]
        return {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "resolution_registry_version": RESOLUTION_REGISTRY_VERSION,
            "resolution_state": (
                "accepted_refresh_required" if action == ACCEPT_ACTION else "remediation_refresh_required"
            ),
            "task": self._context_projection(binding)["task"],
            "basis_profile_id": binding.basis_profile["profile_id"],
            "successor_profile_id": profile["profile_id"],
            "decision_action": action,
            "refresh_required": True,
            "refresh_route": "primary" if binding.task["task_kind"] == "primary_semantic_processing" else "review",
            "persistent_writes": persistent_writes,
            "canonical_scientific_write": False,
        }


def _resolution_state(
    task: Mapping[str, Any],
    basis_profile: Mapping[str, Any],
    successor_profile: Mapping[str, Any] | None,
    freshness: Mapping[str, Any],
    hard_failure: bool,
) -> str:
    if task["status"] == "superseded":
        return "not_required"
    if task["status"] not in {"created", "leased", "submitted"}:
        return "not_required"
    if freshness["state"] != "current":
        return "stale"
    if successor_profile is not None:
        action = successor_profile["user_decision"]["decision"]
        return "accepted_refresh_required" if action == ACCEPT_ACTION else "remediation_refresh_required"
    status = basis_profile["capabilities"][REQUIRED_CAPABILITY]["status"]
    if status == "uncertain" and not hard_failure:
        return "review_required"
    if status == "no" or hard_failure:
        return "not_resolvable"
    return "not_required"


def _single_successor(profiles: list[dict[str, Any]], task_state_id: str) -> dict[str, Any] | None:
    if not profiles:
        return None
    actions = {item["user_decision"]["decision"] for item in profiles}
    if len(actions) != 1:
        raise _conflict(task_state_id, "Source Adequacy basis has conflicting user successors")
    return max(profiles, key=lambda item: (item["assessed_at"], item["profile_id"]))


def _has_hard_failure(profile: Mapping[str, Any]) -> bool:
    return any(
        item["hard_failure"]
        and item["status"] == "fail"
        and REQUIRED_CAPABILITY in item["affected_capabilities"]
        for item in profile["machine_observations"]
    )


def _task_source_digest(task: Mapping[str, Any]) -> str:
    value = task["input_basis"].get("source_digest")
    if not isinstance(value, str) or len(value) != 64:
        raise _conflict(task["state_id"], "Agent Task source identity is unavailable")
    return value


def _validate_handle_binding(handle: SourceReviewHandle, binding: _ResolutionBinding) -> None:
    task = binding.task
    if (
        handle.task_state_id != task["state_id"]
        or handle.task_state_digest != canonical_digest(task)
        or handle.task_kind != task["task_kind"]
        or handle.job_id != task["input_basis"]["job_id"]
        or handle.paper_id != task["input_basis"]["paper_id"]
        or handle.basis_profile_id != binding.basis_profile["profile_id"]
        or handle.basis_profile_digest != canonical_digest(binding.basis_profile)
        or handle.expected_fingerprint != _task_source_digest(task)
        or handle.parse_snapshot_digest != canonical_digest(binding.basis_profile["parse_snapshot"])
        or handle.requested_operation != REQUESTED_OPERATION
        or handle.required_capability != REQUIRED_CAPABILITY
    ):
        raise _source_error(task["task_id"], "Source review handle binding changed before access")


def _validate_attestation(action: str, attestation: str | None, state_id: str) -> None:
    if action == ACCEPT_ACTION and attestation != READING_ORDER_ATTESTATION:
        raise _request_error(state_id, "/user_attestation_code", "Source review attestation is required")
    if action == REMEDIATE_ACTION and attestation is not None:
        raise _request_error(state_id, "/user_attestation_code", "Remediation does not accept an attestation")


def _normalize_expected(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"state_id", "state_digest"}:
        raise _request_error(None, "/expected_task_state", "expected Task state requires state_id and state_digest")
    state_id = validate_id(value.get("state_id"), Namespace.AGENT_TASK_STATE)
    digest = value.get("state_digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(item not in "0123456789abcdef" for item in digest):
        raise _request_error(state_id, "/expected_task_state/state_digest", "expected Task state digest is invalid")
    return {"state_id": state_id, "state_digest": digest}


def _require_expected(task: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    expected = _normalize_expected(value)
    if task["state_id"] != expected["state_id"] or canonical_digest(task) != expected["state_digest"]:
        raise _conflict(expected["state_id"], "Agent Task current state changed before Source Adequacy resolution")


def _require_expected_or_refresh_replay(task: Mapping[str, Any], expected: Mapping[str, str]) -> None:
    current = {"state_id": task["state_id"], "state_digest": canonical_digest(task)}
    if expected == current:
        return
    if task["status"] == "superseded" and expected == task.get("predecessor"):
        return
    raise _conflict(expected["state_id"], "Agent Task current state changed before Source Adequacy resolution")


def _session_layout(session: WorkspaceSession) -> WorkspaceLayout:
    if not isinstance(session, WorkspaceSession):
        raise _request_error(None, "/session", "a Core-owned WorkspaceSession is required")
    return session._layout


def _request_error(record_id: str | None, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(SCHEMA_VALIDATION_FAILED, "source-adequacy-resolution", record_id, path, message))


def _conflict(record_id: str | None, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(WRITE_CONFLICT, "source-adequacy-resolution", record_id, "/state", message))


def _source_error(record_id: str | None, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(SNAPSHOT_MISMATCH, "source-adequacy-resolution", record_id, "/task_id", message))


__all__ = [
    "ACCEPT_ACTION",
    "ALLOWED_ACTIONS",
    "PreparedSourceReview",
    "READING_ORDER_ATTESTATION",
    "REMEDIATE_ACTION",
    "REQUESTED_OPERATION",
    "REQUIRED_CAPABILITY",
    "RESOLUTION_REGISTRY_VERSION",
    "SourceAdequacyResolutionApplicationService",
    "SourceReviewHandle",
]
