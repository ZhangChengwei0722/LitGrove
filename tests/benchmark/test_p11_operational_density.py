from __future__ import annotations

from benchmarks.p11_operational_density import (
    generate_workspace,
    inspect_generated_workspace,
    measure_backup_restore,
    measure_maintenance,
    measure_operational_reads,
    measure_startup,
    profile_by_id,
)
from benchmarks.p11_operational_density.__main__ import _measure_maintenance_isolated
from research_kb.services import CatalogProjectionService, CatalogQueryService, WorkspaceSessionService


def test_formal_profile_freezes_required_operational_counts() -> None:
    profile = profile_by_id("p11-operational-recovery-windows-v1")
    assert profile.job_count == 25_000
    assert profile.task_count == 25_000
    assert profile.report_only_result_count == 5_000
    assert profile.process_event_count == 250_000
    assert profile.guardian_report_count == 10_000
    assert profile.journal_count == 10_000
    assert profile.maintenance_trigger_count == 100_000
    assert profile.maintenance_key_count == 1_000


def test_small_operational_fixture_is_deterministic_valid_and_measurable(tmp_path) -> None:
    first = generate_workspace(tmp_path / "first", profile_id="p11-operational-small")
    second = generate_workspace(tmp_path / "second", profile_id="p11-operational-small")
    assert first.manifest["tracked_digests"] == second.manifest["tracked_digests"]
    assert inspect_generated_workspace(first.target).manifest == first.manifest

    reads = measure_operational_reads(first, repetitions=1)
    assert reads["job_first_page"]["p95_seconds"] >= 0
    assert measure_startup(first.target, repetitions=1)["startup_inspection"]["p95_seconds"] >= 0
    backup = measure_backup_restore(
        first,
        archive_path=tmp_path / "small.rkb-backup.zip",
        restore_target=tmp_path / "restored-small",
    )
    assert backup["passed"] is True
    assert backup["durable_entries_equal"] is True
    maintenance = measure_maintenance(first)
    assert maintenance["archived_journal_count"] == 4
    assert maintenance["maintenance_open_count"] == 2
    assert maintenance["maintenance_trigger_count"] == 20
    assert maintenance["maintenance_trigger_ref_count"] == 20
    assert maintenance["passed"] is True


def test_small_operational_fixture_uses_one_catalog_for_bounded_job_and_task_pages(tmp_path) -> None:
    generated = generate_workspace(tmp_path / "fixture", profile_id="p11-operational-small")
    session = WorkspaceSessionService({"fixture": generated.layout.config.path}).open("fixture")
    projection = CatalogProjectionService(session, tmp_path / "app-state")
    query = CatalogQueryService(projection)
    query.bind_projection_result(projection.rebuild())

    first_jobs = query.operational_page(item_kind="pipeline_job", page_size=4)
    second_jobs = query.operational_page(
        item_kind="pipeline_job",
        page_size=4,
        cursor=first_jobs["next_cursor"],
    )
    first_tasks = query.operational_page(item_kind="agent_task", page_size=4)
    second_tasks = query.operational_page(
        item_kind="agent_task",
        page_size=4,
        cursor=first_tasks["next_cursor"],
    )
    late_tasks = query.operational_page(
        item_kind="agent_task",
        page_size=4,
        cursor=query.operational_late_cursor(item_kind="agent_task", page_size=4),
    )

    assert len(first_jobs["records"]) == 4
    assert len(second_jobs["records"]) == 4
    assert len({item["job_id"] for item in [*first_jobs["records"], *second_jobs["records"]]}) == 8
    assert all(item["state_digest"] for item in first_jobs["records"])
    assert len(first_tasks["records"]) == 4
    assert len(second_tasks["records"]) == 4
    assert len({item["task_id"] for item in [*first_tasks["records"], *second_tasks["records"]]}) == 8
    assert all(item["input_basis_digest"] for item in first_tasks["records"])
    assert len(late_tasks["records"]) == 4
    assert late_tasks["has_more"] is False
    assert all(item["projection_state"] == "current" for item in (first_jobs, second_jobs, first_tasks, second_tasks))


def test_maintenance_memory_measurement_runs_in_an_isolated_process(tmp_path) -> None:
    generated = generate_workspace(
        tmp_path / "isolated-maintenance",
        profile_id="p11-operational-small",
    )

    receipt = _measure_maintenance_isolated(generated.target)

    assert receipt["passed"] is True
    assert receipt["peak_rss_bytes"] <= receipt["thresholds"][
        "maintenance_peak_rss_bytes"
    ]
