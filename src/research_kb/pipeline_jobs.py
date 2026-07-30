from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import INCOMPLETE_TRANSACTION, SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError


TERMINAL_STATUSES = frozenset(
    {"completed", "completed_with_findings", "failed", "cancelled"}
)
WAIT_REASONS_BY_STATUS = {
    "waiting_user": frozenset(
        {
            "authority_required",
            "route_ambiguous",
            "source_selection_required",
            "ocr_required",
            "layout_parse_required",
            "reparse_required",
            "source_adequacy_uncertain",
        }
    ),
    "waiting_source": frozenset(
        {
            "source_missing",
            "source_inaccessible",
            "source_changed",
            "parse_failed",
            "source_incomplete",
            "supplement_missing",
            "source_adequacy_inadequate",
            "source_adequacy_stale",
        }
    ),
    "paused": frozenset({"user_paused"}),
    "recovering": frozenset({"transaction_recovery"}),
}
ALLOWED_TRANSITIONS = {
    "created": frozenset(
        {"running", "waiting_user", "waiting_source", "paused", "failed", "cancelled"}
    ),
    "running": frozenset(
        {
            "waiting_user",
            "waiting_agent",
            "waiting_source",
            "paused",
            "recovering",
            "completed",
            "completed_with_findings",
            "failed",
            "cancelled",
        }
    ),
    "waiting_user": frozenset({"running", "paused", "recovering", "failed", "cancelled"}),
    "waiting_agent": frozenset({"running", "paused", "recovering", "failed", "cancelled"}),
    "waiting_source": frozenset({"running", "paused", "recovering", "failed", "cancelled"}),
    "paused": frozenset({"running", "recovering", "failed", "cancelled"}),
    "recovering": frozenset(
        {
            "running",
            "waiting_user",
            "waiting_source",
            "paused",
            "completed",
            "completed_with_findings",
            "failed",
            "cancelled",
        }
    ),
}

_STABLE_FIELDS = (
    "workspace_id",
    "requested_route",
    "requested_depth",
    "input_refs",
    "authority_snapshot",
    "idempotency_key",
    "created_at",
    "fixture_origin",
)


def validate_wait_state(status: str, wait_reason: str | None, recovery_action: str | None) -> None:
    allowed = WAIT_REASONS_BY_STATUS.get(status)
    if allowed is None:
        if wait_reason is not None:
            raise _job_error("wait reason is not allowed for this status", "/wait_reason")
    elif wait_reason not in allowed:
        raise _job_error("wait reason is incompatible with the requested status", "/wait_reason")

    if status == "recovering":
        if recovery_action is None:
            raise _job_error("recovering status requires a recovery action", "/recovery_action")
    elif recovery_action is not None:
        raise _job_error("recovery action is allowed only while recovering", "/recovery_action")


def validate_transition(previous_status: str, status: str) -> None:
    if previous_status in TERMINAL_STATUSES:
        raise _job_error("terminal Pipeline Job state cannot have a successor", "/status")
    if status not in ALLOWED_TRANSITIONS.get(previous_status, frozenset()):
        raise _job_error(
            f"invalid Pipeline Job transition from {previous_status} to {status}",
            "/status",
        )


def pipeline_job_chain_diagnostics(states: Iterable[dict[str, Any]]) -> list[Diagnostic]:
    state_list = list(states)
    diagnostics: list[Diagnostic] = []
    state_ids: set[str] = set()
    by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    idempotency_jobs: dict[str, str] = {}

    for state in state_list:
        state_id = str(state.get("state_id", ""))
        job_id = str(state.get("job_id", ""))
        if state_id in state_ids:
            diagnostics.append(_chain_diagnostic(state, "/state_id", "duplicate Pipeline Job state ID"))
        state_ids.add(state_id)
        by_job[job_id].append(state)
        key = state.get("idempotency_key")
        if isinstance(key, str) and key:
            represented = idempotency_jobs.setdefault(key, job_id)
            if represented != job_id:
                diagnostics.append(
                    _chain_diagnostic(
                        state,
                        "/idempotency_key",
                        "Pipeline Job idempotency key is bound to another Job",
                    )
                )

    for job_id, revisions in by_job.items():
        ordered = sorted(revisions, key=lambda item: (item.get("revision", 0), item.get("state_id", "")))
        if not ordered:
            continue
        expected_revisions = list(range(1, len(ordered) + 1))
        actual_revisions = [item.get("revision") for item in ordered]
        if actual_revisions != expected_revisions:
            diagnostics.append(
                _chain_diagnostic(
                    ordered[0],
                    "/revision",
                    "Pipeline Job revisions must be unique and contiguous from one",
                )
            )
            continue
        root = ordered[0]
        if root.get("predecessor") is not None:
            diagnostics.append(_chain_diagnostic(root, "/predecessor", "Pipeline Job root must not have a predecessor"))
        if root.get("status") != "created":
            diagnostics.append(_chain_diagnostic(root, "/status", "Pipeline Job root status must be created"))
        if root.get("retry_count") != 0:
            diagnostics.append(_chain_diagnostic(root, "/retry_count", "Pipeline Job root retry count must be zero"))
        if root.get("output_refs") != []:
            diagnostics.append(_chain_diagnostic(root, "/output_refs", "Pipeline Job root cannot contain outputs"))
        if root.get("updated_at") != root.get("created_at"):
            diagnostics.append(_chain_diagnostic(root, "/updated_at", "Pipeline Job root timestamps must match"))
        try:
            validate_wait_state(
                str(root.get("status")),
                root.get("wait_reason"),
                root.get("recovery_action"),
            )
        except ResearchKBError as error:
            diagnostics.append(_from_error(root, error))

        for index, state in enumerate(ordered):
            terminal = state.get("status") in TERMINAL_STATUSES
            if state.get("terminal_receipt") is not terminal:
                diagnostics.append(
                    _chain_diagnostic(
                        state,
                        "/terminal_receipt",
                        "terminal receipt flag does not match Pipeline Job status",
                    )
                )
            if index == 0:
                continue
            previous = ordered[index - 1]
            predecessor = state.get("predecessor")
            expected = {
                "state_id": previous.get("state_id"),
                "state_digest": canonical_digest(previous),
            }
            if predecessor != expected:
                diagnostics.append(
                    _chain_diagnostic(
                        state,
                        "/predecessor",
                        "Pipeline Job predecessor ID or digest does not match the prior revision",
                    )
                )
            for field in _STABLE_FIELDS:
                if state.get(field) != root.get(field):
                    diagnostics.append(
                        _chain_diagnostic(
                            state,
                            f"/{field}",
                            f"Pipeline Job field {field} changed after creation",
                        )
                    )
            if not set(previous.get("output_refs", [])).issubset(state.get("output_refs", [])):
                diagnostics.append(
                    _chain_diagnostic(
                        state,
                        "/output_refs",
                        "Pipeline Job output refs cannot discard prior committed outputs",
                    )
                )
            try:
                validate_transition(str(previous.get("status")), str(state.get("status")))
                validate_wait_state(
                    str(state.get("status")),
                    state.get("wait_reason"),
                    state.get("recovery_action"),
                )
            except ResearchKBError as error:
                diagnostics.append(_from_error(state, error))
            if state.get("retry_count", -1) < previous.get("retry_count", 0):
                diagnostics.append(
                    _chain_diagnostic(
                        state,
                        "/retry_count",
                        "Pipeline Job retry count cannot decrease",
                    )
                )
            if _parse_timestamp(state.get("updated_at")) < _parse_timestamp(previous.get("updated_at")):
                diagnostics.append(
                    _chain_diagnostic(
                        state,
                        "/updated_at",
                        "Pipeline Job update time cannot move backwards",
                    )
                )

    return _deduplicate(diagnostics)


def current_pipeline_states(states: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    state_list = list(states)
    diagnostics = pipeline_job_chain_diagnostics(state_list)
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    current: dict[str, dict[str, Any]] = {}
    for state in state_list:
        existing = current.get(state["job_id"])
        if existing is None or state["revision"] > existing["revision"]:
            current[state["job_id"]] = state
    return tuple(sorted(current.values(), key=lambda item: item["job_id"]))


def _job_error(message: str, path: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "pipeline-job-state", None, path, message)
    )


def _chain_diagnostic(state: dict[str, Any], path: str, message: str) -> Diagnostic:
    return Diagnostic(
        INCOMPLETE_TRANSACTION,
        "pipeline-job-state",
        state.get("state_id"),
        path,
        message,
    )


def _from_error(state: dict[str, Any], error: ResearchKBError) -> Diagnostic:
    return Diagnostic(
        INCOMPLETE_TRANSACTION,
        "pipeline-job-state",
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
    "WAIT_REASONS_BY_STATUS",
    "current_pipeline_states",
    "pipeline_job_chain_diagnostics",
    "validate_transition",
    "validate_wait_state",
]
