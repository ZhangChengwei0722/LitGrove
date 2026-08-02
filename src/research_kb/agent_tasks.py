from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import INCOMPLETE_TRANSACTION, SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError


TERMINAL_STATUSES = frozenset({"revision_requested", "superseded", "rejected", "approved", "cancelled"})
ALLOWED_TRANSITIONS = {
    "created": frozenset({"leased", "superseded", "cancelled"}),
    "leased": frozenset({"submitted", "superseded", "cancelled"}),
    "submitted": frozenset({"revision_requested", "superseded", "rejected", "approved"}),
}
_STABLE_FIELDS = (
    "schema_version",
    "task_id",
    "workspace_id",
    "task_kind",
    "result_contract",
    "privacy_registry_version",
    "executor_id",
    "execution_scope",
    "effective_content_classes",
    "input_basis",
    "input_basis_digest",
    "idempotency_key",
    "lineage",
    "created_at",
)


def validate_task_state(state: Mapping[str, Any]) -> None:
    if state.get("input_basis_digest") != canonical_digest(state.get("input_basis")):
        raise _task_error("input basis digest does not match the exact input basis", "/input_basis_digest")
    classes = state.get("effective_content_classes")
    if isinstance(classes, list) and classes != sorted(classes):
        raise _task_error("effective content classes are not deterministically ordered", "/effective_content_classes")
    status = str(state.get("status"))
    terminal = status in TERMINAL_STATUSES
    if state.get("terminal_receipt") is not terminal:
        raise _task_error("terminal receipt flag does not match Agent Task status", "/terminal_receipt")
    if state.get("revision") == 1:
        if state.get("predecessor") is not None:
            raise _task_error("Agent Task root must not have a predecessor state", "/predecessor")
        if status != "created":
            raise _task_error("Agent Task root status must be created", "/status")
        if state.get("lease") is not None or state.get("staged_result") is not None or state.get("decision") is not None:
            raise _task_error("Agent Task root cannot contain lease, result or decision data", "/status")
    if status == "leased":
        if state.get("lease") is None or state.get("staged_result") is not None or state.get("decision") is not None:
            raise _task_error("leased Agent Task requires only a lease", "/status")
    if status == "submitted":
        if state.get("lease") is None or state.get("staged_result") is None or state.get("decision") is not None:
            raise _task_error("submitted Agent Task requires lease and staged result", "/status")
    if status in {"revision_requested", "rejected", "approved"}:
        decision = state.get("decision")
        if state.get("lease") is None or state.get("staged_result") is None or not isinstance(decision, dict):
            raise _task_error("decided Agent Task requires lease, staged result and decision", "/status")
        if decision.get("action") != status:
            raise _task_error("Agent Task decision action does not match status", "/decision/action")
        if status == "revision_requested" and decision.get("successor_task_id") is None:
            raise _task_error("revision decision requires a successor task", "/decision/successor_task_id")
        if status != "revision_requested" and decision.get("successor_task_id") is not None:
            raise _task_error("non-revision decision cannot name a successor task", "/decision/successor_task_id")
        no_job_approval = status == "approved" and state.get("task_kind") in {
            "knowledge_query_report",
            "organization_proposal",
        }
        if status == "approved" and not no_job_approval and decision.get("applied_job_state_id") is None:
            raise _task_error("approved scientific or route decision requires an applied Job state", "/decision/applied_job_state_id")
        if no_job_approval and decision.get("applied_job_state_id") is not None:
            raise _task_error("approved direct Task cannot name an applied Job state", "/decision/applied_job_state_id")
        if status != "approved" and decision.get("applied_job_state_id") is not None:
            raise _task_error("non-approval decision cannot name an applied Job state", "/decision/applied_job_state_id")
        if status == "revision_requested":
            feedback = decision.get("feedback")
            if not isinstance(feedback, str) or not feedback.strip():
                raise _task_error("revision decision requires non-empty feedback", "/decision/feedback")
            if decision.get("reason_code") is not None:
                raise _task_error("revision decision cannot carry a reason code", "/decision/reason_code")
        elif decision.get("feedback") is not None:
            raise _task_error("non-revision decision cannot carry feedback", "/decision/feedback")
        if status == "rejected" and decision.get("reason_code") != "user_rejected":
            raise _task_error("rejected decision requires the user-rejected reason", "/decision/reason_code")
        if status == "approved":
            expected_reason = {
                "primary_semantic_processing": "primary_bundle_committed",
                "review_semantic_processing": "review_bundle_committed",
                "knowledge_query_report": "report_accepted",
                "organization_proposal": "organization_revision_committed",
            }.get(state.get("task_kind"), "route_confirmed")
            if decision.get("reason_code") != expected_reason:
                raise _task_error("approved decision reason does not match the Task kind", "/decision/reason_code")
    if status == "superseded":
        decision = state.get("decision")
        if not isinstance(decision, dict):
            raise _task_error("superseded Agent Task requires one decision", "/status")
        if decision.get("action") != "superseded" or decision.get("reason_code") != "input_refreshed":
            raise _task_error("superseded Agent Task requires the input-refreshed decision", "/decision")
        if decision.get("successor_task_id") is None or decision.get("applied_job_state_id") is not None:
            raise _task_error("superseded Agent Task requires only a successor Task reference", "/decision")
        if decision.get("feedback") is not None:
            raise _task_error("superseded Agent Task cannot carry feedback", "/decision/feedback")
    result = state.get("staged_result")
    if isinstance(result, dict):
        if result.get("task_id") != state.get("task_id"):
            raise _task_error("staged result belongs to another Agent Task", "/staged_result/task_id")
        if result.get("input_basis_digest") != state.get("input_basis_digest"):
            raise _task_error("staged result input basis does not match Agent Task", "/staged_result/input_basis_digest")


def validate_transition(previous_status: str, status: str) -> None:
    if previous_status in TERMINAL_STATUSES:
        raise _task_error("terminal Agent Task state cannot have a successor", "/status")
    if status not in ALLOWED_TRANSITIONS.get(previous_status, frozenset()):
        raise _task_error(f"invalid Agent Task transition from {previous_status} to {status}", "/status")


def agent_task_chain_diagnostics(states: Iterable[dict[str, Any]]) -> list[Diagnostic]:
    state_list = list(states)
    diagnostics: list[Diagnostic] = []
    state_ids: set[str] = set()
    task_ids: set[str] = set()
    idempotency_tasks: dict[str, str] = {}
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for state in state_list:
        state_id = str(state.get("state_id", ""))
        task_id = str(state.get("task_id", ""))
        if state_id in state_ids:
            diagnostics.append(_chain_diagnostic(state, "/state_id", "duplicate Agent Task state ID"))
        state_ids.add(state_id)
        task_ids.add(task_id)
        by_task[task_id].append(state)
        key = state.get("idempotency_key")
        if isinstance(key, str) and key:
            represented = idempotency_tasks.setdefault(key, task_id)
            if represented != task_id:
                diagnostics.append(
                    _chain_diagnostic(state, "/idempotency_key", "Agent Task idempotency key is bound to another task")
                )

    for revisions in by_task.values():
        ordered = sorted(revisions, key=lambda item: (item.get("revision", 0), item.get("state_id", "")))
        actual = [item.get("revision") for item in ordered]
        if actual != list(range(1, len(ordered) + 1)):
            diagnostics.append(_chain_diagnostic(ordered[0], "/revision", "Agent Task revisions must be contiguous from one"))
            continue
        root = ordered[0]
        try:
            validate_task_state(root)
        except ResearchKBError as error:
            diagnostics.append(_from_error(root, error))
        for index, state in enumerate(ordered[1:], start=1):
            previous = ordered[index - 1]
            expected = {
                "state_id": previous.get("state_id"),
                "state_digest": canonical_digest(previous),
            }
            if state.get("predecessor") != expected:
                diagnostics.append(_chain_diagnostic(state, "/predecessor", "Agent Task predecessor does not match prior revision"))
            for field in _STABLE_FIELDS:
                if state.get(field) != root.get(field):
                    diagnostics.append(_chain_diagnostic(state, f"/{field}", f"Agent Task field {field} changed after creation"))
            if previous.get("lease") is not None and state.get("lease") != previous.get("lease"):
                diagnostics.append(_chain_diagnostic(state, "/lease", "Agent Task lease changed after issue"))
            if previous.get("staged_result") is not None and state.get("staged_result") != previous.get("staged_result"):
                diagnostics.append(_chain_diagnostic(state, "/staged_result", "Agent Task staged result changed after submission"))
            try:
                validate_transition(str(previous.get("status")), str(state.get("status")))
                validate_task_state(state)
            except ResearchKBError as error:
                diagnostics.append(_from_error(state, error))
            if _parse_timestamp(state.get("updated_at")) < _parse_timestamp(previous.get("updated_at")):
                diagnostics.append(_chain_diagnostic(state, "/updated_at", "Agent Task update time moved backwards"))

    for state in state_list:
        lineage = state.get("lineage")
        if state.get("revision") == 1 and isinstance(lineage, dict):
            predecessor = lineage.get("predecessor_task_id")
            if predecessor not in task_ids:
                diagnostics.append(_chain_diagnostic(state, "/lineage/predecessor_task_id", "Agent Task lineage predecessor is unresolved"))
            elif predecessor == state.get("task_id"):
                diagnostics.append(_chain_diagnostic(state, "/lineage/predecessor_task_id", "Agent Task cannot succeed itself"))
            else:
                predecessor_states = sorted(
                    by_task.get(str(predecessor), []),
                    key=lambda item: (item.get("revision", 0), item.get("state_id", "")),
                )
                predecessor_head = predecessor_states[-1] if predecessor_states else None
                result_lineage = "predecessor_result_digest" in lineage
                expected_status = "revision_requested" if result_lineage else "superseded"
                if (
                    predecessor_head is None
                    or predecessor_head.get("status") != expected_status
                    or predecessor_head.get("decision", {}).get("successor_task_id") != state.get("task_id")
                ):
                    diagnostics.append(
                        _chain_diagnostic(
                            state,
                            "/lineage/predecessor_task_id",
                            "Agent Task successor lineage is not reciprocated by its predecessor",
                        )
                    )
                elif result_lineage and canonical_digest(predecessor_head.get("staged_result")) != lineage.get("predecessor_result_digest"):
                    diagnostics.append(
                        _chain_diagnostic(
                            state,
                            "/lineage/predecessor_result_digest",
                            "Agent Task successor result digest does not match its predecessor",
                        )
                    )
                elif result_lineage and predecessor_head.get("decision", {}).get("feedback") != lineage.get("feedback"):
                    diagnostics.append(
                        _chain_diagnostic(
                            state,
                            "/lineage/feedback",
                            "Agent Task successor feedback does not match its predecessor decision",
                        )
                    )
                elif not result_lineage and (
                    (predecessor_head.get("lease") or {}).get(
                        "handoff_digest",
                        predecessor_head.get("input_basis_digest"),
                    )
                    != lineage.get("predecessor_handoff_digest")
                ):
                    diagnostics.append(
                        _chain_diagnostic(
                            state,
                            "/lineage/predecessor_handoff_digest",
                            "Agent Task refreshed successor handoff digest does not match its predecessor",
                        )
                    )

    lineage_predecessors: dict[str, str] = {}
    for task_id, revisions in by_task.items():
        if not revisions:
            continue
        root = min(revisions, key=lambda item: item.get("revision", 0))
        if isinstance(root.get("lineage"), dict):
            lineage_predecessors[task_id] = root["lineage"]["predecessor_task_id"]
    for task_id in sorted(lineage_predecessors):
        seen: set[str] = set()
        current = task_id
        while current in lineage_predecessors:
            if current in seen:
                root = min(by_task[task_id], key=lambda item: item.get("revision", 0))
                diagnostics.append(
                    _chain_diagnostic(root, "/lineage/predecessor_task_id", "Agent Task lineage contains a cycle")
                )
                break
            seen.add(current)
            current = lineage_predecessors[current]

    return _deduplicate(diagnostics)


def current_agent_task_states(states: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    state_list = list(states)
    diagnostics = agent_task_chain_diagnostics(state_list)
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    current: dict[str, dict[str, Any]] = {}
    for state in state_list:
        existing = current.get(state["task_id"])
        if existing is None or state["revision"] > existing["revision"]:
            current[state["task_id"]] = state
    return tuple(sorted(current.values(), key=lambda item: item["task_id"]))


def _task_error(message: str, path: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(SCHEMA_VALIDATION_FAILED, "agent-task-state", None, path, message))


def _chain_diagnostic(state: Mapping[str, Any], path: str, message: str) -> Diagnostic:
    return Diagnostic(INCOMPLETE_TRANSACTION, "agent-task-state", state.get("state_id"), path, message)


def _from_error(state: Mapping[str, Any], error: ResearchKBError) -> Diagnostic:
    return Diagnostic(
        INCOMPLETE_TRANSACTION,
        "agent-task-state",
        state.get("state_id"),
        error.diagnostic.json_path,
        error.diagnostic.message,
    )


def _deduplicate(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str, str | None, str, str]] = set()
    result: list[Diagnostic] = []
    for item in diagnostics:
        key = (item.code, item.record_id, item.json_path, item.message)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _parse_timestamp(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATUSES",
    "agent_task_chain_diagnostics",
    "current_agent_task_states",
    "validate_task_state",
    "validate_transition",
]
