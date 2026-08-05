from __future__ import annotations

from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STRUCTURAL_MARKERS = {"unit", "contract", "integration", "privacy", "benchmark"}
STRUCTURAL_DIRECTORIES = {
    "unit": "unit",
    "contract": "contract",
    "integration": "integration",
    "privacy": "privacy",
    "benchmark": "benchmark",
}
SERIAL_FILES = {
    "tests/unit/test_discovery_acquisition_service.py",
    "tests/unit/test_portable_skill_sync.py",
    "tests/unit/test_tag_service.py",
    "tests/unit/test_transactions.py",
    "tests/unit/test_workspace_bootstrap.py",
}
FAST_UNIT_FILES = {
    "tests/unit/test_agent_task_registry.py",
    "tests/unit/test_capability_service.py",
    "tests/unit/test_catalog_adapters.py",
    "tests/unit/test_cli_input.py",
    "tests/unit/test_discovery_service.py",
    "tests/unit/test_europe_pmc_connector.py",
    "tests/unit/test_europe_pmc_resolution.py",
    "tests/unit/test_evidence_provenance.py",
    "tests/unit/test_identifiers.py",
    "tests/unit/test_organization_bundles.py",
    "tests/unit/test_p7a_contracts.py",
    "tests/unit/test_portable_skill_contract.py",
    "tests/unit/test_primary_semantic_bundle.py",
    "tests/unit/test_question_view.py",
    "tests/unit/test_review_memory_provenance.py",
    "tests/unit/test_review_semantic_bundle.py",
    "tests/unit/test_step7_context_service.py",
    "tests/unit/test_step7_support.py",
    "tests/unit/test_step7_view.py",
    "tests/unit/test_versions.py",
}
MEASURED_SLOW_FILES = {
    "tests/integration/test_real_pdf_runtime.py",
    "tests/integration/test_two_domain_runtime.py",
}
WINDOWS_NODE_IDS = {
    "tests/unit/test_workspace_bootstrap.py::test_lock_file_persistence_retries_transient_concurrent_write",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        relative = Path(str(item.path)).resolve().relative_to(REPOSITORY_ROOT).as_posix()
        parts = Path(relative).parts
        if len(parts) < 3 or parts[0] != "tests" or parts[1] not in STRUCTURAL_DIRECTORIES:
            raise pytest.UsageError(f"test is outside a registered structural class: {item.nodeid}")

        item.add_marker(STRUCTURAL_DIRECTORIES[parts[1]])
        if relative in SERIAL_FILES:
            item.add_marker("serial")
        if (parts[1] == "unit" and relative not in FAST_UNIT_FILES) or relative in MEASURED_SLOW_FILES:
            item.add_marker("slow")
        if parts[1] == "benchmark":
            item.add_marker("scale")
        if item.nodeid in WINDOWS_NODE_IDS:
            item.add_marker("windows")

        assigned = STRUCTURAL_MARKERS.intersection(marker.name for marker in item.iter_markers())
        if len(assigned) != 1:
            raise pytest.UsageError(
                f"test requires exactly one structural marker, got {sorted(assigned)}: {item.nodeid}"
            )
