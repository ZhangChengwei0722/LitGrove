# P4-A Agent Task Staging Closure Manifest

- status: `closed`
- reconciled_at: `2026-07-31T07:17:57+08:00`
- implementation_commit: `c9b11fc76eccfe35cd7970e434ba9d2b25e8f0b4`
- merge_commit: `e5399a1`
- branch: `feature/p4a-agent-task-staging-kernel`
- app_closure_commit: `88f3016`
- application_service_interface: `1.2`
- registry_version: `p4a-v1`
- validation_receipt: `docs/p4a-agent-task-staging-validation-receipt.md`
- next_gate: `bounded_p4b_primary_semantic_bundle_plan`
- cleanup_status: `generated validation workspaces retained until P11 and overall completion`

## Delivered Boundary

- versioned Agent Task and privacy registries with only `document_route_resolution`
  available;
- optional workspace `agent_policy`, absent means deny by default;
- append-only Task states and bounded non-canonical staging under one operational store;
- exact input basis across paper, Pipeline Job, live source digest, Parse output and
  current Source Adequacy;
- deterministic portable manifests for external Codex CLI and Claude Code CLI handoff,
  without embedded execution;
- CAS lease, stale submit rejection, escaped preview, revision/reject/approve decisions,
  stable cursor pagination and transaction-correlated events;
- route approval with deterministic crash recovery when the Job commit precedes the Task
  receipt;
- bundle, workspace bootstrap, capability, privacy and Guardian integration;
- Application Service interface advancement from `1.1` to `1.2`.

## Durable Reconciliation

- contributor audience: `README.md`, `docs/architecture.md`,
  `docs/contributor-guide.md` and `docs/workflow.md` describe the implemented 1.2 flow;
- architecture decisions: ADR 0030 records the implemented P4-A subset and ADR 0031
  assigns route classification to the Task while preserving user approval authority;
- operator/consumer evidence: this manifest and the validation receipt pin the exact Core
  commit, interface, package artifacts and validation matrix;
- downstream App compatibility acceptance is closed at App commit `88f3016`; it pins
  Application Service interface `1.2`, force-installs this exact reviewed Core wheel and
  verifies primary, review and genuine mixed-document deterministic routes;
- external design/roadmap synchronization remains deferred because that workspace was
  unavailable; it is not reported as completed.

## Explicitly Not Delivered

- Primary Paper Card/Evidence/review-queue semantic bundles: P4-B;
- grounded Review Memory semantic bundles: P4-C;
- App Agent Task/preview work surface and Portable Skill update: P4-D;
- Direction/Field Map/Question proposal processing or Research Synthesis drafting;
- embedded Agent runtime, credentials, executable discovery or model API calls;
- PDF reader integration, discovery UI, Exchange, Obsidian, backup, migration or legacy
  cutover.

P4-A is closed across Core and the local App compatibility surface. Post-merge Windows
validation on Core merge commit `e5399a1` passed with `845 passed, 4 expected POSIX
skips`; `compileall` and privacy scan also passed. The next batch may implement the
Primary semantic bundle only after its bounded plan is written. It must not absorb the
Review route, App Agent Task UI or unrelated product surfaces.
