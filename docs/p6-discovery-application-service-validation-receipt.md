# P6 Discovery Application Service Validation Receipt

- validated_at: `2026-08-03`
- branch: `feature/p6-discovery-acquisition`
- baseline: `main@9efa9f1bfe125fae72bed4cebf97f061b6d4a7f3`
- application_service_interface: `1.10`
- provider: `europe-pmc`
- status: `passed`

## Deterministic Test Results

```text
unit A-M:                430 passed, 2 skipped
unit N-Z:                322 passed, 2 skipped
contract + integration: 137 passed
benchmark:                16 passed
aggregate:               905 passed, 4 skipped
compileall:               passed
privacy scan:             passed
wheel smoke:              passed
```

The four skips are the existing POSIX permission contracts on Windows. The first A-M run
identified one inline synthetic PDF signature in the new test; the fixture was changed to
the repository's byte-tuple convention, then the complete A-M shard passed.

The initial monolithic `pytest -q` invocation reached the 15-minute execution limit
without reporting a test failure. All test directories were then covered through the
recorded deterministic shards.

## Artifact Digests

```text
research_kb_core-0.1.0-py3-none-any.whl
sha256 9daff96fe54858d56046fc9b463da5ee7e7ea40f5d1e04ac586ac84ac42c2144

research_kb_core-0.1.0.tar.gz
sha256 b745ca4d7ca6a2569e57cfb7006d2ad5861e885405201e66bacd54e2264199a4
```

The fresh wheel smoke installed the exact wheel into a new virtual environment and
completed existing Registry, Parse, Source Adequacy, Agent Task, reading and Guardian
checks with Application Service `1.10`.

## Boundary Assertions

- search is workspace-independent and reports `persistent_writes: 0`;
- selection consumes the complete report and requires `actor: user`;
- candidate reads use stable cursor pagination and reveal no workspace path;
- resolution reuses the fixed Europe PMC resolver and writes nothing;
- acquisition requires `actor: user`, re-resolves, writes create-only and is idempotent;
- acquired-candidate inspection reports `unregistered` and creates no Registry or Parse
  record;
- discovery candidate writes do not invalidate Catalog projection;
- no private workspace, real PDF or provider expansion was used.
