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


def test_support_closure_reads_active_primary_bundle_children() -> None:
    entries = _entries()
    candidate = _one(entries, "step7-synthesis")
    paper_id = candidate["paper_card_base"][0]["paper_id"]
    card = next(record for kind, record in entries if kind == "paper-card" and record["paper_id"] == paper_id)
    evidence = [record for kind, record in entries if kind == "evidence" and record["paper_id"] == paper_id]
    queues = [record for kind, record in entries if kind == "review-queue" and record["paper_id"] == paper_id]
    entries = [
        (kind, record)
        for kind, record in entries
        if not (
            record.get("paper_id") == paper_id
            and kind in {"paper-card", "evidence", "review-queue"}
        )
    ]
    revision_id = "primaryrev_a1111111-1111-4111-8111-111111111111"
    entries.append(
        (
            "primary-semantic-bundle",
            {
                "paper_id": paper_id,
                "active_revision_id": revision_id,
                "revisions": [
                    {
                        "revision_id": revision_id,
                        "paper_card": card,
                        "evidence": evidence,
                        "review_queue": queues,
                    }
                ],
            },
        )
    )

    closure = derive_support_closure(
        entries,
        question_id=candidate["question_id"],
        paper_card_base=candidate["paper_card_base"],
        record_kind="step7-synthesis",
    )

    assert set(closure.evidence_base) == set(candidate["evidence_base"])
    assert set(closure.review_queue_refs) == set(candidate["review_queue_refs"])


def test_review_background_is_derived_from_current_question_and_review_revisions() -> None:
    entries = _entries()
    candidate = _one(entries, "step7-synthesis")
    mapping = next(
        record
        for kind, record in entries
        if kind == "question-mapping" and record["question_id"] == candidate["question_id"]
    )
    paper_id = mapping["paper_links"][0]["paper_id"]
    memory_id = "reviewmem_a1111111-1111-4111-8111-111111111111"
    review_unit_id = "reviewunit_a1111111-1111-4111-8111-111111111111"
    review_revision_id = "reviewrev_a1111111-1111-4111-8111-111111111111"
    question_revision_id = "questionrev_a1111111-1111-4111-8111-111111111111"
    background_id = "qbackground_a1111111-1111-4111-8111-111111111111"
    review_memory = {
        "review_memory_id": memory_id,
        "paper_id": paper_id,
        "sections": [
            {
                "section_id": "taxonomy_field_structure",
                "units": [
                    {
                        "review_unit_id": review_unit_id,
                        "background_only": True,
                        "can_enter_canonical_evidence": False,
                        "not_fact": True,
                        "source_notes": [{"pdf_page": 1, "section": "Synthetic background"}],
                    }
                ],
            }
        ],
    }
    entries.append(
        (
            "review-semantic-bundle",
            {
                "paper_id": paper_id,
                "active_revision_id": review_revision_id,
                "revisions": [
                    {
                        "revision_id": review_revision_id,
                        "review_memory": review_memory,
                    }
                ],
            },
        )
    )
    link = {
        "schema_version": "1.0",
        "organization_link_id": "orglink_a1111111-1111-4111-8111-111111111111",
        "source_kind": "review_unit",
        "paper_id": paper_id,
        "review_memory_id": memory_id,
        "source_unit_id": review_unit_id,
        "source_revision_id": review_revision_id,
        "role": "question_background",
        "rationale": "Synthetic background only.",
        "evidence_ids": [],
        "background_only": True,
        "can_enter_canonical_evidence": False,
        "not_fact": True,
    }
    entries.append(
        (
            "question-revision-bundle",
            {
                "question_id": candidate["question_id"],
                "active_revision_id": question_revision_id,
                "revisions": [
                    {
                        "revision_id": question_revision_id,
                        "question_mapping": mapping,
                        "background_links": [
                            {"question_background_id": background_id, "link": link}
                        ],
                    }
                ],
            },
        )
    )

    closure = derive_support_closure(
        entries,
        question_id=candidate["question_id"],
        paper_card_base=candidate["paper_card_base"],
        review_background_unit_ids=[review_unit_id],
        record_kind="step7-synthesis",
    )

    assert closure.review_background_base == (
        {
            "paper_id": paper_id,
            "review_memory_id": memory_id,
            "review_revision_id": review_revision_id,
            "question_background_ids": [background_id],
            "review_unit_ids": [review_unit_id],
        },
    )
    assert review_unit_id in closure.input_snapshot["review_unit_ids"]
    assert review_unit_id not in closure.evidence_base

    projected = deepcopy(candidate)
    projected["review_background_base"] = [dict(closure.review_background_base[0])]
    projected["input_snapshot"]["review_unit_ids"] = [review_unit_id]
    assert candidate_freshness(projected, entries) == {"state": "current", "reasons": []}

    link["source_revision_id"] = "reviewrev_a2222222-2222-4222-8222-222222222222"
    assert candidate_freshness(projected, entries)["reasons"] == [
        "review_background_changed"
    ]
