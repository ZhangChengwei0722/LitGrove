# Benchmark Results

This directory stores compact, path-redacted measurement and preflight receipts for named repository benchmark profiles. Raw generated workspaces and SQLite databases are never committed here.

Receipts are append-only observations tied to a generator contract, workload profile, Core revision and measurement method. Re-running a benchmark writes a new reviewed receipt; it does not overwrite an accepted result or modify an acceptance threshold.

## P2-B Receipt Set

- `p2-pilot-v1-core-2026-07-29.json` records the initial bounded pilot.
- `p2-pilot-v1-core-optimized-2026-07-29.json` records the exact-store detail optimization on the same profile.
- `p2-r0-scale-v1-preflight-2026-07-29.json` records the passing disk estimate before full generation.
- `p2-r0-scale-v1-core-preliminary-2026-07-29.json` records the one-run reference build, incremental update, cursor walk and original broad FTS observation.
- `p2-r0-scale-v1-query-supplement-2026-07-29.json` separates selective FTS from the broad high-cardinality stress case and adds monolithic Registry detail timing.

The preliminary receipt predates the final query-case labels in the benchmark helper, so its `queries.fts` row is the broad high-cardinality stress case. The supplement names that class explicitly and supplies selective query samples. Read the two receipts together; neither freezes an acceptance budget.
