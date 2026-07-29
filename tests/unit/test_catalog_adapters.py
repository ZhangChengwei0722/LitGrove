from __future__ import annotations

from copy import deepcopy

import pytest

from research_kb.catalog import CatalogAdapterRegistry
from research_kb.errors import ResearchKBError
from tests.contract.test_review_memory_contract import review_memory_record
from tests.fixture_factory import make_bundle


WORKSPACE_ID = "workspace_a1111111-1111-4111-8111-111111111111"


def _entries() -> list[tuple[str, dict]]:
    bundle = make_bundle("alpha")
    return [(entry["kind"], deepcopy(entry["record"])) for entry in bundle["records"]]


def test_default_registry_covers_current_artifacts_and_excludes_raw_or_deferred_records() -> None:
    registry = CatalogAdapterRegistry()
    entries = _entries()
    entries.extend(
        [
            ("review-memory", review_memory_record()),
            (
                "parsed-page",
                {
                    "schema_version": "1.0",
                    "paper_id": "paper_a1111111-1111-4111-8111-111111111111",
                    "text": "raw-pages-secret-token",
                },
            ),
            (
                "discovery-candidate",
                {"schema_version": "1.0", "candidate_id": "candidate-synthetic"},
            ),
            (
                "future-direction",
                {"schema_version": "1.0", "direction_id": "direction-synthetic"},
            ),
        ]
    )

    snapshot = registry.project_entries(entries, workspace_id=WORKSPACE_ID)

    kinds = {document.item_kind for document in snapshot.documents}
    assert {
        "paper",
        "paper_card_unit",
        "evidence",
        "review_memory",
        "review_unit",
        "question",
        "synthesis",
        "review_angle",
        "insight",
        "cross_view",
        "process_event",
        "guardian_report",
    } <= kinds
    assert "future-direction" in snapshot.unknown_record_kinds
    assert "raw-pages-secret-token" not in "\n".join(
        document.search_text for document in snapshot.documents
    )
    assert not any(
        source.record_kind in {"parsed-page", "review-queue", "discovery-candidate"}
        for source in snapshot.source_records
    )


def test_projection_is_deterministic_and_unknown_records_affect_watermark() -> None:
    registry = CatalogAdapterRegistry()
    entries = _entries()
    first = registry.project_entries(entries, workspace_id=WORKSPACE_ID)
    reordered = registry.project_entries(reversed(entries), workspace_id=WORKSPACE_ID)
    with_unknown = registry.project_entries(
        [*entries, ("future-kind", {"schema_version": "1.0", "value": "one"})],
        workspace_id=WORKSPACE_ID,
    )

    assert first == reordered
    assert with_unknown.source_watermark != first.source_watermark
    assert with_unknown.unknown_record_kinds == ("future-kind",)


def test_duplicate_adapter_and_duplicate_source_fail_closed() -> None:
    adapter = next(iter(CatalogAdapterRegistry().adapters.values()))
    with pytest.raises(ResearchKBError) as duplicate_adapter:
        CatalogAdapterRegistry([adapter, adapter])

    paper = next(record for kind, record in _entries() if kind == "registry-paper")
    with pytest.raises(ResearchKBError) as duplicate_source:
        CatalogAdapterRegistry().project_entries(
            [("registry-paper", paper), ("registry-paper", deepcopy(paper))],
            workspace_id=WORKSPACE_ID,
        )

    assert duplicate_adapter.value.diagnostic.code == "RKBC-004"
    assert duplicate_source.value.diagnostic.code == "RKBC-004"


def test_unsupported_record_contract_is_not_guessed() -> None:
    paper = next(record for kind, record in _entries() if kind == "registry-paper")
    paper["schema_version"] = "2.0"

    with pytest.raises(ResearchKBError) as caught:
        CatalogAdapterRegistry().project_entries(
            [("registry-paper", paper)],
            workspace_id=WORKSPACE_ID,
        )

    assert caught.value.diagnostic.code == "RKBC-003"


def test_catalog_detail_projection_returns_bounded_current_record_data() -> None:
    memory = review_memory_record()
    registry = CatalogAdapterRegistry()
    snapshot = registry.project_entries(
        [("review-memory", memory)],
        workspace_id=WORKSPACE_ID,
    )
    unit_document = next(
        document for document in snapshot.documents if document.item_kind == "review_unit"
    )
    detail = registry.find_adapter("review-memory").detail(memory, unit_document.child_id)

    assert detail["unit"]["review_unit_id"] == unit_document.child_id
    assert detail["background_only"] is True
    assert detail["can_enter_canonical_evidence"] is False
    assert detail["not_fact"] is True


def test_capability_reports_registered_ignored_and_unregistered_kinds() -> None:
    capability = CatalogAdapterRegistry().capability(
        ["registry-paper", "parsed-page", "future-direction"]
    )

    assert capability["registry_version"] == "1.0"
    assert "parsed-page" in capability["ignored_record_kinds"]
    assert capability["unregistered_record_kinds"] == ["future-direction"]
    assert "registry-paper" in {
        adapter["record_kind"] for adapter in capability["adapters"]
    }
