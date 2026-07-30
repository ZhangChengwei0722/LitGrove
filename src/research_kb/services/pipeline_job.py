from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DUPLICATE_ID,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.pipeline_jobs import (
    TERMINAL_STATUSES,
    current_pipeline_states,
    pipeline_job_chain_diagnostics,
    validate_running_progress,
    validate_transition,
    validate_wait_state,
)
from research_kb.process_events import timestamp
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]


@dataclass(frozen=True, slots=True)
class PipelineJobMutationResult:
    state: dict[str, Any]
    transaction: TransactionResult | None


class PipelineJobService:
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

    def create(
        self,
        *,
        requested_route: str,
        requested_depth: str,
        current_node: str,
        input_refs: list[str],
        authority_snapshot: Mapping[str, Any],
        idempotency_key: str,
        actor: str,
        fixture_origin: str | None = None,
    ) -> PipelineJobMutationResult:
        if actor != "user":
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "pipeline-job-state",
                    None,
                    "/actor",
                    "Pipeline Job creation requires explicit user authority",
                )
            )
        if not isinstance(input_refs, list) or not all(isinstance(item, str) for item in input_refs):
            raise _request_error(None, "input refs must be an array of record IDs", "/input_refs")
        if len(input_refs) != len(set(input_refs)):
            raise _request_error(None, "input refs must be unique", "/input_refs")
        normalized_authority = _normalize_authority_snapshot(authority_snapshot)
        states = self._read_states()
        current = current_pipeline_states(states)
        existing = next(
            (item for item in current if item["idempotency_key"] == idempotency_key),
            None,
        )
        requested_intent = {
            "requested_route": requested_route,
            "requested_depth": requested_depth,
            "current_node": current_node,
            "input_refs": sorted(input_refs),
            "authority_snapshot": normalized_authority,
            "idempotency_key": idempotency_key,
            "fixture_origin": fixture_origin,
        }
        if existing is not None:
            root = next(item for item in states if item["job_id"] == existing["job_id"] and item["revision"] == 1)
            if _creation_intent(root) == requested_intent:
                return PipelineJobMutationResult(existing, None)
            raise ResearchKBError(
                Diagnostic(
                    WRITE_CONFLICT,
                    "pipeline-job-state",
                    existing["state_id"],
                    "/idempotency_key",
                    "Pipeline Job idempotency key is already bound to different content",
                )
            )

        job_id = self.id_allocator(Namespace.JOB)
        state_id = self.id_allocator(Namespace.JOB_STATE)
        validate_id(job_id, Namespace.JOB)
        validate_id(state_id, Namespace.JOB_STATE)
        if job_id in {item["job_id"] for item in states} or state_id in {item["state_id"] for item in states}:
            raise ResearchKBError(
                Diagnostic(DUPLICATE_ID, "pipeline-job-state", state_id, "/state_id", "allocated Pipeline Job ID is already in use")
            )
        now = timestamp(self.transactions.clock)
        state = {
            "schema_version": "1.0",
            "state_id": state_id,
            "job_id": job_id,
            "workspace_id": self.layout.workspace_id,
            "revision": 1,
            "predecessor": None,
            "requested_route": requested_route,
            "requested_depth": requested_depth,
            "current_node": current_node,
            "status": "created",
            "wait_reason": None,
            "input_refs": sorted(input_refs),
            "output_refs": [],
            "authority_snapshot": normalized_authority,
            "idempotency_key": idempotency_key,
            "retry_count": 0,
            "recovery_action": None,
            "terminal_receipt": False,
            "created_at": now,
            "updated_at": now,
        }
        if fixture_origin is not None:
            state["fixture_origin"] = fixture_origin
        self._validate_state(state)
        transaction = self._append_state(states, state, operation="pipeline_job_create", actor=actor)
        return PipelineJobMutationResult(state, transaction)

    def transition(
        self,
        job_id: str,
        *,
        expected_state_id: str,
        expected_state_digest: str,
        status: str,
        current_node: str,
        wait_reason: str | None,
        output_refs: list[str],
        retry_increment: int,
        recovery_action: str | None,
        actor: str,
    ) -> PipelineJobMutationResult:
        job_id = validate_id(job_id, Namespace.JOB)
        expected_state_id = validate_id(expected_state_id, Namespace.JOB_STATE)
        if actor not in {"cli", "user"}:
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "pipeline-job-state", expected_state_id, "/actor", "Pipeline Job transition actor is invalid")
            )
        if not isinstance(retry_increment, int) or isinstance(retry_increment, bool) or retry_increment < 0:
            raise _request_error(expected_state_id, "retry increment must be a non-negative integer", "/retry_increment")
        if not isinstance(output_refs, list) or not all(isinstance(item, str) for item in output_refs):
            raise _request_error(expected_state_id, "output refs must be an array of record IDs", "/output_refs")
        if len(output_refs) != len(set(output_refs)):
            raise _request_error(expected_state_id, "output refs must be unique", "/output_refs")
        validate_wait_state(status, wait_reason, recovery_action)

        states = self._read_states()
        history = sorted(
            (item for item in states if item["job_id"] == job_id),
            key=lambda item: item["revision"],
        )
        if not history:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "pipeline-job-state", job_id, "/job_id", "Pipeline Job does not exist")
            )
        head = history[-1]
        expected = next((item for item in history if item["state_id"] == expected_state_id), None)
        if expected is None:
            raise _cas_error(expected_state_id, "expected Pipeline Job state does not exist")
        if canonical_digest(expected) != expected_state_digest:
            raise _cas_error(expected_state_id, "expected Pipeline Job state digest does not match")

        intended = _transition_intent(
            expected,
            status=status,
            current_node=current_node,
            wait_reason=wait_reason,
            output_refs=output_refs,
            retry_increment=retry_increment,
            recovery_action=recovery_action,
        )
        if head["state_id"] != expected_state_id:
            if head.get("predecessor") == {
                "state_id": expected_state_id,
                "state_digest": expected_state_digest,
            } and _state_transition_intent(head) == intended:
                return PipelineJobMutationResult(head, None)
            raise _cas_error(expected_state_id, "Pipeline Job current state changed before transition")

        validate_transition(head["status"], status)
        validate_running_progress(head, intended)
        state_id = self.id_allocator(Namespace.JOB_STATE)
        validate_id(state_id, Namespace.JOB_STATE)
        if state_id in {item["state_id"] for item in states}:
            raise ResearchKBError(
                Diagnostic(DUPLICATE_ID, "pipeline-job-state", state_id, "/state_id", "allocated Pipeline Job state ID is already in use")
            )
        now = timestamp(self.transactions.clock)
        state = {
            **{field: head[field] for field in (
                "schema_version",
                "job_id",
                "workspace_id",
                "requested_route",
                "requested_depth",
                "input_refs",
                "authority_snapshot",
                "idempotency_key",
                "created_at",
            )},
            "state_id": state_id,
            "revision": head["revision"] + 1,
            "predecessor": {
                "state_id": head["state_id"],
                "state_digest": canonical_digest(head),
            },
            "current_node": current_node,
            "status": status,
            "wait_reason": wait_reason,
            "output_refs": intended["output_refs"],
            "retry_count": intended["retry_count"],
            "recovery_action": recovery_action,
            "terminal_receipt": status in TERMINAL_STATUSES,
            "updated_at": now,
        }
        if "fixture_origin" in head:
            state["fixture_origin"] = head["fixture_origin"]
        self._validate_state(state)
        transaction = self._append_state(states, state, operation="pipeline_job_transition", actor=actor)
        return PipelineJobMutationResult(state, transaction)

    def cancel(
        self,
        job_id: str,
        *,
        expected_state_id: str,
        expected_state_digest: str,
        actor: str,
    ) -> PipelineJobMutationResult:
        head = self._current(job_id)
        return self.transition(
            job_id,
            expected_state_id=expected_state_id,
            expected_state_digest=expected_state_digest,
            status="cancelled",
            current_node=head["current_node"],
            wait_reason=None,
            output_refs=[],
            retry_increment=0,
            recovery_action=None,
            actor=actor,
        )

    def recover(
        self,
        job_id: str,
        *,
        expected_state_id: str,
        expected_state_digest: str,
        recovery_action: str,
        actor: str,
    ) -> PipelineJobMutationResult:
        head = self._current(job_id)
        return self.transition(
            job_id,
            expected_state_id=expected_state_id,
            expected_state_digest=expected_state_digest,
            status="recovering",
            current_node=head["current_node"],
            wait_reason="transaction_recovery",
            output_refs=[],
            retry_increment=1,
            recovery_action=recovery_action,
            actor=actor,
        )

    def list(
        self,
        *,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100:
            raise _request_error(None, "page size must be between 1 and 100", "/page_size")
        if cursor is not None:
            validate_id(cursor, Namespace.JOB)
        current = list(current_pipeline_states(self._read_states()))
        if cursor is not None:
            current = [item for item in current if item["job_id"] > cursor]
        page = current[:page_size]
        return {
            "status": "success",
            "interface_version": "1.0",
            "jobs": [self.summary(item) for item in page],
            "next_cursor": page[-1]["job_id"] if len(current) > page_size else None,
        }

    def show(self, job_id: str) -> dict[str, Any]:
        job_id = validate_id(job_id, Namespace.JOB)
        history = sorted(
            (item for item in self._read_states() if item["job_id"] == job_id),
            key=lambda item: item["revision"],
        )
        if not history:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "pipeline-job-state", job_id, "/job_id", "Pipeline Job does not exist")
            )
        return {
            "status": "success",
            "interface_version": "1.0",
            "current_state": history[-1],
            "history": history,
        }

    @staticmethod
    def summary(state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: state[key]
            for key in (
                "job_id",
                "state_id",
                "revision",
                "requested_route",
                "requested_depth",
                "current_node",
                "status",
                "wait_reason",
                "retry_count",
                "terminal_receipt",
                "updated_at",
            )
        }

    def _current(self, job_id: str) -> dict[str, Any]:
        job_id = validate_id(job_id, Namespace.JOB)
        current = next(
            (item for item in current_pipeline_states(self._read_states()) if item["job_id"] == job_id),
            None,
        )
        if current is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "pipeline-job-state", job_id, "/job_id", "Pipeline Job does not exist")
            )
        return current

    def _read_states(self) -> list[dict[str, Any]]:
        states = read_jsonl(
            self.layout.pipeline_jobs_path,
            record_kind="pipeline-job-state",
            id_field="state_id",
        )
        for state in states:
            diagnostics = validate_record("pipeline-job-state", state, actor="stored")
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
        diagnostics = pipeline_job_chain_diagnostics(states)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        return states

    def _validate_state(self, state: dict[str, Any]) -> None:
        diagnostics = validate_record("pipeline-job-state", state, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])

    def _append_state(
        self,
        states: list[dict[str, Any]],
        state: dict[str, Any],
        *,
        operation: str,
        actor: str,
    ) -> TransactionResult:
        proposed = [*states, state]
        diagnostics = pipeline_job_chain_diagnostics(proposed)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        target = self.layout.pipeline_jobs_path
        before_sha256 = file_sha256(target)

        def validate_temp(path: Path) -> None:
            temporary = read_jsonl(
                path,
                record_kind="pipeline-job-state",
                missing_ok=False,
                id_field="state_id",
            )
            chain_diagnostics = pipeline_job_chain_diagnostics(temporary)
            if chain_diagnostics:
                raise ResearchKBError(chain_diagnostics[0])
            entries = load_workspace_entries(
                self.layout,
                overrides={target: [("pipeline-job-state", item) for item in temporary]},
            )
            validate_workspace_entries(entries)

        return self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store="pipeline_jobs",
            operation=operation,
            actor=actor,
            input_refs=([] if state["predecessor"] is None else [state["predecessor"]["state_id"]]),
            output_refs=[state["state_id"]],
            validator=validate_temp,
            expected_before_sha256=before_sha256,
            job_id=state["job_id"],
        )


def _creation_intent(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "requested_route": state["requested_route"],
        "requested_depth": state["requested_depth"],
        "current_node": state["current_node"],
        "input_refs": state["input_refs"],
        "authority_snapshot": state["authority_snapshot"],
        "idempotency_key": state["idempotency_key"],
        "fixture_origin": state.get("fixture_origin"),
    }


def _normalize_authority_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"actor", "granted_operations", "captured_at"}:
        raise _request_error(None, "authority snapshot fields do not match the contract", "/authority_snapshot")
    if value.get("actor") != "user":
        raise _request_error(None, "Pipeline Job creation requires a user authority snapshot", "/authority_snapshot/actor")
    operations = value.get("granted_operations")
    if not isinstance(operations, list) or not all(isinstance(item, str) for item in operations):
        raise _request_error(None, "granted operations must be an array of slugs", "/authority_snapshot/granted_operations")
    if len(operations) != len(set(operations)):
        raise _request_error(None, "granted operations must be unique", "/authority_snapshot/granted_operations")
    return {
        "actor": "user",
        "granted_operations": sorted(operations),
        "captured_at": value.get("captured_at"),
    }


def _transition_intent(
    previous: Mapping[str, Any],
    *,
    status: str,
    current_node: str,
    wait_reason: str | None,
    output_refs: list[str],
    retry_increment: int,
    recovery_action: str | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "current_node": current_node,
        "wait_reason": wait_reason,
        "output_refs": sorted(set(previous["output_refs"]) | set(output_refs)),
        "retry_count": previous["retry_count"] + retry_increment,
        "recovery_action": recovery_action,
    }


def _state_transition_intent(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: state[key]
        for key in (
            "status",
            "current_node",
            "wait_reason",
            "output_refs",
            "retry_count",
            "recovery_action",
        )
    }


def _cas_error(state_id: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(WRITE_CONFLICT, "pipeline-job-state", state_id, "/predecessor", message)
    )


def _request_error(state_id: str | None, message: str, path: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "pipeline-job-state", state_id, path, message)
    )


__all__ = ["PipelineJobMutationResult", "PipelineJobService"]
