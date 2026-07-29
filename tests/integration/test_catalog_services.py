from __future__ import annotations

import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.errors import ResearchKBError
from research_kb.services import (
    CatalogCapabilityService,
    CatalogProjectionService,
    CatalogQueryService,
    RegistryService,
    WorkspaceSessionService,
)
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


def _entries() -> list[tuple[str, dict]]:
    return [
        (entry["kind"], deepcopy(entry["record"]))
        for entry in make_bundle("alpha")["records"]
    ]


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _projection(tmp_path: Path):
    layout = make_runtime_workspace(tmp_path)
    session_service = WorkspaceSessionService({"alpha": layout.config.path})
    session = session_service.open("alpha")
    entries = _entries()
    workspace = next(record for kind, record in entries if kind == "workspace")
    workspace["workspace"]["id"] = session.workspace_id
    projection = CatalogProjectionService(
        session,
        tmp_path / "app-state",
        entry_loader=lambda _: entries,
        entry_validator=lambda _: None,
    )
    return layout, session_service, session, entries, projection


def test_workspace_session_uses_configured_option_and_redacts_paths(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = WorkspaceSessionService({"alpha": layout.config.path})

    listed = service.list_options()
    session = service.open("alpha")

    assert listed == {"status": "success", "workspaces": [session.display()]}
    assert session.option_id == "alpha"
    assert session.workspace_id == layout.workspace_id
    assert not any("path" in key for key in session.display())
    assert str(layout.config.path) not in str(listed)
    with pytest.raises(ResearchKBError) as unknown:
        service.open("not-configured")
    assert unknown.value.diagnostic.code == "RKBC-002"


def test_workspace_session_rejects_relative_duplicate_and_invalid_options(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)

    with pytest.raises(ResearchKBError) as relative:
        WorkspaceSessionService({"alpha": Path("workspace.yaml")})
    with pytest.raises(ResearchKBError) as duplicate:
        WorkspaceSessionService({"alpha": layout.config.path, "beta": layout.config.path})
    with pytest.raises(ResearchKBError) as invalid:
        WorkspaceSessionService({"../alpha": layout.config.path})

    assert {error.value.diagnostic.code for error in (relative, duplicate)} == {"RKBC-007"}
    assert invalid.value.diagnostic.code == "RKBC-002"


def test_projection_rebuild_search_detail_stale_update_and_source_confinement(
    tmp_path: Path,
) -> None:
    layout, _, _, entries, projection = _projection(tmp_path)
    knowledge_before = _tree_bytes(layout.knowledge_root)
    sources_before = _tree_bytes(next(iter(layout.source_roots.values())))

    built = projection.rebuild()
    status = projection.status()
    query = CatalogQueryService(projection)
    result = query.search(query="Fabricated", item_kinds=("paper",), page_size=10)
    selected = result["items"][0]
    detail = query.detail(selected["item_id"])

    assert built["build_mode"] == "full"
    assert status["projection_state"] == "current"
    assert len(result["items"]) == 2
    assert detail["current_record_status"] == "current"
    assert detail["detail"]["bibliography"]["title"].startswith("Fabricated")
    assert _tree_bytes(layout.knowledge_root) == knowledge_before
    assert _tree_bytes(next(iter(layout.source_roots.values()))) == sources_before

    paper = next(record for kind, record in entries if kind == "registry-paper")
    paper["bibliography"]["title"] = "Revised catalog title"
    assert projection.status()["projection_state"] == "stale"
    query.refresh_status()
    changed = query.detail(selected["item_id"])
    assert changed["current_record_status"] == "changed"
    assert changed["detail"] is None

    update = projection.update()
    query.refresh_status()
    revised = query.search(query="Revised", item_kinds=("paper",), page_size=10)
    assert update["build_mode"] == "incremental"
    assert update["changed_source_count"] == 1
    assert [item["title"] for item in revised["items"]] == ["Revised catalog title"]
    assert query.detail(revised["items"][0]["item_id"])["current_record_status"] == "current"
    assert _tree_bytes(layout.knowledge_root) == knowledge_before
    assert _tree_bytes(next(iter(layout.source_roots.values()))) == sources_before


def test_projection_uses_authoritative_workspace_loader_and_validator(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    projection.rebuild()

    source = layout.source_roots["alpha-sources"] / "catalog-paper.txt"
    source.write_text("Fabricated catalog source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {"title": "Authoritative catalog paper"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )

    assert projection.status()["projection_state"] == "stale"
    projection.update()
    query = CatalogQueryService(projection)
    items = query.search(query="Authoritative", item_kinds=("paper",))["items"]
    assert [(item["paper_id"], item["title"]) for item in items] == [
        (paper["paper_id"], "Authoritative catalog paper")
    ]


def test_catalog_query_service_filters_related_paper_and_question_items(tmp_path: Path) -> None:
    _, _, _, entries, projection = _projection(tmp_path)
    projection.rebuild()
    query = CatalogQueryService(projection)
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "paper-card")
    question_id = next(
        record["question_id"] for kind, record in entries if kind == "question-mapping"
    )

    paper_items = query.search(paper_id=paper_id, page_size=100)
    question_items = query.search(question_id=question_id, page_size=100)

    assert paper_items["paper_id"] == paper_id
    assert paper_items["items"]
    assert {item["paper_id"] for item in paper_items["items"]} == {paper_id}
    assert question_items["question_id"] == question_id
    assert question_items["items"]
    assert {item["question_id"] for item in question_items["items"]} == {question_id}


def test_projection_status_classifies_missing_corrupt_and_incompatible_workspace(
    tmp_path: Path,
) -> None:
    _, _, _, _, projection = _projection(tmp_path)
    assert projection.status()["projection_state"] == "missing"
    projection.rebuild()
    with sqlite3.connect(projection.paths.database_path) as connection:
        connection.execute(
            "UPDATE catalog_metadata SET value = ? WHERE key = 'workspace_id'",
            ("workspace_b2222222-2222-4222-8222-222222222222",),
        )
    assert projection.status()["projection_state"] == "incompatible"
    projection.paths.database_path.write_bytes(b"corrupt")
    assert projection.status()["projection_state"] == "corrupt"


def test_projection_rejects_state_overlap_and_unowned_managed_directory(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")

    with pytest.raises(ResearchKBError) as overlap:
        CatalogProjectionService(session, layout.knowledge_root)
    assert overlap.value.diagnostic.code == "RKBC-007"
    with pytest.raises(ResearchKBError) as workspace_overlap:
        CatalogProjectionService(session, layout.config.path.parent / "app-state")
    assert workspace_overlap.value.diagnostic.code == "RKBC-007"

    state_root = tmp_path / "outside-state"
    (state_root / "research-kb-catalog").mkdir(parents=True)
    projection = CatalogProjectionService(
        session,
        state_root,
        entry_loader=lambda _: _entries(),
        entry_validator=lambda _: None,
    )
    with pytest.raises(ResearchKBError) as unowned_status:
        projection.status()
    with pytest.raises(ResearchKBError) as unowned:
        projection.rebuild()
    assert unowned_status.value.diagnostic.code == "RKBC-036"
    assert unowned.value.diagnostic.code == "RKBC-036"


def test_query_requires_a_queryable_projection_and_current_detail_id(tmp_path: Path) -> None:
    _, _, _, _, projection = _projection(tmp_path)
    query = CatalogQueryService(projection)
    with pytest.raises(ResearchKBError) as missing:
        query.search()
    assert missing.value.diagnostic.code == "RKBC-036"

    projection.rebuild()
    query.refresh_status()
    with pytest.raises(ResearchKBError) as malformed:
        query.detail("not-a-catalog-id")
    with pytest.raises(ResearchKBError) as absent:
        query.detail("catalog_" + "0" * 32)
    assert malformed.value.diagnostic.code == "RKBC-002"
    assert absent.value.diagnostic.code == "RKBC-005"


def test_query_binds_only_a_projection_result_matching_stored_metadata(tmp_path: Path) -> None:
    _, _, _, _, projection = _projection(tmp_path)
    built = projection.rebuild()
    query = CatalogQueryService(projection)

    status = query.bind_projection_result(built)
    mismatched = {**built, "source_watermark": "0" * 64}
    with pytest.raises(ResearchKBError) as rejected:
        query.bind_projection_result(mismatched)

    assert status["projection_state"] == "current"
    assert query.search(page_size=1)["items"]
    assert rejected.value.diagnostic.code == "RKBC-036"


def test_catalog_services_are_public_and_capability_is_machine_readable() -> None:
    from research_kb import services

    expected = {
        "CatalogCapabilityService",
        "CatalogProjectionService",
        "CatalogQueryService",
        "WorkspaceSession",
        "WorkspaceSessionService",
    }
    capability = CatalogCapabilityService().show(["registry-paper", "future-kind"])

    assert expected <= set(services.__all__)
    assert all(hasattr(services, name) for name in expected)
    assert capability["application_service_interface_version"] == (
        APPLICATION_SERVICE_INTERFACE_VERSION
    )
    assert capability["projection_storage"] == "disposable_sqlite_fts"
    assert capability["raw_parsed_text_indexed"] is False
    assert capability["query_filters"] == ["item_kinds", "paper_id", "question_id"]
    assert capability["unregistered_record_kinds"] == ["future-kind"]
