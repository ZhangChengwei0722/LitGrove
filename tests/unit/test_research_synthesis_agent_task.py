from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from research_kb.errors import ResearchKBError
from research_kb.mutation import MutationRequest
from research_kb.services import AgentTaskApplicationService, ResearchOrganizationService, WorkspaceSessionService
from research_kb.services.step7_candidate import Step7CandidateService
from research_kb.storage.json_io import serialize_jsonl
from research_kb.workspace import WorkspaceLayout
from tests.unit.test_step7_candidate_service import CORE_OWNED, _seed_workspace


P8_CLASSES = [
    "metadata",
    "canonical_evidence",
    "paper_card_content",
    "review_background",
    "research_routing_context",
    "research_synthesis",
    "operational_context",
]


def _workspace(tmp_path: Path):
    layout, by_kind = _seed_workspace(tmp_path)
    for kind in ("step7-insight", "step7-cross-view"):
        layout.step7_store_path(kind).write_bytes(serialize_jsonl(by_kind[kind]))
    config = yaml.safe_load(layout.config.path.read_text(encoding="utf-8"))
    config["agent_policy"] = {
        "registry_version": "p8-v1",
        "allowed_content_classes": P8_CLASSES,
        "execution_scope": "cloud_allowed",
        "max_prompt_bytes": 2_097_152,
        "max_result_bytes": 1_048_576,
    }
    layout.config.path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n")
    refreshed = WorkspaceLayout.load(layout.config.path)
    session = WorkspaceSessionService({"alpha": refreshed.config.path}).open("alpha")
    return refreshed, session, by_kind


def _expected(task: dict[str, object]) -> dict[str, str]:
    return {"state_id": str(task["state_id"]), "state_digest": str(task["state_digest"])}


def _payload(source: dict) -> dict:
    excluded = CORE_OWNED | {"review_background_base", "approval"}
    return {key: deepcopy(value) for key, value in source.items() if key not in excluded}


def _request(source: dict, *, intent: str, key: str) -> dict[str, object]:
    return {
        "question_id": source["question_id"],
        "candidate_type": source["type"],
        "maintenance_intent": intent,
        "target_candidate_id": source["candidate_id"] if intent == "replace" else None,
        "maintenance_goal": f"Maintain the bounded synthetic {source['type']} candidate.",
        "include_review_background": False,
        "executor_id": "codex_cli",
        "approved_content_classes": P8_CLASSES,
        "idempotency_key": key,
    }


def _result(task: dict, source: dict, *, intent: str, duplicate: str | None = None) -> dict:
    payload = _payload(source)
    if intent == "replace":
        payload["title"] = f"{payload['title']} revised"
    return {
        "contract_version": "p8-research-synthesis-proposal@1.0",
        "task_id": task["task_id"],
        "input_basis_digest": task["input_basis_digest"],
        "candidate_type": source["type"],
        "maintenance_intent": intent,
        "target_candidate_id": source["candidate_id"] if intent == "replace" else None,
        "duplicate_disposition": duplicate or ("updates_target" if intent == "replace" else "distinct"),
        "payload": payload,
    }


@pytest.mark.parametrize(
    "kind",
    ["step7-synthesis", "step7-review-angle", "step7-insight", "step7-cross-view"],
)
@pytest.mark.parametrize("intent", ["append", "replace"])
def test_four_types_append_replace_use_dedicated_approval_and_replay(
    tmp_path: Path,
    kind: str,
    intent: str,
) -> None:
    _, session, by_kind = _workspace(tmp_path)
    service = AgentTaskApplicationService()
    source = by_kind[kind][0]
    created = service.create_research_synthesis_proposal(
        session,
        _request(source, intent=intent, key=f"{kind}-{intent}"),
    )
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        created["task"]["executor_id"],
    )
    assert prepared["handoff"]["manifest_version"] == "p8-agent-handoff@1.0"
    assert all(
        set(item) == {
            "evidence_id", "paper_id", "claim", "evidence_type", "quote", "source_page",
            "locator", "support_scope", "what_it_does_not_support",
        }
        for item in prepared["handoff"]["payload"]["canonical_evidence"]
    )
    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        _result(prepared["task"], source, intent=intent),
    )
    preview = service.preview_result(session, submitted["task"]["task_id"])
    assert preview["candidate"]["approval_blocked"] is False
    with pytest.raises(ResearchKBError):
        service.approve_question_screening_result(
            session,
            submitted["task"]["task_id"],
            _expected(submitted["task"]),
        )
    approved = service.approve_research_synthesis_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )
    assert approved["research_synthesis"]["candidate_type"] == source["type"]
    replay = service.approve_research_synthesis_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )
    assert replay["persistent_writes"] == 0


def test_uncertain_near_duplicate_is_previewed_but_cannot_approve(tmp_path: Path) -> None:
    _, session, by_kind = _workspace(tmp_path)
    service = AgentTaskApplicationService()
    source = by_kind["step7-insight"][0]
    created = service.create_research_synthesis_proposal(
        session,
        _request(source, intent="append", key="uncertain-near-duplicate"),
    )
    prepared = service.prepare_handoff(
        session, created["task"]["task_id"], _expected(created["task"]), created["task"]["executor_id"]
    )
    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        _result(prepared["task"], source, intent="append", duplicate="uncertain_near_duplicate"),
    )
    assert service.preview_result(session, submitted["task"]["task_id"])["candidate"]["approval_blocked"] is True
    with pytest.raises(ResearchKBError, match="near-duplicate"):
        service.approve_research_synthesis_result(
            session, submitted["task"]["task_id"], _expected(submitted["task"])
        )


def test_approved_candidate_requires_a_new_user_approved_proposal_for_replace(tmp_path: Path) -> None:
    layout, session, by_kind = _workspace(tmp_path)
    service = AgentTaskApplicationService()
    source = by_kind["step7-insight"][0]
    created = service.create_research_synthesis_proposal(
        session,
        _request(source, intent="replace", key="approved-replace-boundary"),
    )
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        created["task"]["executor_id"],
    )
    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        _result(prepared["task"], source, intent="replace"),
    )
    approved = service.approve_research_synthesis_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )
    payload = _payload(source)
    payload["title"] = "Unapproved direct replacement"
    request = MutationRequest(
        operation="replace",
        record_kind="step7-insight",
        target_record_id=source["candidate_id"],
        paper_id=None,
        question_origin="existing_question",
        payload=payload,
    )

    with pytest.raises(ResearchKBError) as caught:
        Step7CandidateService(layout).promote(request, actor="agent")

    assert caught.value.diagnostic.code == "RKBC-006"
    assert approved["research_synthesis"]["candidate_id"] == source["candidate_id"]


def test_all_drafting_requires_existing_synthesis_scope_and_replace_cannot_be_distinct(tmp_path: Path) -> None:
    _, session, by_kind = _workspace(tmp_path)
    service = AgentTaskApplicationService()
    cross_view = by_kind["step7-cross-view"][0]
    request = _request(cross_view, intent="append", key="cross-view-without-context")
    request["approved_content_classes"] = [item for item in P8_CLASSES if item != "research_synthesis"]
    with pytest.raises(ResearchKBError, match="lacks a required content class"):
        service.create_research_synthesis_proposal(session, request)

    insight_without_context = _request(
        by_kind["step7-insight"][0],
        intent="append",
        key="insight-without-comparison-context",
    )
    insight_without_context["approved_content_classes"] = [
        item for item in P8_CLASSES if item != "research_synthesis"
    ]
    with pytest.raises(ResearchKBError, match="lacks a required content class"):
        service.create_research_synthesis_proposal(session, insight_without_context)

    insight = by_kind["step7-insight"][0]
    created = service.create_research_synthesis_proposal(
        session, _request(insight, intent="replace", key="replace-distinct")
    )
    prepared = service.prepare_handoff(
        session, created["task"]["task_id"], _expected(created["task"]), created["task"]["executor_id"]
    )
    with pytest.raises(ResearchKBError, match="distinct disposition requires append"):
        service.submit_result(
            session,
            prepared["task"]["task_id"],
            _expected(prepared["task"]),
            prepared["lease"],
            _result(prepared["task"], insight, intent="replace", duplicate="distinct"),
        )


def test_active_question_revision_is_in_basis_and_concurrent_revision_rejects_submit(tmp_path: Path) -> None:
    layout, session, by_kind = _workspace(tmp_path)
    source = by_kind["step7-synthesis"][0]
    legacy = next(item for item in by_kind["question-mapping"] if item["question_id"] == source["question_id"])
    legacy["updated_at"] = "2025-12-31T23:59:59Z"
    layout.question_mappings_path.write_bytes(serialize_jsonl(by_kind["question-mapping"]))
    factual_links = [
        {
            "paper_id": item["paper_id"],
            "selected_card_unit_ids": item["selected_card_unit_ids"],
            "role_in_question": item["role_in_question"],
            "relevance_rationale": item["relevance_rationale"],
            "boundary_refs": item["boundary_refs"],
        }
        for item in legacy["paper_links"]
    ]
    organization = ResearchOrganizationService(layout)
    first_bundle, _ = organization.promote_question(
        {
            "question_text": legacy["question_text"],
            "scope": legacy["scope"],
            "mapping_status": legacy["mapping_status"],
            "factual_links": factual_links,
            "background_links": [],
        },
        question_id=legacy["question_id"],
        approval={"receipt_id": "p8-question-1", "approved_by": "user", "approved_at": "2026-08-03T00:00:00Z", "origin": "user_authored"},
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    service = AgentTaskApplicationService()
    created = service.create_research_synthesis_proposal(
        session, _request(source, intent="append", key="active-question-basis")
    )
    assert created["task"]["question_id"] == source["question_id"]
    prepared = service.prepare_handoff(
        session, created["task"]["task_id"], _expected(created["task"]), created["task"]["executor_id"]
    )
    organization.promote_question(
        {
            "question_text": legacy["question_text"],
            "scope": "Revised synthetic scope.",
            "mapping_status": legacy["mapping_status"],
            "factual_links": factual_links,
            "background_links": [],
        },
        question_id=legacy["question_id"],
        approval={"receipt_id": "p8-question-2", "approved_by": "user", "approved_at": "2026-08-03T00:01:00Z", "origin": "user_authored"},
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    assert first_bundle["active_revision_id"] != organization.read_question(legacy["question_id"])["revision_id"]
    with pytest.raises(ResearchKBError, match="input basis"):
        service.submit_result(
            session,
            prepared["task"]["task_id"],
            _expected(prepared["task"]),
            prepared["lease"],
            _result(prepared["task"], source, intent="append"),
        )
