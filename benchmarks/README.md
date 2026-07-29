# Repository Benchmarks

This directory contains deterministic development tooling, named workload profiles and bounded measurement receipts. It does not contain production workspace logic or private research data.

## Ownership

- Benchmark code and compact synthetic fixtures may be committed.
- Large generated workspaces, runtime state and SQLite projections must remain untracked.
- All generated scientific text must be authored from scratch and marked `synthetic_from_scratch`.
- Benchmark tooling may consume only contracts already owned by the current Core phase.
- Benchmark results are observations. They do not change an acceptance budget or release gate.

## Generated Targets

A generator target must be an absolute, previously absent path outside the repository. The generator creates the target, writes an operation marker and confines every output below it. Existing empty and nonempty targets, symlink/junction/reparse traversal and path escape fail closed.

Generated payload uses UTF-8, LF, deterministic IDs and timestamps, canonical JSON/JSONL serialization and relative POSIX workspace paths. A manifest records exact counts, per-file digests and tree digests. Host paths are never serialized into a fixture or receipt.

## Cleanup

Cleanup is a separate operation. It may remove only an operation-owned target whose marker, manifest, profile/seed identity, file inventory and generated-tree digest still match. Missing markers, digest drift, foreign files and repository paths fail closed. Running a benchmark never implies cleanup authorization.

Keep benchmark code, profiles, portable deterministic fixtures, reviewed receipts, closure manifests, key statistics and digests. Generated large workspaces, rebuildable SQLite projections, build output, wheel-smoke environments and caches are lifecycle cleanup candidates only after the consuming phase closes and no pending performance or regression work depends on them.

The 2026-07-29 P2 lifecycle candidates are:

```text
%TEMP%/research-kb-p2-small-export-v1
%TEMP%/research-kb-p2-pilot-v1-20260729
%TEMP%/research-kb-p2-r0-scale-v1-20260729
```

The R0 scale target must remain available through P2-E incremental-projection diagnosis and regression measurement. After P2-E closes, report each candidate's verified ownership, dependency state and measured reclaimable bytes together with the retained reproduction records, then obtain separate deletion authority. Never remove repository tests, `tests/fixtures/p2_small`, benchmark profiles or reproduction receipts as generated-target cleanup.

## Receipts

Receipt filenames use `<profile>-<measurement-contract>-<date>.json`. They record profile/generator identity, Core identity, host class, measurement method, raw samples and summary statistics. Secrets, usernames, absolute paths, source text and private workspace identifiers are prohibited.

## P2 Catalog Scale

The P2 tooling is invoked from the repository root:

```powershell
python -m benchmarks.p2_catalog_scale generate --profile p2-small --target <absolute-absent-target>
python -m benchmarks.p2_catalog_scale inspect --target <generated-target>
python -m benchmarks.p2_catalog_scale measure --target <generated-target>
```

`p2-small` is committed under `tests/fixtures/p2_small`. `p2-pilot-v1` and `p2-r0-scale-v1` are generated on demand outside the repository.
