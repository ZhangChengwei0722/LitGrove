# ADR 0013: Paper Context Read

- status: accepted_for_m3a_0c

## Decision

Shared Core exposes one additional paper-scoped read command:

```text
paper context --workspace <workspace.yaml> --paper-id <paper_id>
```

The command returns the selected paper's complete stored Paper Card or `null`, its canonical Evidence records, and its review queue records. Evidence and queue arrays are sorted by their canonical IDs. The output uses transient `interface_version: "1.0"` and is serialized completely before one UTF-8/LF stdout write.

This closes a deterministic resume gap for the future Portable Skill. Core allocates Card Unit, Evidence and queue IDs, but `record promote` returns only the top-level promoted record ID and `paper status` intentionally reports counts rather than scientific content. A later Agent can now recover existing IDs and content without parsing workspace configuration or reading canonical paths directly.

## Safety Boundary

The service validates the complete workspace bundle, resolves exactly one registered paper and checks its source SHA-256 before and after projection. Unknown papers fail through the existing reference diagnostic; missing, stale or changing sources fail with `RKBC-009` before stdout.

The response excludes Registry source references, paths, parsed pages, Question Mappings, process events, Guardian reports, transaction journals and all unrelated-paper records. It may contain private Card statements, evidence quotes and queue candidates because the user explicitly selected one local paper. That output is limited to the current user task and must not enter shared fixtures or documentation.

The command creates no canonical record, cache, report, event, journal, lock, full-text copy or persisted workflow state.

## Limits

This decision adds no schema, layout, ID namespace, dependency or generic export. It does not implement semantic resume, document classification, the Portable Skill, Review runtime, Step 7, discovery, acquisition, migration or private-workspace access.
