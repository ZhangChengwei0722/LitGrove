# 0031 Operational Lifecycle And Recovery

Status: accepted

## Context

At tens-of-thousands catalog scale, Jobs, Tasks, events, reports and staging payloads can grow faster than scientific records. One generic unresolved queue would also mix failures owned by different actors.

## Decision

### One writer and recoverable mutations

- One workspace has one active writer authority.
- Every mutation uses an idempotency key or expected-source digest and records transaction intent before promotion.
- Startup recovery classifies complete, roll-forward, rollback and manual-resolution actions using stored digests.
- Cancellation is cooperative and cannot interrupt an atomic promotion halfway.
- Backup acquires a writer barrier or transaction watermark and records checkpoint/journal state.
- A restored workspace remains closed until canonical, operational and projection consistency checks pass.

### Failure routing

| Failure | Responsible record/output |
|---|---|
| missing file, parse failure or inadequate/stale source capability | Pipeline Job waiting for source or reparse |
| document route ambiguity | user decision in P3; versioned Agent Task after P4 |
| unsupported Primary Card Unit | scientific `review_queue` |
| schema, reference, transaction, projection or integrity fault | Guardian finding |
| suspected cross-review disagreement | conflict-check report scoped to the current task |

There is no universal `needs_resolution` queue. A transfer between owners creates an explicit new record/event and preserves the predecessor reference and reason.

### Lifecycle classes

| Class | Durable policy | Cleanup policy |
|---|---|---|
| canonical scientific revision | permanent history with supersede/archive state | never payload-delete in ordinary maintenance |
| Registry identity/manifestation | permanent identity and correction history | source availability may change; identity remains |
| transaction journal | retained through verified recovery closure | compact only after receipt/checkpoint proves outcome |
| process event | append-only active segment, then immutable archived segment | segment compaction preserves order, digest and receipt |
| Pipeline Job | current state plus immutable terminal receipt | closed working payload may be removed after retention window |
| Agent Task | lineage, privacy/authority receipt and terminal result digest retained | source copies, large prompt payloads and rejected staging are cleaned after retention window |
| staging candidate | temporary, non-canonical | remove after commit/reject/expiry; preserve decision receipt and digest |
| current-task/report-only output | retained for configured task/report window when referenced | may archive or expire; never silently promoted |
| Guardian run/report | latest current report plus auditable historical receipts | old detailed payload may enter immutable archive segments |
| temp file | operation-scoped only | delete only if created by that operation and identity still matches |
| projection/cache | disposable | rebuild at any time from durable records |

Retention windows are workspace policy values with conservative defaults defined in the owning implementation phase. A cleanup dry run lists candidates, sizes and preserved receipts. Cleanup never follows arbitrary links or removes source assets.

### Query scale

Operational and catalog queries use stable sort keys, cursor pagination and a server-enforced maximum page size. Offset-only scans and unbounded event/task payload reads are not public App APIs.

### Lazy stale maintenance

P11 projects freshness lazily and coalesces maintenance by `(dependent_id, upstream_revision, reason)`. Repeated triggers update one work item rather than producing a maintenance storm. Historical stale reasons remain auditable through events/receipts.

## Consequences

- P2 introduces pagination before catalog scale.
- P3/P4 clean closed staging/temp payloads while preserving receipts.
- P11 tests operational density separately from paper density.
- Existing transaction recovery remains the P0 baseline; this ADR adds no runtime state.
