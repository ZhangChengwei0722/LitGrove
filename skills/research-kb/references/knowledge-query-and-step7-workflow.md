# Knowledge Query And Step 7 Workflow

Use this workflow only with an initialized existing workspace. Knowledge queries are ephemeral by default. Step 7 persistence is a separate, explicit route.

## Contents

1. Intent gate
2. Selector and read plan
3. Single-paper understanding
4. Cross-paper comparison
5. Claim trace-back
6. Research directions and review gaps
7. Persisted Step 7 maintenance
8. Candidate reconciliation
9. Candidate type order
10. Review and question boundaries
11. Failure and reporting rules

## 1. Intent Gate

Classify exactly one mode before any mutation:

```text
ephemeral_query
explicit_step7_maintenance
full_workflow_step7_refresh
```

`ephemeral_query` covers ordinary explanations, comparisons, trace-back and research discussion. It is read-only and must report:

```yaml
persistent_writes: 0
```

This direct Skill route is distinct from an exact App-generated `knowledge_query_report`
handoff. The latter is handled only by the App Agent Task response workflow and may be
retained by Core as bounded operational Task history after App preview and user approval;
it still performs zero canonical scientific writes.

`explicit_step7_maintenance` requires an explicit create, refresh, revise, reject or render request for one existing question.

`full_workflow_step7_refresh` applies only when the active intake request explicitly asks to continue through Step 7 and Guardian after an authorized Question Mapping exists.

If persistence intent is ambiguous, use `ephemeral_query`. A supplied question for reading or mapping alone is not permission to persist Step 7.

## 2. Selector And Read Plan

Require one of:

- one paper ID;
- an ordered set of paper IDs;
- one existing question ID;
- an equivalent selector already resolved in this active task.

For query and maintenance, call `workspace init --dry-run` only. The public dry-run result is `planned` even for an initialized workspace. Continue only when every managed action is `already_present` except the expected planned `acquire_workspace_lock` action. Never call operational `workspace init` from these modes. If any `create_directory`, `write_identity_marker`, `upgrade_identity_marker` or other change appears, stop and report the separate workspace action instead of changing the workspace as query preflight.

Use structured public reads:

```text
paper context
review context
question list
question show
question render
step7 context
step7 render
parse show
```

For paper selection, read each paper through `paper context`. For a question, call `question show`, then read the linked papers through `paper context`. Use `question render` only as a disposable reading aid. Use `step7 context` only when existing candidates affect the answer or maintenance decision.

Do not parse config or canonical stores. Do not infer IDs from filenames. Use `parse show` only to reopen exact source context; canonical Evidence returned by `paper context` already carries the normal trace-back chain.

## 3. Single-Paper Understanding

Use the section order and IDs stored in the Paper Card. When the same active intake task already has the `intake inspect` section projection, reuse its labels. A standalone `paper context` query may not expose display labels; in that case preserve section IDs and order. Do not invent section labels or parse the domain profile as a fallback.

For the default seven-section profile, cover the returned equivalents of background/significance, research problem, method principle/advantages, conclusions/applications, innovation, limitations and future outlook. A different domain profile may use different IDs or sections; preserve its stored structure.

Use `grounded` and `revised` Units as factual support. Label `interpretive` and `background_only` Units when they add context. Do not present `needs_resolution` Units as conclusions.

For an overview or method explanation, select only the Units needed for the request. Reopen parsed pages when the user asks for exact surrounding text or when a locator needs explicit inspection.

## 4. Cross-Paper Comparison

Choose one explicit analysis operator:

```text
aggregate
compare
contrast
causal_chain_check
contradiction_check
gap_detection
method_transfer
```

Compare selected grounded/revised Card Units on a shared dimension. Keep these outputs separate:

- agreement or recurring pattern;
- difference in method, scope, mechanism or result;
- non-comparability caused by different systems or measurements;
- missing evidence;
- review queue boundaries.

Absence of a result is not a contradiction. A difference in wording is not necessarily a scientific conflict. Return the supporting paper and Card Unit IDs. Expand selected claims to canonical Evidence when the user requests provenance or when persistence is proposed.

## 5. Claim Trace-Back

Resolve a narrow claim to one or more relevant Card Units, then return:

```text
paper_id
card_unit_id
evidence_id
source_page.pdf_page
source_page.printed_page
source_page.section
locator
quote
support_scope
what_it_does_not_support
```

Use canonical Evidence only. If the claim maps only to a boundary or unresolved Unit, say so and return the applicable queue record separately. Review queue records are boundaries, not support.

Do not recursively verify every citation cited by a review. Review excerpts and paraphrases cannot satisfy primary Evidence trace-back.

## 6. Research Directions And Review Gaps

Start from grounded/revised primary Card Units. Review Memory may add labeled background framing, assay guardrails, terminology or primary-paper leads, but Review Memory cannot become primary support.

Return each idea as an ephemeral candidate draft with:

```yaml
candidate_type:
analysis_operator:
paper_card_base:
missing_evidence:
assumptions:
risk:
testability:
next_action:
not_fact: true
persistence_status: report-only
```

Do not allocate a candidate ID. Do not persist merely because the draft resembles Synthesis, Review Angle, Insight or Cross-View.

New Research Question candidates also remain report-only until explicit user approval through the existing question-authority flow.

## 7. Persisted Step 7 Maintenance

Require one existing question. Execute:

```text
step7 context
-> inspect mapping, candidates and freshness
-> paper context for affected mapped papers
-> select only mapped grounded/revised Card Units
-> choose an analysis operator
-> reconcile against current candidates
-> record promote through stdin when a write is warranted
-> step7 context again
-> step7 render when requested or useful
-> Guardian read-only
```

Use only:

```text
research-kb record promote --workspace <config> --request - --actor agent
```

Do not write Step 7 JSONL directly. Do not submit candidate IDs on append or submit Core-owned type, Evidence closure, queue closure, snapshots, timestamps or candidate-only status constants.

Stale upstream state is a reassessment prompt. Reread current affected Card Units before replace. Do not refresh an unrelated current candidate.

## 8. Candidate Reconciliation

Compare the proposed semantic content to every candidate returned by `step7 context` for the question.

- **exact rerun**: write nothing; report `no_change` and the existing ID.
- **same idea, changed content or support**: replace the existing Agent-owned candidate.
- **materially distinct idea**: append one new candidate.
- **uncertain near-duplicate**: stop before mutation and report the candidate pair.
- **unsupported new idea**: keep it report-only rather than manufacturing a weak persisted record.

Do not append a version history. A full refresh may correctly produce zero writes.

On replace, use the current candidate ID returned by `step7 context` as `target_record_id`. Preserve the candidate type and question. Let Core preserve identity and recompute support closure.

## 9. Candidate Type Order

For a requested complete refresh, evaluate in order:

1. `Synthesis`: bounded agreement, conflict, scope and boundary across at least two papers.
2. `Review Angle`: one organizing thesis, axes, included clusters, excluded scope and added value.
3. `Insight`: one testable hypothesis, method transfer, experiment, comparison or application idea.
4. `Cross-View`: one relation among existing current same-question source candidates.

Cross-View must run after its sources. Do not reference rejected, stale, cross-question or nonexistent candidates.

Every persisted candidate retains missing evidence, assumptions, risk, testability, next action and trace status. Core injects `not_fact: true`, `review_status: ai_draft` and `automation_status: pending`.

## 10. Review And Question Boundaries

Review Memory is background-only. It may shape an ephemeral discussion, but Review Units cannot enter Question Mapping, `paper_card_base`, `evidence_base` or persisted Step 7.

Step 7 cannot create a question. Use an existing Question Mapping or one created from a directly supplied or explicitly approved question. Keep all generated question candidates report-only.

## 11. Failure And Reporting Rules

Stop the maintenance task on workspace/layout conflict, incomplete transaction, mutation-unsafe state, unresolved mapping, structural Step 7 error or Cross-View source failure. Do not fall back to direct files.

For an ephemeral query, report selector, query type, Card Unit base, whether Evidence was expanded, answer, non-evidence boundaries, unresolved items and `persistent_writes: 0`.

For maintenance, report before/after freshness, append/replace/no-change/near-duplicate counts, Core-returned candidate IDs, render status, Guardian status and finding codes. The task report remains private and non-canonical.
