# P7-D Question-Specific Screening Implementation Plan

- status: `short_review_passed_for_unattended_implementation`
- prepared_at: `2026-08-03`
- core_baseline: `main@59d4c004a84acd7f87bc204907882b11fa590f64`
- app_baseline: `feature/p7c-tags-work-surface@ad60a79794d83026f7424e36042607c4391036a9`
- core_branch: `feature/p7d-question-screening`
- app_branch: `feature/p7d-question-screening-work-surface`
- parent_design: `bounded P7 organization and screening design maintained outside this shared repository`
- canonical_schema_change: true
- operational_schema_change: true
- migration: false
- private_scientific_workspace_access: false
- next_gate: `tests_then_p7d1_deterministic_kernel`

## 1. Objective

Add optional, auditable screening for one explicit research Question without changing the
ordinary Library workflow:

```text
Library-included Paper
-> Registry / Parse / semantic processing remains available

explicit Question-specific screening request
-> versioned criteria
-> bounded Agent proposal or direct user decision
-> App preview
-> explicit user approval
-> append-only Question-Paper decision
-> criteria change makes dependent decisions stale
```

Screening decides membership in one Question-specific review or screening set. It does not
judge whether a Paper's findings are scientifically true, does not create Evidence, and
does not replace Question Mapping.

## 2. Frozen Boundaries

1. `library_status` and Question-specific screening are independent. A Paper supplied by
   the user or accepted into the Library may be processed before, during or after screening.
2. Screening exists only for Questions with an explicitly created criteria set. Questions
   without criteria retain the existing P7 behavior.
3. Criteria and decisions are local canonical organization records with stable Core-owned
   IDs and append-only revisions. No accepted revision is overwritten or deleted.
4. A decision binds one exact `question_id + paper_id + criteria_revision_id +
   criteria_digest`. A criteria successor makes older decisions `stale_criteria`; it does
   not silently remap or delete them.
5. Final `included` and `excluded` decisions require explicit user approval. An Agent may
   propose an outcome and rationale but cannot exercise final screening authority.
6. Screening records are not Evidence and cannot support factual Knowledge Query or
   Research Synthesis. They may constrain which Papers are eligible for a strict Question
   workflow.
7. No generic `needs_resolution` queue is introduced. Ambiguous screening remains an
   Agent Task/user decision; structural, reference or transaction failures remain Guardian
   findings; source inadequacy remains a Pipeline Job concern.

## 3. Criteria Semantics

One criteria set belongs to one stable Question identity. A retained criteria revision is
user-authored or explicitly user-approved and contains only bounded screening policy:

- a short title and scope;
- ordered inclusion criteria;
- ordered exclusion criteria;
- optional notes defining population, intervention/exposure, study design, date/language
  or source-type boundaries when relevant;
- status `active | archived`;
- approval and predecessor provenance.

Core allocates stable criteria and criterion IDs. Criteria item identity remains stable
within a successor revision when the user explicitly retains it; added or removed items
are auditable. The canonical digest covers order and content. Only one active criteria set
may govern a Question at a time.

Creating or revising criteria does not automatically screen Papers. Archiving criteria
prevents new final decisions but retains existing decision history.

## 4. Decision Semantics

One decision identity belongs to one `question_id + paper_id` pair. Its append-only
revision contains:

- outcome `included | excluded`;
- exact governing criteria revision/digest;
- bounded criterion-level dispositions and rationale;
- basis scope `metadata | available_abstract | paper_card | user_full_text_review | mixed`;
- known limitations and user approval provenance.

Core validates the Question and canonical Paper identities, active criteria head and
criteria item references under the workspace transaction lock. Exact replay is no-change;
stale expected heads or stale criteria are rejected. A criteria successor projects prior
decisions as `stale_criteria` until explicitly reconfirmed or replaced.

Decision reads distinguish `current`, `stale_criteria`, `paper_unavailable` and
`question_unavailable`. Historical revisions remain readable. Registry identity correction
may project aliases, but a decision never silently changes its canonical Paper owner.

## 5. Relation To Question Mapping

Screening and Question Mapping remain distinct:

- Screening answers whether a Paper belongs in one strict Question set.
- Question Mapping selects admissible Card Units and Evidence roles for scientific use.

When a Question has no active criteria, existing mapping behavior is unchanged. When a
Question has active criteria, a new factual mapping or mapping successor may use only a
Paper with a current `included` decision. `excluded` or `stale_criteria` blocks new factual
mapping. Existing mappings are retained; mappings that recorded an older screening basis
project stale and require explicit revision rather than in-place repair.

Screening status never upgrades Card Unit/Evidence admissibility and never turns Review
Memory into factual support.

## 6. Storage, IDs And Dependencies

Add optional stores under the existing organization root:

```text
knowledge/organization/screening_criteria/by_id/<criteria_id>.screening-criteria-bundle.json
knowledge/organization/screening_decisions/by_id/<decision_id>.screening-decision-bundle.json
```

Add stable namespaces for criteria, criterion item, criteria revision, decision and
decision revision. Reuse canonical serialization, approval, predecessor digest,
transaction recovery and workspace-lock conventions.

Freeze these freshness edges before writers are exposed:

```text
Question availability -> criteria
Question + Paper + criteria revision/digest -> decision
criteria successor -> prior decisions stale_criteria
current included decision -> strict Question Mapping eligibility
decision/criteria successor -> affected Catalog projection stale
```

Bootstrap/upgrade may create only empty optional directories. Existing workspaces remain
readable without migration.

## 7. P7-D1 Deterministic Core Kernel

Implement schemas, IDs, storage/service/Application Service, Catalog projection and
Guardian before adding an Agent Task.

Core services provide bounded list/show plus explicit user-authority operations for:

- create/revise/archive criteria;
- record/revise/reconfirm included or excluded decisions;
- list decisions by Question or Paper;
- show projected freshness and exact criteria basis.

The Application Service remains session-bound with closed requests, stable ID cursors,
bounded page sizes and path-free responses. Advance the interface only when the complete
deterministic contract passes.

Catalog projects criteria and decisions as organization records and adds exact Question,
Paper, outcome and freshness filters. Screening projection is disposable and rebuildable.
Guardian checks revision closure, one active criteria set per Question, criteria item
identity, pair uniqueness, criteria digest binding, Question/Paper availability, stale
projection, transaction recovery and strict mapping eligibility.

## 8. P7-D2 Agent Proposal And App Work Surface

Register two versioned Agent Task kinds only after the deterministic kernel is merged:

- `question_screening_criteria_proposal` proposes bounded criteria for App preview; it
  cannot create or approve a criteria revision.
- `question_screening_decision_proposal` applies one current criteria revision to a
  bounded ordered Paper selection; it cannot create final decisions.

Neither task has a Pipeline Job owner. Criteria proposal binds one Question and the user's
declared screening goal. Decision proposal binds:

- one current criteria revision;
- one bounded ordered Paper selection;
- one declared basis scope limited to metadata, an actually available abstract and/or
  current Paper Card content;
- exact allowed metadata/Card/Review-background payload classes;
- no paths, source refs, parsed page bodies, leases or writer authority.

The criteria Agent returns one candidate criteria set with no authority fields. The
decision Agent returns one candidate outcome per Paper, criterion-level dispositions,
rationale, limitations and exact input-basis binding. `uncertain` is allowed only as a
candidate outcome and cannot be promoted to a final decision. Changed criteria, Paper
identity or semantic basis rejects inspection, submission and approval as stale.

The first Agent contract does not claim complete full-text screening. `user_full_text_review`
is available only for a direct user decision that explicitly records that basis; it is not
an Agent payload capability. Any later parsed-text or source-document screening route must
first define its Source Adequacy capability, privacy class, prompt budget and stale binding
in a separate extension.

The localhost App adds a Question Screening work surface for:

- selecting one Question and viewing current criteria;
- authoring or importing criteria candidate text for App preview and user approval;
- selecting bounded Library Papers for screening;
- generating the Codex CLI/Claude Code CLI handoff prompt;
- importing Agent candidates, reviewing each Paper, revising or rejecting proposals;
- explicitly approving included/excluded decisions;
- filtering current/stale/included/excluded decisions and showing audit history.

Simple deterministic reads and direct user decisions stay inside App/Core. The App does not
embed an Agent runtime. It never treats a proposal as final, and bulk approval must show
the exact count and criteria digest before the user action.

## 9. Tests First

### P7-D1 Core

1. schema, ID and bundle diagnostic tests for criteria and decisions;
2. criteria create/no-change/revise/archive and one-active-set uniqueness;
3. decision create/revise/reconfirm for included/excluded outcomes;
4. exact criteria revision/digest and criterion-item closure;
5. criteria successor makes only dependent decisions stale;
6. Library status and paper semantic processing remain unaffected;
7. stale expected head, concurrent pair creation and transaction recovery;
8. Registry correction, unavailable Question/Paper and immutable history;
9. strict Question Mapping eligibility with no-criteria compatibility;
10. Catalog full/incremental convergence, filters and Guardian findings;
11. session-bound privacy, compatibility and installed-wheel smokes.

### P7-D2 Agent And App

1. versioned task-kind/privacy registry and payload-budget tests;
2. separate criteria-proposal and decision-proposal result contracts and privacy budgets;
3. stale input-basis rejection at inspect/lease/submit/revision/approval;
4. candidate closure, `uncertain` approval blocking and user-only final authority;
5. strict App routes, Host/Origin/CSRF, unknown fields and path-shaped rejection;
6. criteria editor, candidate preview, direct decision and history states;
7. server-side Question/Paper/outcome/freshness filters;
8. desktop/mobile Edge flow with escaped hostile text and no overflow;
9. exact-wheel fresh install, package-mode Edge, privacy and diff review.

## 10. Delivery Sequence

### P7-D1

- implement on `feature/p7d-question-screening` from Core `main@59d4c004`;
- tests first, then deterministic storage/services, dependencies, Catalog and Guardian;
- run focused and complete Windows validation, compile, build, base/PDF wheel smokes,
  privacy scan and diff review;
- commit, push, PR and merge when GitHub is available;
- record exact merge tree and reviewed wheel digest.

### P7-D2

- only after P7-D1 merge, write a narrower Agent/App sub-plan if implementation details
  learned in D1 change the adapter boundary;
- implement the task kind and user-approval route in Core, then pin the exact wheel in App;
- implement App routes and browser work surface;
- run deterministic suites, development/package Edge, fresh install and visual review;
- commit App locally; the App repository has no remote.

### P7-D3

- write validation receipts and closure manifests;
- synchronize repository architecture, contributor and cleanup records;
- retry parent `E:` final-design/overall-plan status sync without blocking closure;
- run mandatory `neat-freak`.

## 11. Stop Boundary

P7-D does not add:

- mandatory screening for ordinary Library intake or Paper processing;
- scientific credibility or evidence-quality scoring;
- automatic criteria generation or final Agent authority;
- Evidence, Paper Card, Review Memory or Research Synthesis generation;
- subtype-specific systematic-review protocols, PRISMA report generation or citation graph;
- Discovery provider expansion, acquisition changes or institutional downloads;
- Exchange identity/merge, Obsidian runtime, embedded Agent execution;
- Q001/private workspace access, real PDFs, migration, cutover or cleanup.

Research Synthesis remains P8. Citation/reference graph remains post-R2.
