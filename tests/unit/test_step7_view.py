from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from research_kb.services.step7_view import Step7ReadingViewService
from tests.fixture_factory import make_bundle


ROOT = Path(__file__).resolve().parents[2]


def _entries(domain: str = "alpha") -> list[tuple[str, dict]]:
    return [(item["kind"], deepcopy(item["record"])) for item in make_bundle(domain)["records"]]


def _all_four_entries() -> tuple[list[tuple[str, dict]], str]:
    entries = _entries()
    synthesis = next(record for kind, record in entries if kind == "step7-synthesis")
    insight = next(record for kind, record in entries if kind == "step7-insight")
    insight["question_id"] = synthesis["question_id"]
    mapping = next(
        record
        for kind, record in entries
        if kind == "question-mapping" and record["question_id"] == synthesis["question_id"]
    )
    link = next(item for item in mapping["paper_links"] if item["paper_id"] == insight["paper_card_base"][0]["paper_id"])
    link["selected_card_unit_ids"] = sorted(set(link["selected_card_unit_ids"] + insight["paper_card_base"][0]["card_unit_ids"]))
    link["evidence_ids"] = sorted(set(link["evidence_ids"] + insight["evidence_base"]))
    return entries, synthesis["question_id"]


def test_render_alpha_all_types_matches_reviewed_golden() -> None:
    entries, question_id = _all_four_entries()
    rendered = Step7ReadingViewService(entries).render(question_id)
    assert rendered == (
        ROOT / "tests" / "fixtures" / "rendered" / "step7_reading_view_alpha.md"
    ).read_bytes()


def test_render_is_deterministic_complete_and_noncanonical() -> None:
    entries = _entries()
    question_id = next(record["question_id"] for kind, record in entries if kind == "step7-synthesis")
    service = Step7ReadingViewService(entries)

    first = service.render(question_id)
    second = service.render(question_id)

    assert first == second
    rendered = first.decode("utf-8")
    assert 'view_type: "step7_reading_view"' in rendered
    assert "canonical: false\ngenerated_view: true\neditable_source: false" in rendered
    assert "## Synthesis" in rendered
    assert "## Review Angles" in rendered
    assert "## Insights\n\nNone." in rendered
    assert "## Cross-Views" in rendered
    assert "#### Canonical Evidence Base" in rendered
    assert "#### Review Queue Boundaries (Not Evidence)" in rendered
    assert "#### Missing Evidence" in rendered
    assert "#### Assumptions" in rendered
    assert "#### Risk" in rendered
    assert "#### Testability" in rendered
    assert "#### Next Action" in rendered
    assert "2026-01-01" not in rendered
    assert "study-one.txt" not in rendered
    assert b"\r" not in first
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")


def test_render_includes_stale_and_rejected_boundaries() -> None:
    entries = _entries()
    synthesis = next(record for kind, record in entries if kind == "step7-synthesis")
    synthesis["candidate_status"] = "rejected"
    synthesis["rejection_rationale"] = "The synthetic comparison is too broad."
    synthesis["updated_at"] = "2026-01-02T00:00:00Z"
    question_id = synthesis["question_id"]

    rendered = Step7ReadingViewService(entries).render(question_id).decode("utf-8")

    assert "- Candidate Status: `rejected`" in rendered
    assert "- Rejection Rationale: The synthetic comparison is too broad." in rendered
    assert "- Freshness: `stale_upstream`" in rendered
    assert "`source_view_newer`, `source_view_stale`" in rendered


def test_render_empty_candidate_groups_and_second_domain() -> None:
    entries = [
        (kind, record)
        for kind, record in _entries("beta")
        if not kind.startswith("step7-")
    ]
    question_id = next(record["question_id"] for kind, record in entries if kind == "question-mapping")

    rendered = Step7ReadingViewService(entries).render(question_id).decode("utf-8")

    assert 'candidate_count: 0\nstale_count: 0' in rendered
    assert rendered.count("None.") == 4
    assert "Fictional Beta" not in rendered
    assert question_id in rendered
    assert "domain-alpha" not in rendered
    assert rendered.encode("utf-8") == (
        ROOT / "tests" / "fixtures" / "rendered" / "step7_reading_view_beta_empty.md"
    ).read_bytes()
