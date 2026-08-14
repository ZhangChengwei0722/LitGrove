from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_kb import __version__
from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.bundle import load_workspace_entries, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError
from research_kb.parse.worker_protocol import WorkerParseResult
from research_kb.process_events import read_process_events
from research_kb.services import (
    CapabilityService,
    DeterministicTrunkService,
    IntakeSourceAdequacyResolutionApplicationService,
    TrustedParseIntakeApplicationService,
)
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.source_adequacy import SourceAdequacyService
from research_kb.source_resolution import observe_paper_source
from tests.unit.test_trusted_parse_intake_application_service import (
    NOW,
    _expected_state,
    _trusted_case,
)


def _uncertain_worker(request) -> WorkerParseResult:
    pages = (
        {
            "pdf_page": 2,
            "printed_page": None,
            "text": "Synthetic trusted page with deliberately incomplete page coverage.",
            "locator": "page:2:text",
        },
    )
    return WorkerParseResult(
        pages=pages,
        source_sha256=request.source_sha256,
        parser={"adapter": request.adapter_name, "version": request.adapter_version},
        output_utf8_bytes=sum(len(item["text"].encode("utf-8")) for item in pages),
    )


def _hard_failure_worker(request) -> WorkerParseResult:
    pages = (
        {
            "pdf_page": 1,
            "printed_page": None,
            "text": "",
            "locator": "page:1:text",
        },
    )
    return WorkerParseResult(
        pages=pages,
        source_sha256=request.source_sha256,
        parser={"adapter": request.adapter_name, "version": request.adapter_version},
        output_utf8_bytes=0,
    )


def _build_uncertain_intake(
    tmp_path: Path,
    *,
    route: str = "primary",
    route_reason: str | None = None,
    worker_runner=_uncertain_worker,
):
    layout, session, started = _trusted_case(
        tmp_path,
        route=route,
        route_reason=route_reason,
    )
    trusted = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: f"intake-resolution-{route_reason or route}",
        worker_runner=worker_runner,
    )
    preparation = trusted.prepare(
        session,
        started["pipeline"]["job_id"],
        _expected_state(started),
    )
    result = trusted.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )
    head = PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]
    assert result.outcome == "continued"
    assert head["current_node"] == "source_adequacy"
    assert head["status"] in {"waiting_user", "waiting_source"}
    return layout, session, started, head


def _expected(head: dict[str, object]) -> dict[str, str]:
    return {
        "state_id": str(head["state_id"]),
        "state_digest": canonical_digest(head),
    }


def test_intake_source_adequacy_resolution_is_a_public_core_service() -> None:
    from research_kb import services

    service = getattr(services, "IntakeSourceAdequacyResolutionApplicationService", None)

    assert service is not None
    assert "IntakeSourceAdequacyResolutionApplicationService" in services.__all__
    assert callable(getattr(service, "show_context", None))
    assert callable(getattr(service, "prepare_source_review", None))
    assert callable(getattr(service, "open_source_review", None))
    assert callable(getattr(service, "decide_and_continue", None))


def test_intake_resolution_identity_and_capability_fact_are_exact() -> None:
    assert __version__ == "0.1.1"
    assert APPLICATION_SERVICE_INTERFACE_VERSION == "1.23"
    assert CapabilityService(pdfplumber_probe=lambda: None).show()["features"][
        "intake_source_adequacy_resolution"
    ] is True


def test_deterministic_trunk_exposes_only_the_bounded_profile_continuation_contract() -> None:
    assert callable(getattr(DeterministicTrunkService, "continue_with_profile", None))


@pytest.mark.parametrize(
    ("route", "route_reason", "expected_node"),
    [
        ("primary", None, "primary_semantic_gate"),
        ("review", None, "review_semantic_gate"),
        ("review", "mixed_document", "review_semantic_gate_mixed_document"),
    ],
)
def test_job_bound_context_review_and_accept_continue_without_second_parse(
    tmp_path: Path,
    route: str,
    route_reason: str | None,
    expected_node: str,
) -> None:
    layout, session, started, origin = _build_uncertain_intake(
        tmp_path,
        route=route,
        route_reason=route_reason,
    )

    class NoParseService:
        def run(self, **kwargs):
            raise AssertionError(f"ParseService.run must not be called: {kwargs}")

    class NoParserRegistry:
        def create(self, name):
            raise AssertionError(f"parser factory must not be called: {name}")

    service = IntakeSourceAdequacyResolutionApplicationService(
        clock=lambda: NOW,
        trunk_factory=lambda current_layout: DeterministicTrunkService(
            current_layout,
            parse_service=NoParseService(),
            parse_registry=NoParserRegistry(),
        ),
    )
    context = service.show_context(session, started["pipeline"]["job_id"])
    prepared = service.prepare_source_review(
        session,
        started["pipeline"]["job_id"],
        _expected(origin),
    )
    with service.open_source_review(session, prepared.handle) as opened:
        assert opened.size_bytes > 0

    assert context["resolution_state"] == "review_required"
    assert context["machine_status"] == "uncertain"
    assert context["document_route"] == route
    assert context["route_reason"] == route_reason
    assert context["allowed_actions"] == ["accept_uncertainty", "remediation_required"]
    public_text = json.dumps(context, sort_keys=True).lower()
    for forbidden in ("source_ref", "relative_path", "fingerprint", "sha256", str(layout.knowledge_root).lower()):
        assert forbidden not in public_text
    assert "source_root_id" not in repr(prepared.handle)
    parse_events_before = [
        item
        for item in read_process_events(layout.process_events_path)
        if item["operation"] == "parse_run" and item["result"] == "success"
    ]

    decided = service.decide_and_continue(
        session,
        started["pipeline"]["job_id"],
        _expected(origin),
        "accept_uncertainty",
        "basic_source_reviewed",
    )
    replay = service.decide_and_continue(
        session,
        started["pipeline"]["job_id"],
        _expected(origin),
        "accept_uncertainty",
    )

    assert decided["resolution_state"] == "continued"
    assert decided["persistent_writes"] == 3
    assert decided["job"]["status"] == "completed"
    assert decided["job"]["current_node"] == expected_node
    assert replay["resolution_state"] == "continued"
    assert replay["persistent_writes"] == 0
    parse_events_after = [
        item
        for item in read_process_events(layout.process_events_path)
        if item["operation"] == "parse_run" and item["result"] == "success"
    ]
    assert parse_events_after == parse_events_before


def test_newer_profile_from_another_job_cannot_replace_origin_basis(tmp_path: Path) -> None:
    layout, session, started, origin = _build_uncertain_intake(tmp_path)
    service = IntakeSourceAdequacyResolutionApplicationService(clock=lambda: NOW)
    original = service.show_context(session, started["pipeline"]["job_id"])
    other_job = PipelineJobService(layout).create(
        requested_route="local_source",
        requested_depth="semantic_gate",
        current_node="source_adequacy",
        input_refs=[started["paper_id"]],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["assess_source_adequacy"],
            "captured_at": "2026-08-08T08:00:00Z",
        },
        idempotency_key="cross-job-profile-substitution-probe",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    newer = SourceAdequacyService(layout).assess(
        paper_id=started["paper_id"],
        job_id=other_job.state["job_id"],
        requested_operation="basic_paper_card",
    ).profile

    projected = service.show_context(session, started["pipeline"]["job_id"])

    assert projected["basis_profile_id"] == original["basis_profile_id"]
    assert projected["basis_profile_id"] != newer["profile_id"]
    assert PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"] == origin


def test_remediation_is_idempotent_and_does_not_create_agent_task(tmp_path: Path) -> None:
    layout, session, started, origin = _build_uncertain_intake(tmp_path)
    service = IntakeSourceAdequacyResolutionApplicationService(clock=lambda: NOW)

    decided = service.decide_and_continue(
        session,
        started["pipeline"]["job_id"],
        _expected(origin),
        "remediation_required",
    )
    replay = service.decide_and_continue(
        session,
        started["pipeline"]["job_id"],
        _expected(origin),
        "remediation_required",
    )

    assert decided["resolution_state"] == "remediation_required"
    assert decided["persistent_writes"] == 1
    assert replay["persistent_writes"] == 0
    assert PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"] == origin
    assert not layout.agent_tasks_path.exists()
    with pytest.raises(ResearchKBError):
        service.decide_and_continue(
            session,
            started["pipeline"]["job_id"],
            _expected(origin),
            "accept_uncertainty",
            "basic_source_reviewed",
        )


def test_interrupted_continuation_resumes_only_exact_descendant(tmp_path: Path) -> None:
    layout, session, started, origin = _build_uncertain_intake(tmp_path)

    class InterruptAfterFirstTransition:
        def __init__(self, current_layout):
            self.inner = PipelineJobService(current_layout)
            self.calls = 0

        def show(self, job_id):
            return self.inner.show(job_id)

        def transition(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("synthetic interruption after first continuation transition")
            return self.inner.transition(*args, **kwargs)

    interrupted = IntakeSourceAdequacyResolutionApplicationService(
        clock=lambda: NOW,
        trunk_factory=lambda current_layout: DeterministicTrunkService(
            current_layout,
            jobs=InterruptAfterFirstTransition(current_layout),
        ),
    )
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        interrupted.decide_and_continue(
            session,
            started["pipeline"]["job_id"],
            _expected(origin),
            "accept_uncertainty",
            "basic_source_reviewed",
        )
    running = PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]
    assert running["status"] == "running"
    assert running["current_node"] == "primary_semantic_gate"

    recovered = IntakeSourceAdequacyResolutionApplicationService(clock=lambda: NOW).decide_and_continue(
        session,
        started["pipeline"]["job_id"],
        _expected(origin),
        "accept_uncertainty",
    )
    final_replay = IntakeSourceAdequacyResolutionApplicationService(clock=lambda: NOW).decide_and_continue(
        session,
        started["pipeline"]["job_id"],
        _expected(origin),
        "accept_uncertainty",
    )

    assert recovered["resolution_state"] == "continued"
    assert recovered["persistent_writes"] == 1
    assert final_replay["persistent_writes"] == 0


def test_changed_source_makes_context_stale_and_disables_accept(tmp_path: Path) -> None:
    layout, session, started, _ = _build_uncertain_intake(tmp_path)
    entries = load_workspace_entries(layout)
    paper = next(
        item
        for item in records_of_kind(entries, "registry-paper")
        if item["paper_id"] == started["paper_id"]
    )
    source = observe_paper_source(layout, entries, paper).path
    source.write_bytes(source.read_bytes() + b"changed")

    context = IntakeSourceAdequacyResolutionApplicationService(clock=lambda: NOW).show_context(
        session,
        started["pipeline"]["job_id"],
    )

    assert context["resolution_state"] == "stale"
    assert context["allowed_actions"] == []
    assert context["source_availability"] != "available"


def test_hard_basic_failure_is_visible_but_cannot_be_accepted(tmp_path: Path) -> None:
    _, session, started, origin = _build_uncertain_intake(
        tmp_path,
        worker_runner=_hard_failure_worker,
    )
    service = IntakeSourceAdequacyResolutionApplicationService(clock=lambda: NOW)

    context = service.show_context(session, started["pipeline"]["job_id"])

    assert context["resolution_state"] == "not_resolvable"
    assert context["machine_status"] == "no"
    assert context["hard_failure"] is True
    assert context["allowed_actions"] == []
    with pytest.raises(ResearchKBError):
        service.decide_and_continue(
            session,
            started["pipeline"]["job_id"],
            _expected(origin),
            "accept_uncertainty",
            "basic_source_reviewed",
        )
