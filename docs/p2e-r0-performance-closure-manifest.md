# P2-E R0 Performance Closure Manifest

- status: `implementation_validated`
- closed_at: `2026-07-30T03:20:00+08:00`
- implementation_commit: `460c167cbc10b1c0979d326fd81f0e284f670316`
- branch: `feature/p2e-r0-performance`
- package: `research-kb-core==0.1.0`
- wheel_sha256: `3bd4809ae111b202625a98f97944336323a451f9ac80bc39540cdbbc7e31e59a`
- sdist_sha256: `7b5d07924fe607213b5e9ef92c682726ea12870f0e379b0bad83cfc06f289fcd`
- catalog_projection_schema_version: `2`
- frozen_budget: `r0-windows-catalog-v1`
- cleanup_status: `cleanup_deferred_until_user_returns`

## Delivered Boundary

- canonical byte locators for bounded Registry detail;
- strict ID/schema/digest validation after exact JSONL seek;
- benchmark-only digest/watermark-bound Registry delta;
- safe schema-v2 rebuild behavior;
- cached JSON Schema `$ref` expansion and bulk SQLite/FTS insertion;
- bounded managed-workspace traversal;
- inspect-only restart binding with conservative stale freshness;
- deterministic rebuild, delta and read measurement commands.

Production projection update remains complete. The optimized delta cannot be invoked by
the App or public CLI and is not promoted until a later writer receipt covers Registry,
process events and every other indexed store changed by one transaction.

## Frozen Results

| Metric | Result | Threshold |
|---|---:|---:|
| full projection rebuild | `253.291 s` | `<= 600 s` |
| 1,000-record Registry delta p95 | `18.257 s` | `<= 60 s` |
| selective FTS p95 | `2.814 ms` | `<= 250 ms` |
| authoritative Registry detail p95 | `14.522 ms` | `<= 200 ms` |
| restart inspect-only bind p95 | `3.609 s` | recorded observation |

The 750,000-item, 640,000-source-record synthetic workspace returned to its original
payload digest after each mutation-based measurement. No private workspace, Q001,
source PDF or canonical scientific write was accessed.

## Validation

- full Windows suite: `705 passed, 4 expected POSIX skips`;
- package build and installed-wheel smoke: passed;
- package version: `research-kb 0.1.0`;
- privacy scan: `7 expected, 0 unexpected`;
- App integration against the exact wheel: passed;
- canonical/source/payload digests: unchanged;
- `git diff --check`: passed before implementation commits.

Raw measurement receipts, App ready/memory acceptance and the centralized generated
artifact report are retained in `research-kb-app/docs/`. No generated workspace or
SQLite projection entered Git, and no cleanup was performed.

The next product phase is P3 Pipeline Job and deterministic source intake. It must begin
from its own bounded implementation plan.
