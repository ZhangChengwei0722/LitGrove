from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_record
from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services import (
    AgentTaskApplicationService,
    RegistryService,
    ResearchOrganizationService,
    WorkspaceSessionService,
)
from research_kb.services.agent_task_application import _normalize_organization_create_request
from research_kb.services.organization_proposal_context import _organization_context
from research_kb.workspace import WorkspaceLayout
from tests.unit.test_reading_application_service import _commit_primary


P7B_CLASSES = [
    "metadata",
    "canonical_evidence",
    "paper_card_content",
    "review_background",
    "research_routing_context",
    "operational_context",
]


def _p7b_workspace(tmp_path: Path):
    layout, _, intake, _, _, bundle = _commit_primary(tmp_path)
    config = yaml.safe_load(layout.config.path.read_text(encoding="utf-8"))
    config["agent_policy"]["registry_version"] = "p7b-v1"
    config["agent_policy"]["allowed_content_classes"] = P7B_CLASSES
    config["agent_policy"]["max_prompt_bytes"] = 2_097_152
    config["agent_policy"]["max_result_bytes"] = 1_048_576
    layout.config.path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    refreshed = WorkspaceLayout.load(layout.config.path)
    session = WorkspaceSessionService({"alpha": refreshed.config.path}).open("alpha")
    revision = bundle["revisions"][-1]
    paper = {"paper_id": intake["paper_id"]}
    units = [
        {"paper_id": intake["paper_id"], **unit}
        for section in revision["paper_card"]["sections"]
        for unit in section["units"]
        if unit["grounding_status"] == "grounded"
    ]
    return refreshed, session, [paper], units, revision["evidence"]


def _expected(task: dict[str, object]) -> dict[str, str]:
    return {"state_id": str(task["state_id"]), "state_digest": str(task["state_digest"])}


def _request(paper_id: str) -> dict[str, object]:
    return {
        "target_kind": "direction",
        "target_id": None,
        "proposal_goal": "Create one bounded synthetic research direction.",
        "paper_ids": [paper_id],
        "include_review_background": False,
        "executor_id": "codex_cli",
        "approved_content_classes": P7B_CLASSES,
        "idempotency_key": "organization-proposal-1",
    }


def _result(task: dict[str, object], paper_id: str, unit_id: str) -> dict[str, object]:
    return {
        "contract_version": "p7b-organization-proposal@1.0",
        "task_id": task["task_id"],
        "input_basis_digest": task["input_basis_digest"],
        "target_kind": "direction",
        "target_id": None,
        "proposal": {
            "name": "Synthetic response direction",
            "scope": "Generated records only.",
            "status": "active",
            "unit_links": [
                {
                    "source_kind": "primary",
                    "paper_id": paper_id,
                    "unit_id": unit_id,
                    "role": "factual_example",
                    "rationale": "The current grounded Unit is a bounded example.",
                }
            ],
            "gap_notes": ["A second synthetic example is absent."],
        },
        "duplicate_notes": [],
        "unresolved_conflicts": [],
    }


def _submit_and_approve(
    service: AgentTaskApplicationService,
    session,
    request: dict[str, object],
    result_factory,
) -> dict[str, object]:
    created = service.create_organization_proposal(session, request)
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        result_factory(prepared["task"]),
    )
    return service.approve_organization_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )


def test_organization_proposal_handoff_preview_and_approval(tmp_path: Path) -> None:
    layout, session, papers, units, _ = _p7b_workspace(tmp_path)
    service = AgentTaskApplicationService()
    request = _request(papers[0]["paper_id"])

    created = service.create_organization_proposal(session, request)
    replay = service.create_organization_proposal(session, request)
    assert replay["persistent_writes"] == 0
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    assert prepared["handoff"]["manifest_version"] == "p7b-agent-handoff@1.0"
    assert "source_ref" not in str(prepared["handoff"])
    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        _result(prepared["task"], papers[0]["paper_id"], units[0]["unit_id"]),
    )
    preview = service.preview_result(session, submitted["task"]["task_id"])
    assert preview["candidate"]["approval_blocked"] is False
    approved = service.approve_organization_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )
    assert approved["task"]["status"] == "approved"
    assert approved["canonical_scientific_write"] is True
    direction = ResearchOrganizationService(layout).read_direction(
        approved["organization"]["target_id"]
    )
    assert direction["name"] == "Synthetic response direction"
    repeated = service.approve_organization_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )
    assert repeated["persistent_writes"] == 0
    assert repeated["canonical_scientific_write"] is False
    assert GuardianService(layout).check().report["status"] == "success"


def test_field_map_question_existing_target_and_no_change_approval(tmp_path: Path) -> None:
    layout, session, papers, units, _ = _p7b_workspace(tmp_path)
    service = AgentTaskApplicationService()
    paper_id = papers[0]["paper_id"]
    unit_id = units[0]["unit_id"]

    direction = _submit_and_approve(
        service,
        session,
        _request(paper_id),
        lambda task: _result(task, paper_id, unit_id),
    )["organization"]
    field_request = {
        **_request(paper_id),
        "target_kind": "field_map_entry",
        "proposal_goal": "Create one bounded synthetic field entry.",
        "idempotency_key": "organization-proposal-field-1",
    }
    field = _submit_and_approve(
        service,
        session,
        field_request,
        lambda task: {
            **_result(task, paper_id, unit_id),
            "target_kind": "field_map_entry",
            "proposal": {
                "title": "Synthetic field entry",
                "entry_type": "mechanism",
                "definition": "Generated records only.",
                "status": "active",
                "consensus_level": "review_plus_primary_examples",
                "direction_refs": [direction["target_id"]],
                "unit_links": _result(task, paper_id, unit_id)["proposal"]["unit_links"],
                "aspect_notes": [],
            },
        },
    )["organization"]
    assert ResearchOrganizationService(layout).read_field_map_entry(field["target_id"])["title"] == "Synthetic field entry"

    question_request = {
        **_request(paper_id),
        "target_kind": "question",
        "proposal_goal": "Create one bounded synthetic question.",
        "idempotency_key": "organization-proposal-question-1",
    }
    question = _submit_and_approve(
        service,
        session,
        question_request,
        lambda task: {
            **_result(task, paper_id, unit_id),
            "target_kind": "question",
            "proposal": {
                "question_text": "What is the bounded synthetic response?",
                "scope": "Generated records only.",
                "mapping_status": "ai_draft",
                "factual_links": [
                    {
                        "paper_id": paper_id,
                        "selected_card_unit_ids": [unit_id],
                        "role_in_question": "support",
                        "relevance_rationale": "The current grounded Unit is relevant.",
                        "boundary_refs": [],
                    }
                ],
                "background_links": [],
            },
        },
    )["organization"]
    assert ResearchOrganizationService(layout).read_question(question["target_id"])["question_text"] == "What is the bounded synthetic response?"

    successor_request = {
        **_request(paper_id),
        "target_id": direction["target_id"],
        "proposal_goal": "Revise the bounded synthetic direction.",
        "idempotency_key": "organization-proposal-direction-2",
    }
    successor = _submit_and_approve(
        service,
        session,
        successor_request,
        lambda task: {
            **_result(task, paper_id, unit_id),
            "target_id": direction["target_id"],
            "proposal": {
                **_result(task, paper_id, unit_id)["proposal"],
                "name": "Synthetic response direction revised",
            },
        },
    )
    assert successor["organization"]["revision_number"] == 2
    assert successor["canonical_scientific_write"] is True

    no_change_request = {
        **successor_request,
        "proposal_goal": "Confirm the current bounded synthetic direction.",
        "idempotency_key": "organization-proposal-direction-3",
    }
    no_change = _submit_and_approve(
        service,
        session,
        no_change_request,
        lambda task: {
            **_result(task, paper_id, unit_id),
            "target_id": direction["target_id"],
            "proposal": {
                **_result(task, paper_id, unit_id)["proposal"],
                "name": "Synthetic response direction revised",
            },
        },
    )
    assert no_change["organization"]["revision_number"] == 2
    assert no_change["canonical_scientific_write"] is False
    assert no_change["persistent_writes"] == 1
    no_change_replay = service.approve_organization_result(
        session,
        no_change["task"]["task_id"],
        _expected(no_change["task"]),
    )
    assert no_change_replay["organization"] == no_change["organization"]
    assert no_change_replay["persistent_writes"] == 0
    guardian = GuardianService(layout).check().report
    assert guardian["status"] != "failure"
    assert not any(
        "organization proposal" in finding["message"].lower()
        for finding in guardian["findings"]
    )


def test_result_schema_rejects_target_incompatible_link_roles(tmp_path: Path) -> None:
    _, session, papers, units, _ = _p7b_workspace(tmp_path)
    service = AgentTaskApplicationService()
    created = service.create_organization_proposal(session, _request(papers[0]["paper_id"]))
    candidate = _result(created["task"], papers[0]["paper_id"], units[0]["unit_id"])
    candidate["proposal"]["unit_links"][0]["role"] = "question_background"
    assert validate_record("organization-proposal", candidate, actor="agent")

    candidate = {
        **candidate,
        "target_kind": "question",
        "proposal": {
            "question_text": "What is the bounded synthetic response?",
            "scope": "Generated records only.",
            "mapping_status": "ai_draft",
            "factual_links": [
                {
                    "paper_id": papers[0]["paper_id"],
                    "selected_card_unit_ids": [units[0]["unit_id"]],
                    "role_in_question": "support",
                    "relevance_rationale": "Synthetic relevance.",
                    "boundary_refs": [],
                }
            ],
            "background_links": [
                {
                    "source_kind": "primary",
                    "paper_id": papers[0]["paper_id"],
                    "unit_id": units[0]["unit_id"],
                    "role": "background_context",
                    "rationale": "Synthetic background.",
                }
            ],
        },
    }
    assert validate_record("organization-proposal", candidate, actor="agent")


def test_organization_proposal_requires_current_semantic_units(tmp_path: Path) -> None:
    layout, session, _, _, _ = _p7b_workspace(tmp_path)
    source_root = next(iter(layout.source_roots.values()))
    source = source_root / "unprocessed-organization-source.txt"
    source.write_text("Synthetic unprocessed source.", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id=next(iter(layout.source_roots)),
        relative_path=source.name,
        metadata={
            "bibliography": {
                "title": "Synthetic Unprocessed Organization Source",
                "authors": ["Fixture Author"],
                "year": 2026,
                "doi": None,
            },
            "fixture_origin": "synthetic_from_scratch",
        },
    )

    with pytest.raises(ResearchKBError, match="at least one current admissible semantic Unit"):
        AgentTaskApplicationService().create_organization_proposal(
            session,
            _request(paper["paper_id"]),
        )


def test_organization_proposal_preserves_paper_order() -> None:
    ordered = [
        "paper_22222222-2222-4222-8222-222222222222",
        "paper_11111111-1111-4111-8111-111111111111",
    ]
    request = {
        **_request(ordered[0]),
        "paper_ids": ordered,
        "idempotency_key": "organization-paper-order",
    }

    normalized = _normalize_organization_create_request(request)

    assert normalized["paper_ids"] == ordered


def test_existing_field_map_directions_are_prioritized_before_context_truncation() -> None:
    class StubOrganizationService:
        def list_directions(self):
            return [
                {
                    "direction_id": f"direction_{index:08x}-0000-4000-8000-000000000000",
                    "name": f"Direction {index}",
                    "scope": "Synthetic scope.",
                    "status": "active",
                    "revision_id": f"orgrev_{index:08x}-0000-4000-8000-000000000000",
                    "links": [],
                }
                for index in range(101)
            ]

        def list_field_map_entries(self):
            return []

    required = "direction_00000064-0000-4000-8000-000000000000"
    target = {"direction_refs": [{"direction_id": required}]}

    context = _organization_context(StubOrganizationService(), [], target)

    assert context["available_directions"][0]["direction_id"] == required
    assert len(context["available_directions"]) == 100
    assert context["context_truncated"] is True


def test_existing_target_change_rejects_stale_submission(tmp_path: Path) -> None:
    layout, session, papers, units, _ = _p7b_workspace(tmp_path)
    service = AgentTaskApplicationService()
    paper_id = papers[0]["paper_id"]
    unit_id = units[0]["unit_id"]
    first = _submit_and_approve(
        service,
        session,
        _request(paper_id),
        lambda task: _result(task, paper_id, unit_id),
    )
    request = {
        **_request(paper_id),
        "target_id": first["organization"]["target_id"],
        "proposal_goal": "Revise one bounded synthetic direction.",
        "idempotency_key": "organization-stale-target",
    }
    created = service.create_organization_proposal(session, request)
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    ResearchOrganizationService(layout).promote_direction(
        {
            **_result(prepared["task"], paper_id, unit_id)["proposal"],
            "name": "Concurrent synthetic direction revision",
        },
        target_id=first["organization"]["target_id"],
        approval={
            "receipt_id": "synthetic-concurrent-user-revision",
            "approved_by": "user",
            "approved_at": "2026-08-03T00:00:00Z",
            "origin": "user_authored",
        },
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    candidate = {
        **_result(prepared["task"], paper_id, unit_id),
        "target_id": first["organization"]["target_id"],
    }

    with pytest.raises(ResearchKBError, match="input basis changed"):
        service.submit_result(
            session,
            prepared["task"]["task_id"],
            _expected(prepared["task"]),
            prepared["lease"],
            candidate,
        )


def test_organization_revision_lineage_and_commit_recovery(tmp_path: Path) -> None:
    layout, session, papers, units, _ = _p7b_workspace(tmp_path)
    service = AgentTaskApplicationService()
    paper_id = papers[0]["paper_id"]
    unit_id = units[0]["unit_id"]

    created = service.create_organization_proposal(session, _request(paper_id))
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    candidate = _result(prepared["task"], paper_id, unit_id)
    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        candidate,
    )
    revised = service.request_revision(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
        "Narrow the synthetic scope.",
    )
    assert revised["successor_task"]["lineage"] == {
        "predecessor_task_id": submitted["task"]["task_id"],
        "predecessor_result_digest": canonical_digest(candidate),
        "feedback": "Narrow the synthetic scope.",
    }
    assert revised["successor_task"]["input_basis_digest"] == submitted["task"]["input_basis_digest"]

    recovery_request = {
        **_request(paper_id),
        "idempotency_key": "organization-recovery",
    }
    recovery_created = service.create_organization_proposal(session, recovery_request)
    recovery_prepared = service.prepare_handoff(
        session,
        recovery_created["task"]["task_id"],
        _expected(recovery_created["task"]),
        "codex_cli",
    )
    recovery_candidate = _result(recovery_prepared["task"], paper_id, unit_id)
    recovery_submitted = service.submit_result(
        session,
        recovery_prepared["task"]["task_id"],
        _expected(recovery_prepared["task"]),
        recovery_prepared["lease"],
        recovery_candidate,
    )
    ResearchOrganizationService(layout).promote_direction(
        recovery_candidate["proposal"],
        approval={
            "approved_by": "user",
            "approved_at": "2026-08-03T00:00:00Z",
            "origin": "user_approved_agent_proposal",
            "task_id": recovery_submitted["task"]["task_id"],
            "task_result_digest": canonical_digest(recovery_candidate),
        },
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    recovered = service.approve_organization_result(
        session,
        recovery_submitted["task"]["task_id"],
        _expected(recovery_submitted["task"]),
    )
    assert recovered["task"]["status"] == "approved"
    assert recovered["persistent_writes"] == 1
    assert recovered["canonical_scientific_write"] is False


def test_unresolved_organization_conflict_blocks_approval(tmp_path: Path) -> None:
    _, session, papers, units, _ = _p7b_workspace(tmp_path)
    service = AgentTaskApplicationService()
    created = service.create_organization_proposal(session, _request(papers[0]["paper_id"]))
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    result = _result(prepared["task"], papers[0]["paper_id"], units[0]["unit_id"])
    result["unresolved_conflicts"] = ["Two synthetic classifications remain incompatible."]
    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        result,
    )

    with pytest.raises(ResearchKBError, match="unresolved conflicts"):
        service.approve_organization_result(
            session,
            submitted["task"]["task_id"],
            _expected(submitted["task"]),
        )
