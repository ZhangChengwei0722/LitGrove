# Test Performance Audit

- status: `audit_complete`
- baseline: `1faac4f4ce01d31c3828d1768b3954c86089eec0`
- platform: `Windows`
- measured_at: `2026-08-05`
- implementation_status: `t1_t2_implementation_in_progress`
- next_gate: `test_performance_implementation_review`

## Scope And Boundaries

This is a bounded audit of the public, synthetic Shared Core test suite. It did
not change test code, markers, fixtures or validation policy. It did not access
a private scientific workspace, a real paper PDF or an external provider.

The five-minute executor limit is an outer command timeout. A command that does
not finish within that window is recorded as not completed; it is not reported
as a test failure or a passing suite.

## Measured Baseline

| Selection | Result | Wall time | Observation |
|---|---:|---:|---|
| collection only | `1112 collected` | `1.19 s` | collection is not the bottleneck |
| `tests/contract` | `97 passed` | `8.23 s` | suitable for frequent validation |
| `tests/integration` | `45 passed` | `83.33 s` | bounded but several tests exceed five seconds |
| `tests/privacy` | `4 passed` | `6.02 s` | suitable for frequent validation |
| `tests/benchmark` | `20 passed` | `25.49 s` | small checks are mixed with scale semantics |
| candidate storage/recovery shard | `224 passed, 4 skipped` | `231.88 s` | already close to the executor ceiling |
| 26-file application/semantic candidate | not completed | `> 5 min` | selection is too broad for one command |
| 11-file application/semantic A candidate | not completed | bounded run terminated | must be split before it can be a stable shard |

The four skips in the storage/recovery measurement are the expected POSIX-only
permission checks on Windows.

Representative hotspots from `--durations` measurements are:

| Area | Observed duration |
|---|---:|
| two-domain end-to-end runtime | `8.70-9.12 s/test` |
| Research Synthesis runtime | `6.15-6.96 s/test` |
| Guardian stale mapping/synthesis checks | `5.48-6.38 s/test` |
| Question Mapping runtime | `5.51-5.56 s/test` |
| Source Asset operations | `2.50-4.91 s/test` |
| Registry identity corrections | `2.69-4.89 s/test` |
| real synthetic PDF runtime | `4.09 s/test` |
| backup/restore validation | about `2.20-2.40 s/test` |

## Structural Findings

1. `tests/unit` is not a runtime unit tier. Its 946 collected tests include
   filesystem workspaces, JSONL transactions, catalog projection, Guardian
   scans, PDF generation/parsing, backup/restore and semantic bundle flows.
2. The repository has no registered `unit`, `contract`, `integration`, `slow`,
   `scale`, `serial` or `windows` markers. Directory names are currently the
   only coarse classification.
3. Full-suite execution is the default for both documentation-only and
   high-risk changes. This spends the most expensive validation on changes
   that cannot affect runtime behavior.
4. Repeated workspace bootstrap, schema loading, PDF construction and complete
   Guardian traversal dominate the long tail. These costs are useful in
   acceptance tests but need not be paid by every nearby behavior test.
5. A single full-suite command cannot reliably produce a result inside the
   five-minute executor window, so timeout reports frequently obscure whether
   any test actually failed.

## Parallel-Safety Audit

Static inspection found:

| Risk signal | Files |
|---|---:|
| uses `tmp_path` | `81` |
| subprocess/process APIs | `2` |
| explicit file locking | `1` |
| process-global cwd/stdin/stdout/environment mutation | `3` |
| fixed sleep | `1` |
| fixed localhost port | `0` |

Per-test `tmp_path` isolation is a good basis for parallel execution, but it is
not sufficient proof. Transaction lock tests, process-isolated measurements,
package installation smoke tests and any test that uses a repository-relative
build output or shared TEMP name must be classified before enabling xdist.
Process-global monkeypatches are isolated between xdist workers, but the tests
still require review for spawned child-process inheritance and repository-level
side effects.

`pytest-xdist -n 4` must therefore begin as a pilot over an explicit
parallel-safe allowlist. `serial` tests must run in a separate single-process
command. A writable workspace may never be shared across workers.

## Root Cause

The recurring timeout is not primarily a pytest defect. It is the combination
of three policy and design choices:

```text
behavior taxonomy based on directory names
+ integration-like work inside the unit directory
+ full suite used as the default validation command
-> frequent commands exceed the executor window
```

Increasing the timeout would make occasional full validation easier, but it
would not give contributors fast feedback or make failures easier to localize.

## Recommended Validation Levels

| Level | Use | Required validation |
|---|---|---|
| L0 docs-only | README and documentation-only changes | diff check, relative links, package/interface wording, privacy scan, isolated package build |
| L1 targeted | one bounded module or behavior | exact affected node IDs/files plus directly coupled contract tests |
| L2 normal feature | ordinary implementation without high-risk contract changes | fast `unit + contract`, excluding `slow` and `scale`; run any selected `serial` tests separately |
| L3 high risk | schema, authority, storage, transaction, recovery, merge or release changes | complete suite expressed as stable, exhaustive shards plus installed-wheel smoke |
| L4 scale | scale and benchmark work | dedicated benchmark command with profile and receipt; never hidden inside L2 |

The initial L3 shard families should be:

```text
contract
storage/recovery
application/semantic-a
application/semantic-b
discovery/views/exchange
privacy/platform
integration
```

The shard manifest must prove that its union equals the full collected suite,
apart from separately declared `scale`, `serial`, `windows` and POSIX-only
selections. No test may disappear because it is slow.

## Fixture Optimization Boundary

The following reuse is safe to investigate:

- cache immutable schema documents and compiled read-only validators;
- cache immutable synthetic PDF template bytes, then write independent copies
  under each test's `tmp_path`;
- create an immutable synthetic base workspace, then materialize an independent
  writable copy for each test;
- replace full workspace construction with a minimal builder only where the
  omitted stores are outside the behavior under test;
- keep a smaller targeted Guardian check beside a retained end-to-end Guardian
  acceptance test.

The following is prohibited:

- sharing a writable workspace, SQLite projection, lock directory or journal;
- returning one cached mutable bundle to tests that mutate records;
- weakening transaction, recovery, source-immutability or provenance checks;
- replacing deterministic acceptance with mocks that no longer exercise the
  public contract;
- retaining generated large workspaces, build directories or caches.

## Decision

Adopt validation levels and explicit runtime markers first, then split L3 into
sub-five-minute shards. Optimize immutable fixture setup next. Evaluate xdist
only after the serial inventory is enforced and repeated sequential baselines
are stable.

This makes short changes fast without lowering the acceptance bar: L0-L2 give
early feedback, while L3 and L4 still exercise every high-risk and scale path at
the appropriate gate.
