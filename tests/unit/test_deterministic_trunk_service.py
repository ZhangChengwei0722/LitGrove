from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from research_kb.errors import WRITE_CONFLICT, Diagnostic, ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.deterministic_trunk import DeterministicTrunkService
from research_kb.services.parse import ParseService
from research_kb.services.parse_application import ParseAdapterRegistry
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.paper_status import PaperStatusService
from research_kb.services.registry import RegistryService
from research_kb.services.source_adequacy import SourceAdequacyService
from research_kb.storage.json_io import read_jsonl, serialize_jsonl
from tests.runtime_helpers import make_runtime_workspace


def _registered_job(tmp_path):
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "trunk-study.txt"
    source.write_text("Invented deterministic trunk source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = PipelineJobService(layout).create(
        requested_route="local_source",
        requested_depth="semantic_gate",
        current_node="source_check",
        input_refs=[paper["paper_id"]],
        authority_snapshot={
            "actor": "user",
            "granted_operations": [
                "advance_deterministic_trunk",
                "assess_source_adequacy",
                "observe_source",
                "parse_run",
            ],
            "captured_at": "2026-01-01T00:00:00Z",
        },
        idempotency_key="synthetic-deterministic-trunk",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    return layout, source, paper, job.state


def _advance(service, job, paper, **changes):
    values = {
        "job_id": job["job_id"],
        "paper_id": paper["paper_id"],
        "requested_operation": "basic_paper_card",
        "adapter_name": "synthetic-text",
    }
    values.update(changes)
    return service.advance(**values)


def test_trunk_reaches_route_wait_then_records_explicit_mixed_review_route(tmp_path) -> None:
    layout, _, paper, job = _registered_job(tmp_path)
    service = DeterministicTrunkService(layout)

    waiting = _advance(service, job, paper)
    replay = _advance(service, job, paper)

    assert waiting.state["status"] == "waiting_user"
    assert waiting.state["wait_reason"] == "route_ambiguous"
    assert waiting.profile_id is not None
    assert waiting.gate["status"] == "allowed"
    assert replay.state == waiting.state
    assert replay.persistent_writes == 0
    assert not layout.paper_card_path(paper["paper_id"]).exists()
    assert not layout.evidence_path(paper["paper_id"]).exists()
    assert not layout.review_memory_path(paper["paper_id"]).exists()
    assert not layout.review_queue_path.exists()

    completed = _advance(
        service,
        job,
        paper,
        requested_operation="basic_review_memory",
        actor="user",
        document_route="review",
        route_reason="mixed_document",
    )
    completed_replay = _advance(
        service,
        job,
        paper,
        requested_operation="basic_review_memory",
        actor="user",
        document_route="review",
        route_reason="mixed_document",
    )

    assert completed.state["status"] == "completed"
    assert completed.state["current_node"] == "review_semantic_gate_mixed_document"
    assert completed.document_route == "review"
    assert completed_replay.state == completed.state
    assert completed_replay.persistent_writes == 0
    history = PipelineJobService(layout).show(job["job_id"])["history"]
    assert [item["status"] for item in history[-3:]] == [
        "waiting_user",
        "running",
        "completed",
    ]
    events = read_jsonl(layout.process_events_path, record_kind="process-event")
    for operation in {"parse_run", "source_adequacy_assess"}:
        event = next(item for item in events if item["operation"] == operation)
        assert event["job_id"] == job["job_id"]
    status = PaperStatusService(layout).show(paper_id=paper["paper_id"])
    assert status["source_adequacy"]["count"] == 2
    assert {item["requested_operation"] for item in status["source_adequacy"]["items"]} == {
        "basic_paper_card",
        "basic_review_memory",
    }
    assert all(item["allowed"] is True for item in status["source_adequacy"]["items"])
    assert GuardianService(layout).check().report["status"] == "success"


def test_route_decision_rechecks_source_and_does_not_complete_on_stale_profile(tmp_path) -> None:
    layout, source, paper, job = _registered_job(tmp_path)
    service = DeterministicTrunkService(layout)
    waiting = _advance(service, job, paper)
    source.write_text("Changed while waiting for route.\n", encoding="utf-8", newline="\n")

    result = _advance(
        service,
        job,
        paper,
        actor="user",
        document_route="primary",
    )

    assert waiting.state["wait_reason"] == "route_ambiguous"
    assert result.state["status"] == "waiting_source"
    assert result.state["wait_reason"] == "source_changed"
    assert result.document_route is None
    assert SourceAdequacyService(layout).show(
        paper_id=paper["paper_id"],
        requested_operation="basic_paper_card",
    )["items"][0]["freshness"]["state"] == "stale_upstream"


@pytest.mark.parametrize(
    ("requested_operation", "expected_status", "expected_reason"),
    [
        ("figure_table_evidence", "waiting_user", "layout_parse_required"),
        ("supplementary_analysis", "waiting_source", "supplement_missing"),
    ],
)
def test_trunk_routes_capability_specific_blocks_without_scientific_records(
    tmp_path,
    requested_operation,
    expected_status,
    expected_reason,
) -> None:
    layout, _, paper, job = _registered_job(tmp_path)

    result = _advance(
        DeterministicTrunkService(layout),
        job,
        paper,
        requested_operation=requested_operation,
    )

    assert result.state["status"] == expected_status
    assert result.state["wait_reason"] == expected_reason
    assert not layout.paper_card_path(paper["paper_id"]).exists()
    assert not layout.evidence_path(paper["paper_id"]).exists()
    assert not layout.review_queue_path.exists()


def test_trunk_routes_missing_source_before_parse_or_assessment(tmp_path) -> None:
    layout, source, paper, job = _registered_job(tmp_path)
    source.unlink()

    result = _advance(DeterministicTrunkService(layout), job, paper)

    assert result.state["status"] == "waiting_source"
    assert result.state["wait_reason"] == "source_missing"
    assert not layout.parse_path(paper["paper_id"]).exists()
    assert not layout.source_adequacy_path.exists()


def test_trunk_reuses_job_correlated_parse_and_profile_after_restart(tmp_path) -> None:
    layout, _, paper, job = _registered_job(tmp_path)
    pages, parse_transaction = ParseService(layout).run(
        paper_id=paper["paper_id"],
        adapter=SyntheticTextAdapter(),
        actor="cli",
        job_id=job["job_id"],
    )
    profile = SourceAdequacyService(layout).assess(
        paper_id=paper["paper_id"],
        job_id=job["job_id"],
        requested_operation="basic_paper_card",
    ).profile

    result = _advance(DeterministicTrunkService(layout), job, paper)

    assert result.state["status"] == "waiting_user"
    assert result.profile_id == profile["profile_id"]
    assert len(read_jsonl(layout.parse_path(paper["paper_id"]), record_kind="parsed-page")) == len(pages)
    assert len(read_jsonl(layout.source_adequacy_path, record_kind="source-adequacy-profile")) == 1
    events = read_jsonl(layout.process_events_path, record_kind="process-event")
    assert len([item for item in events if item["operation"] == "parse_run"]) == 1
    assert parse_transaction.event_id in result.state["output_refs"] or profile["profile_id"] in result.state["output_refs"]


def test_guardian_reports_committed_profile_not_yet_consumed_by_job_as_warning(tmp_path) -> None:
    layout, _, paper, job = _registered_job(tmp_path)
    ParseService(layout).run(
        paper_id=paper["paper_id"],
        adapter=SyntheticTextAdapter(),
        actor="cli",
        job_id=job["job_id"],
    )
    profile = SourceAdequacyService(layout).assess(
        paper_id=paper["paper_id"],
        job_id=job["job_id"],
        requested_operation="basic_paper_card",
    ).profile

    report = GuardianService(layout).check().report

    finding = next(item for item in report["findings"] if item["record_ref"] == profile["profile_id"])
    assert report["status"] == "warning"
    assert finding["code"] == "RKBC-018"
    assert "not yet consumed" in finding["message"]


def test_trunk_rejects_non_user_route_and_primary_mixed_document(tmp_path) -> None:
    layout, _, paper, job = _registered_job(tmp_path)
    service = DeterministicTrunkService(layout)

    with pytest.raises(ResearchKBError) as non_user:
        _advance(service, job, paper, document_route="review")
    assert non_user.value.diagnostic.code == "RKBC-006"

    with pytest.raises(ResearchKBError) as wrong_mixed_route:
        _advance(
            service,
            job,
            paper,
            actor="user",
            document_route="primary",
            route_reason="mixed_document",
        )
    assert wrong_mixed_route.value.diagnostic.code == "RKBC-002"


def test_user_resume_from_layout_wait_runs_the_selected_adapter(tmp_path) -> None:
    class AlternateAdapter:
        name = "synthetic-layout-probe"
        version = "1.0"

        def parse(
            self,
            source: Path,
            *,
            paper_id: str,
            parse_run_id: str,
        ) -> list[dict[str, Any]]:
            del paper_id, parse_run_id
            return [
                {
                    "pdf_page": 1,
                    "printed_page": None,
                    "text": source.read_text(encoding="utf-8"),
                    "locator": "page:1:block:1",
                }
            ]

    layout, _, paper, job = _registered_job(tmp_path)
    registry = ParseAdapterRegistry(
        {
            "synthetic-text": SyntheticTextAdapter,
            "synthetic-layout-probe": AlternateAdapter,
        }
    )
    service = DeterministicTrunkService(layout, parse_registry=registry)
    blocked = _advance(
        service,
        job,
        paper,
        requested_operation="figure_table_evidence",
    )

    resumed = _advance(
        service,
        job,
        paper,
        requested_operation="figure_table_evidence",
        adapter_name="synthetic-layout-probe",
        actor="user",
    )

    assert blocked.state["wait_reason"] == "layout_parse_required"
    assert resumed.state["wait_reason"] == "layout_parse_required"
    pages = read_jsonl(layout.parse_path(paper["paper_id"]), record_kind="parsed-page")
    assert {item["parser"]["adapter"] for item in pages} == {"synthetic-layout-probe"}
    events = read_jsonl(layout.process_events_path, record_kind="process-event")
    assert len([item for item in events if item["operation"] == "parse_run"]) == 2
    profiles = read_jsonl(
        layout.source_adequacy_path,
        record_kind="source-adequacy-profile",
    )
    assert len(profiles) == 2
    assert SourceAdequacyService(layout).show(
        paper_id=paper["paper_id"],
        requested_operation="figure_table_evidence",
    )["items"][0]["freshness"]["state"] == "stale_upstream"


def test_adapter_execution_failure_routes_to_parse_wait(tmp_path) -> None:
    class FailingAdapter:
        name = "synthetic-failure"
        version = "1.0"

        def parse(
            self,
            source: Path,
            *,
            paper_id: str,
            parse_run_id: str,
        ) -> list[dict[str, Any]]:
            del source, paper_id, parse_run_id
            raise ValueError("synthetic adapter failure")

    layout, _, paper, job = _registered_job(tmp_path)
    service = DeterministicTrunkService(
        layout,
        parse_registry=ParseAdapterRegistry({"synthetic-failure": FailingAdapter}),
    )

    result = _advance(service, job, paper, adapter_name="synthetic-failure")

    assert result.state["status"] == "waiting_source"
    assert result.state["wait_reason"] == "parse_failed"
    parse_events = [
        item
        for item in read_jsonl(layout.process_events_path, record_kind="process-event")
        if item["operation"] == "parse_run"
    ]
    assert len(parse_events) == 1
    assert parse_events[0]["result"] == "failure"
    assert parse_events[0]["job_id"] == job["job_id"]


def test_structural_parse_error_fails_closed_instead_of_becoming_parse_wait(tmp_path) -> None:
    class StructuralFailureParseService:
        def run(self, **kwargs):
            del kwargs
            raise ResearchKBError(
                Diagnostic(
                    WRITE_CONFLICT,
                    "transaction",
                    None,
                    "",
                    "synthetic structural conflict",
                )
            )

    layout, _, paper, job = _registered_job(tmp_path)
    service = DeterministicTrunkService(
        layout,
        parse_service=StructuralFailureParseService(),
    )

    with pytest.raises(ResearchKBError) as caught:
        _advance(service, job, paper)

    assert caught.value.diagnostic.code == WRITE_CONFLICT
    head = PipelineJobService(layout).show(job["job_id"])["current_state"]
    assert head["status"] == "running"
    assert head["wait_reason"] is None


def test_guardian_does_not_crash_when_job_chain_is_already_invalid(tmp_path) -> None:
    layout, _, paper, job = _registered_job(tmp_path)
    _advance(DeterministicTrunkService(layout), job, paper)
    states = read_jsonl(layout.pipeline_jobs_path, record_kind="pipeline-job-state")
    states[-1]["predecessor"]["state_digest"] = "0" * 64
    layout.pipeline_jobs_path.write_bytes(serialize_jsonl(states))

    report = GuardianService(layout).check().report

    assert report["status"] == "failure"
    assert any(
        item["record_ref"] == states[-1]["state_id"]
        for item in report["findings"]
    )
