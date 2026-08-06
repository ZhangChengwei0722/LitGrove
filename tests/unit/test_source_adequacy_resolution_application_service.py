from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services import (
    AgentTaskApplicationService,
    DeterministicIntakeApplicationService,
    SourceAdequacyResolutionApplicationService,
    WorkspaceSessionService,
)
from research_kb.services.pipeline_job import PipelineJobService
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_agent_task_application_service import APPROVED_CLASSES, P4B_POLICY, P4C_POLICY


DECISION_TIME = datetime(2026, 8, 6, 23, 0, tzinfo=timezone.utc)


def _expected(task: dict[str, object]) -> dict[str, str]:
    return {"state_id": str(task["state_id"]), "state_digest": str(task["state_digest"])}


def _build_uncertain_task(tmp_path: Path, *, route: str):
    policy = P4B_POLICY if route == "primary" else P4C_POLICY
    requested_operation = "basic_paper_card" if route == "primary" else "basic_review_memory"
    task_kind = "primary_semantic_processing" if route == "primary" else "review_semantic_processing"
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
        agent_policy=policy,
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    source = write_synthetic_pdf(
        tmp_path / f"source-adequacy-{route}.pdf",
        ["Synthetic continuous text is deliberately parsed with a conservative reading-order profile."],
    )
    payload = source.read_bytes()
    intake = DeterministicIntakeApplicationService().start_upload(
        session,
        io.BytesIO(payload),
        {
            "idempotency_key": f"source-adequacy-{route}-upload",
            "requested_operation": requested_operation,
            "document_route": route,
            "route_reason": None,
            "bibliography": {
                "title": f"Synthetic {route.title()} Source Adequacy Study",
                "authors": ["Fixture Author"],
                "year": 2026,
                "doi": None,
            },
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
            "expected_size_bytes": len(payload),
        },
    )
    agent = AgentTaskApplicationService()
    created = agent.create_from_pipeline(
        session,
        intake["pipeline"]["job_id"],
        {
            "paper_id": intake["paper_id"],
            "task_kind": task_kind,
            "executor_id": "codex_cli",
            "approved_content_classes": APPROVED_CLASSES,
            "idempotency_key": f"source-adequacy-{route}-task",
        },
    )
    return layout, session, agent, created["task"]


@pytest.mark.parametrize("route", ["primary", "review"])
def test_resolution_context_and_source_review_are_private_and_current(tmp_path: Path, route: str) -> None:
    _, session, _, task = _build_uncertain_task(tmp_path, route=route)
    service = SourceAdequacyResolutionApplicationService(clock=lambda: DECISION_TIME)

    context = service.show_context(session, task["task_id"])

    assert context["resolution_state"] == "review_required"
    assert context["machine_status"] == "uncertain"
    assert context["hard_failure"] is False
    assert context["allowed_actions"] == ["accept_uncertainty", "remediation_required"]
    encoded = json.dumps(context, sort_keys=True)
    assert "source_ref" not in encoded
    assert "fingerprint" not in encoded
    assert "relative_path" not in encoded

    prepared = service.prepare_source_review(session, task["task_id"], _expected(task))
    assert prepared.descriptor["media_type"] == "application/pdf"
    assert prepared.descriptor["persistent_writes"] == 0
    assert "source_relative_path" not in repr(prepared.handle)
    with service.open_source_review(session, prepared.handle) as opened:
        assert opened.stream.read(5) == b"%" + b"PDF-"


def test_accept_decision_requires_closed_attestation_and_refreshes_primary_task(tmp_path: Path) -> None:
    _, session, agent, task = _build_uncertain_task(tmp_path, route="primary")
    service = SourceAdequacyResolutionApplicationService(clock=lambda: DECISION_TIME)
    context = service.show_context(session, task["task_id"])

    with pytest.raises(ResearchKBError):
        service.decide(
            session,
            task["task_id"],
            _expected(task),
            context["basis_profile_id"],
            "accept_uncertainty",
        )

    decided = service.decide(
        session,
        task["task_id"],
        _expected(task),
        context["basis_profile_id"],
        "accept_uncertainty",
        "reading_order_reviewed",
    )
    replay = service.decide(
        session,
        task["task_id"],
        _expected(task),
        context["basis_profile_id"],
        "accept_uncertainty",
        "reading_order_reviewed",
    )

    assert decided["resolution_state"] == "accepted_refresh_required"
    assert decided["persistent_writes"] == 1
    assert replay["successor_profile_id"] == decided["successor_profile_id"]
    assert replay["persistent_writes"] == 0

    refreshed = agent.refresh_primary_task(session, task["task_id"], _expected(task))
    assert refreshed["successor_task"]["status"] == "created"
    assert refreshed["successor_task"]["lineage"]["predecessor_task_id"] == task["task_id"]
    assert service.show_context(session, task["task_id"])["resolution_state"] == "not_required"
    lost_response_replay = service.decide(
        session,
        task["task_id"],
        _expected(task),
        context["basis_profile_id"],
        "accept_uncertainty",
        "reading_order_reviewed",
    )
    refresh_replay = agent.refresh_primary_task(session, task["task_id"], _expected(task))
    assert lost_response_replay["persistent_writes"] == 0
    assert refresh_replay["persistent_writes"] == 0
    assert refresh_replay["successor_task"] == refreshed["successor_task"]


@pytest.mark.parametrize("route", ["primary", "review"])
def test_accept_decision_profile_is_consumed_by_refreshed_pipeline_job(
    tmp_path: Path,
    route: str,
) -> None:
    layout, session, agent, task = _build_uncertain_task(tmp_path, route=route)
    service = SourceAdequacyResolutionApplicationService(clock=lambda: DECISION_TIME)
    context = service.show_context(session, task["task_id"])
    decided = service.decide(
        session,
        task["task_id"],
        _expected(task),
        context["basis_profile_id"],
        "accept_uncertainty",
        "reading_order_reviewed",
    )

    if route == "primary":
        agent.refresh_primary_task(session, task["task_id"], _expected(task))
    else:
        agent.refresh_review_task(session, task["task_id"], _expected(task))

    current_job = PipelineJobService(layout).show(task["job_id"])["current_state"]
    assert decided["successor_profile_id"] in current_job["output_refs"]
    assert not any(
        item["code"] == "RKBC-018"
        and item["record_ref"] == decided["successor_profile_id"]
        for item in GuardianService(layout).check().report["findings"]
    )


def test_remediation_decision_routes_review_job_without_accept_attestation(tmp_path: Path) -> None:
    layout, session, agent, task = _build_uncertain_task(tmp_path, route="review")
    service = SourceAdequacyResolutionApplicationService(clock=lambda: DECISION_TIME)
    context = service.show_context(session, task["task_id"])

    with pytest.raises(ResearchKBError):
        service.decide(
            session,
            task["task_id"],
            _expected(task),
            context["basis_profile_id"],
            "remediation_required",
            "reading_order_reviewed",
        )

    decided = service.decide(
        session,
        task["task_id"],
        _expected(task),
        context["basis_profile_id"],
        "remediation_required",
    )
    blocked = agent.refresh_review_task(session, task["task_id"], _expected(task))
    current_job = PipelineJobService(layout).show(task["job_id"])["current_state"]

    assert decided["resolution_state"] == "remediation_refresh_required"
    assert blocked["status"] == "blocked"
    assert current_job["current_node"] == "source_adequacy_remediation"
    assert current_job["status"] in {"waiting_source", "waiting_user"}


def test_source_review_handle_rejects_forgery_and_source_drift(tmp_path: Path) -> None:
    layout, session, _, task = _build_uncertain_task(tmp_path, route="primary")
    service = SourceAdequacyResolutionApplicationService(clock=lambda: DECISION_TIME)
    prepared = service.prepare_source_review(session, task["task_id"], _expected(task))

    with pytest.raises(ResearchKBError):
        service.open_source_review(session, replace(prepared.handle, expected_fingerprint="0" * 64))

    source_path = layout.source_roots[prepared.handle.source_root_id] / prepared.handle.source_relative_path
    source_path.write_bytes(source_path.read_bytes() + b"changed")
    with pytest.raises(ResearchKBError):
        service.open_source_review(session, prepared.handle)
    assert service.show_context(session, task["task_id"])["resolution_state"] == "stale"


def test_resolution_rejects_changed_action_and_unknown_inputs(tmp_path: Path) -> None:
    _, session, _, task = _build_uncertain_task(tmp_path, route="primary")
    service = SourceAdequacyResolutionApplicationService(clock=lambda: DECISION_TIME)
    context = service.show_context(session, task["task_id"])
    service.decide(
        session,
        task["task_id"],
        _expected(task),
        context["basis_profile_id"],
        "accept_uncertainty",
        "reading_order_reviewed",
    )

    with pytest.raises(ResearchKBError):
        service.decide(
            session,
            task["task_id"],
            _expected(task),
            context["basis_profile_id"],
            "remediation_required",
        )
    with pytest.raises(ResearchKBError):
        service.decide(
            session,
            task["task_id"],
            _expected(task),
            context["basis_profile_id"],
            "accept_everything",
        )
