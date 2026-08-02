from __future__ import annotations

from copy import deepcopy

import pytest

from research_kb.catalog import CatalogAdapterRegistry
from research_kb.errors import ResearchKBError
from tests.contract.test_review_memory_contract import review_memory_record
from tests.contract.test_source_adequacy_contract import _profile as source_adequacy_profile
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


def test_catalog_projects_only_latest_redacted_source_adequacy_profile() -> None:
    earlier = source_adequacy_profile()
    later = deepcopy(earlier)
    later["profile_id"] = "adequacy_b0000002-0000-4000-8000-000000000002"
    later["assessed_at"] = "2026-01-02T00:00:00Z"
    later["known_limitations"] = ["A synthetic layout limitation remains."]
    later["user_decision"] = {
        "actor": "user",
        "decision": "remediation_required",
        "capabilities": ["basic_paper_understanding"],
        "reason": "private-user-reason confidential-note",
        "decided_at": "2026-01-02T00:00:00Z",
    }
    later["assessed_by"] = "user"

    registry = CatalogAdapterRegistry()
    snapshot = registry.project_entries(
        [
            ("source-adequacy-profile", earlier),
            ("source-adequacy-profile", later),
        ],
        workspace_id=WORKSPACE_ID,
    )

    documents = [item for item in snapshot.documents if item.item_kind == "source_adequacy"]
    assert len(documents) == 1
    assert documents[0].record_id == later["profile_id"]
    detail = registry.find_adapter("source-adequacy-profile").detail(later, None)
    serialized = str(detail)
    assert detail["requested_operation"] == "basic_paper_card"
    assert "source_snapshots" not in detail
    assert "parse_snapshot" not in detail
    assert "paper.txt" not in serialized
    assert "sha256:" not in serialized
    assert "private-user-reason" not in serialized
    assert "private-user-reason" not in documents[0].search_text


def test_capability_reports_registered_ignored_and_unregistered_kinds() -> None:
    capability = CatalogAdapterRegistry().capability(
        ["registry-paper", "parsed-page", "future-direction"]
    )

    assert capability["registry_version"] == "1.1"
    assert "parsed-page" in capability["ignored_record_kinds"]
    assert capability["unregistered_record_kinds"] == ["future-direction"]
    assert "registry-paper" in {
        adapter["record_kind"] for adapter in capability["adapters"]
    }


def test_source_and_identity_catalog_use_current_redacted_projections() -> None:
    paper_a = "paper_a1111111-1111-4111-8111-111111111111"
    paper_b = "paper_b2222222-2222-4222-8222-222222222222"
    source_asset = "sourceasset_a1111111-1111-4111-8111-111111111111"
    source_state_1 = "sourceassetstate_a1111111-1111-4111-8111-111111111111"
    source_state_2 = "sourceassetstate_b2222222-2222-4222-8222-222222222222"
    correction = "identitycorr_a1111111-1111-4111-8111-111111111111"
    entries = [item for item in _entries() if item[0] != "registry-paper"]
    papers = [
        {
            "schema_version": "1.0",
            "paper_id": paper_id,
            "bibliography": {"title": paper_id, "authors": [], "year": 2026, "doi": None},
            "source_ref": {"root_id": "alpha-sources", "relative_path": f"{paper_id}.pdf"},
            "source_fingerprint": {"algorithm": "sha256", "value": "a" * 64},
            "duplicate_candidate_ids": [],
            "screening_status": "candidate",
            "review_status": "ai_draft",
            "automation_status": "pending",
            "created_at": "2026-07-30T00:00:00Z",
            "updated_at": "2026-07-30T00:00:00Z",
        }
        for paper_id in (paper_a, paper_b)
    ]
    source_common = {
        "schema_version": "1.0",
        "source_asset_id": source_asset,
        "workspace_id": WORKSPACE_ID,
        "paper_id": paper_a,
        "asset_role": "main_pdf",
        "source_ref": {"root_id": "alpha-sources", "relative_path": "paper.pdf"},
        "availability": "available",
        "job_id": "job_a1111111-1111-4111-8111-111111111111",
        "actor": "cli",
        "created_at": "2026-07-30T00:00:00Z",
    }
    state_1 = {
        **source_common,
        "source_asset_state_id": source_state_1,
        "revision": 1,
        "predecessor": None,
        "source_fingerprint": {"algorithm": "sha256", "value": "a" * 64},
        "manifestation_id": "sha256:" + "a" * 64,
        "manifestation_status": "active",
        "reason": "reference_registered",
        "updated_at": "2026-07-30T00:00:00Z",
    }
    from research_kb.catalog.models import canonical_digest

    state_2 = {
        **source_common,
        "source_asset_state_id": source_state_2,
        "revision": 2,
        "predecessor": {"state_id": source_state_1, "state_digest": canonical_digest(state_1)},
        "source_fingerprint": {"algorithm": "sha256", "value": "b" * 64},
        "manifestation_id": "sha256:" + "b" * 64,
        "manifestation_status": "change_candidate",
        "reason": "changed_bytes_observed",
        "updated_at": "2026-07-30T00:01:00Z",
    }
    identity = {
        "schema_version": "1.0",
        "correction_id": correction,
        "workspace_id": WORKSPACE_ID,
        "previous_correction_id": None,
        "previous_correction_digest": None,
        "operation": "paper_alias",
        "subject_paper_ids": [paper_b],
        "retained_paper_id": paper_a,
        "supersedes_correction_id": None,
        "rationale": "Synthetic alias.",
        "job_id": "job_a1111111-1111-4111-8111-111111111111",
        "actor": "user",
        "created_at": "2026-07-30T00:02:00Z",
    }

    registry = CatalogAdapterRegistry()
    snapshot = registry.project_entries(
        [*entries, *(('registry-paper', paper) for paper in papers), ("source-asset-state", state_2), ("source-asset-state", state_1), ("registry-identity-correction", identity)],
        workspace_id=WORKSPACE_ID,
    )

    source_docs = [item for item in snapshot.documents if item.item_kind == "source_asset"]
    identity_docs = [item for item in snapshot.documents if item.item_kind == "paper_identity"]
    assert len(source_docs) == 1
    assert "source:stale_source" in source_docs[0].status_labels
    assert len(identity_docs) == 1
    assert identity_docs[0].paper_id == paper_b
    source_detail = registry.find_adapter("source-asset-state").detail(state_2, None)
    assert source_detail["source_currentness"] == "stale_source"
    assert "source_fingerprint" not in source_detail
    assert "source_ref" not in source_detail
