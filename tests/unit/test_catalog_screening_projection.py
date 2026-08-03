from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from research_kb.catalog import CatalogAdapterRegistry, CatalogDatabase
from research_kb.services.question_screening import QuestionScreeningService
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


APPROVAL = {"receipt_id": "catalog-screening", "approved_by": "user", "approved_at": "2026-08-03T00:00:00Z", "origin": "user_authored"}


def test_catalog_projects_screening_criteria_decision_and_freshness(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = [(item["kind"], deepcopy(item["record"])) for item in make_bundle("alpha")["records"]]
    service = QuestionScreeningService(layout, entries_loader=lambda _: deepcopy(entries))
    question_id = next(record["question_id"] for kind, record in entries if kind == "question-mapping")
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    criteria, _ = service.promote_criteria({"question_id": question_id, "title": "Synthetic criteria", "scope": "Synthetic scope.", "inclusion_criteria": ["Synthetic inclusion."], "exclusion_criteria": [], "notes": "", "status": "active"}, approval=APPROVAL, actor="user", fixture_origin="synthetic_from_scratch")
    item = criteria["revisions"][-1]["criteria"]["inclusion_criteria"][0]
    decision, _ = service.promote_decision({"question_id": question_id, "paper_id": paper_id, "outcome": "included", "criteria_revision_id": criteria["active_revision_id"], "criteria_digest": criteria["revisions"][-1]["content_digest"], "criterion_dispositions": [{"criterion_id": item["criterion_id"], "disposition": "met", "rationale": "Synthetic."}], "basis_scope": "metadata", "rationale": "Synthetic inclusion.", "known_limitations": []}, approval=APPROVAL, actor="user", fixture_origin="synthetic_from_scratch")

    snapshot = CatalogAdapterRegistry().project_entries([*entries, ("screening-criteria-bundle", criteria), ("screening-decision-bundle", decision)], workspace_id=layout.workspace_id)
    criteria_item = next(item for item in snapshot.documents if item.item_kind == "screening_criteria")
    decision_item = next(item for item in snapshot.documents if item.item_kind == "screening_decision")
    assert criteria_item.question_id == question_id
    assert decision_item.paper_id == paper_id
    assert {"screening:included", "freshness:current"} <= set(decision_item.status_labels)
    assert not snapshot.unknown_record_kinds
    database = tmp_path / "screening.sqlite3"
    CatalogDatabase.build(database, snapshot, build_mode="full")
    filtered = CatalogDatabase.query(database, status_labels=("screening:included", "freshness:current"))
    assert [item["item_id"] for item in filtered["items"]] == [decision_item.item_id]
