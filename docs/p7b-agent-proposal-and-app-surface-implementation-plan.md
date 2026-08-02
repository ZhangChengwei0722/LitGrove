# P7-B Agent Proposal And App Organization Surface Implementation Plan

- status: `approved_for_unattended_implementation`
- prepared_at: `2026-08-03`
- core_baseline: `main@7183c61940a43222d977dabb9a37ed31d1fab688`
- app_baseline: `feature/p6-discovery-acquisition@ab10afc3c03ceccf2a61f01cd1ee1286f3851912`
- core_branch: `feature/p7b-organization-proposal`
- app_branch: `feature/p7b-organization-work-surface`
- parent_design: `bounded P7 organization and screening design maintained outside this shared repository`
- target_application_service_interface: `1.12`
- target_agent_registry: `p7b-v1`
- canonical_schema_change: false
- operational_schema_change: true
- workspace_layout_change: false
- migration: false
- private_scientific_workspace_access: false
- next_gate: `core_tests_then_core_implementation`

## 1. Objective

Deliver the only product route by which an external Agent proposal can become one P7-A
organization revision:

```text
explicit target and current source selection
-> versioned organization_proposal Agent Task
-> exact manual Codex CLI or Claude Code CLI handoff
-> untrusted one-target candidate
-> confined staging and escaped App preview
-> explicit revise / reject / approve
-> current-basis revalidation
-> one atomic P7-A target promotion
```

P7-B does not ask the Agent to own IDs, provenance, Evidence closure, transactions or
approval. It does not add an embedded Agent runtime.

## 2. Design-To-Implementation Review

The accepted P7 design maps to the merged repositories as follows:

1. Reuse `AgentTaskApplicationService` state, lease, input-basis, staging, lineage and
   manual handoff behavior. Organization proposals are direct scientific organization
   Tasks and do not create or depend on a Pipeline Job.
2. Reuse `ResearchOrganizationService` as the sole canonical writer. The P7-B approval
   method composes an already staged candidate into one lower-level P7-A promotion call;
   the App never supplies a canonical bundle or approval receipt.
3. Reuse the read-only `ResearchOrganizationApplicationService` for target browsing and
   context. Advance the interface only for the new Task and approved-promotion methods.
4. Reuse the existing App Agent handoff, JSON import, preview, revision and rejection
   controls. Add a dedicated organization work surface for target/source selection and
   organization-specific approval.
5. Keep one Task bound to exactly one `direction`, `field_map_entry` or `question` target.
   A request with no target ID proposes a new target; a request with a target ID proposes
   one successor revision and binds the current target head in its input basis.

No unresolved architecture decision blocks this bounded implementation.

## 3. Core Contract Slice

Register Agent privacy registry `p7b-v1` and one available Task kind:

```text
task_kind: organization_proposal
required: paper_card_content, research_routing_context, operational_context
optional: metadata, canonical_evidence, review_background
result_contract: p7b-organization-proposal@1.0
```

The result contract carries one target kind, one proposed definition/revision payload,
sparse source-link proposals, duplicate/corroboration notes and bounded unresolved-conflict
notes. It cannot carry canonical IDs, caller-authored Evidence unions, Tags, Screening
decisions or Research Synthesis content.

The creation request is closed and bounded. It includes:

- one target kind;
- zero or one existing target ID;
- one concise proposal goal;
- an ordered, unique set of one to twenty-five paper IDs;
- whether current Review background may be included;
- executor, explicitly approved content classes and idempotency key.

Core builds the payload from current Primary Card Units, optional current Review Units and
the current target/organization context. It does not accept Unit text, Evidence IDs,
definitions or source paths from the browser.

## 4. Basis, Privacy And Stale Rules

The input basis binds:

- workspace and task registry versions;
- target kind and target ID;
- current target revision/head digest, or an explicit absent-target basis;
- selected paper IDs and active Primary/Review revision digests;
- current admissible Unit identities and Core-derived Evidence closure;
- current Direction references needed by a Field Map proposal;
- proposal goal and Review-background choice.

Submission and approval both recompute this basis. A changed Unit, Evidence closure,
Review revision, target head or referenced Direction rejects stale submission/promotion.
The old Task remains audit history; revision creates a successor with a freshly derived
basis and preserved feedback/result digest.

All payload classes pass the existing explicit workspace/task/user/executor intersection.
Source text and Agent output remain untrusted data. Prompt construction cannot grant tool,
filesystem, network or write authority.

## 5. Candidate Composition And Approval

Add a focused organization-candidate composer used only by
`AgentTaskApplicationService.approve_organization_result`:

1. validate the staged result contract and exact Task binding;
2. require zero unresolved conflicts before approval;
3. re-resolve every source Unit through P7-A admissibility and freshness rules;
4. derive Evidence IDs for factual Primary links inside Core;
5. preserve Review links as background-only and non-evidence;
6. report exact duplicates as no-change and reuse the existing stable link identity where
   the same source-target-role link already exists;
7. allocate target, revision and new link IDs only inside Core;
8. call exactly one P7-A promotion with `actor: user` and a user-approved Agent-proposal
   origin;
9. append the terminal Agent Task approval state only after the canonical transaction
   succeeds or an exact transaction recovery proves it already succeeded.

Approval is idempotent. Partial cross-target promotion is structurally impossible. A
candidate with unresolved conflict notes may be previewed but must be revised or rejected,
not promoted.

## 6. Core Application Service Surface

Extend `AgentTaskApplicationService` with bounded methods equivalent to:

```text
create_organization_proposal(session, request)
refresh_organization_task(session, task_id, expected_state)
approve_organization_result(session, task_id, expected_state)
```

Existing inspect, prepare, submit, preview, request-revision, reject, list and show methods
must support the new kind without exposing leases, paths, fingerprints, raw canonical
stores or writer authority. Advance Application Service interface to `1.12` and advertise
the capability explicitly.

No CLI mutation command is added. The Portable Skill receives only the new App handoff
contract compatibility needed to return one bare schema-conforming candidate.

## 7. App Backend Slice

After the Core branch is merged and an exact reviewed wheel is built:

1. pin the App compatibility record to the merged Core commit, wheel digest, interface
   `1.12` and required organization-proposal capability;
2. expose browser-safe list/show/context reads for Directions, Field Map Entries and
   Questions through the public Core service;
3. add a strict organization Task creation endpoint that accepts IDs and configured option
   values only;
4. reuse existing Agent handoff inspection/preparation, bounded JSON submission, preview,
   revision and rejection endpoints;
5. add one organization-specific approval endpoint that accepts only expected Task state;
6. serialize organization mutations through the existing App operation coordinator and
   refresh Catalog state after a successful canonical promotion.

All mutation routes require session authentication, exact Host/Origin, CSRF and closed
request bodies. HTTP errors remain path/source/result-redacted.

## 8. App Work Surface

Add one first-class `研究组织` view with stable responsive dimensions and these complete
states:

- browse and inspect current Directions, Field Map Entries and Questions;
- choose `new` or one existing target and see its current revision/freshness;
- select one to twenty-five Library papers by stable ID;
- choose whether Review background is allowed;
- write one concise proposal goal and choose Codex CLI or Claude Code CLI;
- create the Task, inspect/copy the exact handoff, import one JSON result and preview all
  proposed definition/link/duplicate/conflict sections as escaped text;
- request revision, reject or explicitly approve;
- show stale-basis, conflict-blocked, no-change and committed outcomes;
- refresh the organization lists after approval.

The view does not launch an Agent, render untrusted Markdown, expose source paths or allow
the browser to edit canonical IDs/Evidence closure. Generic Pipeline Agent Tasks remain in
the existing Agent view; organization Tasks are owned by this dedicated surface.

## 9. Tests First

### Core

1. registry `p7b-v1`, privacy classes and result schema;
2. direct Task creation for new and existing targets, exact one-target enforcement and
   idempotency;
3. bounded context and absence of paths/fingerprints/raw stores;
4. manual handoff, stale submit rejection and successor revision lineage;
5. escaped/bounded preview with duplicate and unresolved-conflict sections;
6. new Direction, Field Map Entry and Question approval through P7-A;
7. existing target successor revision, stable duplicate-link reuse and exact no-change;
8. factual/Review admissibility, Core-derived Evidence and target-head stale rejection;
9. transaction recovery, Guardian/Catalog refresh and installed-wheel contract smoke.

### App

1. Core compatibility and capability fail-closed tests;
2. strict read/create/approve API security and redaction tests;
3. organization Task lifecycle API tests for all three target kinds;
4. React unit tests for loading, empty, handoff, preview, conflict, stale, revision,
   rejection, no-change and committed states;
5. desktop and mobile Playwright flow using only synthetic fixtures;
6. packaged fresh-install production-start smoke with organization read and one approved
   synthetic proposal.

## 10. Delivery Sequence And Validation

### P7-B1 Core

- create the Core feature branch from `main@7183c61`;
- write failing focused tests, then implement registry/contract/Task/composer/service;
- run focused and complete Windows tests, `compileall`, build, base/PDF installed-wheel
  smokes, privacy scan and diff review;
- commit, push, create a small PR, merge and fast-forward local main when GitHub is
  available;
- build and record the exact merged Core wheel.

### P7-B2 App

- create the App feature branch from `ab10afc`;
- pin the exact merged Core wheel and implement backend before frontend;
- run Python, Vitest, TypeScript, ESLint, Vite, Playwright, package and fresh-install
  validation;
- commit locally. The App repository has no remote, so no push or PR is invented.

### P7-B3 Closure

- create Core and App validation receipts plus one P7-B closure manifest;
- verify Core and App working trees and compatibility hashes;
- update the parent design, roadmap, overall plan and durable project records;
- run mandatory `neat-freak` before reporting closure.

Generated test/build workspaces remain retained until P11 and overall project completion.

## 11. Stop Boundary

Do not add in P7-B:

- personal Tags or Tag facets;
- Screening Sets, criteria, recommendations or decisions;
- Research Synthesis creation, refresh or rendering;
- embedded Agent execution or provider-specific API calls;
- private scientific workspace or real-PDF access;
- migration, legacy cutover, Exchange, Obsidian runtime, citation graph or discovery
  changes;
- cleanup or deletion of retained test/benchmark workspaces.

If one staged proposal cannot be promoted as one atomic P7-A target revision, fail closed
and retain the Task for revision/rejection. Do not split it into implicit child writes.
