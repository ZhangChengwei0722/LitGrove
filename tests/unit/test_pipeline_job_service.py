from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_record
from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.identifiers import Namespace, allocate_id
from research_kb.pipeline_jobs import pipeline_job_chain_diagnostics
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.storage.json_io import read_json_document, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.runtime_helpers import make_runtime_workspace


NOW = datetime(2026, 7, 30, 1, 0, tzinfo=timezone.utc)
JOB_ID = "job_11111111-1111-4111-8111-111111111111"
STATE_IDS = (
    "jobstate_21111111-1111-4111-8111-111111111111",
    "jobstate_31111111-1111-4111-8111-111111111111",
    "jobstate_41111111-1111-4111-8111-111111111111",
    "jobstate_51111111-1111-4111-8111-111111111111",
)
EVENT_IDS = (
    "event_61111111-1111-4111-8111-111111111111",
    "event_71111111-1111-4111-8111-111111111111",
    "event_81111111-1111-4111-8111-111111111111",
    "event_91111111-1111-4111-8111-111111111111",
)


class InjectedCrash(BaseException):
    pass


class InterruptingTransactionManager(TransactionManager):
    def __init__(self, *args, interrupt_phase: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.interrupt_phase = interrupt_phase

    def promote_bytes(self, **kwargs):
        def interrupt(phase: str) -> None:
            if phase == self.interrupt_phase:
                raise InjectedCrash()

        return super().promote_bytes(**kwargs, phase_hook=interrupt)


def prepared_service(tmp_path):
    layout = make_runtime_workspace(tmp_path)
    state_ids = iter(STATE_IDS)
    event_ids = iter(EVENT_IDS)

    def allocate(namespace: Namespace) -> str:
        if namespace == Namespace.JOB:
            return JOB_ID
        if namespace == Namespace.JOB_STATE:
            return next(state_ids)
        return allocate_id(namespace, lambda: uuid.UUID("a1111111-1111-4111-8111-111111111111"))

    transactions = TransactionManager(
        layout,
        clock=lambda: NOW,
        event_id_factory=lambda: next(event_ids),
    )
    return layout, PipelineJobService(
        layout,
        transaction_manager=transactions,
        id_allocator=allocate,
    )


def create_job(service: PipelineJobService):
    return service.create(
        requested_route="local_source",
        requested_depth="semantic_gate",
        current_node="intake_preflight",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["register_by_reference"],
            "captured_at": "2026-07-30T01:00:00Z",
        },
        idempotency_key="synthetic-job-1",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )


def test_pipeline_job_append_only_chain_and_current_projection(tmp_path) -> None:
    layout, service = prepared_service(tmp_path)

    created = create_job(service)
    running = service.transition(
        JOB_ID,
        expected_state_id=created.state["state_id"],
        expected_state_digest=canonical_digest(created.state),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    waiting = service.transition(
        JOB_ID,
        expected_state_id=running.state["state_id"],
        expected_state_digest=canonical_digest(running.state),
        status="waiting_source",
        current_node="parse",
        wait_reason="source_missing",
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    states = read_jsonl(layout.pipeline_jobs_path, record_kind="pipeline-job-state", id_field="state_id")
    assert [item["revision"] for item in states] == [1, 2, 3]
    assert states[1]["predecessor"]["state_id"] == states[0]["state_id"]
    assert states[1]["predecessor"]["state_digest"] == canonical_digest(states[0])
    assert waiting.state["status"] == "waiting_source"
    assert waiting.state["wait_reason"] == "source_missing"

    shown = service.show(JOB_ID)
    assert shown["current_state"] == waiting.state
    assert [item["state_id"] for item in shown["history"]] == list(STATE_IDS[:3])
    listed = service.list(page_size=10)
    assert listed["jobs"] == [service.summary(waiting.state)]

    events = read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
    assert [item["job_id"] for item in events] == [JOB_ID, JOB_ID, JOB_ID]
    assert [item["output_refs"] for item in events] == [[STATE_IDS[0]], [STATE_IDS[1]], [STATE_IDS[2]]]
    assert read_json_document(layout.journal_path(EVENT_IDS[0]))["job_id"] == JOB_ID


def test_pipeline_job_create_and_transition_reruns_are_idempotent(tmp_path) -> None:
    layout, service = prepared_service(tmp_path)
    created = create_job(service)
    repeated_create = create_job(service)

    assert repeated_create.state == created.state
    assert repeated_create.transaction is None

    transition = service.transition(
        JOB_ID,
        expected_state_id=created.state["state_id"],
        expected_state_digest=canonical_digest(created.state),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    repeated_transition = service.transition(
        JOB_ID,
        expected_state_id=created.state["state_id"],
        expected_state_digest=canonical_digest(created.state),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    assert repeated_transition.state == transition.state
    assert repeated_transition.transaction is None
    assert len(read_jsonl(layout.pipeline_jobs_path, record_kind="pipeline-job-state")) == 2
    assert len(read_jsonl(layout.process_events_path, record_kind="process-event")) == 2


def test_pipeline_job_rejects_stale_cas_invalid_wait_and_invalid_agent_transition(tmp_path) -> None:
    layout, service = prepared_service(tmp_path)
    created = create_job(service)
    before = layout.pipeline_jobs_path.read_bytes()

    with pytest.raises(ResearchKBError, match="digest"):
        service.transition(
            JOB_ID,
            expected_state_id=created.state["state_id"],
            expected_state_digest="sha256_" + "0" * 64,
            status="running",
            current_node="registry",
            wait_reason=None,
            output_refs=[],
            retry_increment=0,
            recovery_action=None,
            actor="cli",
        )
    with pytest.raises(ResearchKBError, match="wait reason"):
        service.transition(
            JOB_ID,
            expected_state_id=created.state["state_id"],
            expected_state_digest=canonical_digest(created.state),
            status="waiting_source",
            current_node="parse",
            wait_reason="route_ambiguous",
            output_refs=[],
            retry_increment=0,
            recovery_action=None,
            actor="cli",
        )
    with pytest.raises(ResearchKBError, match="invalid Pipeline Job transition"):
        service.transition(
            JOB_ID,
            expected_state_id=created.state["state_id"],
            expected_state_digest=canonical_digest(created.state),
            status="waiting_agent",
            current_node="semantic_route",
            wait_reason=None,
            output_refs=[],
            retry_increment=0,
            recovery_action=None,
            actor="cli",
        )

    assert layout.pipeline_jobs_path.read_bytes() == before


def test_terminal_pipeline_job_revision_is_receipt_and_has_no_successor(tmp_path) -> None:
    _, service = prepared_service(tmp_path)
    created = create_job(service)
    running = service.transition(
        JOB_ID,
        expected_state_id=created.state["state_id"],
        expected_state_digest=canonical_digest(created.state),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    terminal = service.transition(
        JOB_ID,
        expected_state_id=running.state["state_id"],
        expected_state_digest=canonical_digest(running.state),
        status="completed",
        current_node="semantic_gate",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    assert terminal.state["terminal_receipt"] is True
    with pytest.raises(ResearchKBError, match="terminal"):
        service.transition(
            JOB_ID,
            expected_state_id=terminal.state["state_id"],
            expected_state_digest=canonical_digest(terminal.state),
            status="running",
            current_node="registry",
            wait_reason=None,
            output_refs=[],
            retry_increment=0,
            recovery_action=None,
            actor="cli",
        )


def test_pipeline_job_store_is_optional_for_existing_workspace(tmp_path) -> None:
    layout = make_runtime_workspace(tmp_path)

    assert not layout.pipeline_jobs_path.exists()
    assert PipelineJobService(layout).list(page_size=10)["jobs"] == []


def test_pipeline_job_record_validation_rejects_invalid_root_and_wait_reason(tmp_path) -> None:
    _, service = prepared_service(tmp_path)
    state = create_job(service).state
    state["status"] = "waiting_source"
    state["wait_reason"] = "route_ambiguous"

    diagnostics = validate_record("pipeline-job-state", state, actor="stored")

    assert any("root status" in item.message for item in diagnostics)
    assert any("wait reason" in item.message for item in diagnostics)


@pytest.mark.parametrize(
    ("status", "wait_reason"),
    [
        ("waiting_user", "ocr_required"),
        ("waiting_user", "layout_parse_required"),
        ("waiting_user", "reparse_required"),
        ("waiting_user", "source_adequacy_uncertain"),
        ("waiting_source", "source_incomplete"),
        ("waiting_source", "supplement_missing"),
    ],
)
def test_pipeline_job_accepts_specific_source_adequacy_wait_reasons(
    tmp_path,
    status,
    wait_reason,
) -> None:
    _, service = prepared_service(tmp_path)
    created = create_job(service)
    running = service.transition(
        JOB_ID,
        expected_state_id=created.state["state_id"],
        expected_state_digest=canonical_digest(created.state),
        status="running",
        current_node="source_adequacy",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    waiting = service.transition(
        JOB_ID,
        expected_state_id=running.state["state_id"],
        expected_state_digest=canonical_digest(running.state),
        status=status,
        current_node="source_adequacy",
        wait_reason=wait_reason,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    assert waiting.state["wait_reason"] == wait_reason


def test_cancel_preserves_outputs_and_recovery_increments_retry(tmp_path) -> None:
    _, service = prepared_service(tmp_path)
    created = create_job(service)
    running = service.transition(
        JOB_ID,
        expected_state_id=created.state["state_id"],
        expected_state_digest=canonical_digest(created.state),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[created.state["state_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    waiting = service.transition(
        JOB_ID,
        expected_state_id=running.state["state_id"],
        expected_state_digest=canonical_digest(running.state),
        status="waiting_source",
        current_node="parse",
        wait_reason="source_missing",
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    recovering = service.recover(
        JOB_ID,
        expected_state_id=waiting.state["state_id"],
        expected_state_digest=canonical_digest(waiting.state),
        recovery_action="reparse_after_source_restore",
        actor="cli",
    )

    assert recovering.state["output_refs"] == [created.state["state_id"]]
    assert recovering.state["retry_count"] == 1
    assert recovering.state["wait_reason"] == "transaction_recovery"


def test_cancel_is_terminal_and_preserves_outputs(tmp_path) -> None:
    _, service = prepared_service(tmp_path)
    created = create_job(service)
    running = service.transition(
        JOB_ID,
        expected_state_id=created.state["state_id"],
        expected_state_digest=canonical_digest(created.state),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[created.state["state_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    cancelled = service.cancel(
        JOB_ID,
        expected_state_id=running.state["state_id"],
        expected_state_digest=canonical_digest(running.state),
        actor="user",
    )

    assert cancelled.state["status"] == "cancelled"
    assert cancelled.state["terminal_receipt"] is True
    assert cancelled.state["output_refs"] == [created.state["state_id"]]


def test_guardian_detects_missing_correlated_job_event(tmp_path) -> None:
    layout, service = prepared_service(tmp_path)
    created = create_job(service)
    layout.process_events_path.write_bytes(serialize_jsonl([]))

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    assert any(
        finding["record_ref"] == created.state["state_id"]
        and "correlated success event" in finding["message"]
        for finding in result.report["findings"]
    )


def test_guardian_detects_tampered_pipeline_job_chain(tmp_path) -> None:
    layout, service = prepared_service(tmp_path)
    created = create_job(service)
    running = service.transition(
        JOB_ID,
        expected_state_id=created.state["state_id"],
        expected_state_digest=canonical_digest(created.state),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    states = read_jsonl(layout.pipeline_jobs_path, record_kind="pipeline-job-state", id_field="state_id")
    states[1]["predecessor"]["state_digest"] = "0" * 64
    layout.pipeline_jobs_path.write_bytes(serialize_jsonl(states))

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    assert any(
        finding["record_ref"] == running.state["state_id"]
        and "predecessor" in finding["message"]
        for finding in result.report["findings"]
    )


@pytest.mark.parametrize(
    ("interrupt_phase", "expected_action", "state_exists", "event_result"),
    [
        ("prepared", "append_missing_failure_event", False, "failure"),
        ("target_replaced", "append_missing_success_event", True, "success"),
    ],
)
def test_pipeline_job_transaction_recovery_converges(
    tmp_path,
    interrupt_phase,
    expected_action,
    state_exists,
    event_result,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    manager = InterruptingTransactionManager(
        layout,
        interrupt_phase=interrupt_phase,
        clock=lambda: NOW,
        event_id_factory=lambda: EVENT_IDS[0],
    )

    def allocate(namespace: Namespace) -> str:
        return JOB_ID if namespace == Namespace.JOB else STATE_IDS[0]

    service = PipelineJobService(
        layout,
        transaction_manager=manager,
        id_allocator=allocate,
    )
    with pytest.raises(InjectedCrash):
        create_job(service)

    assert manager.recover(dry_run=True) == [
        {"event_id": EVENT_IDS[0], "action": expected_action}
    ]
    manager.recover(dry_run=False)
    events = read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
    assert events == [
        {
            "schema_version": "1.0",
            "event_id": EVENT_IDS[0],
            "operation": "pipeline_job_create",
            "actor": "user",
            "result": event_result,
            "input_refs": [],
            "output_refs": [STATE_IDS[0]] if state_exists else [],
            "created_at": "2026-07-30T01:00:00Z",
            "job_id": JOB_ID,
        }
    ]
    assert layout.pipeline_jobs_path.exists() is state_exists
    if state_exists:
        assert PipelineJobService(layout).show(JOB_ID)["current_state"]["state_id"] == STATE_IDS[0]
    assert GuardianService(layout).check().report["status"] == "success"


def test_running_progress_transition_retains_outputs_without_changing_wait_semantics(tmp_path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = PipelineJobService(layout)
    created = create_job(service).state
    running = service.transition(
        created["job_id"],
        expected_state_id=created["state_id"],
        expected_state_digest=canonical_digest(created),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[created["state_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    ).state

    progressed = service.transition(
        running["job_id"],
        expected_state_id=running["state_id"],
        expected_state_digest=canonical_digest(running),
        status="running",
        current_node="source_association",
        wait_reason=None,
        output_refs=[running["state_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    ).state

    assert progressed["status"] == "running"
    assert progressed["wait_reason"] is None
    assert progressed["retry_count"] == running["retry_count"]
    assert set(progressed["output_refs"]) == {
        created["state_id"],
        running["state_id"],
    }


@pytest.mark.parametrize(
    ("current_node", "include_new_output", "retry_increment", "expected_path"),
    [
        ("registry", True, 0, "/current_node"),
        ("source_association", False, 0, "/output_refs"),
        ("source_association", True, 1, "/retry_count"),
    ],
)
def test_running_progress_rejects_non_progress_or_retry_changes(
    tmp_path,
    current_node,
    include_new_output,
    retry_increment,
    expected_path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = PipelineJobService(layout)
    created = create_job(service).state
    running = service.transition(
        created["job_id"],
        expected_state_id=created["state_id"],
        expected_state_digest=canonical_digest(created),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[created["state_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    ).state

    with pytest.raises(ResearchKBError) as rejected:
        service.transition(
            running["job_id"],
            expected_state_id=running["state_id"],
            expected_state_digest=canonical_digest(running),
            status="running",
            current_node=current_node,
            wait_reason=None,
            output_refs=[running["state_id"]] if include_new_output else [],
            retry_increment=retry_increment,
            recovery_action=None,
            actor="cli",
        )

    assert rejected.value.diagnostic.json_path == expected_path


def test_pipeline_job_chain_diagnostics_rejects_tampered_running_progress(tmp_path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = PipelineJobService(layout)
    created = create_job(service).state
    running = service.transition(
        created["job_id"],
        expected_state_id=created["state_id"],
        expected_state_digest=canonical_digest(created),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[created["state_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    ).state
    progressed = service.transition(
        running["job_id"],
        expected_state_id=running["state_id"],
        expected_state_digest=canonical_digest(running),
        status="running",
        current_node="source_association",
        wait_reason=None,
        output_refs=[running["state_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    ).state
    history = PipelineJobService(layout).show(created["job_id"])["history"]
    history[-1] = {**progressed, "current_node": running["current_node"]}

    diagnostics = pipeline_job_chain_diagnostics(history)

    assert any(item.json_path == "/current_node" for item in diagnostics)
