from __future__ import annotations

import math
import os
import platform
import shutil
import statistics
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from benchmarks.p2_catalog_scale.generator import (
    GeneratedWorkspace,
    inspect_generated_workspace,
    verify_generated_payload,
)
from benchmarks.p2_catalog_scale.profiles import profile_by_id
from research_kb.catalog import CatalogDatabase
from research_kb.services import CatalogProjectionService, CatalogQueryService, WorkspaceSessionService
from research_kb.storage.json_io import read_jsonl, serialize_jsonl, sha256_bytes


MEASUREMENT_CONTRACT_VERSION = "p2-core-catalog-measurement@1.0"
CATALOG_READ_MEASUREMENT_VERSION = "p2-catalog-read-measurement@1.0"
REGISTRY_DELTA_MEASUREMENT_VERSION = "p2-registry-delta-measurement@1.0"
PROJECTION_REBUILD_MEASUREMENT_VERSION = "p2-projection-rebuild-measurement@1.0"
GIB = 1024**3


def measure_core_catalog(
    target: Path | GeneratedWorkspace,
    *,
    repetitions: int = 5,
    incremental_change_count: int = 1_000,
    warm_up_runs: int = 1,
    restore_projection: bool = True,
) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("measurement repetitions must be positive")
    if warm_up_runs < 0:
        raise ValueError("measurement warm-up count must be non-negative")
    if repetitions > 1 and not restore_projection:
        raise ValueError("multiple incremental repetitions require projection restoration")
    generated = (
        target
        if isinstance(target, GeneratedWorkspace)
        else inspect_generated_workspace(Path(target))
    )
    if incremental_change_count < 1 or incremental_change_count > generated.profile.paper_count:
        raise ValueError("incremental change count is outside the generated paper range")
    runtime_root = generated.target / "runtime"
    runtime_root.mkdir(exist_ok=True)
    session = WorkspaceSessionService({"benchmark": generated.layout.config.path}).open("benchmark")
    projection = CatalogProjectionService(session, runtime_root / "app-state")
    query = CatalogQueryService(projection)

    full_result = None
    for _ in range(warm_up_runs):
        full_result = projection.rebuild()
    full_samples = []
    for _ in range(repetitions):
        started = time.perf_counter()
        full_result = projection.rebuild()
        full_samples.append(round((time.perf_counter() - started) * 1_000, 6))
    assert full_result is not None
    query.bind_projection_result(full_result)
    query_receipts = _measure_queries(query, generated, repetitions)
    cursor_count = _cursor_walk_count(query)
    database_bytes = projection.paths.database_path.stat().st_size

    registry_path = generated.layout.registry_path
    original_registry = registry_path.read_bytes()
    original_registry_digest = sha256_bytes(original_registry)
    changed_counts: list[int] = []
    incremental_samples: list[float] = []
    latest_projection_result = full_result
    try:
        for repetition in range(repetitions):
            records = read_jsonl(
                registry_path,
                record_kind="registry-paper",
                missing_ok=False,
                id_field="paper_id",
            )
            for record in records[:incremental_change_count]:
                record["bibliography"]["title"] += f" Revised {repetition + 1}"
            registry_path.write_bytes(serialize_jsonl(records))
            revised_registry_digest = sha256_bytes(registry_path.read_bytes())
            started = time.perf_counter()
            result = projection._benchmark_registry_delta(
                base_source_watermark=latest_projection_result["source_watermark"],
                before_registry_store_digest=original_registry_digest,
                after_registry_store_digest=revised_registry_digest,
            )
            latest_projection_result = result
            incremental_samples.append((time.perf_counter() - started) * 1_000)
            changed_counts.append(int(result["changed_source_count"]))
            registry_path.write_bytes(original_registry)
            if repetition < repetitions - 1 or restore_projection:
                latest_projection_result = projection._benchmark_registry_delta(
                    base_source_watermark=result["source_watermark"],
                    before_registry_store_digest=revised_registry_digest,
                    after_registry_store_digest=original_registry_digest,
                )
    finally:
        registry_path.write_bytes(original_registry)

    restored_manifest = verify_generated_payload(generated.target)
    return {
        "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
        "generator_contract_version": generated.profile.generator_contract_version,
        "profile_id": generated.profile.profile_id,
        "seed": generated.seed,
        "catalog_item_count": generated.profile.catalog_item_count,
        "environment": _environment(generated.target),
        "method": {
            "warm_up_runs": warm_up_runs,
            "measured_repetitions": repetitions,
            "summary": "median_and_nearest_rank_p95",
            "clock": "time.perf_counter",
        },
        "full_build": {
            "item_count": generated.profile.catalog_item_count,
            "samples_ms": full_samples,
            **_summary(full_samples),
        },
        "incremental_update": {
            "implementation": "benchmark_registry_delta_v1",
            "store_key": "registry",
            "requested_changed_records": incremental_change_count,
            "changed_source_count": min(changed_counts),
            "observed_changed_source_counts": changed_counts,
            "samples_ms": incremental_samples,
            **_summary(incremental_samples),
        },
        "queries": query_receipts,
        "cursor_walk": {"item_count": cursor_count, "page_size": 100},
        "catalog_database_bytes": database_bytes,
        "source_watermark": latest_projection_result["source_watermark"],
        "payload_digest_before": generated.manifest["content_tree_digest"],
        "payload_digest_after": restored_manifest["content_tree_digest"],
        "payload_restored": (
            generated.manifest["content_tree_digest"]
            == restored_manifest["content_tree_digest"]
        ),
        "projection_state_after_payload_restore": (
            "current" if restore_projection else "stale_expected"
        ),
        "contains_private_paths": False,
    }


def measure_registry_delta(
    target: Path | GeneratedWorkspace,
    *,
    repetitions: int = 5,
    incremental_change_count: int = 1_000,
) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("measurement repetitions must be positive")
    generated = (
        target
        if isinstance(target, GeneratedWorkspace)
        else inspect_generated_workspace(Path(target))
    )
    if incremental_change_count < 1 or incremental_change_count > generated.profile.paper_count:
        raise ValueError("incremental change count is outside the generated paper range")

    runtime_root = generated.target / "runtime"
    session = WorkspaceSessionService({"benchmark": generated.layout.config.path}).open("benchmark")
    projection = CatalogProjectionService(session, runtime_root / "app-state")
    inspection = CatalogDatabase.inspect(projection.paths.database_path)
    if inspection.state != "ready":
        raise ValueError("Registry delta measurement requires an existing compatible projection")

    registry_path = generated.layout.registry_path
    original_registry = registry_path.read_bytes()
    original_registry_digest = sha256_bytes(original_registry)
    stored_registry_digest = inspection.metadata.get("source_store_digests", {}).get("registry")
    if stored_registry_digest != original_registry_digest:
        raise ValueError("Registry delta measurement requires a current Registry projection")

    latest_source_watermark = inspection.metadata["source_watermark"]
    changed_counts: list[int] = []
    samples: list[float] = []
    try:
        for repetition in range(repetitions):
            records = read_jsonl(
                registry_path,
                record_kind="registry-paper",
                missing_ok=False,
                id_field="paper_id",
            )
            for record in records[:incremental_change_count]:
                record["bibliography"]["title"] += f" Delta {repetition + 1}"
            registry_path.write_bytes(serialize_jsonl(records))
            revised_registry_digest = sha256_bytes(registry_path.read_bytes())
            started = time.perf_counter()
            result = projection._benchmark_registry_delta(
                base_source_watermark=latest_source_watermark,
                before_registry_store_digest=original_registry_digest,
                after_registry_store_digest=revised_registry_digest,
            )
            samples.append(round((time.perf_counter() - started) * 1_000, 6))
            changed_counts.append(int(result["changed_source_count"]))
            registry_path.write_bytes(original_registry)
            restored = projection._benchmark_registry_delta(
                base_source_watermark=result["source_watermark"],
                before_registry_store_digest=revised_registry_digest,
                after_registry_store_digest=original_registry_digest,
            )
            latest_source_watermark = restored["source_watermark"]
    finally:
        registry_path.write_bytes(original_registry)

    restored_manifest = verify_generated_payload(generated.target)
    final_inspection = CatalogDatabase.inspect(projection.paths.database_path)
    return {
        "measurement_contract_version": REGISTRY_DELTA_MEASUREMENT_VERSION,
        "generator_contract_version": generated.profile.generator_contract_version,
        "profile_id": generated.profile.profile_id,
        "seed": generated.seed,
        "environment": _environment(generated.target),
        "implementation": "benchmark_registry_delta_v1",
        "store_key": "registry",
        "requested_changed_records": incremental_change_count,
        "changed_source_count": min(changed_counts),
        "observed_changed_source_counts": changed_counts,
        "samples_ms": samples,
        **_summary(samples),
        "source_watermark": latest_source_watermark,
        "payload_digest_before": generated.manifest["content_tree_digest"],
        "payload_digest_after": restored_manifest["content_tree_digest"],
        "payload_restored": (
            generated.manifest["content_tree_digest"]
            == restored_manifest["content_tree_digest"]
        ),
        "projection_registry_restored": (
            final_inspection.state == "ready"
            and final_inspection.metadata.get("source_store_digests", {}).get("registry")
            == original_registry_digest
        ),
        "contains_private_paths": False,
    }


def measure_projection_rebuild(
    target: Path | GeneratedWorkspace,
    *,
    repetitions: int = 1,
    warm_up_runs: int = 0,
) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("measurement repetitions must be positive")
    if warm_up_runs < 0:
        raise ValueError("measurement warm-up count must be non-negative")
    generated = (
        target
        if isinstance(target, GeneratedWorkspace)
        else inspect_generated_workspace(Path(target))
    )
    session = WorkspaceSessionService({"benchmark": generated.layout.config.path}).open("benchmark")
    projection = CatalogProjectionService(session, generated.target / "runtime" / "app-state")
    latest = None
    for _ in range(warm_up_runs):
        latest = projection.rebuild()
    samples: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        latest = projection.rebuild()
        samples.append(round((time.perf_counter() - started) * 1_000, 6))
    assert latest is not None
    restored_manifest = verify_generated_payload(generated.target)
    inspection = CatalogDatabase.inspect(projection.paths.database_path)
    return {
        "measurement_contract_version": PROJECTION_REBUILD_MEASUREMENT_VERSION,
        "generator_contract_version": generated.profile.generator_contract_version,
        "profile_id": generated.profile.profile_id,
        "seed": generated.seed,
        "environment": _environment(generated.target),
        "warm_up_runs": warm_up_runs,
        "measured_repetitions": repetitions,
        "samples_ms": samples,
        **_summary(samples),
        "item_count": latest["item_count"],
        "source_record_count": latest["source_record_count"],
        "source_watermark": latest["source_watermark"],
        "catalog_database_bytes": projection.paths.database_path.stat().st_size,
        "catalog_schema_version": inspection.metadata.get("catalog_schema_version"),
        "payload_digest_before": generated.manifest["content_tree_digest"],
        "payload_digest_after": restored_manifest["content_tree_digest"],
        "payload_restored": (
            generated.manifest["content_tree_digest"]
            == restored_manifest["content_tree_digest"]
        ),
        "contains_private_paths": False,
    }


def measure_catalog_reads(
    target: Path | GeneratedWorkspace,
    *,
    repetitions: int = 20,
) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("measurement repetitions must be positive")
    generated = (
        target
        if isinstance(target, GeneratedWorkspace)
        else inspect_generated_workspace(Path(target))
    )
    session = WorkspaceSessionService({"benchmark": generated.layout.config.path}).open("benchmark")
    projection = CatalogProjectionService(
        session,
        generated.target / "runtime" / "app-state",
    )

    bind_samples: list[float] = []
    bind_status = None
    for _ in range(repetitions):
        query = CatalogQueryService(projection)
        started = time.perf_counter()
        bind_status = query.bind_existing_projection()
        bind_samples.append(round((time.perf_counter() - started) * 1_000, 6))
    assert bind_status is not None
    query = CatalogQueryService(projection)
    query.bind_existing_projection()

    selective_operation = lambda: query.search(
        query="response token 00000001",
        page_size=20,
    )
    selective = selective_operation()
    selective_samples = [_elapsed_ms(selective_operation) for _ in range(repetitions)]

    paper_id = generated.manifest["expected_queries"]["paper_id"]
    paper_item = query.search(
        paper_id=paper_id,
        item_kinds=("paper",),
        page_size=1,
    )["items"][0]
    detail_operation = lambda: query.detail(paper_item["item_id"])
    detail = detail_operation()
    detail_samples = [_elapsed_ms(detail_operation) for _ in range(repetitions)]

    restored_manifest = verify_generated_payload(generated.target)
    inspection = CatalogDatabase.inspect(projection.paths.database_path)
    return {
        "measurement_contract_version": CATALOG_READ_MEASUREMENT_VERSION,
        "generator_contract_version": generated.profile.generator_contract_version,
        "profile_id": generated.profile.profile_id,
        "seed": generated.seed,
        "environment": _environment(generated.target),
        "method": {
            "measured_repetitions": repetitions,
            "summary": "median_and_nearest_rank_p95",
            "clock": "time.perf_counter",
        },
        "restart_bind": {
            "projection_state": bind_status["projection_state"],
            "freshness_verification": bind_status["freshness_verification"],
            "samples_ms": bind_samples,
            **_summary(bind_samples),
        },
        "fts_selective": {
            "result_count": len(selective["items"]),
            "projection_state": selective["projection_state"],
            "samples_ms": selective_samples,
            **_summary(selective_samples),
        },
        "registry_detail": {
            "record_kind": detail["item"]["record_kind"],
            "current_record_status": detail["current_record_status"],
            "projection_state": detail["projection_state"],
            "samples_ms": detail_samples,
            **_summary(detail_samples),
        },
        "catalog_schema_version": inspection.metadata.get("catalog_schema_version"),
        "payload_digest_before": generated.manifest["content_tree_digest"],
        "payload_digest_after": restored_manifest["content_tree_digest"],
        "payload_restored": (
            generated.manifest["content_tree_digest"]
            == restored_manifest["content_tree_digest"]
        ),
        "contains_private_paths": False,
    }


def estimate_reference_workload(
    pilot_manifest: dict[str, Any],
    pilot_receipt: dict[str, Any],
    *,
    target_profile_id: str,
    free_bytes: int,
) -> dict[str, Any]:
    target = profile_by_id(target_profile_id)
    pilot_catalog_items = int(pilot_manifest["expected_catalog_item_counts"]["total"])
    pilot_file_count = int(pilot_manifest["total_file_count"])
    if pilot_catalog_items < 1 or pilot_file_count < 1:
        raise ValueError("pilot manifest has no measurable workload")
    item_ratio = target.catalog_item_count / pilot_catalog_items
    paper_ratio = target.paper_count / int(pilot_manifest["parameters"]["primary_paper_count"] + pilot_manifest["parameters"]["review_paper_count"])
    payload_bytes = math.ceil(int(pilot_manifest["byte_count"]) * max(item_ratio, paper_ratio) * 1.2)
    projection_bytes = math.ceil(int(pilot_receipt["catalog_database_bytes"]) * item_ratio * 1.2)
    file_count = math.ceil(pilot_file_count * paper_ratio * 1.1)
    combined = payload_bytes + projection_bytes
    reserve = max(20 * GIB, 2 * combined)
    return {
        "estimation_contract_version": "p2-reference-estimate@1.0",
        "pilot_profile_id": pilot_manifest["profile_id"],
        "target_profile_id": target.profile_id,
        "target_counts": {
            "papers": target.paper_count,
            "scientific_catalog_items": target.scientific_catalog_item_count,
            "operational_catalog_items": target.operational_catalog_item_count,
            "catalog_items": target.catalog_item_count,
        },
        "estimated_payload_bytes": payload_bytes,
        "estimated_projection_bytes": projection_bytes,
        "estimated_total_bytes": combined,
        "estimated_file_count": file_count,
        "free_bytes": int(free_bytes),
        "required_free_space_reserve_bytes": reserve,
        "may_proceed": int(free_bytes) - combined >= reserve,
        "safety_multiplier": 1.2,
    }


def _measure_queries(
    query: CatalogQueryService,
    generated: GeneratedWorkspace,
    repetitions: int,
) -> dict[str, Any]:
    expected = generated.manifest["expected_queries"]
    cases: dict[str, Callable[[], dict[str, Any]]] = {
        "browse": lambda: query.search(page_size=20),
        "fts_selective": lambda: query.search(
            query="response token 00000001",
            page_size=20,
        ),
        "fts_broad_stress": lambda: query.search(query="Synthetic", page_size=20),
        "paper_filter": lambda: query.search(paper_id=expected["paper_id"], page_size=100),
    }
    if expected["question_id"] is not None:
        cases["question_filter"] = lambda: query.search(
            question_id=expected["question_id"], page_size=100
        )
    receipts: dict[str, Any] = {}
    for name, operation in cases.items():
        result = operation()
        samples = [_elapsed_ms(operation) for _ in range(repetitions)]
        receipts[name] = {
            "result_count": len(result["items"]),
            "has_more": result["has_more"],
            "samples_ms": samples,
            **_summary(samples),
        }
    first = query.search(
        paper_id=expected["paper_id"],
        item_kinds=("paper",),
        page_size=1,
    )["items"][0]
    detail_operation = lambda: query.detail(first["item_id"])
    detail = detail_operation()
    detail_samples = [_elapsed_ms(detail_operation) for _ in range(repetitions)]
    receipts["detail"] = {
        "current_record_status": detail["current_record_status"],
        "samples_ms": detail_samples,
        **_summary(detail_samples),
    }
    return receipts


def _cursor_walk_count(query: CatalogQueryService) -> int:
    count = 0
    cursor = None
    while True:
        page = query.search(page_size=100, cursor=cursor)
        count += len(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return count


def _elapsed_ms(operation: Callable[[], Any]) -> float:
    started = time.perf_counter()
    operation()
    return round((time.perf_counter() - started) * 1_000, 6)


def _summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "median_ms": round(statistics.median(samples), 6),
        "p95_ms": round(ordered[index], 6),
    }


def _environment(target: Path) -> dict[str, Any]:
    try:
        core_version = version("research-kb-core")
    except PackageNotFoundError:
        core_version = "source-tree"
    disk = shutil.disk_usage(target)
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "core_distribution_version": core_version,
        "logical_cpu_count": os.cpu_count(),
        "storage_class": "not_auto_detected",
        "free_bytes_at_measurement": disk.free,
    }


__all__ = [
    "CATALOG_READ_MEASUREMENT_VERSION",
    "MEASUREMENT_CONTRACT_VERSION",
    "PROJECTION_REBUILD_MEASUREMENT_VERSION",
    "REGISTRY_DELTA_MEASUREMENT_VERSION",
    "estimate_reference_workload",
    "measure_catalog_reads",
    "measure_core_catalog",
    "measure_projection_rebuild",
    "measure_registry_delta",
]
