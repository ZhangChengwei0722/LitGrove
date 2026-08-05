# Test Validation

## Validation Levels

| Level | Use | Command |
|---|---|---|
| L0 | Documentation-only changes | `python tools/run_validation.py --level L0 --receipt .validation/l0.json` |
| L1 | One bounded behavior | `python tools/run_validation.py --level L1 --selector <node-or-file> --receipt .validation/l1.json` |
| L2 | Normal feature feedback | `python tools/run_validation.py --level L2 --receipt .validation/l2.json` |
| L3 | Complete high-risk acceptance | `python tools/run_validation.py --level L3 --shard all --receipt .validation/l3.json` |
| L4 | Scale and benchmark validation | `python tools/run_validation.py --level L4 --shard scale --receipt .validation/l4.json` |

Schema, authority, storage, transaction, recovery, merge, release, privacy-boundary, and
source-write changes require targeted tests, complete L3, and L4. L0-L2 provide earlier
feedback; they do not waive the higher gate for a high-risk change.

## Classification Contract

Every collected test receives exactly one structural marker from its directory: `unit`,
`contract`, `integration`, `privacy`, or `benchmark`. Runtime markers may overlap:

- `slow` marks integration-like unit files with workspace/filesystem setup and measured
  integration hotspots. L2 uses an explicit workspace-free unit allowlist; new unit files
  default to `slow` until a timing review admits them;
- `scale` marks benchmark validation outside normal feature feedback;
- `serial` marks files that must remain in a single-process shard;
- `windows` marks required Windows-specific behavior.

Pytest runs with `--strict-markers`. Collection fails when a test is outside a registered
structural directory or receives an invalid structural classification.

## Stable Shards

`tools/test-shards.json` is the canonical file-to-shard manifest. Unit files are listed
explicitly. Contract, integration, privacy, and benchmark directories are included as
bounded structural selectors. `tools/run_validation.py --verify --collect-nodeids` proves
that:

- every L3 file and node ID has exactly one shard owner;
- the L3 union equals unit, contract, integration, and privacy collection;
- L4 equals the complete benchmark collection;
- L3 and L4 do not overlap.

The Windows workflow runs each L3 shard and L4 on an independent hosted runner. A separate
packaging job reconciles node IDs, builds wheel/sdist, runs installed-wheel smoke, and checks
the CLI/privacy boundary. `Windows validation` succeeds only when every shard and packaging
job succeeds, so branch protection retains one stable required-check name.

## Receipts

Each invocation writes `test-validation-receipt@1.0` JSON containing the level, shard,
platform, Python version, exact command, node count/digest, durations, return code, and final
status. CI uploads receipts for 30 days. `.validation/` is ignored locally.

Timeout is not success or test failure. A timed-out CI job fails its shard and therefore the
aggregate Windows gate.

## Serial And Slow Inventory

| Classification | Files | Reason | Owner |
|---|---|---|---|
| serial | `test_workspace_bootstrap.py`, `test_transactions.py` | lock and transaction behavior | repository maintainer |
| serial | `test_tag_service.py`, `test_discovery_acquisition_service.py` | explicit concurrency scenarios | repository maintainer |
| serial | `test_portable_skill_sync.py` | subprocess and generated-tree checks | repository maintainer |
| slow | workspace/filesystem-backed unit files | repeated workspace setup is the accepted audit's dominant long-tail cause; L2 admission is explicit | repository maintainer |
| slow | two-domain and real-PDF integration | measured hotspot in the accepted audit | repository maintainer |

Serial files run sequentially in their own CI shard. No writable workspace, SQLite database,
lock directory, journal, or mutable fixture is shared between shards.

## Performance Gates

The first CI implementation retains the accepted provisional budgets: L2 at 180 seconds and
each ordinary L3 shard at 240 seconds of test execution. CI job timeout is 15 minutes to
distinguish a budget observation from infrastructure startup and installation failure.

The first broad L2 candidate exceeded 180 seconds and was rejected. The accepted L2
selection therefore admits only explicitly reviewed workspace-free unit files plus contract
tests; all other unit files remain in exhaustive L3 shards.

After repeated CI receipts establish median and p95, an over-budget shard must be split or
its fixture setup optimized. Thresholds cannot be relaxed after a failing run merely to make
the gate pass. `pytest-xdist` remains a later bounded pilot over an explicit allowlist; it is
not enabled by this implementation.
