# P7-D2 Screening Agent And App Implementation Plan

- status_at_plan_time: `p7d2a_validation_passed_pending_remote_closure`
- lifecycle_reconciliation: `closed_core_and_app_delivered`
- prepared_at: `2026-08-03`
- core_baseline: `main@7bf01ea4e4b64a891590035df1c9940c7e669ab2`
- app_baseline: `feature/p7c-tags-work-surface@ad60a79794d83026f7424e36042607c4391036a9`
- core_branch: `feature/p7d2-screening-proposals`
- app_branch: `feature/p7d-screening-work-surface`
- schema_change: `additive Agent Task/result contracts plus screening approval provenance variant`
- migration: false
- private_scientific_workspace_access: false
- core_pull_request: `#53`
- core_merge_commit: `16013d5c58cf4f592129bb93f07425adf522b32d`
- app_implementation_commit: `54d5a362bdb0192a308204432196344b93cba70f`
- app_closure_commit: `6efdbd54189609c75734e0c063164641c5fc77ec`
- next_gate: `closed_superseded_by_p8_research_synthesis`

## 1. Goal

Complete optional Question-specific screening through the existing external-Agent and
localhost App boundaries without changing Library inclusion or embedding an Agent runtime:

```text
manual path
-> App criteria/decision editor
-> explicit user action
-> P7-D1 deterministic writer

Agent-assisted path
-> Core creates one bounded proposal Task
-> App exports exact Codex CLI / Claude Code CLI handoff
-> external Agent returns one contract-bound candidate
-> App escaped preview
-> explicit user approval or revision/rejection
-> P7-D1 deterministic writer
```

The App owns interaction only. Core owns task identity, privacy, stale checks, candidate
validation, final authority, transaction recovery and canonical promotion.

## 2. Frozen Semantics

1. Screening remains optional and Question-specific. It never blocks Library Registry,
   Parse, Paper Card, Review Memory or Evidence processing.
2. Screening decides set membership, not scientific credibility and not Evidence quality.
3. Add exactly two direct, no-Pipeline-Job Task kinds under registry `p7d-v1`:
   - `question_screening_criteria_proposal`;
   - `question_screening_decision_proposal`.
4. Criteria candidates contain bounded title, scope, ordered inclusion/exclusion text and
   notes. Existing items are referenced only through task-local aliases; new items carry
   no identity. Candidates contain no canonical IDs, approval or writer authority.
5. Decision Tasks bind one exact Question, canonical Paper, current criteria revision and
   criteria digest. Their candidate closes over every task-local criterion alias and returns
   `included | excluded | uncertain` plus rationale and limitations.
6. `uncertain` is a valid Agent candidate but cannot be promoted. The user must request a
   revision, reject the Task or use the deterministic direct-decision form.
7. Final criteria and `included/excluded` decisions remain user-only. Generic Agent Task
   approval must reject both screening Task kinds.
8. Agent output remains untrusted staging. Prompt content cannot widen task authority,
   read files, access the network or allocate IDs.
9. No active criteria preserves existing behavior. Active criteria continues to require a
   current included decision for new factual Question links.

## 3. Core Contracts And Privacy

Add two resolved result schemas:

```text
p7d-screening-criteria-proposal@1.0
p7d-screening-decision-proposal@1.0
```

Extend the versioned Agent Task registry additively to `p7d-v1`. Existing Task kinds keep
their previous result contracts and remain compatible when a workspace opts into the new
registry.

Extend the P7-D1 screening approval object additively with a second exact variant:

```text
origin: user_approved_agent_proposal
approved_by: user
task_id: exact Agent Task
task_result_digest: exact staged candidate
```

The existing direct `user_authored` receipt remains unchanged. Core must never relabel an
Agent-derived candidate as directly user-authored.

The task-kind/privacy matrix is closed:

| Task kind | Required content classes | Optional content classes | Forbidden |
|---|---|---|---|
| criteria proposal | `research_routing_context`, `operational_context` | none | parsed text, source document, Review background |
| decision proposal | `metadata`, `research_routing_context`, `operational_context` | `paper_card_content` | source document, parsed text, Review background, Research Synthesis |

Criteria payload contains only the selected Question's active organization projection,
existing criteria summary if revising, a user goal and bounded operational context.
Decision payload contains exact criteria content, one canonical Paper metadata projection
and, only when explicitly requested and available, current admissible Paper Card content.
Canonical Evidence is not required for membership screening and is not added merely to
justify the Agent's suggestion.

The context builder assigns deterministic task-local aliases to existing criteria items.
Criteria candidates may retain an alias with revised text, omit it, or add identity-free
text; decision candidates must use every supplied alias exactly once. Core alone translates
aliases back to retained criterion IDs or allocates IDs for new items.

## 4. Core Services

Extend `AgentTaskApplicationService` with dedicated methods:

```text
create_question_screening_criteria_proposal(session, request)
create_question_screening_decision_proposal(session, request)
approve_question_screening_result(session, task_id, request)
```

Creation validates current Question/Paper identity, exact criteria head, selected basis
scope, approved content classes and idempotency key. Criteria revision Tasks may target a
new criteria set or the exact current criteria head. Decision Tasks target one existing
current criteria revision and one Paper. Agent-assisted decision basis is limited to
`metadata | paper_card | mixed`; the wider deterministic direct-decision basis vocabulary
is not evidence that an abstract or source document was sent to the Agent.

If the Question-Paper pair already has a decision, the Task also binds its exact active
decision revision. Approval must use that expected head; concurrent direct or Agent-assisted
successors make the Task stale instead of creating a second pair owner.

Reuse the common inspect, prepare handoff, submit, preview, request revision and reject
lifecycle. Extend stale-input checks to Question, Paper, criteria revision/digest and
optional Paper Card revision. A changed basis rejects lease, submission and approval; it
does not silently restart or remap the Task.

Dedicated approval:

- criteria proposal calls the P7-D1 criteria writer with the current expected head;
- included/excluded decision proposal calls the P7-D1 decision writer;
- uncertain candidate fails closed before canonical mutation;
- exact replay returns the same terminal receipt;
- canonical write completed before the Task receipt is recovered by Task/result/output
  digests without creating a duplicate revision.

Guardian validates both Task lineages, result contracts, privacy registry, stale basis,
terminal receipts and output references. Capability output advertises P7-D2 separately
from the deterministic P7-D1 kernel.

## 5. App Backend

Pin the exact merged P7-D2 Core wheel and require Application Service interface/capability
compatibility at startup. Add strict session-bound HTTP routes for:

- criteria list/show/create/revise/archive;
- decision list/show/direct create or revise;
- criteria-proposal and decision-proposal Task creation;
- common handoff/import/preview/revision/reject;
- dedicated screening approval.

Requests use configured option IDs and record IDs only. The backend does not read
canonical files, parse CLI output or expose paths, source refs, digests, approval objects,
transactions, leases or full task payloads to the browser.

## 6. Browser Work Surface

Add one dedicated `问题筛选` surface with four views:

1. Question criteria: select a Question, inspect current/history state, create/revise/
   archive manually or request an Agent criteria proposal.
2. Paper decisions: filter by Question, outcome and freshness; inspect criteria basis and
   criterion-level dispositions.
3. Direct decision: select one Library Paper, choose basis scope, complete every criterion
   and submit an explicit user decision without Agent involvement.
4. Agent proposal: create one criteria/decision Task, choose Codex CLI or Claude Code CLI,
   export prompt, import JSON, preview escaped content and approve/revise/reject.

The UI must visibly separate `Library included` from Question-specific included/excluded.
It must label stale decisions, disable approval for `uncertain`, changed basis or incomplete
criterion closure, and keep screening out of the generic Pipeline Agent work surface.

## 7. Implementation Sequence

### P7-D2A Core

1. Add failing registry/result-schema/privacy tests.
2. Implement task definitions and bounded context builders.
3. Implement create, stale lifecycle and dedicated approval/recovery.
4. Extend Guardian, capability, wheel smoke and contributor/architecture docs.
5. Run focused and complete Windows validation, build, base/PDF wheel smokes, privacy and
   diff review.
6. Commit, push, PR, merge and validate the exact merged head.

### P7-D2B App

1. Create the App feature branch from the P7-C closure head.
2. Pin the exact merged Core wheel and compatibility facts.
3. Add backend adapters and security/request-budget tests.
4. Add the `问题筛选` work surface and typed API client.
5. Add React tests for manual and Agent-assisted paths, stale/uncertain blocking and
   Library-vs-Question labels.
6. Run Python, Vitest, TypeScript, ESLint, Vite, development/package Edge, desktop/mobile
   screenshot and overflow, fresh install, privacy/path and diff validation.
7. Commit the local App implementation and closure records. The App repository has no
   remote and must not gain one.

### P7-D3 Closure

1. Record Core/App validation receipts and exact artifact digests.
2. Synchronize README, architecture, operator guide, parent final design/overall plan and
   cleanup inventory.
3. Run mandatory `neat-freak` and mark P7 closed only after both Core and App pass.

## 8. Validation Matrix

Core must cover:

- task registry/version compatibility and exact privacy-class intersection;
- criteria candidate without caller IDs or authority fields;
- decision criterion closure and included/excluded/uncertain behavior;
- current/stale Question, Paper, criteria and optional Card basis;
- generic approval rejection and dedicated user-only approval;
- exact replay, revision lineage, crash recovery and concurrent successor conflict;
- no-criteria mapping compatibility and unchanged deterministic P7-D1 direct operations;
- Guardian, Catalog unaffected semantics, build and installed-wheel smokes.

App must cover:

- Host/Origin/session/CSRF and closed request schemas;
- no browser paths, Core handles, source refs, fingerprints or task authority internals;
- manual criteria and decision flows without Agent;
- Codex/Claude handoff, escaped preview and dedicated approval;
- uncertain/stale/conflict blocking and recovery after restart;
- Library inclusion remaining independent in labels and navigation;
- package-mode synthetic end-to-end canonical criteria and decision commit;
- desktop/mobile text fit, overflow and no incoherent overlap.

## 9. Stop Boundary

P7-D2 does not add mandatory screening, scientific credibility scoring, Evidence quality
review, source-document/full-parse Agent payloads, embedded Agent execution, subtype-specific
systematic-review protocols, PRISMA generation, Research Synthesis, citation/reference
graph, new Discovery providers, Exchange, Obsidian runtime, migration, legacy cutover,
private scientific workspace access, legacy-question access, real PDFs or cleanup deletion.

Research Synthesis remains P8. Citation/reference graph remains post-R2.
