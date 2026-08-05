from __future__ import annotations

from pathlib import Path
from shutil import copytree, rmtree

import pytest
import yaml

from research_kb.bundle import load_workspace_entries, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services import AgentTaskApplicationService, WorkspaceSessionService
from research_kb.services.question_screening import QuestionScreeningService
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.workspace import WorkspaceLayout
from tests.unit.test_organization_proposal_agent_task import P7B_CLASSES, _build_p7b_workspace


def _build_workspace(tmp_path: Path):
    layout, _, papers, units, _ = _build_p7b_workspace(tmp_path)
    config = yaml.safe_load(layout.config.path.read_text(encoding="utf-8"))
    config["agent_policy"]["registry_version"] = "p7d-v1"
    config["agent_policy"]["allowed_content_classes"] = P7B_CLASSES
    layout.config.path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n")
    refreshed = WorkspaceLayout.load(layout.config.path)
    session = WorkspaceSessionService({"alpha": refreshed.config.path}).open("alpha")
    question_bundle, _ = ResearchOrganizationService(refreshed).promote_question(
        {
            "question_text": "Which synthetic records belong to this bounded set?",
            "scope": "Synthetic records only.",
            "mapping_status": "ai_draft",
            "factual_links": [
                {
                    "paper_id": papers[0]["paper_id"],
                    "selected_card_unit_ids": [units[0]["unit_id"]],
                    "role_in_question": "support",
                    "relevance_rationale": "The synthetic Unit is relevant.",
                    "boundary_refs": [],
                }
            ],
            "background_links": [],
        },
        approval={
            "receipt_id": "synthetic-question-receipt",
            "approved_by": "user",
            "approved_at": "2026-08-03T00:00:00Z",
            "origin": "user_authored",
        },
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    question_id = question_bundle["question_id"]
    return refreshed, session, question_id, papers[0]["paper_id"]


def _restore_workspace(snapshot_root: Path, active_root: Path):
    if active_root.exists():
        rmtree(active_root)
    copytree(snapshot_root, active_root)
    config_path = active_root / "workspace.yaml"
    layout = WorkspaceLayout.load(config_path)
    session = WorkspaceSessionService({"alpha": config_path}).open("alpha")
    return layout, session


@pytest.fixture(scope="module")
def screening_workspace_template(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("screening-p7d-template")
    active_parent = root / "active"
    active_parent.mkdir()
    layout, _, question_id, paper_id = _build_workspace(active_parent)
    active_root = layout.config.path.parent
    snapshot_root = root / "snapshot"
    copytree(active_root, snapshot_root)
    return snapshot_root, active_root, question_id, paper_id


@pytest.fixture
def screening_workspace(screening_workspace_template):
    snapshot_root, active_root, question_id, paper_id = screening_workspace_template
    layout, session = _restore_workspace(snapshot_root, active_root)
    return layout, session, question_id, paper_id


def _expected(task: dict[str, object]) -> dict[str, str]:
    return {"state_id": str(task["state_id"]), "state_digest": str(task["state_digest"])}


def _prepare(service, session, created):
    return service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        created["task"]["executor_id"],
    )


def _criteria_request(question_id: str, *, criteria_id: str | None = None, key: str = "criteria-proposal-1"):
    return {
        "question_id": question_id,
        "criteria_id": criteria_id,
        "proposal_goal": "Define bounded synthetic eligibility criteria.",
        "executor_id": "codex_cli",
        "approved_content_classes": ["research_routing_context", "operational_context"],
        "idempotency_key": key,
    }


def _criteria_result(task, aliases=(None, None)):
    return {
        "contract_version": "p7d-screening-criteria-proposal@1.0",
        "task_id": task["task_id"],
        "input_basis_digest": task["input_basis_digest"],
        "title": "Synthetic eligibility",
        "scope": "Synthetic records only.",
        "inclusion_criteria": [{"source_alias": aliases[0], "text": "Includes the synthetic intervention."}],
        "exclusion_criteria": [{"source_alias": aliases[1], "text": "Excludes unrelated synthetic records."}],
        "notes": "",
        "rationale": "The criteria match the bounded synthetic Question.",
        "known_limitations": [],
    }


def _commit_criteria(service, session, question_id: str):
    created = service.create_question_screening_criteria_proposal(session, _criteria_request(question_id))
    prepared = _prepare(service, session, created)
    assert prepared["handoff"]["manifest_version"] == "p7d-agent-handoff@1.0"
    assert "criterion_id" not in str(prepared["handoff"]["payload"])
    submitted = service.submit_result(
        session, prepared["task"]["task_id"], _expected(prepared["task"]), prepared["lease"], _criteria_result(prepared["task"])
    )
    approved = service.approve_question_screening_result(
        session, submitted["task"]["task_id"], _expected(submitted["task"])
    )
    return approved, submitted


def test_criteria_proposal_approval_revision_and_replay(screening_workspace) -> None:
    layout, session, question_id, _ = screening_workspace
    service = AgentTaskApplicationService()
    approved, submitted = _commit_criteria(service, session, question_id)
    screening = QuestionScreeningService(layout)
    current = screening.read_criteria(approved["screening"]["record_id"])
    bundle = next(item for item in records_of_kind(load_workspace_entries(layout), "screening-criteria-bundle") if item["criteria_id"] == current["criteria_id"])
    approval = bundle["revisions"][-1]["approval"]
    assert approval["origin"] == "user_approved_agent_proposal"
    assert approval["task_result_digest"] == canonical_digest(submitted["staged_result"])
    assert service.approve_question_screening_result(session, submitted["task"]["task_id"], _expected(submitted["task"]))["persistent_writes"] == 0

    retained_ids = [item["criterion_id"] for field in ("inclusion_criteria", "exclusion_criteria") for item in current[field]]
    created = service.create_question_screening_criteria_proposal(
        session, _criteria_request(question_id, criteria_id=current["criteria_id"], key="criteria-proposal-2")
    )
    prepared = _prepare(service, session, created)
    aliases = tuple(item["alias"] for field in ("inclusion_criteria", "exclusion_criteria") for item in prepared["handoff"]["payload"]["current_criteria"][field])
    submitted_revision = service.submit_result(
        session, prepared["task"]["task_id"], _expected(prepared["task"]), prepared["lease"], _criteria_result(prepared["task"], aliases)
    )
    service.approve_question_screening_result(session, submitted_revision["task"]["task_id"], _expected(submitted_revision["task"]))
    revised = screening.read_criteria(current["criteria_id"])
    assert [item["criterion_id"] for field in ("inclusion_criteria", "exclusion_criteria") for item in revised[field]] == retained_ids

    stale_created = service.create_question_screening_criteria_proposal(
        session, _criteria_request(question_id, criteria_id=current["criteria_id"], key="criteria-stale")
    )
    stale_prepared = _prepare(service, session, stale_created)
    stale_aliases = tuple(item["alias"] for field in ("inclusion_criteria", "exclusion_criteria") for item in stale_prepared["handoff"]["payload"]["current_criteria"][field])
    screening.promote_criteria(
        {**revised, "notes": "Concurrent direct user revision."},
        criteria_id=revised["criteria_id"],
        expected_revision_id=revised["revision_id"],
        approval={
            "receipt_id": "concurrent-screening-revision",
            "approved_by": "user",
            "approved_at": "2026-08-03T00:00:00Z",
            "origin": "user_authored",
        },
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    with pytest.raises(ResearchKBError) as stale_error:
        service.submit_result(
            session,
            stale_prepared["task"]["task_id"],
            _expected(stale_prepared["task"]),
            stale_prepared["lease"],
            _criteria_result(stale_prepared["task"], stale_aliases),
        )
    assert stale_error.value.diagnostic.code == "RKBC-017"

    recovery_created = service.create_question_screening_criteria_proposal(
        session, _criteria_request(question_id, criteria_id=current["criteria_id"], key="criteria-recovery")
    )
    recovery_prepared = _prepare(service, session, recovery_created)
    recovery_aliases = tuple(item["alias"] for field in ("inclusion_criteria", "exclusion_criteria") for item in recovery_prepared["handoff"]["payload"]["current_criteria"][field])
    recovery_result = {**_criteria_result(recovery_prepared["task"], recovery_aliases), "scope": "Recovered synthetic scope."}
    recovery_submitted = service.submit_result(
        session, recovery_prepared["task"]["task_id"], _expected(recovery_prepared["task"]), recovery_prepared["lease"], recovery_result
    )
    latest = screening.read_criteria(current["criteria_id"])
    screening.promote_criteria(
        {
            **latest,
            "scope": recovery_result["scope"],
            "inclusion_criteria": latest["inclusion_criteria"],
            "exclusion_criteria": latest["exclusion_criteria"],
        },
        criteria_id=latest["criteria_id"],
        expected_revision_id=latest["revision_id"],
        approval={
            "approved_by": "user",
            "approved_at": "2026-08-03T00:00:00Z",
            "origin": "user_approved_agent_proposal",
            "task_id": recovery_submitted["task"]["task_id"],
            "task_result_digest": canonical_digest(recovery_result),
        },
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    recovered = service.approve_question_screening_result(
        session, recovery_submitted["task"]["task_id"], _expected(recovery_submitted["task"])
    )
    assert recovered["persistent_writes"] == 1
    guardian = GuardianService(layout).check().report
    assert [item["message"] for item in guardian["findings"]] == [
        "question mapping is stale relative to its active Question-specific screening criteria or decisions"
    ]


def _decision_request(question_id: str, paper_id: str, *, key: str, include_card: bool = False):
    return {
        "question_id": question_id,
        "paper_id": paper_id,
        "basis_scope": "paper_card" if include_card else "metadata",
        "include_paper_card": include_card,
        "executor_id": "claude_code_cli",
        "approved_content_classes": ["metadata", "research_routing_context", "operational_context", *(["paper_card_content"] if include_card else [])],
        "idempotency_key": key,
    }


def _decision_result(task, aliases, outcome="included"):
    return {
        "contract_version": "p7d-screening-decision-proposal@1.0",
        "task_id": task["task_id"],
        "input_basis_digest": task["input_basis_digest"],
        "outcome": outcome,
        "criterion_dispositions": [{"criterion_alias": alias, "disposition": "met", "rationale": "Synthetic metadata is sufficient."} for alias in aliases],
        "rationale": "The paper belongs to the synthetic Question set.",
        "known_limitations": [],
    }


def test_decision_proposal_alias_closure_card_scope_and_uncertain_gate(screening_workspace) -> None:
    layout, session, question_id, paper_id = screening_workspace
    service = AgentTaskApplicationService()
    _commit_criteria(service, session, question_id)
    created = service.create_question_screening_decision_proposal(session, _decision_request(question_id, paper_id, key="decision-1"))
    prepared = _prepare(service, session, created)
    aliases = [item["alias"] for field in ("inclusion_criteria", "exclusion_criteria") for item in prepared["handoff"]["payload"]["criteria"][field]]
    with pytest.raises(ResearchKBError):
        service.submit_result(session, prepared["task"]["task_id"], _expected(prepared["task"]), prepared["lease"], _decision_result(prepared["task"], aliases[:-1]))

    submitted = service.submit_result(session, prepared["task"]["task_id"], _expected(prepared["task"]), prepared["lease"], _decision_result(prepared["task"], aliases))
    approved = service.approve_question_screening_result(session, submitted["task"]["task_id"], _expected(submitted["task"]))
    assert QuestionScreeningService(layout).read_decision(approved["screening"]["record_id"])["outcome"] == "included"

    no_change_created = service.create_question_screening_decision_proposal(session, _decision_request(question_id, paper_id, key="decision-no-change"))
    no_change_prepared = _prepare(service, session, no_change_created)
    assert "criterion_id" not in str(no_change_prepared["handoff"]["payload"]["current_decision"])
    no_change_submitted = service.submit_result(
        session, no_change_prepared["task"]["task_id"], _expected(no_change_prepared["task"]), no_change_prepared["lease"], _decision_result(no_change_prepared["task"], aliases)
    )
    no_change = service.approve_question_screening_result(
        session, no_change_submitted["task"]["task_id"], _expected(no_change_submitted["task"])
    )
    assert no_change["persistent_writes"] == 1
    assert service.approve_question_screening_result(session, no_change_submitted["task"]["task_id"], _expected(no_change_submitted["task"]))["persistent_writes"] == 0

    card_created = service.create_question_screening_decision_proposal(session, _decision_request(question_id, paper_id, key="decision-card", include_card=True))
    card_prepared = _prepare(service, session, card_created)
    assert card_prepared["handoff"]["payload"]["paper"]["paper_card"] is not None

    uncertain_created = service.create_question_screening_decision_proposal(session, _decision_request(question_id, paper_id, key="decision-uncertain"))
    uncertain_prepared = _prepare(service, session, uncertain_created)
    uncertain_submitted = service.submit_result(session, uncertain_prepared["task"]["task_id"], _expected(uncertain_prepared["task"]), uncertain_prepared["lease"], _decision_result(uncertain_prepared["task"], aliases, "uncertain"))
    assert service.preview_result(session, uncertain_submitted["task"]["task_id"])["candidate"]["approval_blocked"] is True
    with pytest.raises(ResearchKBError):
        service.approve_question_screening_result(session, uncertain_submitted["task"]["task_id"], _expected(uncertain_submitted["task"]))
    revision = service.request_revision(
        session,
        uncertain_submitted["task"]["task_id"],
        _expected(uncertain_submitted["task"]),
        "Resolve the bounded membership uncertainty.",
    )
    assert revision["successor_task"]["lineage"]["predecessor_task_id"] == uncertain_submitted["task"]["task_id"]
    with pytest.raises(ResearchKBError):
        service.approve_organization_result(session, revision["successor_task"]["task_id"], _expected(revision["successor_task"]))
    guardian = GuardianService(layout).check().report
    assert guardian["findings"] == []
