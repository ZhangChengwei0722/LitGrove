"""P2 deterministic catalog-scale generator and measurement tooling."""

from benchmarks.p2_catalog_scale.generator import (
    GENERATOR_CONTRACT_VERSION,
    GeneratedWorkspace,
    GenerationProfile,
    export_portable_small_seed,
    generate_workspace,
    inspect_generated_workspace,
    materialize_portable_seed,
    profile_by_id,
    verify_generated_payload,
)
from benchmarks.p2_catalog_scale.measurement import (
    CATALOG_READ_MEASUREMENT_VERSION,
    MEASUREMENT_CONTRACT_VERSION,
    PROJECTION_REBUILD_MEASUREMENT_VERSION,
    REGISTRY_DELTA_MEASUREMENT_VERSION,
    estimate_reference_workload,
    measure_catalog_reads,
    measure_core_catalog,
    measure_projection_rebuild,
    measure_registry_delta,
)

__all__ = [
    "GENERATOR_CONTRACT_VERSION",
    "CATALOG_READ_MEASUREMENT_VERSION",
    "MEASUREMENT_CONTRACT_VERSION",
    "PROJECTION_REBUILD_MEASUREMENT_VERSION",
    "REGISTRY_DELTA_MEASUREMENT_VERSION",
    "GeneratedWorkspace",
    "GenerationProfile",
    "estimate_reference_workload",
    "export_portable_small_seed",
    "generate_workspace",
    "inspect_generated_workspace",
    "materialize_portable_seed",
    "measure_catalog_reads",
    "measure_core_catalog",
    "measure_projection_rebuild",
    "measure_registry_delta",
    "profile_by_id",
    "verify_generated_payload",
]
