from __future__ import annotations

from copy import deepcopy

from research_kb.bundle import records_of_kind
from research_kb.catalog import CatalogAdapterRegistry
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_bundle, validate_record
from research_kb.review_bundles import active_review_entries, review_bundle_diagnostics
from tests.contract.test_review_memory_contract import review_memory_record
from tests.fixture_factory import make_bundle


REVISION_ID = "reviewrev_a1111111-1111-4111-8111-111111111111"
TASK_ID = "task_a1111111-1111-4111-8111-111111111111"
PROFILE_ID = "adequacy_a1111111-1111-4111-8111-111111111111"


def _review_bundle() -> tuple[dict, list[tuple[str, dict]]]:
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    paper = records_of_kind(entries, "registry-paper")[0]
    paper_id = paper["paper_id"]
    page = next(item for item in records_of_kind(entries, "parsed-page") if item["paper_id"] == paper_id)
    memory = review_memory_record()
    memory.update(
        {
            "paper_id": paper_id,
            "source_fingerprint": paper["source_fingerprint"],
            "parse_snapshot": {
                "parse_run_id": page["parse_run_id"],
                "adapter": page["parser"]["adapter"],
                "version": page["parser"]["version"],
            },
            "updated_at": "2026-01-01T00:00:02Z",
        }
    )
    unit = memory["sections"][2]["units"][0]
    revision = {
        "revision_id": REVISION_ID,
        "revision_number": 1,
        "predecessor": None,
        "approval": {
            "task_id": TASK_ID,
            "task_result_digest": "1" * 64,
            "approved_by": "user",
            "approved_at": "2026-01-01T00:00:02Z",
        },
        "input_snapshot": {
            "source_fingerprint": paper["source_fingerprint"],
            "parse_run_id": page["parse_run_id"],
            "parse_output_digest": "2" * 64,
            "adequacy_profiles": [
                {
                    "requested_operation": "basic_review_memory",
                    "profile_id": PROFILE_ID,
                    "profile_digest": "3" * 64,
                },
                {
                    "requested_operation": "continuous_text_evidence",
                    "profile_id": "adequacy_a2222222-2222-4222-8222-222222222222",
                    "profile_digest": "4" * 64,
                },
            ],
        },
        "provenance_bindings": [
            {
                "review_unit_id": unit["review_unit_id"],
                "source_note_index": 0,
                "requested_operation": "continuous_text_evidence",
                "profile_id": "adequacy_a2222222-2222-4222-8222-222222222222",
                "profile_digest": "4" * 64,
            }
        ],
        "review_memory": memory,
        "created_at": "2026-01-01T00:00:02Z",
    }
    bundle = {
        "schema_version": "1.0",
        "paper_id": paper_id,
        "active_revision_id": REVISION_ID,
        "revisions": [revision],
        "created_at": "2026-01-01T00:00:02Z",
        "updated_at": "2026-01-01T00:00:02Z",
        "fixture_origin": "synthetic_from_scratch",
    }
    without_primary = [
        (kind, record)
        for kind, record in entries
        if not (
            (kind == "paper-card" and record["paper_id"] == paper_id)
            or (kind == "evidence" and record["paper_id"] == paper_id)
            or (kind == "review-queue" and record["paper_id"] == paper_id)
        )
    ]
    return bundle, without_primary


def test_review_bundle_schema_and_active_projection_are_complete() -> None:
    bundle, legacy = _review_bundle()

    assert validate_record("review-semantic-bundle", bundle, actor="stored") == []
    projected = active_review_entries(bundle)
    assert [kind for kind, _ in projected] == ["review-memory"]
    assert projected[0][1]["review_memory_id"] == bundle["revisions"][0]["review_memory"]["review_memory_id"]

    diagnostics = validate_bundle(
        {"records": [{"kind": kind, "record": record} for kind, record in [*legacy, ("review-semantic-bundle", bundle)]]},
        actor="stored",
    )
    assert {
        item.message.split(" reference:", 1)[0]
        for item in diagnostics
        if item.record_kind == "review-semantic-bundle"
    } >= {"unresolved Agent Task", "unresolved Source Adequacy profile"}


def test_review_revision_chain_and_note_bindings_are_closed() -> None:
    bundle, _ = _review_bundle()
    first = bundle["revisions"][0]
    second = deepcopy(first)
    second["revision_id"] = "reviewrev_a2222222-2222-4222-8222-222222222222"
    second["revision_number"] = 2
    second["predecessor"] = {
        "revision_id": first["revision_id"],
        "revision_digest": canonical_digest(first),
    }
    second["review_memory"]["review_memory_id"] = "reviewmem_a2222222-2222-4222-8222-222222222222"
    unit = second["review_memory"]["sections"][2]["units"][0]
    unit["review_unit_id"] = "reviewunit_a3333333-3333-4333-8333-333333333333"
    second["provenance_bindings"][0]["review_unit_id"] = unit["review_unit_id"]
    second["created_at"] = "2026-01-01T00:00:03Z"
    bundle["revisions"].append(second)
    bundle["active_revision_id"] = second["revision_id"]
    bundle["updated_at"] = second["created_at"]

    assert review_bundle_diagnostics(bundle) == []
    second["provenance_bindings"] = []
    assert any("close exactly" in item.message for item in review_bundle_diagnostics(bundle))


def test_review_bundle_and_legacy_memory_cannot_both_be_active() -> None:
    bundle, legacy = _review_bundle()
    legacy.append(("review-memory", deepcopy(bundle["revisions"][0]["review_memory"])))

    diagnostics = validate_bundle(
        {"records": [{"kind": kind, "record": record} for kind, record in [*legacy, ("review-semantic-bundle", bundle)]]},
        actor="stored",
    )

    assert any("legacy Review Memory cannot coexist" in item.message for item in diagnostics)


def test_source_note_can_explain_missing_section_without_weakening_other_provenance() -> None:
    bundle, _ = _review_bundle()
    note = bundle["revisions"][0]["review_memory"]["sections"][2]["units"][0]["source_notes"][0]
    note["section"] = None
    note["section_missing_reason"] = "The synthetic source has no section labels."

    assert validate_record("review-semantic-bundle", bundle, actor="stored") == []
    del note["section_missing_reason"]
    assert validate_record("review-semantic-bundle", bundle, actor="stored")


def test_catalog_expands_only_the_active_review_child_and_ignores_bundle_container() -> None:
    bundle, entries = _review_bundle()
    snapshot = CatalogAdapterRegistry().project_entries(
        [*entries, ("review-semantic-bundle", bundle)],
        workspace_id="workspace_a1111111-1111-4111-8111-111111111111",
    )

    review_sources = [
        item for item in snapshot.source_records if item.record_kind == "review-memory"
    ]
    assert [item.record_id for item in review_sources] == [
        bundle["revisions"][0]["review_memory"]["review_memory_id"]
    ]
    assert "review-semantic-bundle" not in snapshot.unknown_record_kinds
