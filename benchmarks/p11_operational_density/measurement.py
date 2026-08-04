from __future__ import annotations

import math
import os
import platform
import statistics
import sys
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from benchmarks.p11_operational_density.generator import (
    GeneratedOperationalWorkspace,
    inspect_generated_workspace,
    maintenance_triggers,
)
from benchmarks.p11_operational_density.profiles import GENERATOR_CONTRACT_VERSION
from research_kb.backup import BackupArchiveReader, BackupService
from research_kb.guardian import GuardianService
from research_kb.identifiers import Namespace
from research_kb.operational_maintenance import MaintenanceWorkService, OperationalMaintenanceService
from research_kb.services.catalog import CatalogProjectionService, CatalogQueryService
from research_kb.services.workspace_session import WorkspaceSessionService
from research_kb.storage.json_io import file_sha256, read_jsonl
from research_kb.workspace import WorkspaceLayout


MEASUREMENT_CONTRACT_VERSION = "p11-operational-recovery-measurement@1.0"
BACKUP_MEASUREMENT_CONTRACT_VERSION = "p11-backup-restore-measurement@1.0"
OPERATIONAL_THRESHOLDS = {
    "startup_seconds": 15.0,
    "page_p95_seconds": 3.0,
    "archive_seconds": 120.0,
    "trigger_seconds": 30.0,
    "maintenance_peak_rss_bytes": 512 * 1024**2,
}
BACKUP_THRESHOLDS = {
    "backup_seconds": 180.0,
    "inspect_seconds": 120.0,
    "restore_seconds": 240.0,
}


def measure_operational_reads(
    generated: GeneratedOperationalWorkspace,
    *,
    repetitions: int = 3,
) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("measurement repetitions must be positive")
    layout = generated.layout
    session = WorkspaceSessionService({"benchmark": layout.config.path}).open("benchmark")
    state_root = generated.target.parent / f"{generated.target.name}-catalog-state"
    projection = CatalogProjectionService(session, state_root)
    query = CatalogQueryService(projection)
    prepare_started = time.perf_counter()
    query.bind_existing_projection()
    operational_status = query.bind_operational_projection()
    if operational_status["projection_state"] == "current":
        inspection = projection.inspect_status()
        build = {
            "item_count": inspection["item_count"],
            "build_mode": "reused_current_operational_projection",
        }
    else:
        build = projection.rebuild()
        query.bind_projection_result(build)
    projection_prepare_seconds = time.perf_counter() - prepare_started
    late_job_cursor = query.operational_late_cursor(item_kind="pipeline_job")
    late_task_cursor = query.operational_late_cursor(item_kind="agent_task")
    result = {
        "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": generated.profile.profile_id,
        "environment": _environment(),
        "projection_prepare_seconds": projection_prepare_seconds,
        "projection_prepare_mode": build["build_mode"],
        "projection_item_count": build["item_count"],
        "job_first_page": _measure(
            lambda: query.operational_page(item_kind="pipeline_job", page_size=100),
            repetitions,
        ),
        "job_late_page": _measure(
            lambda: query.operational_page(
                item_kind="pipeline_job",
                page_size=100,
                cursor=late_job_cursor,
            ),
            repetitions,
        ),
        "task_first_page": _measure(
            lambda: query.operational_page(item_kind="agent_task", page_size=100),
            repetitions,
        ),
        "task_late_page": _measure(
            lambda: query.operational_page(
                item_kind="agent_task",
                page_size=100,
                cursor=late_task_cursor,
            ),
            repetitions,
        ),
    }
    result["thresholds"] = {"page_p95_seconds": OPERATIONAL_THRESHOLDS["page_p95_seconds"]}
    result["passed"] = all(
        result[key]["p95_seconds"] <= OPERATIONAL_THRESHOLDS["page_p95_seconds"]
        for key in ("job_first_page", "job_late_page", "task_first_page", "task_late_page")
    )
    result["contains_private_paths"] = False
    return result


def measure_startup(target: Path, *, repetitions: int = 3) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("measurement repetitions must be positive")
    samples = _measure(
        lambda: inspect_generated_workspace(Path(target), validate_full_bundle=False),
        repetitions,
    )
    profile_id = inspect_generated_workspace(Path(target)).profile.profile_id
    return {
        "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": profile_id,
        "startup_inspection": samples,
        "threshold_seconds": OPERATIONAL_THRESHOLDS["startup_seconds"],
        "passed": samples["p95_seconds"] <= OPERATIONAL_THRESHOLDS["startup_seconds"],
        "environment": _environment(),
        "contains_private_paths": False,
    }


def measure_maintenance(generated: GeneratedOperationalWorkspace) -> dict[str, Any]:
    archive = OperationalMaintenanceService(generated.layout)
    preview = archive.preview_journal_archive()
    started = time.perf_counter()
    archived = archive.archive_journals(expected_basis_digest=preview["basis_digest"], actor="user")
    archive_seconds = time.perf_counter() - started
    maintenance = MaintenanceWorkService(generated.layout)
    started = time.perf_counter()
    result = maintenance.enqueue(maintenance_triggers(generated.profile, generated.seed), actor="cli")
    trigger_seconds = time.perf_counter() - started
    items = read_jsonl(
        generated.layout.maintenance_work_path,
        record_kind="maintenance-work",
        id_field="maintenance_id",
    )
    trigger_ref_count = sum(len(item["trigger_refs"]) for item in items)
    peak_rss = _peak_rss_bytes()
    receipt = {
        "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": generated.profile.profile_id,
        "environment": _environment(),
        "archive_seconds": archive_seconds,
        "archived_journal_count": archived["archived_journal_count"],
        "trigger_seconds": trigger_seconds,
        "maintenance_open_count": result["open_count"],
        "maintenance_trigger_count": generated.profile.maintenance_trigger_count,
        "maintenance_trigger_ref_count": trigger_ref_count,
        "peak_rss_bytes": peak_rss,
        "thresholds": {
            "archive_seconds": OPERATIONAL_THRESHOLDS["archive_seconds"],
            "trigger_seconds": OPERATIONAL_THRESHOLDS["trigger_seconds"],
            "maintenance_peak_rss_bytes": OPERATIONAL_THRESHOLDS["maintenance_peak_rss_bytes"],
        },
        "contains_private_paths": False,
    }
    receipt["passed"] = (
        receipt["archived_journal_count"] == generated.profile.journal_count
        and receipt["maintenance_open_count"] == generated.profile.maintenance_key_count
        and trigger_ref_count == generated.profile.maintenance_trigger_count
        and archive_seconds <= OPERATIONAL_THRESHOLDS["archive_seconds"]
        and trigger_seconds <= OPERATIONAL_THRESHOLDS["trigger_seconds"]
        and peak_rss <= OPERATIONAL_THRESHOLDS["maintenance_peak_rss_bytes"]
    )
    return receipt


def measure_backup_restore(
    generated: GeneratedOperationalWorkspace,
    *,
    archive_path: Path,
    restore_target: Path,
) -> dict[str, Any]:
    archive = Path(archive_path)
    restored_root = Path(restore_target)
    backup_id = _deterministic_id(Namespace.BACKUP, generated.seed, "backup")
    restore_id = _deterministic_id(Namespace.RESTORE, generated.seed, "restore")
    service = BackupService(generated.layout)
    preview = service.preview(include_sources=False)
    request = {
        "backup_id": backup_id,
        "include_sources": False,
        "expected_basis_digest": preview["basis_digest"],
        "created_at": "2026-08-05T00:00:00Z",
    }
    started = time.perf_counter()
    created = service.create(request, target=archive, actor="user")
    backup_seconds = time.perf_counter() - started

    reader = BackupArchiveReader()
    started = time.perf_counter()
    inspection = reader.inspect(archive)
    inspect_seconds = time.perf_counter() - started

    mappings = {root_id: str(path) for root_id, path in generated.layout.source_roots.items()}
    started = time.perf_counter()
    restored = BackupService.restore(
        archive,
        {
            "restore_id": restore_id,
            "expected_archive_sha256": inspection["archive_sha256"],
            "source_root_mappings": mappings,
            "created_at": "2026-08-05T00:00:00Z",
        },
        target_root=restored_root,
        actor="user",
    )
    restore_seconds = time.perf_counter() - started

    restored_config = Path(restored["workspace_config_path"])
    restored_layout = WorkspaceLayout.load(restored_config)
    durable_entries_equal = _restored_payload_matches(
        restored_layout.knowledge_root,
        inspection["manifest"]["entries"],
    )
    guardian_status = GuardianService(restored_layout).check().report["status"]
    receipt = {
        "measurement_contract_version": BACKUP_MEASUREMENT_CONTRACT_VERSION,
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": generated.profile.profile_id,
        "environment": _environment(),
        "source_mode": inspection["manifest"]["source_mode"],
        "entry_count": len(inspection["manifest"]["entries"]),
        "archive_bytes": created["archive_bytes"],
        "archive_sha256": inspection["archive_sha256"],
        "manifest_sha256": inspection["manifest_sha256"],
        "backup_seconds": backup_seconds,
        "inspect_seconds": inspect_seconds,
        "restore_seconds": restore_seconds,
        "durable_entries_equal": durable_entries_equal,
        "guardian_status": guardian_status,
        "thresholds": dict(BACKUP_THRESHOLDS),
        "contains_private_paths": False,
    }
    receipt["passed"] = (
        receipt["source_mode"] == "inventory_only"
        and durable_entries_equal
        and guardian_status != "failure"
        and backup_seconds <= BACKUP_THRESHOLDS["backup_seconds"]
        and inspect_seconds <= BACKUP_THRESHOLDS["inspect_seconds"]
        and restore_seconds <= BACKUP_THRESHOLDS["restore_seconds"]
    )
    return receipt


def _measure(operation: Callable[[], Any], repetitions: int) -> dict[str, Any]:
    values = []
    for _ in range(repetitions):
        started = time.perf_counter()
        operation()
        values.append(time.perf_counter() - started)
    ordered = sorted(values)
    p95_index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return {"seconds": values, "median_seconds": statistics.median(values), "p95_seconds": ordered[p95_index]}


def _restored_payload_matches(knowledge_root: Path, entries: list[dict[str, Any]]) -> bool:
    for item in entries:
        path = item["path"]
        if not path.startswith("knowledge/"):
            continue
        relative = path.removeprefix("knowledge/")
        restored = knowledge_root.joinpath(*relative.split("/"))
        if not restored.is_file() or file_sha256(restored) != item["sha256"]:
            return False
    return True


def _deterministic_id(namespace: Namespace, seed: str, label: str) -> str:
    import hashlib

    value = uuid.UUID(bytes=hashlib.sha256(f"{seed}|{label}".encode("utf-8")).digest()[:16], version=4)
    return f"{namespace.value}_{value}"


def _environment() -> dict[str, Any]:
    try:
        core_version = version("research-kb-core")
    except PackageNotFoundError:
        core_version = "source-tree"
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "core_distribution_version": core_version,
        "logical_cpu_count": os.cpu_count(),
        "peak_rss_bytes": _peak_rss_bytes(),
        "storage_class": "not_auto_detected",
    }


def _peak_rss_bytes() -> int:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        if not get_process_memory_info(
            get_current_process(),
            ctypes.byref(counters),
            counters.cb,
        ):
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.PeakWorkingSetSize)
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


__all__ = [
    "BACKUP_MEASUREMENT_CONTRACT_VERSION",
    "BACKUP_THRESHOLDS",
    "MEASUREMENT_CONTRACT_VERSION",
    "OPERATIONAL_THRESHOLDS",
    "measure_backup_restore",
    "measure_maintenance",
    "measure_operational_reads",
    "measure_startup",
]
