# P4-D0 Agent Handoff Inspection And Recovery Plan

- status: `closed`
- prepared_at: `2026-08-01`
- branch: `feature/p4d-agent-handoff-inspection`
- baseline: `p4c feature tip 2524ac4; remote merge cccbad1`
- baseline_tree: `ec310c2300f60f940e8cf6faf21a86af77d58fc0`
- current_application_service_interface: `1.4`
- target_application_service_interface: `1.5`
- next_gate: `p4d_app_backend_and_work_surface`

## Objective

Close two App-facing gaps without changing Agent Task scientific or storage contracts:

```text
created Task
-> inspect exact bounded handoff payload with zero writes
-> explicit user confirmation in App
-> prepare prompt and lease
-> recover the same leased handoff after App/browser restart
```

## Changes

1. Add `AgentTaskApplicationService.inspect_handoff(...)`.
2. Accept only a current `created` or `leased` Task, matching executor and exact expected
   state.
3. Recheck the current source/Parse/Profile/bundle input basis before returning data.
4. Return the exact payload, manifest/result contract, effective content classes and
   prompt byte count; return no prompt and no lease.
5. Preserve `persistent_writes: 0` and `canonical_scientific_write: false`.
6. Extend `prepare_handoff(...)` replay so an authenticated App may pass the current
   leased state after restart. Preserve the existing predecessor-state replay for an
   interrupted original response.
7. Require executor equality, current basis and exact handoff digest for both replay
   forms.
8. Advance Application Service interface to `1.5`; update public docs and installed-wheel
   smoke.

## Validation

- inspect before lease returns exact untrusted payload and zero writes;
- inspect does not expose source refs, paths, raw source documents or leases;
- stale source/input basis rejects inspection;
- wrong executor or expected state rejects inspection;
- current leased-state replay returns the identical manifest/lease with zero writes;
- wrong leased-state digest and changed handoff content reject recovery;
- P4-A/P4-B/P4-C task behavior remains compatible;
- focused tests, full Windows suite, compile, build, base/PDF wheel smoke, version,
  privacy and `git diff --check` pass.

## Stop Boundaries

Do not add CLI Agent Task commands, HTTP endpoints, App UI, Skill content, schema/layout/ID
changes, embedded execution, credentials, scheduler, source-document export, new Task
kinds, Field Map, Question Mapping, Research Synthesis, private workspace or real PDF
access. App and Portable Skill work remain separate P4-D batches.
