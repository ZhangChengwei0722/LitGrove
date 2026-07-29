# P2-B Catalog Scale Closure Manifest

- phase: `P2-B`
- date: `2026-07-29`
- status: `implementation_validated_pending_delivery`
- baseline: `main@3e2b6cefcd69bab824faaa949ad670e205fe3176`
- branch: `feature/p2-catalog-scale-generator`
- generator: `p2-catalog-generator@1.0`
- measurement: `p2-core-catalog-measurement@1.0`

## Delivered Boundary

P2-B delivers exact paper/Question catalog filters, cursor binding, safe projection-result binding, exact-store detail loading, a deterministic current-contract synthetic generator, the portable `p2-small` fixture and preliminary Core-only scale receipts. It adds no App repository, production schema, private data, private-domain access, large generated artifact or final performance budget.

## Reference Workload

```text
50,000 papers
250,000 scientific catalog items
500,000 operational catalog items
750,000 total projected items
```

The generated full-scale payload contained 190,012 files and 356,431,111 bytes. Its disposable SQLite projection was 1,001,340,928 bytes. Payload restoration after the incremental measurement reproduced the original digest.

## Preliminary Result

| Operation | Observation | Provisional target | Result |
|---|---:|---:|---|
| Full projection build | 557.532 s | <= 600 s | pass |
| Incremental update, 1,000 records | 815.772 s | <= 60 s | fail |
| Selective FTS p95 | 1.2207 ms | <= 250 ms | pass |
| Direct Paper Card detail | 27.8499 ms | <= 200 ms | pass |
| Monolithic Registry detail p95 | 1,119.599 ms | <= 200 ms | fail |

The 11.857 s broad FTS observation is a high-cardinality stress case and is not used as the representative normal query. These are preliminary development measurements, not frozen release acceptance.

## P2-E Freeze Blockers

1. Incremental projection currently pays complete workspace loading and validation costs before applying its SQLite delta.
2. Registry detail still scans one monolithic JSONL store; exact-store selection alone cannot make that record lookup bounded.

P2-E must implement and validate a compatible bounded strategy for both issues, rerun the named reference workload and freeze a versioned R0 Windows budget. Thresholds may not be relaxed merely because the preliminary implementation missed them.

## Safety And Reproduction

- The committed fixture and receipts contain authored synthetic data only.
- Large generated roots and SQLite projections remain outside Git.
- Benchmark targets must be absolute, absent and outside the repository.
- Marker, profile, inventory and digest mismatch fail closed.
- Benchmark execution never grants cleanup authority.
- Private workspaces, legacy records and real source documents were not read.

## Lifecycle Cleanup Handoff

The following generated targets are registered for later review, not deletion in P2-B:

```text
%TEMP%/research-kb-p2-small-export-v1
%TEMP%/research-kb-p2-pilot-v1-20260729
%TEMP%/research-kb-p2-r0-scale-v1-20260729
```

P2-E still needs the R0 scale target to diagnose and remeasure the incremental projection blocker, so it must remain intact. After the P2-E benchmark and related regression checks close, the lifecycle report must list exact target paths, measured reclaimable bytes, ownership/digest verification, remaining dependencies and retained reproduction artifacts. Deletion then requires separate explicit authority. Repository tests, the portable fixture, scripts, profiles, receipts, this manifest and recorded digests are permanent project artifacts rather than cleanup candidates.

## Validation Record

- targeted catalog/generator tests: `32 passed`;
- full Windows suite: `696 passed, 4 expected POSIX skips`;
- source-tree version: `research-kb 0.1.0`;
- sdist and wheel build: passed;
- installed-wheel version and public Catalog service import: passed;
- repository privacy scan: `7 expected, 0 unexpected`;
- `git diff --check`: passed;
- untracked delivery payload: 43 files, 133,784 bytes; no generated scale workspace or SQLite projection is present.

## Closure Gate

P2-B closes only after full tests, package build, installed-wheel smoke, version, privacy and diff checks pass; the branch is merged; post-merge validation passes; and the parent plans plus durable project record are reconciled through `neat-freak`. This manifest records implementation and measurement scope. The final merged revision and P2-C next gate are recorded in the project plans and durable project page during milestone reconciliation.
