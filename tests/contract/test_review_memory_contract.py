from copy import deepcopy

import pytest

from research_kb.contracts.validator import validate_record


SECTIONS = (
    "review_objective_scope",
    "review_question_search_boundaries",
    "taxonomy_field_structure",
    "major_synthesis",
    "methods_metrics_guardrails",
    "gaps_frontiers",
    "primary_leads_reuse",
)


def review_memory_record(*, subtype: str = "narrative_review") -> dict:
    unit = {
        "review_unit_id": "reviewunit_a2222222-2222-4222-8222-222222222222",
        "section_id": "taxonomy_field_structure",
        "unit_type": "field_axis",
        "content": "The fabricated review separates two invented response classes.",
        "source_notes": [
            {
                "pdf_page": 1,
                "printed_page": None,
                "section": "Synthetic taxonomy",
                "figure_or_table": None,
                "note_type": "paraphrase",
                "text": "The invented taxonomy contains two response classes.",
                "locator": None,
                "reopen_priority": "high",
            }
        ],
        "workflow_impacts": [
            {
                "target": "primary_paper_reading",
                "action": "Separate the two fabricated response classes during later reading.",
            }
        ],
        "evidence_use": {
            "can_support_canonical_evidence": False,
            "can_guide_primary_grounding": True,
            "primary_grounding_required_before": ["comparative_claim"],
        },
        "reuse_quality": {
            "reuse_confidence": "medium",
            "staleness_risk": "low",
            "reason": "The taxonomy is explicit in the synthetic source.",
        },
        "primary_paper_lead": None,
        "background_only": True,
        "can_enter_canonical_evidence": False,
        "not_fact": True,
    }
    units = {section: [] for section in SECTIONS}
    units["taxonomy_field_structure"] = [unit]
    return {
        "schema_version": "1.0",
        "review_memory_id": "reviewmem_a1111111-1111-4111-8111-111111111111",
        "paper_id": "paper_a1111111-1111-4111-8111-111111111111",
        "source_type": "review",
        "review_subtype": subtype,
        "review_subtype_source": "agent_high_confidence",
        "review_subtype_reason": "The synthetic document explicitly presents a secondary synthesis.",
        "read_status": "targeted_read",
        "scope_tags": ["synthetic_review"],
        "one_sentence_reuse_value": "Provides a fabricated taxonomy for later primary-paper reading.",
        "memory_value": {"status": "reusable", "reason": "One actionable taxonomy is retained."},
        "coverage_limits": {
            "unread_sections": ["Synthetic appendix"],
            "weakly_read_sections": [],
            "reason": "The appendix was outside the targeted read.",
        },
        "sections": [
            {"section_id": section, "units": units[section]}
            for section in SECTIONS
        ],
        "non_reusable_notes": [
            {"content": "A broad promotional sentence was omitted.", "reason": "promotional"}
        ],
        "source_fingerprint": {"algorithm": "sha256", "value": "a" * 64},
        "parse_snapshot": {
            "parse_run_id": "event_a3333333-3333-4333-8333-333333333333",
            "adapter": "synthetic-text",
            "version": "1.0",
        },
        "background_only": True,
        "can_enter_canonical_evidence": False,
        "not_fact": True,
        "review_status": "ai_checked",
        "automation_status": "passed_auto_checks",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "fixture_origin": "synthetic_from_scratch",
    }


@pytest.mark.parametrize(
    "subtype",
    [
        "narrative_review",
        "systematic_review",
        "scoping_review",
        "meta_analysis",
        "perspective_or_commentary",
    ],
)
def test_all_review_subtypes_share_one_common_contract(subtype: str) -> None:
    assert validate_record("review-memory", review_memory_record(subtype=subtype), actor="cli") == []


def test_low_value_review_may_persist_zero_units() -> None:
    record = review_memory_record()
    record["memory_value"] = {
        "status": "low_value",
        "reason": "The synthetic review duplicates an existing orientation record.",
    }
    for section in record["sections"]:
        section["units"] = []

    assert validate_record("review-memory", record, actor="cli") == []


@pytest.mark.parametrize(("status", "remove_units"), [("reusable", True), ("low_value", False)])
def test_memory_value_must_match_reusable_unit_count(status: str, remove_units: bool) -> None:
    record = review_memory_record()
    record["memory_value"] = {"status": status, "reason": "Synthetic boundary case."}
    if remove_units:
        record["sections"][2]["units"] = []

    assert "RKBC-009" in {item.code for item in validate_record("review-memory", record, actor="cli")}


def test_review_quote_locator_requires_positive_character_range() -> None:
    record = review_memory_record()
    note = record["sections"][2]["units"][0]["source_notes"][0]
    note.update(
        {
            "note_type": "quote_excerpt",
            "text": "Synthetic excerpt",
            "locator": "page:1:char:5-5",
        }
    )

    assert "RKBC-009" in {item.code for item in validate_record("review-memory", record, actor="cli")}


def test_review_quote_locator_page_must_match_source_note_page() -> None:
    record = review_memory_record()
    note = record["sections"][2]["units"][0]["source_notes"][0]
    note.update(
        {
            "note_type": "quote_excerpt",
            "text": "Synthetic excerpt",
            "locator": "page:2:char:0-5",
        }
    )

    assert "RKBC-009" in {item.code for item in validate_record("review-memory", record, actor="cli")}


def test_fixed_section_order_and_non_evidence_flags_are_required() -> None:
    record = review_memory_record()
    record["sections"][0], record["sections"][1] = record["sections"][1], record["sections"][0]
    record["sections"][2]["units"][0]["can_enter_canonical_evidence"] = True

    assert {item.code for item in validate_record("review-memory", record, actor="cli")} == {"RKBC-002"}


def test_lead_payload_is_allowed_only_for_primary_paper_lead_unit() -> None:
    record = review_memory_record()
    unit = record["sections"][2]["units"][0]
    unit["primary_paper_lead"] = {
        "citation_label": "Synthetic Author 2024",
        "title": "Invented primary study",
        "authors": ["Synthetic Author"],
        "year": 2024,
        "doi": "10.0000/synthetic.1",
        "related_topics": ["invented response"],
        "why_follow": "It is presented as a foundational fabricated example.",
        "priority": "high",
        "priority_reasons": ["method_foundational"],
    }

    assert "RKBC-002" in {item.code for item in validate_record("review-memory", record, actor="cli")}


def test_agent_cannot_assign_human_review_status() -> None:
    record = deepcopy(review_memory_record())
    record["review_status"] = "verified"

    assert "RKBC-006" in {item.code for item in validate_record("review-memory", record, actor="agent")}
