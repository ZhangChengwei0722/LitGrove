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
sha256 c7afb307fd3740278b557d87707d9d80fa31edf374f578a55d6e52aecca2e078

research_kb_core-0.1.0.tar.gz
sha256 539038e28bc53c4db2dd26bd52275a1db38d2916f28c7ff7c024d51d76f85f94
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
