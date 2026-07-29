# 0033 Versioned Acceptance Budgets And Phase Dependencies

Status: accepted

## Context

Performance gates cannot be invented after a failing release, and phase numbers must not imply false dependencies. P0 needs a reproducible provisional profile without pretending that an unbuilt App has been benchmarked.

## Decision

### Budget versioning

Every profile records a version, fixture/generator version, operating system, CPU/logical cores, RAM, storage class, Python/Core/App versions, cold/warm state, repetitions, percentile calculation and measurement command. Threshold changes create a new dated profile with rationale; a failed run never changes the profile under test.

Lifecycle:

```text
P0 -> provisional measurement profile and target envelope
P2 -> materialize scale fixture, measure and freeze R0 catalog/search/startup profile
P3/P5/P8/P10 -> append first measurable capability-specific profiles
P11 plan approval -> freeze recovery and backup/restore profile
P11 acceptance -> test named frozen versions only
```

### P0 provisional profile `p0-provisional-r0-v1`

Reference class: Windows, at least 8 logical cores, 16 GiB RAM, SSD, Python 3.12-compatible runtime. Measurements run five times after one warm-up; report median and p95, plus a separate cold-start run. These are targets for P2 measurement, not release gates yet.

| Metric | Synthetic load | Provisional target |
|---|---|---|
| full catalog/projection build | 50,000 papers, 250,000 scientific records, 500,000 operational summaries | <= 600 s |
| incremental projection update | 1,000 changed records | <= 60 s |
| metadata/FTS search | 50,000-paper catalog, 20-result page | p95 <= 250 ms |
| record detail load | one paper with 200 related records | p95 <= 200 ms |
| App/Core ready state | existing catalog, no recovery work | cold <= 10 s |
| steady read-only memory | catalog open plus one search/detail workflow | <= 1.5 GiB RSS |
| inventory-only backup | full synthetic durable state | <= 900 s |
| restore plus consistency validation | same fixture | <= 1,800 s |

P2 may freeze different measured R0 thresholds only before release testing and with raw results/rationale. It cannot relax them retroactively after an acceptance failure.

### Scale dimensions

Paper density and operational density are independent. The generator specification must vary registered papers, parsed pages, scientific records, completed Jobs/Tasks, process events, Guardian records and retained reports separately. Generated text is deterministic and authored from scratch; no source document or private path is embedded.

### Actual phase dependencies

```text
P0 -> P1 -> P2 -> P3 -> P4 -> P5
P5 + P0 provider boundary -> P6
P5 + P4 organization proposals -> P7
P4 + P7 -> P8
P2 plus record surfaces from P7/P8 -> P9
P5 plus Exchange prerequisites -> P10
P6 + P7 + P8 + P9 + P10 -> P11
```

After R1, P6 and P7 may proceed independently. P9 rendering primitives may be prototyped after P2, but final managed views wait for the records they render. P10 does not depend on P9. Numeric adjacency alone is never a dependency.

Each phase must first close the stale edges introduced by its writers, meet its authority/privacy/transaction tests and name the budget profile it consumes. A later phase cannot repair an earlier phase's missing correctness edge as ordinary follow-up.

### Third-party reuse gate

No P0-P1 code reuse from an external project is proposed, so reuse audit is `not_applicable`. If a later phase copies code rather than behavior, it must first pin commit and license, map ownership, record attribution/dependency duties, and reject any second backend/canonical owner/embedded Agent runtime.

## Consequences

- P0 does not generate or benchmark a large fixture.
- P2 cannot claim scale acceptance against an unfrozen or unnamed profile.
- P11 covers both scientific-record and operational-record density.
