from __future__ import annotations

from copy import deepcopy

from research_kb.catalog.models import canonical_digest
from research_kb.catalog import CatalogAdapterRegistry
from research_kb.organization_bundles import (
    active_organization_record,
    expand_active_organization_entries,
    organization_bundle_diagnostics,
    organization_entries_diagnostics,
    organization_link_freshness,
)
from tests.fixture_factory import make_bundle


def _direction_bundle() -> dict:
    revision = {
        "revision_id": "orgrev_a1111111-1111-4111-8111-111111111111",
        "revision_number": 1,
        "predecessor": None,
        "content_digest": None,
        "approval": {
            "origin": "user_approved_candidate",
            "approved_by": "user",
            "approved_at": "2026-01-01T00:00:00Z",
            "receipt_id": "receipt-1",
        },
        "direction": {
            "schema_version": "1.0",
            "direction_id": "direction_a1111111-1111-4111-8111-111111111111",
            "name": "Synthetic response direction",
            "scope": "Synthetic records only.",
            "status": "active",
            "links": [],
            "gap_notes": [],
        },
        "created_at": "2026-01-01T00:00:00Z",
    }
    revision["content_digest"] = canonical_digest(revision["direction"])
    return {
        "schema_version": "1.0",
        "direction_id": revision["direction"]["direction_id"],
        "active_revision_id": revision["revision_id"],
        "revisions": [revision],
        "created_at": revision["created_at"],
        "updated_at": revision["created_at"],
        "fixture_origin": "synthetic_from_scratch",
    }


def test_active_record_and_append_only_revision_chain() -> None:
    bundle = _direction_bundle()
    first = bundle["revisions"][0]
    second = deepcopy(first)
    second["revision_id"] = "orgrev_a2222222-2222-4222-8222-222222222222"
    second["revision_number"] = 2
    second["predecessor"] = {
        "revision_id": first["revision_id"],
        "revision_digest": canonical_digest(first),
    }
    second["direction"]["scope"] = "Two synthetic settings."
    second["content_digest"] = canonical_digest(second["direction"])
    second["created_at"] = "2026-01-02T00:00:00Z"
    bundle["revisions"].append(second)
    bundle["active_revision_id"] = second["revision_id"]
    bundle["updated_at"] = second["created_at"]

    assert organization_bundle_diagnostics(
        bundle,
        bundle_kind="direction-bundle",
        target_id_field="direction_id",
        child_field="direction",
    ) == []
    assert active_organization_record(bundle, child_field="direction") == second["direction"]

    second["predecessor"]["revision_digest"] = "0" * 64
    assert any(
        "predecessor" in item.message
        for item in organization_bundle_diagnostics(
            bundle,
            bundle_kind="direction-bundle",
            target_id_field="direction_id",
            child_field="direction",
        )
    )


def test_effective_projection_replaces_legacy_question_without_changing_identity() -> None:
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    legacy = next(record for kind, record in entries if kind == "question-mapping")
    successor = deepcopy(legacy)
    successor["scope"] = "Successor scope."
    revision = {
        "revision_id": "questionrev_a1111111-1111-4111-8111-111111111111",
        "revision_number": 1,
        "predecessor": {
            "basis_kind": "legacy_question_mapping",
            "basis_id": legacy["question_id"],
            "basis_digest": canonical_digest(legacy),
        },
        "content_digest": "0" * 64,
        "approval": {},
        "question_mapping": successor,
        "background_links": [],
        "created_at": "2026-01-01T00:00:00Z",
    }
    bundle = {
        "question_id": legacy["question_id"],
        "active_revision_id": revision["revision_id"],
        "revisions": [revision],
    }

    projected = expand_active_organization_entries(
        [*entries, ("question-revision-bundle", bundle)]
    )
    questions = [
        record
        for kind, record in projected
        if kind == "question-mapping" and record["question_id"] == legacy["question_id"]
    ]

    assert len(questions) == 1
    assert questions[0]["question_id"] == legacy["question_id"]
    assert questions[0]["scope"] == "Successor scope."


def test_duplicate_organization_bundle_owner_fails_closed() -> None:
    bundle = _direction_bundle()
    diagnostics = organization_entries_diagnostics(
        [("direction-bundle", bundle), ("direction-bundle", deepcopy(bundle))]
    )

    assert any(item.code == "RKBC-004" for item in diagnostics)


def test_question_successor_predecessor_must_match_exact_legacy_digest() -> None:
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    legacy = next(record for kind, record in entries if kind == "question-mapping")
    bundle = {
        "question_id": legacy["question_id"],
        "active_revision_id": "questionrev_a1111111-1111-4111-8111-111111111111",
        "revisions": [
            {
                "revision_id": "questionrev_a1111111-1111-4111-8111-111111111111",
                "revision_number": 1,
                "predecessor": {
                    "basis_kind": "legacy_question_mapping",
                    "basis_id": legacy["question_id"],
                    "basis_digest": "0" * 64,
                },
                "question_mapping": legacy,
            }
        ],
    }

    diagnostics = organization_entries_diagnostics(
        [*entries, ("question-revision-bundle", bundle)]
    )

    assert any("exact legacy base" in item.message for item in diagnostics)


def test_catalog_question_identity_is_stable_when_successor_replaces_legacy() -> None:
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    legacy = next(record for kind, record in entries if kind == "question-mapping")
    baseline = CatalogAdapterRegistry().project_entries(entries, workspace_id="workspace_test")
    legacy_item = next(
        item
        for item in baseline.documents
        if item.question_id == legacy["question_id"] and item.item_kind == "question"
    )
    revision = {
        "revision_id": "questionrev_a1111111-1111-4111-8111-111111111111",
        "question_mapping": {**deepcopy(legacy), "scope": "Successor scope."},
    }
    bundle = {
        "question_id": legacy["question_id"],
        "active_revision_id": revision["revision_id"],
        "revisions": [revision],
    }

    successor = CatalogAdapterRegistry().project_entries(
        [*entries, ("question-revision-bundle", bundle)],
        workspace_id="workspace_test",
    )
    successor_items = [
        item
        for item in successor.documents
        if item.question_id == legacy["question_id"] and item.item_kind == "question"
    ]

    assert len(successor_items) == 1
    assert successor_items[0].item_id == legacy_item.item_id
    assert successor_items[0].summary == "Successor scope."


def test_lazy_freshness_distinguishes_current_factual_context_and_review_links() -> None:
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    cards = [record for kind, record in entries if kind == "paper-card"]
    first_card = cards[0]
    factual = first_card["sections"][1]["units"][0]
    contextual = first_card["sections"][4]["units"][0]
    paper_id = first_card["paper_id"]
    revision_id = "primaryrev_a1111111-1111-4111-8111-111111111111"
    evidence = [record for kind, record in entries if kind == "evidence" and record["paper_id"] == paper_id]
    queues = [record for kind, record in entries if kind == "review-queue" and record["paper_id"] == paper_id]
    entries = [
        (kind, record)
        for kind, record in entries
        if not (kind in {"paper-card", "evidence", "review-queue"} and record.get("paper_id") == paper_id)
    ]
    entries.append(
        (
            "primary-semantic-bundle",
            {
                "paper_id": paper_id,
                "active_revision_id": revision_id,
                "revisions": [{"revision_id": revision_id, "paper_card": first_card, "evidence": evidence, "review_queue": queues}],
            },
        )
    )

    factual_link = {
        "schema_version": "1.0",
        "organization_link_id": "orglink_a1111111-1111-4111-8111-111111111111",
        "source_kind": "primary_unit",
        "paper_id": paper_id,
        "source_unit_id": factual["unit_id"],
        "source_revision_id": revision_id,
        "role": "factual_example",
        "rationale": "Synthetic factual example.",
        "evidence_ids": list(factual["evidence_ids"]),
        "background_only": False,
        "can_enter_canonical_evidence": False,
        "not_fact": False,
    }
    contextual_link = {
        **factual_link,
        "organization_link_id": "orglink_a2222222-2222-4222-8222-222222222222",
        "source_unit_id": contextual["unit_id"],
        "role": "background_context",
        "evidence_ids": [],
        "background_only": True,
        "can_enter_canonical_evidence": False,
        "not_fact": True,
    }

    assert organization_link_freshness(factual_link, entries)["status"] == "current"
    assert organization_link_freshness(contextual_link, entries)["status"] == "current"

    stale_entries = deepcopy(entries)
    stale_bundle = next(record for kind, record in stale_entries if kind == "primary-semantic-bundle" and record["paper_id"] == paper_id)
    stale_card = stale_bundle["revisions"][0]["paper_card"]
    stale_card["sections"][1]["units"] = []
    result = organization_link_freshness(factual_link, stale_entries)
    assert result["status"] == "stale_upstream"
    assert result["reasons"] == ["source_unit_superseded"]


def test_review_link_is_background_only_and_stales_when_review_unit_disappears() -> None:
    review_memory = {
        "review_memory_id": "reviewmem_a1111111-1111-4111-8111-111111111111",
        "paper_id": "paper_a1111111-1111-4111-8111-111111111111",
        "sections": [
            {
                "section_id": "taxonomy_field_structure",
                "units": [
                    {
                        "review_unit_id": "reviewunit_a1111111-1111-4111-8111-111111111111",
                        "background_only": True,
                        "can_enter_canonical_evidence": False,
                        "not_fact": True,
                        "source_notes": [{"pdf_page": 1, "section": "Synthetic taxonomy"}],
                    }
                ],
            }
        ],
    }
    revision_id = "reviewrev_a1111111-1111-4111-8111-111111111111"
    entries = [
        (
            "review-semantic-bundle",
            {
                "paper_id": review_memory["paper_id"],
                "active_revision_id": revision_id,
                "revisions": [{"revision_id": revision_id, "review_memory": review_memory}],
            },
        )
    ]
    link = {
        "schema_version": "1.0",
        "organization_link_id": "orglink_a1111111-1111-4111-8111-111111111111",
        "source_kind": "review_unit",
        "paper_id": review_memory["paper_id"],
        "review_memory_id": review_memory["review_memory_id"],
        "source_unit_id": "reviewunit_a1111111-1111-4111-8111-111111111111",
        "source_revision_id": revision_id,
        "role": "background_context",
        "rationale": "Synthetic background.",
        "evidence_ids": [],
        "background_only": True,
        "can_enter_canonical_evidence": False,
        "not_fact": True,
    }

    assert organization_link_freshness(link, entries)["status"] == "current"
    entries[0][1]["revisions"][0]["review_memory"]["sections"][0]["units"] = []
    result = organization_link_freshness(link, entries)
    assert result["status"] == "stale_upstream"
    assert result["reasons"] == ["review_source_revision_superseded"]
