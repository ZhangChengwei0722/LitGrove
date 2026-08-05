# Test Performance Implementation Plan

- status: `t1_t3_implementation_complete_pending_ci`
- audit: `docs/test-performance-audit.md`
- baseline: `1faac4f4ce01d31c3828d1768b3954c86089eec0`
- implementation_authorized: `true`
- authorization: `user_approved_2026-08-05`
- next_gate: `ci_shard_timing_acceptance`

## Objective

Make normal validation complete within the five-minute executor window while
preserving every high-risk acceptance test. Separate fast feedback from full
and scale validation, produce reproducible timing receipts and introduce
parallel execution only for tests proven safe.

## Current Implementation

- T1/T2 define a reconciled manifest with 24 exhaustive L3 shards and one
  separate L4 scale shard.
- Each ordinary L3 shard enforces the `240 s` subprocess budget and records a
  distinct `timed_out` receipt with exit code `124`.
- The bounded T3 optimization restores module-scoped synthetic snapshots at
  their original configured path before each test. No mutated tree is reused.
- T4 `pytest-xdist` remains deferred. Independent GitHub-hosted matrix jobs
  provide CI concurrency without introducing in-process worker sharing.
- Final acceptance remains gated on fresh hosted receipts for every shard.

## Non-Goals

- Do not delete or weaken tests to meet a time target.
- Do not access private scientific workspaces, real paper PDFs or live providers.
- Do not share writable workspaces between tests or workers.
- Do not make live Agent login, network state or model output a CI condition.
- Do not combine this work with the Chinese README PR.

## Phase T1: Classification Contract

1. Register these markers in `pyproject.toml`:

   ```text
   unit
   contract
   integration
   slow
   scale
   serial
   windows
   ```

2. Assign structural markers from the current test directories and add explicit
   runtime markers to individual files or tests. A test may have both a
   structural and runtime marker, for example `integration + serial`.
3. Treat tests as `slow` from measured behavior, not name alone. Start with a
   provisional threshold and freeze it after one clean sequential baseline.
4. Mark POSIX-only tests with their existing skip condition; `windows` means a
   required Windows behavior test, not every test that happens to run there.
5. Add a collection check that rejects unknown markers.

Validation:

- collection remains exhaustive;
- every collected test has exactly one structural class;
- runtime classes may overlap;
- no `serial` or `scale` test enters the parallel-safe selection.

Exit gate:

- the classified collection count reconciles with the unclassified baseline,
  with every intentional count change explained.

## Phase T2: Validation Commands And Stable Shards

1. Add one repository-owned validation entry point that prints the selected
   level, exact pytest selectors and output receipt path before execution.
2. Implement L0-L4 without changing application behavior:

   ```text
   L0 -> docs-only checks and isolated TEMP package build
   L1 -> explicit file/node selectors
   L2 -> unit + contract, excluding slow/scale
   L3 -> exhaustive stable shards and wheel smoke
   L4 -> benchmark/scale only
   ```

3. Split L3 into these initial manifests:

   ```text
   contract
   storage/recovery
   application/semantic-a
   application/semantic-b
   discovery/views/exchange
   privacy/platform
   integration
   serial
   ```

4. Keep each ordinary L3 shard below four minutes on the measured Windows host,
   leaving one minute for startup and executor variance. Split a shard again if
   its p95 exceeds that budget.
5. Produce a machine-readable collection receipt proving that L3 shard union,
   plus separately run platform/scale selections, covers the intended suite
   without accidental overlap or omission.

Validation:

- run every command against synthetic repository fixtures;
- compare shard collection IDs with full collection IDs;
- force one failure and confirm the receipt identifies its shard and node ID;
- confirm all temporary build and workspace outputs are removed.

Exit gate:

- L0, L1 and L2 complete within their frozen budgets;
- every non-scale L3 shard completes within the executor window;
- the aggregate L3 result can be stated without claiming one timed-out command
  passed.

## Phase T3: Fixture Setup Optimization

Apply optimizations one class at a time and retain a before/after receipt:

1. Reuse immutable schema JSON and compiled read-only validators.
2. Prebuild bounded synthetic PDF template bytes and copy bytes to each
   independent `tmp_path` before parse tests.
3. Introduce immutable base workspace materialization only where every consumer
   receives a separate writable tree.
4. Reduce full Guardian construction in narrow behavior tests while retaining
   complete Guardian scenarios in L3.
5. Review repeated bootstrap and catalog projection setup for a smaller
   behavior-specific builder; preserve at least one public-service end-to-end
   path for each contract.

Validation for each optimization:

- run the targeted tests three times sequentially;
- compare output bytes, record IDs and failure diagnostics with the baseline;
- verify no repository fixture or sibling test workspace changed;
- keep the optimization only when results do not regress and median time
  improves materially.

Exit gate:

- no mutable object or writable file tree is reused across tests;
- high-risk acceptance coverage remains present;
- L2 and hotspot shard timings are lower or unchanged.

## Phase T4: Bounded xdist Pilot

1. Add `pytest-xdist` only after T1-T3 are accepted.
2. Run `-n 4` over an explicit parallel-safe allowlist. Do not infer safety from
   absence of a `serial` marker until the audit is complete.
3. Run `serial` in a separate command with one process.
4. Repeat sequential and parallel selections at least three times. Compare
   collection, pass/fail results, temporary-output cleanup and wall time.
5. Expand the allowlist gradually; on any race, leaked process, lock collision
   or nondeterministic output, remove the affected group and classify the cause
   before another expansion.

Acceptance:

- three consecutive parallel runs have identical outcomes;
- no orphan process, shared TEMP artifact or repository mutation remains;
- parallel execution improves median wall time by at least 20 percent on the
  selected group;
- `serial` tests are never scheduled under xdist.

If these conditions are not met, retain stable sequential shards. xdist is an
optimization, not a release requirement.

## Phase T5: Documentation And Governance Closure

1. Update `AGENTS.md` and `docs/contributor-guide.md` to select L0-L4 by change
   risk instead of always prescribing one full-suite command.
2. Document that schema, authority, storage, transaction, recovery and merge
   changes require L3; benchmark changes require L4 in addition.
3. Keep full-suite history as aggregate shard receipts with commit, platform,
   Python/pytest versions, counts, durations and expected skips.
4. Record any test left `serial` or `slow` with its reason and owner.

Final validation:

- `git diff --check`;
- marker and shard collection reconciliation;
- L0 docs-only validation;
- L2 normal-feature validation;
- complete L3 aggregate validation;
- isolated L4 benchmark smoke;
- package build, wheel smoke and privacy scan;
- generated-output cleanup verification.

## Provisional Budgets

These are implementation targets, not frozen acceptance thresholds. T2 freezes
the first measured Windows profile before optimization.

| Level | Provisional target |
|---|---:|
| L0 | `<= 45 s` |
| L1 | `<= 60 s` for a normal targeted module |
| L2 | `<= 180 s` |
| each ordinary L3 shard | `<= 240 s` |
| L4 | separately budgeted by benchmark profile |

Threshold changes after freezing require a receipt and rationale; a failing
gate cannot be made green by moving its threshold without review.

## Completion Standard

The work is complete when normal changes no longer require one monolithic
five-minute-plus command, all existing acceptance behavior remains reachable
through L3/L4, stable receipts distinguish timeout from pass/fail, and any
parallel speedup is supported by repeated race-free evidence.
