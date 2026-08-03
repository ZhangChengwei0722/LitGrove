from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.p2_catalog_scale import (
    GENERATOR_CONTRACT_VERSION,
    estimate_reference_workload,
    generate_workspace,
    inspect_generated_workspace,
    materialize_portable_seed,
    measure_catalog_reads,
    measure_core_catalog,
    measure_projection_rebuild,
    measure_registry_delta,
    profile_by_id,
)
from benchmarks.p2_catalog_scale.__main__ import main
from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.errors import ResearchKBError
from research_kb.services import CatalogProjectionService, CatalogQueryService, WorkspaceSessionService


def test_profiles_have_exact_independent_scientific_and_operational_counts() -> None:
    small = profile_by_id("p2-small")
    pilot = profile_by_id("p2-pilot-v1")
    scale = profile_by_id("p2-r0-scale-v1")

    assert small.generator_contract_version == GENERATOR_CONTRACT_VERSION
    assert pilot.paper_count == 500
    assert pilot.scientific_catalog_item_count == 2_500
    assert pilot.operational_catalog_item_count == 5_000
    assert scale.paper_count == 50_000
    assert scale.primary_paper_count == 40_000
    assert scale.review_paper_count == 10_000
    assert scale.scientific_catalog_item_count == 250_000
    assert scale.operational_catalog_item_count == 500_000
    assert scale.catalog_item_count == 750_000
    assert scale.process_event_count == 490_000
    assert scale.guardian_report_count == 10_000


def test_generated_small_workspaces_are_byte_deterministic_and_valid(tmp_path: Path) -> None:
    first = generate_workspace(tmp_path / "first", profile_id="p2-small", seed="contract-seed")
    second = generate_workspace(tmp_path / "second", profile_id="p2-small", seed="contract-seed")

    assert first.manifest == second.manifest
    assert first.manifest["content_tree_digest"] == second.manifest["content_tree_digest"]
    assert first.manifest["canonical_tree_digest"] == second.manifest["canonical_tree_digest"]
    assert first.manifest["source_tree_digest"] == second.manifest["source_tree_digest"]
    assert first.manifest["file_digests"] == second.manifest["file_digests"]
    assert first.manifest["expected_catalog_item_counts"] == {
        "operational": 14,
        "scientific": 26,
        "total": 40,
    }

    entries = load_workspace_entries(first.layout)
    validate_workspace_entries(entries)
    assert not any(
        document.record_kind == "parsed-page"
        for document in first.catalog_snapshot.documents
    )
    assert {kind for kind, _ in entries} >= {
        "registry-paper",
        "parsed-page",
        "paper-card",
        "evidence",
        "review-memory",
        "question-mapping",
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
        "process-event",
        "guardian-report",
    }


def test_small_workspace_projects_expected_filter_and_detail_answers(tmp_path: Path) -> None:
    generated = generate_workspace(tmp_path / "generated", profile_id="p2-small")
    session = WorkspaceSessionService({"small": generated.layout.config.path}).open("small")
    projection = CatalogProjectionService(session, generated.target / "runtime" / "app-state")
    built = projection.rebuild()
    query = CatalogQueryService(projection)
    expected = generated.manifest["expected_queries"]

    paper = query.search(paper_id=expected["paper_id"], page_size=100)
    question = query.search(question_id=expected["question_id"], page_size=100)
    detail = query.detail(paper["items"][0]["item_id"])

    assert built["item_count"] == generated.profile.catalog_item_count
    assert len(paper["items"]) == expected["paper_item_count"]
    assert len(question["items"]) == expected["question_item_count"]
    assert detail["current_record_status"] == "current"


def test_default_detail_resolves_exact_store_without_full_bundle_reload(tmp_path: Path) -> None:
    generated = generate_workspace(tmp_path / "generated", profile_id="p2-small")
    session = WorkspaceSessionService({"small": generated.layout.config.path}).open("small")
    projection = CatalogProjectionService(session, generated.target / "runtime" / "app-state")
    projection.rebuild()
    query = CatalogQueryService(projection)
    paper_id = generated.manifest["expected_queries"]["paper_id"]
    item = query.search(
        paper_id=paper_id,
        item_kinds=("paper_card_unit",),
        page_size=1,
    )["items"][0]

    projection.entry_loader = lambda _: (_ for _ in ()).throw(AssertionError("full reload"))
    detail = query.detail(item["item_id"])

    assert detail["current_record_status"] == "current"
    assert detail["detail"]["unit"]["unit_id"] == item["child_id"]


def test_default_detail_resolves_every_projected_record_kind(tmp_path: Path) -> None:
    generated = generate_workspace(tmp_path / "generated", profile_id="p2-small")
    session = WorkspaceSessionService({"small": generated.layout.config.path}).open("small")
    projection = CatalogProjectionService(session, generated.target / "runtime" / "app-state")
    projection.rebuild()
    query = CatalogQueryService(projection)
    items = query.search(page_size=100)["items"]

    details = [query.detail(item["item_id"]) for item in items]

    assert len(details) == generated.profile.catalog_item_count
    assert {detail["item"]["record_kind"] for detail in details} == {
        "registry-paper",
        "paper-card",
        "evidence",
        "review-memory",
        "question-mapping",
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
        "process-event",
        "guardian-report",
    }
    assert {detail["current_record_status"] for detail in details} == {"current"}


def test_generator_refuses_existing_relative_and_repository_targets(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(ResearchKBError) as existing_error:
        generate_workspace(existing, profile_id="p2-small")
    with pytest.raises(ResearchKBError) as relative_error:
        generate_workspace(Path("relative-target"), profile_id="p2-small")
    with pytest.raises(ResearchKBError) as repository_error:
        generate_workspace(Path(__file__).resolve().parents[2] / "generated", profile_id="p2-small")

    assert {error.value.diagnostic.code for error in (existing_error, relative_error, repository_error)} == {
        "RKBC-007"
    }


def test_inspection_rejects_payload_drift_and_foreign_files(tmp_path: Path) -> None:
    drifted = generate_workspace(tmp_path / "drifted", profile_id="p2-small")
    source = next((drifted.target / "workspace" / "sources").glob("*.txt"))
    source.write_text("changed\n", encoding="utf-8", newline="\n")
    with pytest.raises(ResearchKBError) as digest_error:
        inspect_generated_workspace(drifted.target)

    foreign = generate_workspace(tmp_path / "foreign", profile_id="p2-small")
    (foreign.target / "workspace" / "foreign.txt").write_text(
        "unexpected\n", encoding="utf-8", newline="\n"
    )
    with pytest.raises(ResearchKBError) as foreign_error:
        inspect_generated_workspace(foreign.target)

    assert digest_error.value.diagnostic.code == "RKBC-036"
    assert foreign_error.value.diagnostic.code == "RKBC-036"


def test_manifest_and_marker_do_not_serialize_target_path(tmp_path: Path) -> None:
    generated = generate_workspace(tmp_path / "generated", profile_id="p2-small")
    manifest_text = (generated.target / "generator-manifest.json").read_text(encoding="utf-8")
    marker_text = (generated.target / ".p2-catalog-generator.json").read_text(encoding="utf-8")

    assert str(generated.target) not in manifest_text
    assert str(generated.target) not in marker_text
    assert json.loads(marker_text)["state"] == "complete"


def test_core_measurement_restores_generated_payload_and_reports_filters(tmp_path: Path) -> None:
    generated = generate_workspace(tmp_path / "generated", profile_id="p2-small")
    before = generated.manifest["content_tree_digest"]

    receipt = measure_core_catalog(generated.target, repetitions=2, incremental_change_count=1)
    inspected = inspect_generated_workspace(generated.target)

    assert receipt["measurement_contract_version"] == "p2-core-catalog-measurement@1.0"
    assert receipt["profile_id"] == "p2-small"
    assert receipt["catalog_item_count"] == 40
    assert receipt["full_build"]["item_count"] == 40
    assert receipt["incremental_update"]["changed_source_count"] == 1
    assert receipt["queries"]["paper_filter"]["result_count"] == 5
    assert receipt["queries"]["question_filter"]["result_count"] == 5
    assert receipt["payload_restored"] is True
    assert inspected.manifest["content_tree_digest"] == before
    assert str(generated.target) not in json.dumps(receipt)


def test_reference_estimate_uses_pilot_and_enforces_disk_reserve(tmp_path: Path) -> None:
    pilot = generate_workspace(tmp_path / "pilot", profile_id="p2-small")
    receipt = measure_core_catalog(pilot.target, repetitions=1, incremental_change_count=1)

    enough = estimate_reference_workload(
        pilot.manifest,
        receipt,
        target_profile_id="p2-r0-scale-v1",
        free_bytes=100 * 1024**3,
    )
    constrained = estimate_reference_workload(
        pilot.manifest,
        receipt,
        target_profile_id="p2-r0-scale-v1",
        free_bytes=1,
    )

    assert enough["target_counts"]["catalog_items"] == 750_000
    assert enough["estimated_payload_bytes"] > pilot.manifest["byte_count"]
    assert enough["required_free_space_reserve_bytes"] >= 20 * 1024**3
    assert enough["may_proceed"] is True
    assert constrained["may_proceed"] is False


def test_registry_delta_measurement_uses_existing_projection_and_restores_payload(
    tmp_path: Path,
) -> None:
    generated = generate_workspace(tmp_path / "generated", profile_id="p2-small")
    session = WorkspaceSessionService({"small": generated.layout.config.path}).open("small")
    projection = CatalogProjectionService(session, generated.target / "runtime" / "app-state")
    projection.rebuild()

    receipt = measure_registry_delta(
        generated,
        repetitions=2,
        incremental_change_count=1,
    )

    assert receipt["measurement_contract_version"] == "p2-registry-delta-measurement@1.0"
    assert receipt["implementation"] == "benchmark_registry_delta_v1"
    assert receipt["changed_source_count"] == 1
    assert receipt["payload_restored"] is True
    assert receipt["projection_registry_restored"] is True


def test_projection_rebuild_measurement_is_separate_and_restores_payload(tmp_path: Path) -> None:
    generated = generate_workspace(tmp_path / "generated", profile_id="p2-small")

    receipt = measure_projection_rebuild(generated, repetitions=1, warm_up_runs=0)

    assert receipt["measurement_contract_version"] == "p2-projection-rebuild-measurement@1.0"
    assert receipt["item_count"] == generated.profile.catalog_item_count
    assert receipt["catalog_schema_version"] == 3
    assert receipt["payload_restored"] is True


def test_catalog_read_measurement_uses_stale_bind_and_current_registry_detail(
    tmp_path: Path,
) -> None:
    generated = generate_workspace(tmp_path / "generated", profile_id="p2-small")
    session = WorkspaceSessionService({"small": generated.layout.config.path}).open("small")
    projection = CatalogProjectionService(session, generated.target / "runtime" / "app-state")
    projection.rebuild()

    receipt = measure_catalog_reads(generated, repetitions=2)

    assert receipt["measurement_contract_version"] == "p2-catalog-read-measurement@1.0"
    assert receipt["restart_bind"]["projection_state"] == "stale"
    assert receipt["restart_bind"]["freshness_verification"] == "unverified_after_restart"
    assert receipt["fts_selective"]["projection_state"] == "stale"
    assert receipt["registry_detail"]["record_kind"] == "registry-paper"
    assert receipt["registry_detail"]["current_record_status"] == "current"
    assert receipt["payload_restored"] is True


def test_benchmark_cli_profile_generate_inspect_and_measure(tmp_path: Path, capsys) -> None:
    target = tmp_path / "generated"
    receipt = tmp_path / "receipt.json"

    assert main(["profile", "--profile", "p2-small"]) == 0
    profile = json.loads(capsys.readouterr().out)
    assert profile["catalog_item_count"] == 40

    assert main(["generate", "--profile", "p2-small", "--target", str(target)]) == 0
    generated = json.loads(capsys.readouterr().out)
    assert generated["status"] == "success"
    assert generated["workspace_config"] == "workspace/workspace.yaml"
    assert str(target) not in json.dumps(generated)

    assert main(["inspect", "--target", str(target)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["content_tree_digest"] == generated["content_tree_digest"]

    assert main(
        [
            "measure",
            "--target",
            str(target),
            "--repetitions",
            "1",
            "--incremental-change-count",
            "1",
            "--output",
            str(receipt),
        ]
    ) == 0
    measured = json.loads(capsys.readouterr().out)
    assert measured["status"] == "success"
    assert measured["receipt_written"] is True
    assert json.loads(receipt.read_text(encoding="utf-8"))["payload_restored"] is True


def test_committed_p2_small_seed_materializes_through_core_bootstrap(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "p2_small"

    materialized = materialize_portable_seed(fixture, tmp_path / "materialized")

    assert materialized.profile.profile_id == "p2-small"
    assert materialized.layout.marker_path.is_file()
    assert materialized.manifest["expected_catalog_item_counts"]["total"] == 40
    assert len(materialized.catalog_snapshot.documents) == 40


def test_generate_measure_requires_and_consumes_matching_preflight(tmp_path: Path, capsys) -> None:
    pilot = generate_workspace(tmp_path / "pilot", profile_id="p2-small")
    pilot_receipt = measure_core_catalog(
        pilot,
        repetitions=1,
        incremental_change_count=1,
        warm_up_runs=0,
    )
    preflight = estimate_reference_workload(
        pilot.manifest,
        pilot_receipt,
        target_profile_id="p2-small",
        free_bytes=100 * 1024**3,
    )
    preflight_path = tmp_path / "preflight.json"
    output = tmp_path / "combined-receipt.json"
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8", newline="\n")

    assert main(
        [
            "generate-measure",
            "--profile",
            "p2-small",
            "--target",
            str(tmp_path / "combined"),
            "--preflight-receipt",
            str(preflight_path),
            "--incremental-change-count",
            "1",
            "--leave-stale-projection",
            "--output",
            str(output),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["payload_restored"] is True
    assert json.loads(output.read_text(encoding="utf-8"))[
        "projection_state_after_payload_restore"
    ] == "stale_expected"
