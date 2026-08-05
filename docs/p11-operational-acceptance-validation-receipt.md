# P11 Scale, Recovery And Operational Acceptance Validation Receipt

- status: `passed`
- validated_at: `2026-08-05`
- baseline: `main@9346e140c0f376f24814c9aa0accdbb30c0ce8fc`
- implementation_commit: `17403a4dd3cecc98954f25934906bb28f31ceb4c`
- first_merge_commit: `ef730bbe45fb1cce9dc36e5a8446b463a2fd9019`
- correction_commit: `2d550ac`
- final_merge_commit: `8a14666e8b2b3c168a6044719db04773f803eab0`
- profile: `p11-operational-recovery-windows-v1`

## Delivered Contract

- writer-barrier, create-only backup with durable manifests and receipts;
- confined restore into an absent target, followed by reference validation, Guardian and
  projection rebuild checks before publication;
- immutable transaction-journal archive segments with digest-bound cleanup receipts;
- stable cursor reads over dense Pipeline Job and Agent Task projections;
- explicit lazy-maintenance persistence with coalescing by
  `(dependent_id, upstream_revision, reason)`;
- capability and Guardian coverage for backup, restore, operational archive and
  maintenance state;
- deterministic operational-density generator and frozen Windows measurement profiles.

No private workspace, private legacy record, real PDF, real Obsidian vault, migration or legacy
cutover was opened or modified.

## Deterministic Validation

Final validation on the merged Core source snapshot:

```text
targeted P11 matrix: 34 passed
full suite:          1108 passed, 4 expected POSIX skips
compileall:          passed
package build:       passed
installed wheel:     passed
privacy scan:        passed
git diff --check:    passed
```

The final merged wheel is:

```text
name: research_kb_core-0.1.0-py3-none-any.whl
size: 489069 bytes
sha256: e099e786e84303a603ff15b859e58fa34a7d9be1a9e9fc800e108c38ecf100e0
```

## Formal Measurements

| Measurement | Result | Frozen threshold | Status |
|---|---:|---:|---|
| operational startup p95 | `4.0961 s` | `15 s` | pass |
| Job first-page p95 | `0.1081 s` | `3 s` | pass |
| Job late-page p95 | `0.1436 s` | `3 s` | pass |
| Agent Task first-page p95 | `0.2805 s` | `3 s` | pass |
| Agent Task late-page p95 | `0.3048 s` | `3 s` | pass |
| 10,000-journal archive | `12.6168 s` | `120 s` | pass |
| 100,000-trigger coalescing | `10.6754 s` | `30 s` | pass |
| open maintenance keys | `1,000` | exactly `1,000` | pass |
| maintenance peak RSS | `482,893,824 bytes` | `536,870,912 bytes` | pass |
| source-free backup | `120.4231 s` | `180 s` | pass |
| archive inspection | `1.1965 s` | `120 s` | pass |
| restore and validate | `182.2758 s` | `240 s` | pass |

Backup and restore covered `10,024` durable entries. The source-free archive was
`247,186,963` bytes with SHA-256
`d118b330b70631bcd4be2abf4ea40e93342c5380cf18b671ea4363b14c8d92c9`; restored durable
entry digests were equivalent. The restored synthetic workspace Guardian status was
`warning`, not failure, because source-free mode intentionally restores source inventory
without external source bytes.

The path-redacted machine-readable measurements are retained under
`docs/receipts/p11-operational-acceptance/`.

## R0 Carry-Forward

The retained R0 workspace completed a reversible shadow probe without regenerating its
approximately 190,000 files:

```text
750,000-item projection rebuild: 277.1381 s <= 600 s
App workspace ready:               7.249 s <= 15 s
App process ready:                 2.571 s
steady read-only RSS:              88,842,240 bytes <= 256 MiB
peak working set:                  150,409,216 bytes
selective query:                   passed
```

Its original compatibility marker was restored byte-for-byte and all 19 temporary empty
directories were removed. The fresh process reported projection freshness as
`stale / unverified_after_restart`; this is preserved as an explicit freshness state, not
misreported as `current`. Existing frozen P2-E Registry delta, FTS and authoritative-detail
receipts remain the acceptance evidence for those unchanged operations.

## Result

The P11 Core acceptance contract passed without changing any frozen threshold. The current
layout remains adequate for R3; the separate layout decision records why no layout-v2 or
migration plan is opened.
