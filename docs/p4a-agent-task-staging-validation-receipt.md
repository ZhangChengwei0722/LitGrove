# P4-A Agent Task Staging Validation Receipt

- status: `passed`
- validated_at: `2026-07-31T06:42:41+08:00`
- implementation_commit: `c9b11fc76eccfe35cd7970e434ba9d2b25e8f0b4`
- branch: `feature/p4a-agent-task-staging-kernel`
- baseline: `main@b5844c8ca592626661ba6123e6297fdeccce8ead`
- package: `research-kb-core==0.1.0`
- application_service_interface: `1.2`
- agent_task_registry: `p4a-v1`
- wheel_sha256: `5ca29745a19df8febb5d261abd3174799f218f51bf0158df37925a557400fef4`
- sdist_sha256: `69889d3e9d23edbd90587c5e421043d93f4058971c12ecf1ed5c90b80cc95117`
- fixture_scope: `synthetic sources and generated workspaces only`

## Validation Matrix

| Check | Result |
|---|---|
| focused Agent Task registry/application tests | `10 passed` |
| final complete Windows suite | `845 passed, 4 expected POSIX skips` |
| `compileall` | passed |
| source build of sdist and wheel | passed |
| isolated installed-wheel smoke | passed |
| package version | `research-kb 0.1.0` |
| privacy scan | `7 expected, 0 unexpected` |
| `git diff --check` | passed |

The installed-wheel smoke used registry `p4a-v1` and executed a synthetic
`document_route_resolution` flow through Task creation, portable handoff, bounded result
submission, escaped preview and explicit approval. Approval advanced only the Pipeline
Job to `primary_semantic_gate`; no Paper Card, Evidence or Review Memory was created by
the Agent Task service.

## Integrity And Security Coverage

- absent policy, unknown/deferred task kind, unsupported executor and missing required
  content class fail closed;
- effective privacy scope is an explicit set intersection with no implied hierarchy;
- handoff and submit replays require matching current/predecessor CAS digest, executor,
  lease and result intent;
- changed source/input basis rejects late submission with zero staging write;
- revision creates an atomic successor with reciprocal, acyclic lineage, prior result
  digest and exact feedback;
- staged output is bounded, untrusted and non-canonical; preview exposes no private refs;
- Guardian validates Task chains, route receipts and correlated success events;
- prompt-injection-shaped synthetic text remains labeled data and cannot expand authority;
- external Agent execution, credentials, network calls and live model availability are
  absent from Core and deterministic CI.

## Scope Notes

The external product-design workspace was unavailable during this batch. Its final design,
overall execution plan and roadmap remain a deferred synchronization action. No private
workspace, real PDF, legacy scientific record or external Agent session was accessed.
