# ADR 0014: Intake Preflight

- status: accepted_for_m3a_0d

## Decision

Shared Core exposes one read-only source preflight:

```text
intake inspect --workspace <workspace.yaml> --source <absolute-source-path>
```

The command returns transient interface `1.0` with the workspace ID, portable `root_id + relative_path`, `sha256` algorithm name, exact-path Registry state and paper IDs, and the active domain profile's ordered Paper Card section IDs and labels.

This closes three deterministic gaps for the future Portable Skill: mapping a user path without parsing workspace config, reusing one current exact registration during a sequential rerun, and constructing Card requests without parsing the domain profile.

## Registration State

Exact registration identity is stored `root_id + relative_path`, not content hash alone:

- no exact match: `unregistered`;
- one exact match with current hash: `registered_current`;
- one exact match with changed hash: `registered_stale`;
- multiple exact matches: `ambiguous`.

Same bytes at another path remain `unregistered` for the selected path. Paper IDs are sorted; no bibliography or unrelated Registry record is exposed.

## Safety Boundary

The source argument must be absolute and resolve to one regular file owned by exactly one declared source root. The service rejects relative paths, missing or directory inputs, root/link escapes and ambiguous nested-root ownership. The derived POSIX source reference must round-trip through `WorkspaceLayout`.

The complete workspace bundle is validated, and the source is hashed before and after projection. Changed sources fail with `RKBC-009` before stdout. Diagnostics and successful output contain neither the absolute path nor the hash value.

The command writes no Registry record, ID, duplicate link, parse, Card, Evidence, queue item, event, journal, report, lock, cache or preflight file.

## Limits

This decision provides sequential rerun routing only. It does not add atomic inspect-and-register, concurrent same-source deduplication, scanning, classification, metadata extraction, schema/layout/dependency changes, Portable Skill files, Review runtime, Step 7, discovery, acquisition or migration.
