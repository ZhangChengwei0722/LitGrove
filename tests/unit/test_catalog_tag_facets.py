from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest

from research_kb.catalog.adapters import CatalogAdapterRegistry
from research_kb.catalog.storage import CatalogDatabase
from research_kb.errors import ResearchKBError
from research_kb.services import CatalogProjectionService, RegistryService, WorkspaceSessionService
from research_kb.services.tags import TagService
from research_kb.storage.json_io import file_sha256, read_json_document
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


APPROVAL = {
    "receipt_id": "catalog-tag-test",
    "approved_by": "user",
    "approved_at": "2026-08-03T00:00:00Z",
    "origin": "user_authored",
}


def _materialized_tag_entries(layout, entries, tag_id, link_id=None):
    materialized = [
        *entries,
        ("tag-bundle", read_json_document(layout.tag_bundle_path(tag_id))),
    ]
    if link_id is not None:
        materialized.append(
            ("tag-link-bundle", read_json_document(layout.tag_link_bundle_path(link_id)))
        )
    return materialized


def test_catalog_projects_and_filters_current_tag_facets(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    service = TagService(layout, entries_loader=lambda _: deepcopy(entries))
    tag_bundle, _ = service.promote_tag(
        {"name": "Priority", "description": "Current synthetic priority.", "aliases": [], "status": "active"},
        approval=APPROVAL,
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    link_bundle, _ = service.set_assignment(
        tag_id=tag_bundle["tag_id"],
        target_kind="paper",
        target_id=paper_id,
        state="assigned",
        approval=APPROVAL,
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    materialized = [
        *entries,
        ("tag-bundle", read_json_document(layout.tag_bundle_path(tag_bundle["tag_id"]))),
        ("tag-link-bundle", read_json_document(layout.tag_link_bundle_path(link_bundle["tag_link_id"]))),
    ]
    snapshot = CatalogAdapterRegistry().project_entries(materialized, workspace_id=layout.workspace_id)
    assert CatalogAdapterRegistry().registry_version == "1.3"
    paper = next(item for item in snapshot.documents if item.record_kind == "registry-paper" and item.record_id == paper_id)
    assert paper.tag_ids == (tag_bundle["tag_id"],)
    assert paper.tag_names == ("Priority",)
    assert any(item.item_kind == "tag" and item.record_id == tag_bundle["tag_id"] for item in snapshot.documents)

    database = tmp_path / "catalog.sqlite3"
    CatalogDatabase.build(database, snapshot, build_mode="full")
    result = CatalogDatabase.query(database, tag_id=tag_bundle["tag_id"], page_size=100)
    assert [item["record_id"] for item in result["items"]] == [paper_id]
    assert result["items"][0]["tags"] == [{"tag_id": tag_bundle["tag_id"], "name": "Priority"}]


def test_incremental_projection_refreshes_target_when_only_tag_name_changes(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    service = TagService(layout, entries_loader=lambda _: deepcopy(entries))
    tag_bundle, _ = service.promote_tag(
        {"name": "Old name", "description": "", "aliases": [], "status": "active"},
        approval=APPROVAL, actor="user", fixture_origin="synthetic_from_scratch",
    )
    link_bundle, _ = service.set_assignment(
        tag_id=tag_bundle["tag_id"], target_kind="paper", target_id=paper_id, state="assigned",
        approval=APPROVAL, actor="user", fixture_origin="synthetic_from_scratch",
    )
    registry = CatalogAdapterRegistry()

    def snapshot():
        return registry.project_entries([
            *entries,
            ("tag-bundle", read_json_document(layout.tag_bundle_path(tag_bundle["tag_id"]))),
            ("tag-link-bundle", read_json_document(layout.tag_link_bundle_path(link_bundle["tag_link_id"]))),
        ], workspace_id=layout.workspace_id)

    database = tmp_path / "catalog.sqlite3"
    CatalogDatabase.build(database, snapshot(), build_mode="full")
    changed, _ = service.promote_tag(
        {"name": "New name"}, tag_id=tag_bundle["tag_id"], approval=APPROVAL, actor="user",
        expected_revision_id=tag_bundle["active_revision_id"], fixture_origin="synthetic_from_scratch",
    )
    tag_bundle = changed
    result = CatalogDatabase.update(database, snapshot())
    filtered = CatalogDatabase.query(database, tag_id=tag_bundle["tag_id"], page_size=100)
    assert result["changed_source_count"] >= 2
    assert filtered["items"][0]["tags"][0]["name"] == "New name"


@pytest.mark.parametrize("mutation", ["assign", "remove", "rename", "archive"])
def test_tag_mutation_incremental_projection_matches_full_rebuild(
    tmp_path: Path,
    mutation: str,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    service = TagService(layout, entries_loader=lambda _: deepcopy(entries))
    tag_bundle, _ = service.promote_tag(
        {"name": "Mutable", "description": "", "aliases": [], "status": "active"},
        approval=APPROVAL,
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    link_bundle = None
    if mutation != "assign":
        link_bundle, _ = service.set_assignment(
            tag_id=tag_bundle["tag_id"],
            target_kind="paper",
            target_id=paper_id,
            state="assigned",
            approval=APPROVAL,
            actor="user",
            fixture_origin="synthetic_from_scratch",
        )
    registry = CatalogAdapterRegistry()
    before = registry.project_entries(
        _materialized_tag_entries(
            layout,
            entries,
            tag_bundle["tag_id"],
            None if link_bundle is None else link_bundle["tag_link_id"],
        ),
        workspace_id=layout.workspace_id,
    )
    incremental = tmp_path / "incremental.sqlite3"
    rebuilt = tmp_path / "rebuilt.sqlite3"
    CatalogDatabase.build(incremental, before, build_mode="full")
    before_item_id = next(
        item.item_id
        for item in before.documents
        if item.record_kind == "registry-paper" and item.record_id == paper_id
    )

    if mutation == "assign":
        link_bundle, _ = service.set_assignment(
            tag_id=tag_bundle["tag_id"], target_kind="paper", target_id=paper_id,
            state="assigned", approval=APPROVAL, actor="user",
            fixture_origin="synthetic_from_scratch",
        )
    elif mutation == "remove":
        link_bundle, _ = service.set_assignment(
            tag_id=tag_bundle["tag_id"], target_kind="paper", target_id=paper_id,
            state="removed", approval=APPROVAL, actor="user",
            expected_revision_id=link_bundle["active_revision_id"],
            fixture_origin="synthetic_from_scratch",
        )
    else:
        payload = {"name": "Renamed"} if mutation == "rename" else {"status": "archived"}
        tag_bundle, _ = service.promote_tag(
            payload,
            tag_id=tag_bundle["tag_id"],
            approval=APPROVAL,
            actor="user",
            expected_revision_id=tag_bundle["active_revision_id"],
            fixture_origin="synthetic_from_scratch",
        )

    after = registry.project_entries(
        _materialized_tag_entries(
            layout,
            entries,
            tag_bundle["tag_id"],
            None if link_bundle is None else link_bundle["tag_link_id"],
        ),
        workspace_id=layout.workspace_id,
    )
    update = CatalogDatabase.update(incremental, after)
    CatalogDatabase.build(rebuilt, after, build_mode="full")

    assert update["changed_source_count"] >= 1
    assert before.source_watermark != after.source_watermark
    assert CatalogDatabase.query(incremental, page_size=100) == CatalogDatabase.query(
        rebuilt, page_size=100
    )
    after_item = next(
        item
        for item in CatalogDatabase.query(incremental, page_size=100)["items"]
        if item["record_kind"] == "registry-paper" and item["record_id"] == paper_id
    )
    assert after_item["item_id"] == before_item_id
    assert after_item["tags"] == (
        [{"tag_id": tag_bundle["tag_id"], "name": "Renamed" if mutation == "rename" else "Mutable"}]
        if mutation in {"assign", "rename"}
        else []
    )


def test_registry_delta_fails_closed_with_facets_and_corruption_is_not_current(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "tagged-registry-paper.txt"
    source.write_text("Synthetic tagged Registry source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {"title": "Tagged Registry paper"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    tag_service = TagService(layout)
    tag_bundle, _ = tag_service.promote_tag(
        {"name": "Registry facet", "description": "", "aliases": [], "status": "active"},
        approval=APPROVAL,
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    tag_service.set_assignment(
        tag_id=tag_bundle["tag_id"], target_kind="paper", target_id=paper["paper_id"],
        state="assigned", approval=APPROVAL, actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    built = projection.rebuild()
    registry_digest = file_sha256(layout.registry_path)
    assert registry_digest is not None
    before = projection.paths.database_path.read_bytes()

    with pytest.raises(ResearchKBError) as rejected:
        projection._benchmark_registry_delta(
            base_source_watermark=built["source_watermark"],
            before_registry_store_digest=registry_digest,
            after_registry_store_digest=registry_digest,
        )
    assert rejected.value.diagnostic.code == "RKBC-036"
    assert projection.paths.database_path.read_bytes() == before

    with sqlite3.connect(projection.paths.database_path) as connection:
        connection.execute("DELETE FROM catalog_item_tags")
    assert CatalogDatabase.inspect(projection.paths.database_path).state == "corrupt"
    assert projection.inspect_status()["projection_state"] == "corrupt"
    assert projection.status()["projection_state"] == "corrupt"
