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

`tools/test-shards.json` is the canonical shard manifest. Unit files are listed explicitly.
Contract, integration, privacy, and benchmark directories are included as bounded structural
selectors. A slow file may be split by registered pytest markers, but each selected node ID
still has exactly one shard owner. `tools/run_validation.py --verify --collect-nodeids`
proves that:

- every L3 file is covered and every L3 node ID has exactly one shard owner;
- a file selected by multiple shards uses distinct registered markers with no node overlap;
- the L3 union equals unit, contract, integration, and privacy collection;
- L4 equals the complete benchmark collection;
- L3 and L4 do not overlap.

The Windows workflow runs 24 L3 shards and L4 on independent hosted runners. A separate
packaging job reconciles node IDs, builds wheel/sdist, runs installed-wheel smoke, and checks
the CLI/privacy boundary. `Windows validation` succeeds only when every shard and packaging
job succeeds, so branch protection retains one stable required-check name.

## Receipts

Each invocation writes `test-validation-receipt@1.0` JSON containing the level, shard,
platform, Python version, exact command, node count/digest, durations, return code, and final
status. CI uploads receipts for 30 days. `.validation/` is ignored locally.

Timeout is not success or an assertion failure. Every ordinary L3 invocation receives the
240-second execution budget directly. A timeout writes `status=timed_out`, uses exit code
`124`, fails its shard, and therefore fails the aggregate Windows gate. The separate
15-minute job timeout remains an infrastructure guard for setup and artifact handling.

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

The slow reading, agent-route, organization, and screening tests build a synthetic workspace
snapshot once per module. Before each test, the fixture restores that snapshot at the same
configured path. This preserves config-fingerprint and journal validation while isolating
test mutations; copying an initialized workspace to a different configured path is not an
accepted shortcut.

## Performance Gates

The accepted provisional budgets are L2 at 180 seconds and each ordinary L3 shard at
240 seconds of test execution. The budget is enforced by the validation subprocess, not
inferred from total job duration.

The first broad L2 candidate exceeded 180 seconds and was rejected. The accepted L2
selection therefore admits only explicitly reviewed workspace-free unit files plus contract
tests; all other unit files remain in exhaustive L3 shards.

The first GitHub-hosted run kept full coverage but exposed two over-budget groups:
`application-semantic-a3` took `393.887 s` and `application-semantic-b1` took `483.365 s`.
That result was rejected even though the old jobs completed successfully. The two groups were
replaced by marker-bounded reading and agent-route shards plus separate knowledge,
organization, and screening shards. The final manifest contains 24 L3 shards, covers 108 L3
files and 1,096 L3 node IDs exactly once, and keeps 20 scale node IDs in the separate L4
shard.

Pre-push Windows measurements for the new slow groups ranged from `17.53 s` to `154.28 s`;
these are development evidence, not hosted-runner p95. The GitHub receipts remain the
acceptance evidence for the 240-second budget.

After repeated CI receipts establish median and p95, an over-budget shard must be split or
its fixture setup optimized. Thresholds cannot be relaxed after a failing run merely to make
the gate pass. `pytest-xdist` remains a later bounded pilot over an explicit allowlist; it is
not enabled by this implementation.
