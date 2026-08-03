from __future__ import annotations

import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

import research_kb.services.catalog as catalog_module
from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.catalog import CatalogDatabase
from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError
from research_kb.services import (
    CatalogCapabilityService,
    CatalogProjectionService,
    CatalogQueryService,
    RegistryIdentityCorrectionService,
    RegistryService,
    ResearchOrganizationService,
    SourceAssetService,
    WorkspaceSessionService,
)
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl, sha256_bytes
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


def test_catalog_projects_only_current_pipeline_job_heads_with_stable_pagination(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    jobs = PipelineJobService(layout)
    authority = {
        "actor": "user",
        "granted_operations": ["register_by_reference"],
        "captured_at": "2026-07-30T01:00:00Z",
    }
    first = jobs.create(
        requested_route="local_source",
        requested_depth="semantic_gate",
        current_node="intake_preflight",
        input_refs=[],
        authority_snapshot=authority,
        idempotency_key="synthetic-catalog-job-1",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    current_first = jobs.transition(
        first.state["job_id"],
        expected_state_id=first.state["state_id"],
        expected_state_digest=canonical_digest(first.state),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    second = jobs.create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="intake_preflight",
        input_refs=[],
        authority_snapshot=authority,
        idempotency_key="synthetic-catalog-job-2",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    projection.rebuild()
    query = CatalogQueryService(projection)
    first_page = query.search(item_kinds=("pipeline_job",), page_size=1)
    second_page = query.search(
        item_kinds=("pipeline_job",),
        page_size=1,
        cursor=first_page["next_cursor"],
    )
    items = [*first_page["items"], *second_page["items"]]

    assert len(items) == 2
    assert {item["record_id"] for item in items} == {
        current_first.state["state_id"],
        second.state["state_id"],
    }
    assert query.detail(items[0]["item_id"])["detail"]["revision"] in {1, 2}


def test_catalog_projects_and_resolves_active_direction_detail(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    bundle, _ = ResearchOrganizationService(layout).promote_direction(
        {
            "name": "Synthetic catalog direction",
            "scope": "Synthetic catalog scope.",
            "status": "active",
            "unit_links": [],
            "gap_notes": [],
        },
        approval={
            "receipt_id": "catalog-direction-receipt",
            "approved_by": "user",
            "approved_at": "2026-01-01T00:00:00Z",
            "origin": "user_authored",
        },
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    projection.rebuild()
    query = CatalogQueryService(projection)

    result = query.search(item_kinds=("research_direction",), page_size=10)
    detail = query.detail(result["items"][0]["item_id"])

    assert [item["record_id"] for item in result["items"]] == [bundle["direction_id"]]
    assert detail["current_record_status"] == "current"
    assert detail["detail"]["name"] == "Synthetic catalog direction"


def test_source_and_identity_catalog_details_resolve_current_derived_records(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    papers = []
    for ordinal in range(2):
        source = layout.source_roots["alpha-sources"] / f"identity-{ordinal}.pdf"
        source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic catalog identity\n%%EOF\n")
        paper, _ = RegistryService(layout).add(
            root_id="alpha-sources",
            relative_path=source.name,
            metadata={
                "bibliography": {"title": f"Identity paper {ordinal}"},
                "fixture_origin": "synthetic_from_scratch",
            },
        )
        papers.append(paper)
    jobs = PipelineJobService(layout)
    source_job = jobs.create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="source_intake",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["register_by_reference"],
            "captured_at": "2026-07-30T01:00:00Z",
        },
        idempotency_key="catalog-source-detail",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    ).state
    SourceAssetService(layout).register_reference(
        job_id=source_job["job_id"],
        paper_id=papers[0]["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path="identity-0.pdf",
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    identity_job = jobs.create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="identity_correction",
        input_refs=[paper["paper_id"] for paper in papers],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["registry_identity_correction"],
            "captured_at": "2026-07-30T01:00:00Z",
        },
        idempotency_key="catalog-identity-detail",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    ).state
    RegistryIdentityCorrectionService(layout).record(
        job_id=identity_job["job_id"],
        operation="paper_alias",
        subject_paper_ids=[papers[1]["paper_id"]],
        retained_paper_id=papers[0]["paper_id"],
        supersedes_correction_id=None,
        rationale="Synthetic catalog identity projection.",
        expected_previous_correction_id=None,
        expected_previous_correction_digest=None,
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    projection.rebuild()
    query = CatalogQueryService(projection)
    source_item = query.search(item_kinds=("source_asset",))["items"][0]
    identity_item = query.search(item_kinds=("paper_identity",))["items"][0]

    source_detail = query.detail(source_item["item_id"])
    identity_detail = query.detail(identity_item["item_id"])
    assert source_detail["current_record_status"] == "current"
    assert source_detail["detail"]["paper_id"] == papers[0]["paper_id"]
    assert identity_detail["current_record_status"] == "current"
    assert identity_detail["detail"]["canonical_paper_id"] == papers[0]["paper_id"]


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


def test_projection_update_rebuilds_older_schema_with_explicit_reason(tmp_path: Path) -> None:
    _, _, _, _, projection = _projection(tmp_path)
    projection.rebuild()
    connection = sqlite3.connect(projection.paths.database_path)
    try:
        connection.execute(
            "UPDATE catalog_metadata SET value = '2' WHERE key = 'catalog_schema_version'"
        )
        connection.commit()
    finally:
        connection.close()

    result = projection.update()

    assert result["build_mode"] == "full"
    assert result["reason"] == "schema_upgrade"
    assert CatalogDatabase.inspect(projection.paths.database_path).state == "ready"


@pytest.mark.parametrize("failure", ["workspace_mismatch", "corrupt"])
def test_projection_update_does_not_rebuild_wrong_workspace_or_corrupt_database(
    tmp_path: Path,
    failure: str,
) -> None:
    _, _, _, _, projection = _projection(tmp_path)
    projection.rebuild()
    connection = sqlite3.connect(projection.paths.database_path)
    try:
        if failure == "workspace_mismatch":
            connection.execute(
                "UPDATE catalog_metadata SET value = ? WHERE key = 'workspace_id'",
                ("workspace_b2222222-2222-4222-8222-222222222222",),
            )
        else:
            connection.execute(
                "UPDATE catalog_metadata SET value = ? WHERE key = 'facet_digest'",
                ("0" * 64,),
            )
        connection.commit()
    finally:
        connection.close()
    before = projection.paths.database_path.read_bytes()

    with pytest.raises(ResearchKBError) as rejected:
        projection.update()

    assert rejected.value.diagnostic.code == "RKBC-036"
    assert projection.paths.database_path.read_bytes() == before

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


def test_registry_detail_uses_canonical_locator_without_monolithic_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "locator-paper.txt"
    source.write_text("Synthetic locator source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {"title": "Locator-backed paper"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    built = projection.rebuild()
    query = CatalogQueryService(projection)
    query.bind_projection_result(built)
    item = query.search(paper_id=paper["paper_id"], item_kinds=("paper",))["items"][0]

    monkeypatch.setattr(
        catalog_module,
        "_find_jsonl_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full JSONL scan")),
    )
    detail = query.detail(item["item_id"])

    assert detail["current_record_status"] == "current"
    assert detail["detail"]["bibliography"]["title"] == "Locator-backed paper"
    assert "byte_offset" not in str(detail)


def test_registry_locator_drift_never_returns_current_detail(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    papers = []
    for ordinal in range(2):
        source = layout.source_roots["alpha-sources"] / f"drift-paper-{ordinal}.txt"
        source.write_text(f"Synthetic drift source {ordinal}.\n", encoding="utf-8", newline="\n")
        paper, _ = RegistryService(layout).add(
            root_id="alpha-sources",
            relative_path=source.name,
            metadata={
                "bibliography": {"title": f"Drift paper {ordinal}"},
                "fixture_origin": "synthetic_from_scratch",
            },
        )
        papers.append(paper)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    built = projection.rebuild()
    query = CatalogQueryService(projection)
    query.bind_projection_result(built)
    second = query.search(paper_id=papers[1]["paper_id"], item_kinds=("paper",))["items"][0]

    records = read_jsonl(
        layout.registry_path,
        record_kind="registry-paper",
        missing_ok=False,
        id_field="paper_id",
    )
    records[0]["bibliography"]["title"] += " with offset-changing content"
    layout.registry_path.write_bytes(serialize_jsonl(records))

    detail = query.detail(second["item_id"])
    assert detail["current_record_status"] == "changed"
    assert detail["detail"] is None


def test_benchmark_registry_delta_matches_full_rebuild_without_workspace_loader(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "delta-paper.txt"
    source.write_text("Synthetic delta source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {"title": "Delta paper old"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    incremental = CatalogProjectionService(session, tmp_path / "incremental-state")
    base = incremental.rebuild()
    before = file_sha256(layout.registry_path)
    assert before is not None
    records = read_jsonl(
        layout.registry_path,
        record_kind="registry-paper",
        missing_ok=False,
        id_field="paper_id",
    )
    records[0]["bibliography"]["title"] = "Delta paper revised"
    layout.registry_path.write_bytes(serialize_jsonl(records))
    after = file_sha256(layout.registry_path)
    assert after is not None
    incremental.entry_loader = lambda _: (_ for _ in ()).throw(AssertionError("workspace loader"))

    result = incremental._benchmark_registry_delta(
        base_source_watermark=base["source_watermark"],
        before_registry_store_digest=before,
        after_registry_store_digest=after,
    )
    rebuilt = CatalogProjectionService(session, tmp_path / "rebuilt-state")
    rebuilt_result = rebuilt.rebuild()

    assert result["changed_source_count"] == 1
    assert result["source_watermark"] == rebuilt_result["source_watermark"]
    assert CatalogDatabase.query(incremental.paths.database_path, page_size=100) == (
        CatalogDatabase.query(rebuilt.paths.database_path, page_size=100)
    )
    query = CatalogQueryService(incremental)
    query.bind_projection_result(result)
    revised = query.search(paper_id=paper["paper_id"], item_kinds=("paper",))["items"][0]
    assert revised["title"] == "Delta paper revised"


def test_benchmark_registry_delta_rejects_stale_base_without_mutation(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    built = projection.rebuild()
    digest = file_sha256(layout.registry_path) or sha256_bytes(b"")
    before = projection.paths.database_path.read_bytes()

    with pytest.raises(ResearchKBError) as rejected:
        projection._benchmark_registry_delta(
            base_source_watermark="0" * 64,
            before_registry_store_digest=digest,
            after_registry_store_digest=digest,
        )

    assert rejected.value.diagnostic.code == "RKBC-036"
    assert projection.paths.database_path.read_bytes() == before
    assert built["item_count"] == 0


def test_inspect_only_binding_is_stale_and_does_not_load_workspace(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    projection.rebuild()
    projection.entry_loader = lambda _: (_ for _ in ()).throw(AssertionError("workspace loader"))
    query = CatalogQueryService(projection)

    status = query.bind_existing_projection()

    assert status["projection_state"] == "stale"
    assert status["freshness_verification"] == "unverified_after_restart"
    assert query.search(page_size=1)["projection_state"] == "stale"


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
    assert capability["query_filters"] == ["item_kinds", "paper_id", "question_id", "tag_id"]
    assert capability["unregistered_record_kinds"] == ["future-kind"]
