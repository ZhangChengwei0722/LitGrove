# P11 Layout-V2 Decision

- decision: `retain_current_layout`
- status: `accepted`
- decided_at: `2026-08-05`
- evidence: `docs/p11-operational-acceptance-validation-receipt.md`

## Decision

Keep the current canonical and operational layout for the completed P0-P11 roadmap. Do not
open a layout-v2 implementation or migration plan.

## Basis

- the retained 750,000-item R0 projection rebuilt within the frozen budget;
- bounded App startup, selective reads and steady memory remained within budget;
- 25,000 completed Jobs, 25,000 terminal Tasks, 250,000 events and the other formal
  operational-density records remained readable through bounded cursor APIs;
- 10,000 eligible transaction journals archived without rewriting scientific records;
- 100,000 stale observations coalesced into exactly 1,000 maintenance keys;
- source-free backup and validated restore completed within their frozen budgets and
  preserved durable entry digests;
- no measured blocker required unbounded memory, unsafe mutation or canonical record
  rewriting.

## Consequences

- SQLite/FTS remains a disposable projection and can be rebuilt from durable records.
- Lazy freshness remains the default; durable maintenance work is created only by an
  explicit operation.
- Existing large inline operational results remain readable. P11 does not claim a lossy
  rewrite or speculative compaction.
- A future layout-v2 proposal requires a new measured failure against a frozen profile or
  a new scale requirement. It must have a separate design, migration plan and authority.
- This decision does not authorize private legacy-workspace access or migration, write freeze,
  legacy cutover or deletion of retained benchmark assets.
