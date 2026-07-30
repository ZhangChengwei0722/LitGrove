from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from research_kb.bundle import load_workspace_entries
from research_kb.contracts.validator import validate_bundle
from research_kb.errors import ResearchKBError
from research_kb.services.parse import ParseService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.registry import RegistryService
from research_kb.services.source_adequacy import SourceAdequacyService
from tests.runtime_helpers import make_runtime_workspace


PAPER_ID = "paper_a3000001-0000-4000-8000-000000000001"
JOB_ID = "job_a3000001-0000-4000-8000-000000000001"
JOB_STATE_ID = "jobstate_a3000001-0000-4000-8000-000000000001"
PROFILE_ID = "adequacy_a3000001-0000-4000-8000-000000000001"
PROFILE_ID_2 = "adequacy_a3000002-0000-4000-8000-000000000002"


@dataclass
class FixtureAdapter:
    name: str = "synthetic-text"
    version: str = "1.0"
    locator: str = "page:1:block:1"

    def parse(self, source: Path, *, paper_id: str, parse_run_id: str) -> list[dict[str, Any]]:
        del paper_id, parse_run_id
        return [
            {
                "pdf_page": 1,
                "printed_page": None,
                "text": source.read_text(encoding="utf-8"),
                "locator": self.locator,
            }
        ]


def _registered_parsed_job(tmp_path: Path, *, adapter: FixtureAdapter | None = None):
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented source response.", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout, id_allocator=lambda namespace: PAPER_ID).add(
        root_id="alpha-sources",
        relative_path="study.txt",
        metadata={
            "bibliography": {"title": "Invented source study"},
            "fixture_origin": "synthetic_from_scratch",
        },
        actor="cli",
    )
    ParseService(layout).run(paper_id=PAPER_ID, adapter=adapter or FixtureAdapter())
    allocated = iter((JOB_ID, JOB_STATE_ID))
    job = PipelineJobService(layout, id_allocator=lambda namespace: next(allocated)).create(
        requested_route="local_source",
        requested_depth="semantic_gate",
        current_node="source_adequacy",
        input_refs=[PAPER_ID],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["assess_source_adequacy", "advance_deterministic_trunk"],
            "captured_at": "2026-01-01T00:00:00Z",
        },
        idempotency_key="synthetic-source-adequacy",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    return layout, source, paper, job.state


def test_assessment_splits_capabilities_and_is_exact_replay_idempotent(tmp_path) -> None:
    layout, _, _, _ = _registered_parsed_job(tmp_path)
    service = SourceAdequacyService(layout, id_allocator=lambda namespace: PROFILE_ID)

    first = service.assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="basic_paper_card",
    )
    replay = service.assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="basic_paper_card",
    )

    assert first.transaction is not None
    assert replay.transaction is None
    assert replay.profile == first.profile
    assert first.profile["capabilities"]["basic_paper_understanding"]["status"] == "yes"
    assert first.profile["capabilities"]["continuous_text_citation"]["status"] == "yes"
    assert first.profile["capabilities"]["figure_table_evidence_extraction"]["status"] == "no"
    assert first.profile["capabilities"]["supplementary_material_analysis"]["status"] == "no"
    gate = service.gate(paper_id=PAPER_ID, requested_operation="basic_paper_card")
    assert gate["status"] == "allowed"
    assert gate["profile_id"] == PROFILE_ID


def test_missing_supplement_blocks_only_supplementary_operation(tmp_path) -> None:
    layout, _, _, _ = _registered_parsed_job(tmp_path)
    allocated = iter((PROFILE_ID, PROFILE_ID_2))
    service = SourceAdequacyService(layout, id_allocator=lambda namespace: next(allocated))
    basic = service.assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="basic_review_memory",
    )
    supplementary = service.assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="supplementary_analysis",
    )

    assert basic.profile["capabilities"]["basic_paper_understanding"]["status"] == "yes"
    assert supplementary.profile["capabilities"]["supplementary_material_analysis"]["status"] == "no"
    assert service.gate(
        paper_id=PAPER_ID,
        requested_operation="basic_review_memory",
    )["status"] == "allowed"
    blocked = service.gate(paper_id=PAPER_ID, requested_operation="supplementary_analysis")
    assert blocked["status"] == "blocked"
    assert blocked["pipeline_status"] == "waiting_source"
    assert blocked["wait_reason"] == "supplement_missing"


def test_source_change_stales_profile_without_rewriting_it(tmp_path) -> None:
    layout, source, _, _ = _registered_parsed_job(tmp_path)
    service = SourceAdequacyService(layout, id_allocator=lambda namespace: PROFILE_ID)
    original = service.assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="basic_paper_card",
    ).profile
    source.write_text("Changed invented source response.", encoding="utf-8", newline="\n")

    projected = service.show(paper_id=PAPER_ID, requested_operation="basic_paper_card")
    assert projected["items"][0]["freshness"]["state"] == "stale_upstream"
    assert "source_unavailable_or_changed" in projected["items"][0]["freshness"]["reasons"]
    blocked = service.gate(paper_id=PAPER_ID, requested_operation="basic_paper_card")
    assert blocked["status"] == "blocked"
    assert blocked["wait_reason"] == "source_adequacy_stale"
    assert original["profile_id"] == PROFILE_ID


def test_user_decision_creates_successor_for_non_hard_uncertainty(tmp_path) -> None:
    layout, _, _, _ = _registered_parsed_job(
        tmp_path,
        adapter=FixtureAdapter(name="unregistered-linear"),
    )
    allocated = iter((PROFILE_ID, PROFILE_ID_2))
    service = SourceAdequacyService(layout, id_allocator=lambda namespace: next(allocated))
    basis = service.assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="basic_paper_card",
    ).profile
    assert basis["capabilities"]["basic_paper_understanding"]["status"] == "uncertain"

    successor = service.assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="basic_paper_card",
        actor="user",
        basis_profile_id=PROFILE_ID,
        user_decision={
            "decision": "accept_uncertainty",
            "capabilities": ["basic_paper_understanding"],
            "reason": "The user accepts the bounded synthetic uncertainty.",
        },
    ).profile

    assert successor["basis_profile"]["profile_id"] == PROFILE_ID
    assert successor["capabilities"]["basic_paper_understanding"]["status"] == "yes"
    assert successor["capabilities"]["basic_paper_understanding"]["authority_layers"] == [
        "machine",
        "user",
    ]
    projected = service.show(
        paper_id=PAPER_ID,
        requested_operation="basic_paper_card",
    )
    assert "bounded synthetic uncertainty" not in str(projected)
    assert "User accepted the recorded non-hard uncertainty." in str(projected)
    assert service.gate(paper_id=PAPER_ID, requested_operation="basic_paper_card")["profile_id"] == PROFILE_ID_2


def test_user_cannot_override_hard_locator_failure(tmp_path) -> None:
    layout, _, _, _ = _registered_parsed_job(
        tmp_path,
        adapter=FixtureAdapter(locator="not-a-page-locator"),
    )
    allocated = iter((PROFILE_ID, PROFILE_ID_2))
    service = SourceAdequacyService(layout, id_allocator=lambda namespace: next(allocated))
    basis = service.assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="basic_paper_card",
    ).profile
    assert basis["capabilities"]["basic_paper_understanding"]["status"] == "no"

    with pytest.raises(ResearchKBError) as caught:
        service.assess(
            paper_id=PAPER_ID,
            job_id=JOB_ID,
            requested_operation="basic_paper_card",
            actor="user",
            basis_profile_id=PROFILE_ID,
            user_decision={
                "decision": "accept_uncertainty",
                "capabilities": ["basic_paper_understanding"],
                "reason": "Attempted override.",
            },
        )
    assert caught.value.diagnostic.code == "RKBC-009"


def test_agent_and_missing_job_authority_fail_before_profile_write(tmp_path) -> None:
    layout, _, _, state = _registered_parsed_job(tmp_path)
    service = SourceAdequacyService(layout, id_allocator=lambda namespace: PROFILE_ID)
    with pytest.raises(ResearchKBError) as agent_error:
        service.assess(
            paper_id=PAPER_ID,
            job_id=JOB_ID,
            requested_operation="basic_paper_card",
            actor="agent",
        )
    assert agent_error.value.diagnostic.code == "RKBC-006"

    denied_job_id = "job_a3000002-0000-4000-8000-000000000002"
    denied_state_id = "jobstate_a3000002-0000-4000-8000-000000000002"
    allocated = iter((denied_job_id, denied_state_id))
    PipelineJobService(layout, id_allocator=lambda namespace: next(allocated)).create(
        requested_route="local_source",
        requested_depth="semantic_gate",
        current_node="source_adequacy",
        input_refs=[PAPER_ID],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["advance_deterministic_trunk"],
            "captured_at": "2026-01-01T00:00:00Z",
        },
        idempotency_key="denied-source-adequacy",
        actor="user",
    )
    with pytest.raises(ResearchKBError) as denied:
        service.assess(
            paper_id=PAPER_ID,
            job_id=denied_job_id,
            requested_operation="basic_paper_card",
        )
    assert denied.value.diagnostic.code == "RKBC-006"
    assert not layout.source_adequacy_path.exists() or PROFILE_ID not in layout.source_adequacy_path.read_text(encoding="utf-8")
    assert state["job_id"] == JOB_ID


def test_workspace_validator_rejects_corrupt_profile_context(tmp_path) -> None:
    layout, _, _, _ = _registered_parsed_job(tmp_path)
    service = SourceAdequacyService(layout, id_allocator=lambda namespace: PROFILE_ID)
    service.assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="basic_paper_card",
    )
    entries = load_workspace_entries(layout)

    def diagnostics_for(mutate):
        bundle = [
            {"kind": kind, "record": deepcopy(record)}
            for kind, record in entries
        ]
        profile = next(
            item["record"]
            for item in bundle
            if item["kind"] == "source-adequacy-profile"
        )
        mutate(profile)
        return validate_bundle(bundle, actor="stored")

    wrong_root = diagnostics_for(
        lambda profile: profile["source_snapshots"][0]["source_ref"].update(
            {"root_id": "undeclared-root"}
        )
    )
    assert any(item.code == "RKBC-007" for item in wrong_root)

    wrong_page_count = diagnostics_for(
        lambda profile: profile["parse_snapshot"].update({"page_count": 2})
    )
    assert any(item.code == "RKBC-014" for item in wrong_page_count)

    wrong_main_ref = diagnostics_for(
        lambda profile: profile["source_snapshots"][0]["source_ref"].update(
            {"relative_path": "other.txt"}
        )
    )
    assert any(
        item.code == "RKBC-014"
        and item.json_path == "/source_snapshots/0/source_ref"
        for item in wrong_main_ref
    )

    wrong_main_role = diagnostics_for(
        lambda profile: profile["source_snapshots"][0].update({"role": "supplement"})
    )
    assert any(
        item.code == "RKBC-014"
        and item.json_path == "/source_snapshots/0/role"
        for item in wrong_main_role
    )

    wrong_main_manifestation = diagnostics_for(
        lambda profile: profile["source_snapshots"][0].update(
            {"manifestation_id": f"sha256:{'0' * 64}"}
        )
    )
    assert any(
        item.code == "RKBC-014"
        and item.json_path == "/source_snapshots/0/manifestation_id"
        for item in wrong_main_manifestation
    )

    wrong_parser_profile = diagnostics_for(
        lambda profile: profile["parse_snapshot"]["parser_identity"].update(
            {"profile_digest": "0" * 64}
        )
    )
    assert any(
        item.code == "RKBC-014"
        and item.json_path == "/parse_snapshot/parser_identity/profile_digest"
        for item in wrong_parser_profile
    )

    non_parse_event_id = next(
        record["event_id"]
        for kind, record in entries
        if kind == "process-event" and record["operation"] == "registry_add"
    )
    wrong_parse_event = diagnostics_for(
        lambda profile: profile["parse_snapshot"].update(
            {"active_parse_ref": non_parse_event_id}
        )
    )
    assert any(
        item.code == "RKBC-009"
        and item.json_path == "/parse_snapshot/active_parse_ref"
        for item in wrong_parse_event
    )

    agent_assessment = diagnostics_for(
        lambda profile: profile.update(
            {
                "agent_assessment": {
                    "actor": "agent",
                    "status": "yes",
                    "reason": "Synthetic invalid P3 authority.",
                    "assessed_at": "2026-01-01T00:00:00Z",
                }
            }
        )
    )
    assert any(item.code == "RKBC-006" for item in agent_assessment)


def test_workspace_validator_rejects_hard_failure_promoted_to_adequate(tmp_path) -> None:
    layout, _, _, _ = _registered_parsed_job(
        tmp_path,
        adapter=FixtureAdapter(locator="not-a-page-locator"),
    )
    SourceAdequacyService(layout, id_allocator=lambda namespace: PROFILE_ID).assess(
        paper_id=PAPER_ID,
        job_id=JOB_ID,
        requested_operation="basic_paper_card",
    )
    bundle = [
        {"kind": kind, "record": deepcopy(record)}
        for kind, record in load_workspace_entries(layout)
    ]
    profile = next(
        item["record"]
        for item in bundle
        if item["kind"] == "source-adequacy-profile"
    )
    profile["capabilities"]["basic_paper_understanding"]["status"] = "yes"

    diagnostics = validate_bundle(bundle, actor="stored")

    assert any(
        item.code == "RKBC-009"
        and item.json_path == "/capabilities/basic_paper_understanding/status"
        for item in diagnostics
    )
