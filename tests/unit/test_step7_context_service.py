from __future__ import annotations

from copy import deepcopy

import pytest

from research_kb.errors import ResearchKBError
from research_kb.services.step7_context import project_step7_context
from tests.fixture_factory import make_bundle


def _entries(domain: str = "alpha") -> list[tuple[str, dict]]:
    return [(item["kind"], deepcopy(item["record"])) for item in make_bundle(domain)["records"]]


def test_context_returns_only_selected_question_candidates_in_fixed_order() -> None:
    entries = _entries()
    question_id = next(record["question_id"] for kind, record in entries if kind == "step7-synthesis")

    result = project_step7_context(entries, question_id)

    assert result["status"] == "success"
    assert result["interface_version"] == "1.0"
    assert [item["candidate"]["type"] for item in result["candidates"]] == [
        "synthesis",
        "review_angle",
        "cross_view",
    ]
    assert result["summary"] == {
        "total": 3,
        "by_type": {"synthesis": 1, "review_angle": 1, "insight": 0, "cross_view": 1},
        "by_status": {"keep": 3, "revise": 0, "rejected": 0, "needs_resolution": 0},
        "stale_count": 0,
    }
    assert all(item["freshness"] == {"state": "current", "reasons": []} for item in result["candidates"])


def test_context_projects_stale_candidate_without_mutating_it() -> None:
    entries = _entries()
    candidate = next(record for kind, record in entries if kind == "step7-insight")
    mapping = next(record for kind, record in entries if kind == "question-mapping" and record["question_id"] == candidate["question_id"])
    mapping["updated_at"] = "2026-01-02T00:00:00Z"
    before = deepcopy(candidate)

    result = project_step7_context(entries, candidate["question_id"])

    assert result["summary"]["stale_count"] == 1
    assert result["candidates"][0]["freshness"] == {
        "state": "stale_upstream",
        "reasons": ["question_mapping_newer"],
    }
    assert candidate == before


def test_context_missing_or_invalid_question_uses_structured_diagnostic() -> None:
    entries = _entries()
    for question_id, code in (
        ("not-an-id", "RKBC-002"),
        ("question_f0000000-0000-4000-8000-000000000000", "RKBC-005"),
    ):
        with pytest.raises(ResearchKBError) as caught:
            project_step7_context(entries, question_id)
        assert caught.value.diagnostic.code == code


def test_context_is_domain_neutral() -> None:
    entries = _entries("beta")
    question_id = next(record["question_id"] for kind, record in entries if kind == "step7-synthesis")
    result = project_step7_context(entries, question_id)
    assert result["question_mapping"]["domain_profile_id"] == "domain-beta"
    assert "alpha" not in str(result).lower()
