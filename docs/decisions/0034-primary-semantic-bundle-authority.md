# ADR 0034: Primary Semantic Bundle Authority

- status: `accepted`
- accepted_at: `2026-08-01`
- implementation: `d85c1955fd65b0de9c4bb77e772ce82e33dec35c`

## Context

P4-A can stage and preview external Agent results but does not create scientific
records. A Primary result contains one seven-section Paper Card, canonical Evidence and
scientific review boundaries that must become visible together. Promoting those records
through independent per-kind stores would allow partial scientific state after a crash
and would not provide an append-only correction boundary.

## Decision

Core stores each P4-B-managed Primary result in one canonical per-paper bundle:

```text
knowledge/primary_bundles/by_paper/<paper_id>.primary.json
```

The bundle owns an ordered append-only revision chain. Every revision contains the full
Paper Card, Evidence and review-queue child set, exact source/parse/adequacy input
snapshot, predecessor ID/digest and user-approval receipt. Only the active revision's
children enter factual reads, Question Mapping, Research Synthesis and the Artifact
Catalog. Historical child IDs remain resolvable for audit and freshness diagnostics but
are not factual current records.

Core allocates all canonical IDs during approval. An external Agent may submit only
task-local aliases. Submission and approval both fail closed when the source, Parse,
consumed Source Adequacy profile, semantic Job or bundle head has changed. A stale
created, leased or submitted Task may be superseded only by a lineage-linked successor
bound to current inputs.

Legacy per-kind Primary stores remain readable. A paper cannot combine legacy Primary
authority with a P4-B bundle, and no adoption or migration is implied. Correction creates
a new approved revision and never overwrites or deletes history.

## Consequences

- Primary approval has one physical transaction boundary and cannot expose a partial
  Card/Evidence/queue set.
- Crash recovery may complete Job and Task receipts without creating a duplicate
  revision.
- Question Mapping and Research Synthesis must distinguish audit-resolvable historical
  IDs from active factual children.
- Bundle revisions can grow with correction history; retention and compaction cannot
  discard canonical revisions.
- Review Memory remains a separate background-only authority and requires its own
  bounded P4-C design. This ADR does not define a Review bundle by analogy.

## Rejected Alternatives

- Coordinating three independent canonical writes: rejected because recovery could
  expose partial scientific state.
- Replacing the active Card/Evidence files in place: rejected because it destroys
  correction history and approval lineage.
- Automatically adopting legacy Primary records into a bundle: rejected because it is
  a migration and authority change outside P4-B.
