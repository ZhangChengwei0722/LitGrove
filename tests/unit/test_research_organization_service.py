from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from research_kb.bundle import records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError
from research_kb.contracts.validator import validate_bundle
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.storage.json_io import read_json_document, serialize_json
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


def _entries() -> list[tuple[str, dict]]:
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    cards = [record for kind, record in entries if kind == "paper-card"]
    revisions = {
        card["paper_id"]: f"primaryrev_{index:08x}-0000-4000-8000-{index:012d}"
        for index, card in enumerate(cards, start=1)
    }
    bundled: list[tuple[str, dict]] = [
        (kind, record)
        for kind, record in entries
        if kind not in {"paper-card", "evidence", "review-queue"}
    ]
    for card in cards:
        paper_id = card["paper_id"]
        revision_id = revisions[paper_id]
        revision = {
            "revision_id": revision_id,
            "revision_number": 1,
            "predecessor": None,
            "paper_card": card,
            "evidence": [record for kind, record in entries if kind == "evidence" and record["paper_id"] == paper_id],
            "review_queue": [record for kind, record in entries if kind == "review-queue" and record["paper_id"] == paper_id],
        }
        bundled.append(
            (
                "primary-semantic-bundle",
                {
                    "paper_id": paper_id,
                    "active_revision_id": revision_id,
                    "revisions": [revision],
                },
            )
        )
    return bundled


def _approval() -> dict:
    return {
        "task_id": "task_a1111111-1111-4111-8111-111111111111",
        "task_result_digest": "d" * 64,
        "origin": "user_approved_agent_proposal",
        "approved_by": "user",
        "approved_at": "2026-01-03T00:00:00Z",
    }


def _primary_link(entries: list[tuple[str, dict]], *, role: str = "factual_example") -> dict:
    card = records_of_kind(entries, "paper-card")[0]
    unit = next(
        unit
        for section in card["sections"]
        for unit in section["units"]
        if unit["grounding_status"] == ("grounded" if role == "factual_example" else "interpretive")
    )
    return {
        "source_kind": "primary",
        "paper_id": card["paper_id"],
        "unit_id": unit["unit_id"],
        "role": role,
        "rationale": "Synthetic organization rationale.",
    }


def _service(layout, entries):
    counters: dict[str, int] = {}

    def allocate(namespace):
        value = namespace.value
        counters[value] = counters.get(value, 0) + 1
        number = counters[value]
        return f"{value}_a{number:07d}-0000-4000-8000-{number:012d}"

    return ResearchOrganizationService(
        layout,
        id_allocator=allocate,
        entries_loader=lambda _: deepcopy(entries),
    )


def test_direction_append_successor_and_exact_rerun_no_change(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    service = _service(layout, entries)
    payload = {
        "name": "Synthetic response direction",
        "scope": "Synthetic records only.",
        "status": "active",
        "unit_links": [_primary_link(entries)],
        "gap_notes": ["A fabricated replication is absent."],
    }

    first, first_tx = service.promote_direction(
        payload,
        approval=_approval(),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    repeated, repeated_tx = service.promote_direction(
        payload,
        target_id=first["direction_id"],
        approval=_approval(),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    assert first_tx is not None
    assert repeated_tx is None
    assert repeated == first
    assert len(first["revisions"]) == 1
    stored = read_json_document(layout.direction_bundle_path(first["direction_id"]))
    assert stored == first

    changed = {**payload, "scope": "Two synthetic settings."}
    second, second_tx = service.promote_direction(
        changed,
        target_id=first["direction_id"],
        approval=_approval(),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    assert second_tx is not None
    assert len(second["revisions"]) == 2
    assert second["revisions"][1]["predecessor"] == {
        "revision_id": first["active_revision_id"],
        "revision_digest": canonical_digest(first["revisions"][0]),
    }
    diagnostics = validate_bundle(
        {
            "records": [
                *({"kind": kind, "record": record} for kind, record in entries),
                {"kind": "direction-bundle", "record": second},
            ]
        },
        actor="stored",
    )
    link_id = second["revisions"][0]["direction"]["links"][0]["organization_link_id"]
    assert not any(item.code == "RKBC-004" and item.record_id == link_id for item in diagnostics)


@pytest.mark.parametrize("grounding_status", ["needs_resolution", "background_only"])
def test_factual_link_rejects_non_admissible_primary_unit(tmp_path: Path, grounding_status: str) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    card, unit = next(
        (card, unit)
        for card in records_of_kind(entries, "paper-card")
        for section in card["sections"]
        for unit in section["units"]
        if unit["grounding_status"] == grounding_status
    )
    payload = {
        "name": "Rejected synthetic direction",
        "scope": "Synthetic records only.",
        "status": "active",
        "unit_links": [
            {
                "source_kind": "primary",
                "paper_id": card["paper_id"],
                "unit_id": unit["unit_id"],
                "role": "factual_example",
                "rationale": "This must be rejected.",
            }
        ],
        "gap_notes": [],
    }

    with pytest.raises(ResearchKBError) as caught:
        _service(layout, entries).promote_direction(payload, approval=_approval(), actor="user")

    assert caught.value.diagnostic.code == "RKBC-009"


def test_contextual_primary_link_has_no_evidence_and_review_link_is_background_only(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    review_memory = {
        "review_memory_id": "reviewmem_a1111111-1111-4111-8111-111111111111",
        "paper_id": entries[2][1]["paper_id"],
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
    entries.append(("review-memory", review_memory))
    entries.append(
        (
            "review-semantic-bundle",
            {
                "paper_id": review_memory["paper_id"],
                "active_revision_id": "reviewrev_a1111111-1111-4111-8111-111111111111",
                "revisions": [
                    {
                        "revision_id": "reviewrev_a1111111-1111-4111-8111-111111111111",
                        "revision_number": 1,
                        "predecessor": None,
                        "review_memory": review_memory,
                    }
                ],
            },
        )
    )
    service = _service(layout, entries)
    payload = {
        "title": "Synthetic field entry",
        "entry_type": "frontier",
        "definition": "A fabricated organization node.",
        "status": "active",
        "consensus_level": "single_review",
        "direction_refs": [],
        "unit_links": [
            _primary_link(entries, role="background_context"),
            {
                "source_kind": "review",
                "paper_id": review_memory["paper_id"],
                "review_memory_id": review_memory["review_memory_id"],
                "unit_id": review_memory["sections"][0]["units"][0]["review_unit_id"],
                "role": "background_context",
                "rationale": "Synthetic review background.",
            },
        ],
        "aspect_notes": [],
    }

    bundle, transaction = service.promote_field_map_entry(
        payload,
        approval=_approval(),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    assert transaction is not None
    links = bundle["revisions"][0]["field_map_entry"]["links"]
    assert all(link["evidence_ids"] == [] for link in links)
    assert all(link["background_only"] is True for link in links)
    assert all(link["can_enter_canonical_evidence"] is False for link in links)
    assert all(link["not_fact"] is True for link in links)


def test_legacy_question_becomes_predecessor_basis_without_rewriting_legacy(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    legacy = next(record for kind, record in entries if kind == "question-mapping")
    legacy_before = deepcopy(legacy)
    service = _service(layout, entries)
    payload = {
        "question_text": legacy["question_text"],
        "scope": legacy["scope"],
        "mapping_status": "ai_checked",
        "factual_links": [
            {
                "paper_id": link["paper_id"],
                "selected_card_unit_ids": link["selected_card_unit_ids"],
                "role_in_question": link["role_in_question"],
                "relevance_rationale": link["relevance_rationale"],
                "boundary_refs": link["boundary_refs"],
            }
            for link in legacy["paper_links"]
        ],
        "background_links": [],
    }

    bundle, transaction = service.promote_question(
        payload,
        question_id=legacy["question_id"],
        approval=_approval(),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    assert transaction is not None
    assert bundle["revisions"][0]["predecessor"] == {
        "basis_kind": "legacy_question_mapping",
        "basis_id": legacy["question_id"],
        "basis_digest": canonical_digest(legacy),
    }
    assert legacy == legacy_before
    projected = service.read_question(legacy["question_id"])
    assert projected["paper_links"][0]["freshness"]["status"] == "current"
    assert projected["paper_links"][0]["factual_support_eligible"] is True


def test_read_projects_lazy_freshness_without_rewriting_bundle(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    service = _service(layout, entries)
    payload = {
        "name": "Synthetic response direction",
        "scope": "Synthetic records only.",
        "status": "active",
        "unit_links": [_primary_link(entries)],
        "gap_notes": [],
    }
    bundle, _ = service.promote_direction(payload, approval=_approval(), actor="user")
    before = layout.direction_bundle_path(bundle["direction_id"]).read_bytes()

    current = service.read_direction(bundle["direction_id"])
    assert current["links"][0]["freshness"]["status"] == "current"

    stale_entries = deepcopy(entries)
    primary = next(record for kind, record in stale_entries if kind == "primary-semantic-bundle")
    primary["revisions"][0]["paper_card"]["sections"][1]["units"] = []
    stale_service = ResearchOrganizationService(
        layout,
        entries_loader=lambda _: stale_entries,
    )
    stale = stale_service.read_direction(bundle["direction_id"])
    assert stale["links"][0]["freshness"]["status"] == "stale_upstream"
    assert layout.direction_bundle_path(bundle["direction_id"]).read_bytes() == before


def test_direct_read_rejects_corrupted_revision_chain(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    service = _service(layout, entries)
    bundle, _ = service.promote_direction(
        {
            "name": "Synthetic response direction",
            "scope": "Synthetic records only.",
            "status": "active",
            "unit_links": [_primary_link(entries)],
            "gap_notes": [],
        },
        approval=_approval(),
        actor="user",
    )
    path = layout.direction_bundle_path(bundle["direction_id"])
    corrupted = read_json_document(path)
    corrupted["revisions"][0]["content_digest"] = "0" * 64
    path.write_bytes(serialize_json(corrupted))

    with pytest.raises(ResearchKBError) as caught:
        service.read_direction(bundle["direction_id"])

    assert caught.value.diagnostic.code == "RKBC-018"
