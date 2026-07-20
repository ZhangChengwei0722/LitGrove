from copy import deepcopy

from research_kb.review_memory_provenance import (
    build_active_parse_index,
    lead_registry_matches,
    review_memory_freshness,
    validate_review_memory_provenance,
)
from tests.contract.test_review_memory_contract import review_memory_record


def _page(*, paper_id: str | None = None, parse_run_id: str | None = None, pdf_page: int = 1) -> dict:
    return {
        "schema_version": "1.0",
        "paper_id": paper_id or "paper_a1111111-1111-4111-8111-111111111111",
        "parse_run_id": parse_run_id or "event_a3333333-3333-4333-8333-333333333333",
        "parser": {"adapter": "synthetic-text", "version": "1.0"},
        "pdf_page": pdf_page,
        "printed_page": None,
        "text": "The fabricated review separates two invented response classes.",
        "locator": f"page:{pdf_page}:block:1",
        "created_at": "2026-01-01T00:00:00Z",
        "fixture_origin": "synthetic_from_scratch",
    }


def _quote_memory() -> dict:
    memory = review_memory_record()
    note = memory["sections"][2]["units"][0]["source_notes"][0]
    note.update(
        {
            "note_type": "quote_excerpt",
            "text": "fabricated review",
            "locator": "page:1:char:4-21",
        }
    )
    return memory


def test_exact_quote_excerpt_resolves_to_current_page_slice() -> None:
    active, failures = build_active_parse_index([_page()])

    assert failures == []
    assert review_memory_freshness(_quote_memory(), active) == "current"
    assert validate_review_memory_provenance(_quote_memory(), active) == []


def test_off_by_one_quote_excerpt_is_rejected_without_payload_echo() -> None:
    active, _ = build_active_parse_index([_page()])
    memory = _quote_memory()
    memory["sections"][2]["units"][0]["source_notes"][0]["locator"] = "page:1:char:5-21"

    failures = validate_review_memory_provenance(memory, active)

    assert [item.code for item in failures] == ["RKBC-009"]
    assert failures[0].json_path.endswith("/text")
    assert "fabricated review" not in failures[0].message


def test_missing_page_and_locator_page_mismatch_are_distinct_failures() -> None:
    active, _ = build_active_parse_index([_page()])
    missing = _quote_memory()
    missing["sections"][2]["units"][0]["source_notes"][0]["pdf_page"] = 2
    missing["sections"][2]["units"][0]["source_notes"][0]["locator"] = "page:2:char:4-21"
    mismatch = _quote_memory()
    mismatch["sections"][2]["units"][0]["source_notes"][0]["locator"] = "page:2:char:4-21"

    missing_failures = validate_review_memory_provenance(missing, active)
    mismatch_failures = validate_review_memory_provenance(mismatch, active)

    assert missing_failures[0].json_path.endswith("/pdf_page")
    assert mismatch_failures[0].json_path.endswith("/locator")
    assert {item.code for item in (*missing_failures, *mismatch_failures)} == {"RKBC-005", "RKBC-009"}


def test_paraphrase_locator_is_rejected() -> None:
    active, _ = build_active_parse_index([_page()])
    memory = review_memory_record()
    memory["sections"][2]["units"][0]["source_notes"][0]["locator"] = "page:1:char:0-3"

    failures = validate_review_memory_provenance(memory, active)

    assert [item.code for item in failures] == ["RKBC-009"]
    assert failures[0].json_path.endswith("/locator")


def test_stale_snapshot_is_not_reinterpreted_against_new_active_parse() -> None:
    active, _ = build_active_parse_index(
        [_page(parse_run_id="event_b3333333-3333-4333-8333-333333333333")]
    )
    memory = _quote_memory()
    memory["sections"][2]["units"][0]["source_notes"][0]["text"] = "not in the new parse"

    assert review_memory_freshness(memory, active) == "stale_parse"
    assert validate_review_memory_provenance(memory, active) == []


def test_malformed_active_page_set_is_reported_and_not_indexed() -> None:
    active, failures = build_active_parse_index([_page(), _page()])

    assert active == {}
    assert {item.code for item in failures} == {"RKBC-009"}


def test_primary_lead_doi_matches_are_local_exact_and_sorted() -> None:
    memory = review_memory_record()
    unit = memory["sections"][2]["units"][0]
    unit["unit_type"] = "primary_paper_lead"
    unit["primary_paper_lead"] = {
        "citation_label": "Synthetic Author 2024",
        "title": "Invented primary study",
        "authors": ["Synthetic Author"],
        "year": 2024,
        "doi": " HTTPS://DOI.ORG/10.0000/SYNTHETIC.1 ",
        "related_topics": ["invented response"],
        "why_follow": "It is a fabricated lead.",
        "priority": "high",
        "priority_reasons": ["method_foundational"],
    }
    papers = [
        {
            "paper_id": "paper_b1111111-1111-4111-8111-111111111111",
            "bibliography": {"doi": "doi:10.0000/synthetic.1"},
        },
        {
            "paper_id": "paper_a1111111-1111-4111-8111-111111111111",
            "bibliography": {"doi": "10.0000/synthetic.1"},
        },
    ]

    matches = lead_registry_matches(memory, papers)

    assert matches == [
        {
            "review_unit_id": unit["review_unit_id"],
            "status": "exact_multiple_matches",
            "matched_paper_ids": sorted(item["paper_id"] for item in papers),
        }
    ]


def test_primary_lead_without_doi_is_not_evaluable() -> None:
    memory = deepcopy(review_memory_record())
    unit = memory["sections"][2]["units"][0]
    unit["unit_type"] = "primary_paper_lead"
    unit["primary_paper_lead"] = {
        "citation_label": "Synthetic Author, undated",
        "title": None,
        "authors": [],
        "year": None,
        "doi": None,
        "related_topics": [],
        "why_follow": "It may identify a fabricated foundational method.",
        "priority": "medium",
        "priority_reasons": ["method_foundational"],
    }

    assert lead_registry_matches(memory, []) == [
        {
            "review_unit_id": unit["review_unit_id"],
            "status": "not_evaluable_no_doi",
            "matched_paper_ids": [],
        }
    ]


def test_primary_lead_with_unmatched_doi_reports_registry_scope_only() -> None:
    memory = review_memory_record()
    unit = memory["sections"][2]["units"][0]
    unit["unit_type"] = "primary_paper_lead"
    unit["primary_paper_lead"] = {
        "citation_label": "Synthetic Author 2024",
        "title": "Invented primary study",
        "authors": ["Synthetic Author"],
        "year": 2024,
        "doi": "10.0000/unmatched.synthetic",
        "related_topics": [],
        "why_follow": "It is a fabricated lead.",
        "priority": "low",
        "priority_reasons": ["recent_frontier"],
    }

    assert lead_registry_matches(memory, []) == [
        {
            "review_unit_id": unit["review_unit_id"],
            "status": "no_registered_doi_match",
            "matched_paper_ids": [],
        }
    ]
