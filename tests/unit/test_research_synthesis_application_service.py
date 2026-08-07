from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.services import ResearchSynthesisApplicationService
from tests.unit.test_research_synthesis_agent_task import _workspace


def test_browser_safe_candidate_reads_and_question_context(tmp_path: Path) -> None:
    _, session, by_kind = _workspace(tmp_path)
    service = ResearchSynthesisApplicationService()
    question_id = by_kind["step7-synthesis"][0]["question_id"]

    limits = service.limits(session)
    assert limits["application_service_interface_version"] == "1.21"
    assert limits["ordinary_query_can_write"] is False
    listed = service.list_candidates(session, question_id=question_id, page_size=2)
    assert len(listed["candidates"]) == 2
    assert listed["next_cursor"] is not None
    assert all("input_snapshot" not in item and "approval" not in item for item in listed["candidates"])

    shown = service.show_candidate(session, listed["candidates"][0]["candidate_id"])["candidate"]
    assert shown["not_fact"] is True
    assert "type_content" in shown
    assert "input_snapshot" not in shown

    context = service.question_context(session, question_id)
    assert context["candidate_count"] == 3
    assert context["question"]["factual_link_count"] == 2
    assert context["persistent_writes"] == 0


def test_candidate_reads_validate_filters_and_cursor(tmp_path: Path) -> None:
    _, session, _ = _workspace(tmp_path)
    service = ResearchSynthesisApplicationService()
    with pytest.raises(ResearchKBError):
        service.list_candidates(session, candidate_type="invented")
    with pytest.raises(ResearchKBError):
        service.list_candidates(session, page_size=0)
    with pytest.raises(ResearchKBError):
        service.list_candidates(session, cursor="missing")
