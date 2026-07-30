# P4 Agent Task And Semantic Preview Implementation Plan

- status: `p4a_core_validated`
- prepared_at: `2026-07-31`
- core_baseline: `main@b5844c8ca592626661ba6123e6297fdeccce8ead`
- app_p3d2_implementation: `d43be4e768fbd3c3ee2477dd8e7daa816aa6eb4a`
- app_p3d_closure: `10b49a0`
- current_batch: `p4a_agent_task_staging_kernel`
- implementation_authorized: `current_unattended_authorization_after_phase_plan`
- next_gate: `p4a_app_compatibility_and_integrated_acceptance`

## 1. Objective

Deliver the external-Agent handoff and user-approval substrate required before any App
semantic write:

```text
current deterministic Pipeline Job gate
-> versioned Agent Task kind/privacy registry
-> exact task input basis and effective content classes
-> escaped portable prompt manifest for Codex CLI or Claude Code CLI
-> external Agent result submission
-> stale-basis rejection
-> confined non-canonical staging
-> App-safe preview projection
-> explicit revise/reject/approve route
-> later P4-B/P4-C scientific commit contracts
```

The software prepares the prompt and validates the handoff. It does not start, supervise
or authenticate an Agent process. Agent output is untrusted data and never becomes
canonical merely because it validates structurally.

## 2. Fixed Decisions

### 2.1 Authority

- Core owns task IDs, state IDs, task-kind registry, privacy intersection, input-basis
  digests, leases, CAS, staging paths, validation, lineage and transaction receipts.
- The App owns browser security, selected executor UI, explicit content preview and user
  actions. It never reads Core stores or accepts browser filesystem paths.
- Codex/Claude Code own semantic judgment only. They receive one portable manifest and
  return one bounded JSON result. They cannot allocate canonical IDs or enlarge scope.
- Existing `RecordService` remains the only canonical scientific promotion authority.
  P4-B/P4-C add approved bundle composition around it; P4-A does not bypass it.

### 2.2 Privacy

Materialize ADR 0030 content classes as an explicit set:

```text
metadata
parsed_excerpt
canonical_evidence
paper_card_content
review_background
research_routing_context
research_synthesis
operational_context
source_document
```

No class implies another. Effective classes are the intersection of workspace policy,
task-kind allowance, explicit user approval and a versioned executor profile. Required
classes must survive the intersection. `local_only` cannot be handed to Codex CLI or
Claude Code CLI merely because either executable is installed locally.

Workspace configuration gains an optional Agent policy with deny-by-default behavior when
absent. The additive config property does not change layout paths. Existing workspaces
remain readable and retain all non-Agent capabilities without upgrade.

### 2.3 First result contract

P4-A fully supports only `document_route_resolution`. Its staged result selects
`primary` or `review`, records bounded rationale/confidence and may identify
`mixed_document` as the review-route reason. User approval applies the route to the
current Pipeline Job through existing Core authority.

The full ADR task-kind registry is published with explicit support status. Primary and
Review semantic processing kinds are not submit-capable until P4-B/P4-C register their
exact result contracts. Unknown/deferred kinds fail closed; arbitrary JSON is never used
as a temporary scientific contract.

### 2.4 State and staging

- Agent Task state is append-only and CAS-bound, parallel in discipline to Pipeline Job
  state but with task-specific transitions.
- Prompt preparation issues one lease. Submission requires the current task state and
  lease basis plus the current input-basis digest.
- Submission atomically appends one task state containing a bounded staging envelope in
  the operational Agent Task store. It does not require an unsafe two-target transaction.
- Staging is non-canonical, absent from factual query and safe to remove after terminal
  receipt/retention policy. P4-A preserves all result and decision digests.
- `revision_requested` terminalizes the submitted task and atomically creates a successor
  preserving route, predecessor task/result digest, feedback and freshly derived refs.
- Changed source, parse, Source Adequacy or relevant canonical head rejects late submit.
  A stale task cannot restart document routing or silently refresh itself.

## 3. P4-A Agent Task And Staging Kernel

### 3.1 Contracts and layout

Add only the P4-A owned contracts:

- Agent Task append-only state;
- staged Agent result envelope;
- document-route decision result;
- optional workspace Agent policy;
- required task/state/staging ID definitions.

Add one managed operational path for Agent Task states with confined staging envelopes.
Advance the layout contract by one exact predecessor step only if new managed directories require it.
Workspace bootstrap must plan and apply the upgrade without creating synthetic tasks or
staging payloads.

### 3.2 Public service

Expose one session-bound `AgentTaskApplicationService` with bounded methods equivalent to:

```text
registry(session)
create_from_pipeline(session, job_id, request)
prepare_handoff(session, task_id, expected_state, executor_id)
list_tasks(session, page_size, cursor)
show_task(session, task_id)
submit_result(session, task_id, expected_state, lease, result)
preview_result(session, task_id)
request_revision(session, task_id, expected_state, feedback)
reject_result(session, task_id, expected_state, reason_code)
approve_route_result(session, task_id, expected_state)
```

Public App projections omit source refs, absolute paths, fingerprints, raw parser bundles,
authority snapshots and task-private payloads. `prepare_handoff` is the sole private
payload projection and returns only approved classes under explicit user action.

### 3.3 Prompt manifest

The manifest is deterministic JSON plus a bounded plain-text prompt. It contains task
identity, expected contract, exact input-basis digest, route-safe metadata, selected
parsed excerpts and operational constraints. Every source-derived string is labeled data.
No source text can request tools, files, network, authority changes or alternate output.

Codex CLI and Claude Code CLI profiles are deterministic capability declarations only.
P4-A does not locate executables, read credentials, spawn processes, call APIs or make live
Agent availability a CI condition.

### 3.4 Validation

Test at minimum:

- unknown registry version, task kind, executor or content class fails closed;
- workspace/task/user/executor intersection is exact and non-hierarchical;
- missing required class and local-only/cloud mismatch fail before task creation;
- prompt data cannot expand scope or become executable markup;
- source/parse/adequacy change rejects stale submit with zero staging write;
- exact prepare/submit replay is idempotent and changed intent conflicts;
- staging and state append are atomic under injected failures;
- preview is escaped/bounded and cannot expose private refs;
- revision successor preserves required lineage and current inputs;
- route approval is explicit, CAS-bound and leaves no scientific record;
- cursor pagination is stable and maximum page size is enforced;
- Guardian validates task chains, staging ownership and orphan/missing payloads;
- installed-wheel smoke exercises registry, handoff, submit, preview and route approval.

## 4. P4-B Primary Semantic Bundle

Define a specific Primary candidate result contract with task-local aliases for Evidence,
review boundaries and Card Units. On explicit approval, Core allocates final IDs, resolves
aliases, checks operation-specific Source Adequacy, validates quote/page/locator trace-back
and atomically commits canonical Evidence, scientific review-queue boundaries and one
seven-section Paper Card revision.

Unsupported or overbroad Units are narrowed, rejected or routed to scientific
`review_queue`; missing figure/SI capability blocks only the consuming item. Corrections
create successor revisions and supersede history without in-place edits.

## 5. P4-C Review Semantic Bundle

Define a specific Review candidate result contract. Basic orientation consumes
`basic_paper_understanding`; each retained Unit consumes its own current text, figure,
formula/layout or supplementary trace-back capability. A failing Unit is revised or
rejected without blocking unrelated Units.

Zero reusable Units are valid only with a low-value/redundant reason and coverage limits.
Every retained Unit carries same-review page/section provenance, short excerpt or accurate
paraphrase, and immutable background-only/no-canonical-Evidence flags.

## 6. P4-D App And Portable Skill

Add an App task/preview work surface that:

- creates tasks only from eligible current Pipeline Jobs;
- shows exact content classes before prompt generation;
- lets the user choose Codex CLI or Claude Code CLI;
- produces a prompt for external use without launching the Agent;
- accepts pasted/uploaded bounded JSON result;
- renders escaped candidate content and provenance;
- offers revise, reject and approve actions;
- never labels automated validation as human scientific verification.

Update the portable Skill authoring source and generated mirrors only in their owning
repository/process. The released Skill consumes public Core/App contracts and does not
duplicate validation or write stores directly.

## 7. P4-E Integrated Acceptance

Run deterministic adapter conformance for Codex and Claude manifest/result shapes. A
bounded live smoke is optional and cannot gate CI on login, network, model behavior or
cost. Browser acceptance uses only generated synthetic source content and verifies the
complete task -> prompt -> import -> preview -> decision loop at desktop and mobile
widths.

Full closure also verifies package identities, privacy scan, stale edges, transaction
recovery, task/staging cleanup receipts, Catalog freshness and no private legacy-workspace
access.

## 8. Stop And Defer Boundaries

Proceed through ordinary implementation defects without interruption. Do not add:

- embedded Agent execution, API keys, credential handling or background model calls;
- arbitrary provider plugins or `source_document` export in P4-A;
- Direction/Field Map/Question proposal processing or Research Synthesis drafting;
- PDF.js/UPDF, discovery, Exchange, Obsidian, backup or migration;
- real workspaces, real PDFs or private legacy-workspace access.

The external `E:` final-design/overall-plan documents are currently unavailable. Record
their synchronization as a recoverable documentation action and continue repository-local
work; do not invent or overwrite their contents while the drive is absent.
