from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.services.question_screening_application import QuestionScreeningApplicationService
from research_kb.services.workspace_session import WorkspaceSessionService
from research_kb.storage.json_io import serialize_jsonl
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


def _session(tmp_path: Path):
    layout = make_runtime_workspace(tmp_path)
    records = make_bundle("alpha")["records"]
    papers = [item["record"] for item in records if item["kind"] == "registry-paper"]
    questions = [item["record"] for item in records if item["kind"] == "question-mapping"]
    layout.registry_path.write_bytes(serialize_jsonl(papers))
    layout.question_mappings_path.write_bytes(serialize_jsonl(questions))
    return layout, WorkspaceSessionService({"alpha": layout.config.path}).open("alpha"), papers[0], questions[0]


def test_application_create_list_show_and_decide(tmp_path: Path) -> None:
    _, session, paper, question = _session(tmp_path)
    service = QuestionScreeningApplicationService()
    created = service.promote_criteria(session, {"question_id": question["question_id"], "title": "Synthetic criteria", "scope": "Synthetic scope.", "inclusion_criteria": ["Synthetic inclusion."], "exclusion_criteria": [], "notes": "", "status": "active", "receipt_id": "criteria-create"})
    criterion = created["criteria"]["inclusion_criteria"][0]
    decision = service.promote_decision(session, {"question_id": question["question_id"], "paper_id": paper["paper_id"], "outcome": "included", "criteria_revision_id": created["criteria"]["revision_id"], "criteria_digest": created["criteria"]["criteria_digest"], "criterion_dispositions": [{"criterion_id": criterion["criterion_id"], "disposition": "met", "rationale": "Synthetic."}], "basis_scope": "metadata", "rationale": "Synthetic inclusion.", "known_limitations": [], "receipt_id": "decision-create"})

    assert created["application_service_interface_version"] == "1.16"
    assert decision["result"] == "committed"
    assert service.list_criteria(session)["criteria"] == [created["criteria"]]
    assert service.list_decisions(session, freshness="current")["decisions"][0]["decision_id"] == decision["decision"]["decision_id"]
    assert service.show_decision(session, decision["decision"]["decision_id"])["decision"]["freshness"]["state"] == "current"
    assert not _forbidden_keys({"criteria": created, "decision": decision})


def test_application_is_closed_session_bound_and_user_only(tmp_path: Path) -> None:
    _, session, _, _ = _session(tmp_path)
    service = QuestionScreeningApplicationService()
    with pytest.raises(ResearchKBError):
        service.limits(object())  # type: ignore[arg-type]
    with pytest.raises(ResearchKBError):
        service.promote_criteria(session, {"receipt_id": "x", "unexpected": True})
    with pytest.raises(ResearchKBError):
        service.list_decisions(session, page_size=101)


def _forbidden_keys(value: object) -> set[str]:
    forbidden = {"source_ref", "source_fingerprint", "path", "approval", "transaction"}
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(forbidden & set(value))
        for item in value.values():
            found.update(_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_forbidden_keys(item))
    return found
