# P8 Research Synthesis Implementation Plan

- status: `short_review_passed_for_unattended_implementation`
- date: `2026-08-03`
- core_baseline: `main@16013d5c58cf4f592129bb93f07425adf522b32d`
- app_baseline: `feature/p7d-screening-work-surface@6efdbd54189609c75734e0c063164641c5fc77ec`
- delivery_split: `P8-A Core and Portable Skill; P8-B localhost App`
- q001_access_authorized: false
- private_workspace_access_authorized: false
- migration_authorized: false
- next_gate: `short_review_then_p8a_tests_first`

## Goal

Expose explicit, traceable maintenance for the existing four Research Synthesis candidate
types without turning ordinary Knowledge Query into a write path or allowing Review Memory
to become factual support.

The existing `step7-*` candidate stores, IDs, schemas, CLI context/render commands and
deterministic promotion service remain the compatibility substrate. P8 adds the current P7
Question basis, labeled Review-background closure, a dedicated external-Agent Task route,
App preview/approval and complete synthesis freshness. It does not rename or migrate
existing stores.

## Entry Conditions

- P7 is closed at Core `16013d5` and local App closure `6efdbd5`.
- The active Question projection is resolved through the P7 revision journal when present;
  a legacy Question mapping remains readable when no successor exists.
- Existing `Step7CandidateService`, `Step7ContextService`, `candidate_freshness` and
  `step7 render` behavior is characterized before extension.
- The App continues to use only Core public Application Services and never allocates
  candidate IDs or writes `knowledge/step7` directly.

## P8-A: Core And Portable Skill

### A1. Active Question And Support Closure

1. Add characterization tests proving Step 7 consumes the active P7 Question revision and
   becomes stale when its factual membership or revision changes.
2. Include the active Question revision/basis in transaction race signatures. A concurrent
   Question change must reject promotion rather than commit against a superseded mapping.
3. Keep factual support restricted to current retained Primary Card Units with
   `grounded | revised` status and their complete canonical Evidence closure.
4. Keep `review_queue` as boundary context only. It never enters `evidence_base`.

### A2. Labeled Review Background

1. Extend the existing Step 7 common contract additively with optional
   `review_background_base` and exact Review Unit snapshot refs. Existing records without
   Review background remain valid and unchanged.
2. A Review Unit is admissible only when it is current, retained, provenance-complete,
   `background_only=true`, `can_enter_canonical_evidence=false`, `not_fact=true`, and linked
   to the active Question as current `question_background`.
3. Core derives and validates Review Memory ID, Review revision ID and Review Unit refs.
   Caller-supplied Evidence closure remains forbidden.
4. Review background is rendered in a separately labeled section and can never appear in
   `evidence_base`. A Review or Question-background revision stales only candidates that
   consumed it.
5. `Cross-View` source candidates remain candidate-to-candidate context. They are validated
   separately from Primary/Evidence support and never become Evidence merely by being
   referenced.

### A3. Dedicated Agent Task

1. Register `p8-v1` with available `research_synthesis_drafting`, preserving all previous
   registry versions. Required content classes are `paper_card_content`,
   `canonical_evidence`, `research_routing_context` and `operational_context`; optional
   classes are `review_background`, `research_synthesis` and `metadata`.
2. Add one versioned result contract covering `synthesis`, `review_angle`, `insight` and
   `cross_view`. One Task belongs to one Question, one candidate type and one explicit
   `append | replace` maintenance intent. Replace binds one current target candidate.
3. Build a bounded payload from the current Question mapping, selected admissible Primary
   Units, exact canonical Evidence, optional current Review background and existing
   candidates needed for duplicate/replace comparison.
4. Preserve external-manual Codex CLI or Claude Code CLI handoff. No provider credential,
   subprocess launch or embedded Agent runtime is added.
5. Submission validates all selected refs against the Task allowlist. Generic approval
   rejects synthesis Tasks; dedicated approval alone calls the deterministic candidate
   promotion service after exact basis revalidation.
6. Revision, rejection, stale-submit rejection and successor lineage reuse the existing
   Agent Task contract. Exact deterministic reruns are no-change; an uncertain semantic
   near-duplicate is approval-blocked and must be revised or rejected.

### A4. Public Application Service And Skill

1. Add a `ResearchSynthesisApplicationService` for bounded Question/candidate reads,
   current context and limits. Browser-safe projections omit paths, transaction internals
   and unrestricted canonical payloads.
2. Extend `AgentTaskApplicationService` with dedicated create and approval methods. Bump
   the Application Service interface only once after the complete additive contract is
   stable.
3. Update capability projection, agent protocol and Portable Skill references to use the
   user-facing name `Research Synthesis` while retaining internal `step7-*` identifiers.
4. The Skill consumes exact App handoffs or explicitly requested direct CLI maintenance;
   it never treats ordinary query output as a maintenance request.

## P8-B: Localhost App

1. Pin an exact reviewed P8-A Core wheel and fail closed on interface, feature or digest
   mismatch.
2. Add session/CSRF/request-budget adapters for synthesis context, Task creation and
   dedicated approval. Generic Agent approval remains forbidden for synthesis Tasks.
3. Turn the existing read-only `综合` surface into a dedicated maintenance workspace that
   still lists current and stale candidates. Users select one Question, one of the four
   types, append or replace intent, source Units/background policy and external Agent.
4. Show the exact escaped payload and prompt manifest, import bounded JSON, and expose
   revision/reject/dedicated approval. Stale, unresolved, uncertain-near-duplicate or
   approval-blocked results cannot be approved.
5. Display canonical Evidence separately from labeled Review background and boundary refs.
   `not_fact=true`, `review_status=ai_draft` and `automation_status=pending` remain visible.
6. Keep Knowledge Query unchanged and zero canonical scientific write. No background
   polling or navigation action may refresh candidates implicitly.

## Tests First

### Core

- active P7 Question successor overrides its legacy mapping for context and promotion;
- concurrent Question revision causes stale-submit/promotion rejection;
- all four candidate types append and replace through the dedicated Task;
- exact rerun writes nothing and uncertain near-duplicate cannot approve;
- Review background requires current Question link and grounded same-review provenance;
- Review Unit never enters `evidence_base`; review/background change stales only consumers;
- Primary Unit/Evidence/Question changes propagate expected freshness reasons;
- generic approval rejects synthesis Tasks and dedicated approval is replay-safe;
- `p4a-v1` through `p7d-v1` registry projections remain unchanged;
- Guardian, Catalog, CLI context/render, wheel smoke and privacy/path scans regress cleanly.

### App

- authentication, CSRF, path-shaped ID and request-budget failures;
- exact request serialization and Core-error mapping;
- four-type append/replace, external handoff, escaped preview and explicit approval;
- stale/background/boundary rendering and approval-blocked states;
- Knowledge Query creates no synthesis write;
- development and exact installed-package Edge E2E;
- desktop and `390x844` overflow/screenshot inspection;
- full Python, Vitest, TypeScript, ESLint, production build, package smoke and
  `git diff --check`.

## Delivery And Closure

1. Commit this reviewed plan before implementation.
2. Implement and fully validate P8-A in a small Core commit; create/merge one Core PR.
3. Build the exact merged-head Core wheel and record its digest.
4. Implement and fully validate P8-B in the local App repository; the App receives no
   remote.
5. Record validation receipts, closure manifests and generated-artifact cleanup candidates.
6. Run `neat-freak`, reconcile final design/overall plan/roadmap, then select P9.

## Boundaries

- no Q001, private scientific workspace, real PDF or legacy CLI mutation;
- no schema/store rename, bulk migration, write freeze or cutover;
- no automatic synthesis on intake, mapping, query, navigation or startup;
- no Review Memory in canonical Evidence and no scientific credibility score;
- no Obsidian, Exchange, citation graph, provider expansion or manuscript review runtime;
- no embedded Agent execution, credentials, deployment or generated-artifact deletion.
