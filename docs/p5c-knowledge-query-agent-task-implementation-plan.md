# P5-C Knowledge Query Agent Task Implementation Plan

- status: `approved_for_unattended_implementation`
- prepared_at: `2026-08-02`
- branch: `feature/p5c-knowledge-query-tasks`
- baseline: `main@7bd94f1152d831a84ffc23b01b7da7bac76315a8`
- master_plan: `2026-08-02-local-research-workspace-manager-p5c-knowledge-query`
- target_application_service_interface: `1.9`
- target_agent_registry: `p5c-v1`
- canonical_scientific_schema_change: false
- operational_schema_change: true
- workspace_layout_change: false
- migration: false
- domain_specific_private_workspace_access: false
- next_gate: `tests_then_core_implementation`

## Objective

Extend the existing external Agent Task runtime with one report-only Knowledge Query kind:

```text
committed Library selectors
-> Core admissibility snapshot
-> manual Codex/Claude handoff
-> contract-bound answer blocks
-> App preview
-> report accepted / revision requested / rejected
```

The Task creates operational Agent Task revisions only. It cannot write Paper Card,
Evidence, Review Memory, Question Mapping or Research Synthesis records.

## Contract Slice

1. Add `p5c-v1` as an additive registry version. Keep `p4a-v1` through `p4c-v1`
   behavior unchanged.
2. Register one `knowledge_query_report` Task kind with six `query_type` operations.
3. Add `knowledge-query-report@1.0` and a query-specific Agent Task input basis.
4. Require `paper_card_content`, `canonical_evidence` and `operational_context`; allow
   metadata, Review background and routing context only when policy permits.
5. Exclude parsed excerpts, source documents and existing Research Synthesis from this
   Task payload.
6. Add a per-kind result-byte budget and enforce the tighter kind/workspace limit.
7. Add terminal reason `report_accepted`, no applied Pipeline Job state, and
   `retention_class: current_task_report` in public Task projection.

One result schema contains ordered answer blocks with roles `factual`,
`cross_paper_synthesis`, `background` and `unresolved`. Factual/synthesis blocks require
allowlisted Primary Card Unit plus Evidence groups; background blocks accept only
allowlisted Review Unit refs; unresolved blocks do not claim support.

## Deterministic Context Slice

Add a focused Knowledge Query context builder consumed by `AgentTaskApplicationService`:

- validate query type, bounded text and ordered paper cardinality;
- load and validate workspace records through existing public helpers;
- select only active Primary Card Units with `grounded` or `revised` status;
- retain only canonical Evidence whose exact owning revision is active and whose source
  trace is available/current;
- return stale, unavailable or otherwise excluded records as reason-only descriptors;
- include Review Units only as optional `background_only` context;
- include current/stale-labeled Question Mapping only as optional routing context;
- bind the input basis to exact paper, revision, Card/Evidence, Review and mapping digests;
- rederive the basis before inspect, lease, submit, revision and acceptance.

The builder returns IDs and safe scientific content only. It never returns local paths,
source refs/fingerprints, parsed pages, PDF bytes, leases or writer authority.

## Task Lifecycle Slice

- add `create_knowledge_query` independent of Pipeline Jobs;
- preserve idempotency and reject a duplicate key bound to different selectors/text;
- generate a self-contained `p5c-agent-handoff@1.0` manifest with the resolved result
  schema and query-specific instructions;
- validate every returned support/background ref against the exact payload allowlist;
- support valid zero-match evidence-find and background-only reports;
- specialize preview for escaped report blocks;
- add `accept_report` with `canonical_scientific_write: false` and no Job transition;
- reuse revision and reject transitions, preserving exact lineage and result digest;
- make refresh unsupported in P5-C; the user creates a new Task after selector changes;
- keep the bounded result in append-only Task history until P11 archive/compaction.

Guardian must accept report-only Tasks without Pipeline Job ownership, reject canonical
output claims from them and preserve all existing checks for route/Primary/Review Tasks.

## Portable Skill Slice

Edit only the configured CC Switch authoring source, then export and verify the repository
snapshot and generate the Codex mirror. Extend `app_agent_task_response` guidance for the
new Task kind; do not add another route or copy the Core schema. Direct `ephemeral_query`
remains zero-write, while App-managed report persistence remains operational only.

## Tests First

Write failing focused tests in this order:

1. registry/schema/config backward compatibility and P5-C budgets;
2. query cardinality, privacy intersection and deterministic input basis;
3. admissibility filtering for current Primary, stale source, Review background and
   excluded context;
4. handoff payload privacy and prompt-injection boundary;
5. result support/background closure, zero-match report and stale-submit rejection;
6. report acceptance/revision/reject/idempotent replay with zero scientific writes;
7. Guardian and existing Task-kind compatibility;
8. installed-wheel interface/capability smoke and portable Skill contract.

Run small focused pytest targets serially during implementation. The phase-start registry
baseline is `3 passed`. Two broad baseline attempts reached external command time limits
without a test failure or summary, so the final complete suite receives one dedicated long
timeout rather than overlapping pytest processes.

## Validation And Delivery

- focused P5-C tests;
- complete pytest with the four expected POSIX-only skips classified separately;
- compileall, sdist/wheel build, base and PDF installed-wheel smokes;
- package/version/capability checks;
- privacy scan with zero unexpected findings;
- source and scientific-tree byte-identity checks;
- portable Skill source/snapshot/mirror digest equality;
- `git diff --check`, diff review, validation receipt and closure manifest.

Commit implementation and closure separately. Push/review/merge under standing
authorization when GitHub is available, validate final `main`, and build the exact merged
wheel for the App pin. Do not modify the older dirty Core worktree.

## Stop Boundary

Do not add a second report database, embedded Agent runtime, API credentials, parsed-text
query, source-document payload, new Evidence grounding, Research Synthesis refresh,
Question/Direction proposal, Discovery, Exchange, Obsidian, migration, domain-specific
private workspaces, real PDFs or generated-workspace cleanup.
