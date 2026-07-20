from __future__ import annotations

from copy import deepcopy

import pytest

from research_kb.errors import ResearchKBError
from research_kb.step7_support import (
    candidate_freshness,
    derive_support_closure,
    validate_cross_view_sources,
)
from tests.fixture_factory import make_bundle


def _entries(domain: str = "alpha") -> list[tuple[str, dict]]:
    return [
        (entry["kind"], deepcopy(entry["record"]))
        for entry in make_bundle(domain)["records"]
    ]


def _one(entries: list[tuple[str, dict]], kind: str) -> dict:
    return next(record for entry_kind, record in entries if entry_kind == kind)


def test_support_closure_uses_mapped_units_and_ignores_question_only_boundaries() -> None:
    entries = _entries()
    candidate = _one(entries, "step7-synthesis")
    mapping = _one(entries, "question-mapping")
    mapping["paper_links"][0]["boundary_refs"].append(
        next(record["queue_id"] for kind, record in entries if kind == "review-queue" and record["paper_id"] == mapping["paper_links"][0]["paper_id"] and record["queue_id"] not in mapping["paper_links"][0]["boundary_refs"])
    )

    closure = derive_support_closure(
        entries,
        question_id=candidate["question_id"],
        paper_card_base=list(reversed(candidate["paper_card_base"])),
        record_kind="step7-synthesis",
    )

    assert list(closure.paper_card_base) == sorted(candidate["paper_card_base"], key=lambda item: item["paper_id"])
    assert list(closure.evidence_base) == sorted(candidate["evidence_base"])
    assert list(closure.review_queue_refs) == sorted(candidate["review_queue_refs"])
    assert closure.input_snapshot["card_unit_ids"] == sorted(closure.input_snapshot["card_unit_ids"])
    assert candidate["question_id"] in closure.upstream_refs


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda entries, candidate: candidate["paper_card_base"].append(deepcopy(candidate["paper_card_base"][0])), "RKBC-004"),
        (
            lambda entries, candidate: candidate["paper_card_base"][0]["card_unit_ids"].append(
                next(
                    unit["unit_id"]
                    for kind, card in entries
                    if kind == "paper-card" and card["paper_id"] == candidate["paper_card_base"][0]["paper_id"]
                    for section in card["sections"]
                    for unit in section["units"]
                    if unit["grounding_status"] == "interpretive"
                )
            ),
            "RKBC-011",
        ),
        (lambda entries, candidate: _one(entries, "question-mapping").update(mapping_status="needs_resolution"), "RKBC-011"),
        (lambda entries, candidate: _one(entries, "paper-card").update(updated_at="2026-01-02T00:00:00Z"), "RKBC-011"),
    ],
)
def test_support_closure_rejects_invalid_or_stale_admission(mutate, code: str) -> None:
    entries = _entries()
    candidate = _one(entries, "step7-synthesis")
    mutate(entries, candidate)

    with pytest.raises(ResearchKBError) as caught:
        derive_support_closure(
            entries,
            question_id=candidate["question_id"],
            paper_card_base=candidate["paper_card_base"],
            record_kind="step7-synthesis",
        )

    assert caught.value.diagnostic.code == code


def test_cross_view_sources_must_be_current_admissible_and_same_question() -> None:
    entries = _entries()
    cross_view = _one(entries, "step7-cross-view")

    assert validate_cross_view_sources(
        entries,
        question_id=cross_view["question_id"],
        source_views=cross_view["source_views"],
        record_kind="step7-cross-view",
        record_id=cross_view["candidate_id"],
    ) == tuple(sorted(cross_view["source_views"]))

    source = next(record for kind, record in entries if kind == "step7-synthesis")
    source["candidate_status"] = "rejected"
    source["rejection_rationale"] = "Synthetic rejection."
    with pytest.raises(ResearchKBError) as caught:
        validate_cross_view_sources(
            entries,
            question_id=cross_view["question_id"],
            source_views=cross_view["source_views"],
            record_kind="step7-cross-view",
            record_id=cross_view["candidate_id"],
        )
    assert caught.value.diagnostic.code == "RKBC-011"


def test_candidate_freshness_orders_upstream_reasons_deterministically() -> None:
    entries = _entries()
    candidate = _one(entries, "step7-synthesis")
    mapping = next(
        record
        for kind, record in entries
        if kind == "question-mapping" and record["question_id"] == candidate["question_id"]
    )
    mapping["updated_at"] = "2026-01-02T00:00:00Z"
    mapping["paper_links"][0]["selected_card_unit_ids"] = []
    card = next(record for kind, record in entries if kind == "paper-card" and record["paper_id"] == candidate["paper_card_base"][0]["paper_id"])
    card["updated_at"] = "2026-01-02T00:00:00Z"
    selected = candidate["paper_card_base"][0]["card_unit_ids"][0]
    unit = next(unit for section in card["sections"] for unit in section["units"] if unit["unit_id"] == selected)
    unit["evidence_ids"] = []
    unit["boundary_refs"] = [
        next(record["queue_id"] for kind, record in entries if kind == "review-queue" and record["paper_id"] == card["paper_id"])
    ]
    evidence = next(record for kind, record in entries if kind == "evidence" and record["evidence_id"] in candidate["evidence_base"])
    evidence["updated_at"] = "2026-01-02T00:00:00Z"
    queue = next(record for kind, record in entries if kind == "review-queue" and record["queue_id"] in unit["boundary_refs"])
    queue["updated_at"] = "2026-01-02T00:00:00Z"
    profile = _one(entries, "domain-profile")
    profile["domain_profile"]["version"] = "1.1"

    assert candidate_freshness(candidate, entries) == {
        "state": "stale_upstream",
        "reasons": [
            "question_mapping_newer",
            "mapping_membership_changed",
            "card_newer",
            "support_expansion_changed",
            "evidence_newer",
            "boundary_expansion_changed",
            "review_queue_newer",
            "domain_profile_changed",
        ],
    }


def test_cross_view_freshness_includes_source_view_state() -> None:
    entries = _entries()
    cross_view = _one(entries, "step7-cross-view")
    source = next(record for kind, record in entries if kind == "step7-synthesis")
    source["updated_at"] = "2026-01-02T00:00:00Z"
    source["candidate_status"] = "needs_resolution"

    freshness = candidate_freshness(cross_view, entries)

    assert freshness["state"] == "stale_upstream"
    assert freshness["reasons"][-2:] == ["source_view_newer", "source_view_stale"]


def test_current_candidate_has_no_freshness_reasons() -> None:
    entries = _entries("beta")
    candidate = _one(entries, "step7-insight")
    assert candidate_freshness(candidate, entries) == {"state": "current", "reasons": []}
