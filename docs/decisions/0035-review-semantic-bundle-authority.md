# ADR 0035: Review Semantic Bundle Authority

- status: `accepted`
- accepted_at: `2026-08-01`
- implementation: `pending_validation_commit`

## Context

The legacy common Review Memory runtime can store one background-only reading record,
but it edits that logical record in place and has no App preview or correction revision
boundary. P4-C needs an external-Agent workflow whose retained Review Units are traced
back to the same review, whose corrections remain auditable and whose approval never
turns secondary-source background into canonical Evidence.

## Decision

Core stores each P4-C-managed Review result in one per-paper file:

```text
knowledge/review_bundles/by_paper/<paper_id>.review-bundle.json
```

The file owns an append-only revision chain. Every approved revision contains one full
common Review Memory, newly allocated Memory and Unit IDs, exact source/Parse/Source
Adequacy snapshots, a closed provenance binding for every retained source note and the
approving Agent Task receipt. Only the active Memory enters Review Context and Catalog;
historical IDs remain audit-resolvable.

The base route consumes `basic_review_memory`. A retained text, figure/table,
formula/layout or supplementary note must consume its matching current and adequate
Source Adequacy Profile. An unrelated inadequate capability does not block a text-only or
zero-Unit Memory. Failure returns the semantic Job to a specific source/reparse wait and
does not create scientific review queue data.

All Review Units remain `background_only=true`,
`can_enter_canonical_evidence=false` and `not_fact=true`. Legacy Review Memory and P4-C
bundle authority are mutually exclusive for one paper. Correction creates a new revision,
Memory ID and Unit IDs; history is never overwritten or deleted.

## Consequences

- Review approval is one crash-safe physical transaction plus idempotent Job and Task
  receipts.
- Source-note provenance proves that a reusable Unit came from the review; it is not a
  canonical Evidence quote and cannot support an experimental claim.
- Catalog and Review Context must expand only the active child, while Guardian validates
  every revision's Task, source, Parse, Profile and note-binding closure.
- Bundle history can grow over time and is canonical audit state, so ordinary operational
  cleanup cannot compact it away.
- Subtype-specific schemas, Field Map integration, Review Unit Question Mapping and
  Research Synthesis drafting remain separate milestones.

## Rejected Alternatives

- Replacing the legacy Review Memory in place: rejected because corrections would erase
  approval and provenance history.
- Writing each Unit independently: rejected because partial failure could expose an
  incomplete Memory.
- Promoting review quotations into canonical Evidence: rejected because secondary-source
  trace-back and primary experimental evidence have different authority.
