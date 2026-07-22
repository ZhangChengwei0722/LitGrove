---
name: research-kb
description: >-
  Orchestrate the deterministic Research KB CLI to search public paper metadata on demand, persist explicitly user-selected discovery candidates, explicitly acquire eligible Europe PMC OA PDFs into a configured local_inbox, or operate an existing workspace: ingest primary-research or review PDF files, inspect or explicitly audit an exact local DOCX/PDF manuscript, resume paper status, build evidence-traceable records, map approved questions, answer read-only knowledge queries, run paper comparison and claim trace-back, discuss research directions, maintain Step 7 candidates, and run Guardian. Use for bounded literature discovery, approved candidate handoff or OA acquisition, local intake, manuscript projection, explicit-criteria manuscript audit, Paper Card, Review Memory, Evidence Grounding, question-scoped query work, or controlled Step 7 refresh. Do not use for arbitrary or institutional downloads, Field Map integration, manuscript rewriting, migration, or creating workspace configuration.
---

# Research KB

Use Core CLI as the deterministic execution layer. Keep scientific reading, comparison and candidate judgment in the active Agent. Core owns paths, hashes, IDs, validation, writes, transactions, provenance closure and Guardian.

## Load References

- Read [CLI contract](references/cli-contract.md) before invoking commands or interpreting diagnostics.
- Read [on-demand discovery workflow](references/discovery-workflow.md) before public metadata search.
- Read [local intake workflow](references/local-intake-workflow.md) for intake, resume, grounding and question mapping.
- Read [review intake workflow](references/review-intake-workflow.md) before routing or processing a review-like document.
- Read [knowledge query and Step 7 workflow](references/knowledge-query-and-step7-workflow.md) before answering from an existing workspace or maintaining Step 7.
- Read [manuscript audit workflow](references/manuscript-audit-workflow.md) before any criterion-based manuscript inspection.
- Read [authority and failure boundaries](references/authority-and-failure-boundaries.md) before mutation and whenever a command fails.
- Read [task report contract](references/task%2Dreport-contract.md) before returning results.

## Select One Mode

Classify the invocation mode before any mutation:

- `local_intake`: one or more absolute local PDF paths, with an optional supplied or approved question.
- `on_demand_discovery`: one explicit date range and bounded title/abstract keywords; search remains report-only unless the user explicitly selects result keys for candidate handoff.
- `explicit_oa_acquisition`: exact already-selected candidate IDs that the user explicitly asks to acquire through the supported Europe PMC OA route.
- `acquired_candidate_intake`: exact acquired candidate IDs that the user explicitly asks to add to the knowledge base; use `registry_only` only when explicitly requested, otherwise resume the existing intake workflow through Guardian.
- `ephemeral_query`: one paper, selected papers or one existing question; all answers remain in the task report only.
- `explicit_step7_maintenance`: the user explicitly requests Step 7 create, refresh, revise, reject or render work for one existing question.
- `full_workflow_step7_refresh`: a local-path or already-acquired intake request explicitly asks for the complete workflow through Step 7 and Guardian.
- `manuscript_projection`: one exact user-supplied DOCX or PDF under a declared source root; return deterministic units and stop without semantic audit.
- `manuscript_audit`: one exact user-supplied DOCX or PDF plus one or more explicit criteria and exact current-request knowledge selectors; return a criterion-scoped private report with zero writes.

If persistence intent is unclear, use `ephemeral_query` or intake without Step 7 persistence. Ordinary knowledge queries never persist a Question Mapping, Step 7 candidate, Markdown view, answer, cache or report.

## Required Inputs

Require an existing workspace config for intake, manuscript projection, manuscript audit, query, Step 7, approved discovery-candidate handoff, explicit OA acquisition and acquired-candidate intake. Discovery search is workspace-independent.

For local-path intake, require absolute PDF paths and accept bounded bibliography, a supplied document type and a supplied or explicitly approved question. For acquired-candidate intake, require exact acquired candidate IDs instead of paths. For queries, require a paper ID, ordered paper IDs, an existing question ID or an equivalent selector already resolved in the active task. For Step 7 maintenance, require one existing question ID.

For discovery, require explicit inclusive dates, field-bound title/abstract keywords, `any` or `all`, a preprint choice and `max_results` from 1 through 15. Resolve relative dates in the Agent before CLI invocation.

For manuscript projection, require one exact absolute user-supplied DOCX or PDF path. The file must already belong to exactly one declared source root.

For manuscript audit, additionally require at least one non-empty criterion and explicit question/paper selectors or exact selectors already present in the current request that can be resolved unambiguously. If criteria or selectors are missing or materially ambiguous, stop before `manuscript inspect`.

Initialize only the managed layout described by an existing config. Do not create workspace or domain-profile configuration.

## Execute Discovery

Call `capability show`, require the built-in `europe-pmc` connector, then send one bounded JSON request to `discovery search --provider europe-pmc --request -`. Return 0-15 normalized metadata results exactly as filtered, including a true zero-result outcome. Search keeps `persistent_writes: 0`.

Show the results before any write. If and only if the user explicitly names selected `result_key` values, preserve the complete report, require an existing workspace, resolve optional question labels through `question list/show`, and call `discovery select --request - --actor user`. Re-read through `discovery list/show`.

When the active task asks only whether one selected candidate has a supported OA route, require `legal_oa_resolution: true`, call `discovery resolve --provider europe-pmc`, report the status with `persistent_writes: 0`, and stop.

For `explicit_oa_acquisition`, require exact candidate IDs named by the user, `explicit_oa_acquisition: true`, an available `pdfplumber`, and a no-change workspace preflight. Re-read each candidate, call `discovery resolve`, then call `discovery acquire --provider europe-pmc --actor user`; Core re-resolves again before writing. Re-read the candidate, run Guardian, report only the portable `source_ref` and receipt facts, and stop before Registry or intake. Never infer this authority from selection or `auto_acquisition_eligible` alone.

For `acquired_candidate_intake`, require exact candidate IDs and an explicit later-task request to add them to the knowledge base. Call `discovery show`, then `intake inspect-acquired`. On `unregistered`, pass the returned `source` and `registry_metadata` unchanged to `registry add --metadata -`; on `registered_current`, reuse the sole paper ID; stop on `registered_stale` or `ambiguous`. Stop after Registry only for an explicit `registry_only` depth. Otherwise continue from `paper status` into the same Parse and mutually exclusive primary/review route used for local intake. Do not infer intake authority from `acquired` alone.

## Execute Intake

1. Read applicable project and workspace rules.
2. Call capability and workspace preflight.
3. Process sources sequentially. Resolve absolute local PDFs with `intake inspect`; resolve exact acquired candidate IDs with `discovery show` plus `intake inspect-acquired`.
4. Call `paper status`, `paper context` and `review context` before resume or mutation decisions.
5. Parse new primary/review records only through explicit available `pdfplumber-text-flow`, then read through `parse show` and stop if the reading order remains ambiguous.
6. Classify in task memory and choose one mutually exclusive primary or review route.
7. Ground a question-independent primary Card or retain a background-only Review Memory.
8. Map only a user-supplied or explicitly approved question from primary Card Units.
9. Run Step 7 only for `full_workflow_step7_refresh`; otherwise stop after Guardian/reporting.
10. Run Guardian read-only and return the route-appropriate report.

## Execute Queries And Step 7

For query and maintenance modes, call capability plus `workspace init --dry-run`. Allow only `already_present` plus the planned `acquire_workspace_lock` action; the dry-run result itself remains `planned`. Never call operational `workspace init` from these modes. Stop if initialization, adoption, upgrade or repair would be required.

For `ephemeral_query`, start from grounded/revised Paper Card Units returned by `paper context`. Expand to canonical Evidence for trace-back or when exact support matters. Review Memory may inform labeled background discussion but cannot become primary support. Return `persistent_writes: 0`.

For persisted maintenance, call `step7 context` first, select only grounded/revised Units in the current Question Mapping, reconcile against existing candidates, and use `record promote --request - --actor agent`. Exact reruns write nothing. Replace an existing candidate when the same idea changes; append only a materially distinct candidate; stop on uncertain near-duplicates. Re-read context, optionally call `step7 render`, then run Guardian.

## Execute Manuscript Projection

For `manuscript_projection`, call capability plus `workspace init --dry-run`, require `manuscript_projection: true` and the `manuscript inspect` read. Accept only no-change preflight, then call `manuscript inspect --workspace <config> --source <absolute.docx|absolute.pdf>`.

Return the source fingerprint, parser identity, stable units, locators and `coverage_limits` in the private task report with `persistent_writes: 0`. Stop after the projection report. Do not perform or claim semantic claim extraction, criteria evaluation, evidence matching or rewriting. Do not register the manuscript or treat it as canonical knowledge.

## Execute Manuscript Audit

For `manuscript_audit`, follow the manuscript audit reference. Preserve the user's exact criteria and do not add default dimensions. Resolve only current-request selectors through public `question list/show` and `paper context` reads, run the same no-change preflight and `manuscript inspect`, then build only the bounded invocation-local map needed by those criteria.

Start knowledge comparison from grounded/revised Card Units. Expand to canonical Evidence for exact factual support, citation checking or wording-strength judgment. Review Memory may provide labeled orientation, but Review Memory, review queue and Step 7 cannot support factual findings. Return only the criterion-scoped private report with `persistent_writes: 0`; do not persist a claim map, finding, cache, report or Markdown, and do not rewrite the manuscript.

## Hard Boundaries

Do not parse workspace/domain-profile configuration or read canonical JSON/JSONL directly. Do not allocate IDs, write canonical stores, call an LLM API from Core, move source files or infer success from file presence.

Review Memory is background-only and cannot support canonical Evidence, Question Mapping or persisted Step 7. Review Unit Question Mapping, Field Map integration and subtype-specific review schemas are not implemented. New question candidates remain report-only until explicit approval.

Arbitrary connectors, institutional/browser acquisition, metadata refresh/deletion, OCR, figure/table interpretation, supplementary-data processing, automatic/default-rubric manuscript audit, manuscript rewriting and migration remain outside this Skill. Never infer discovery selection, acquisition or acquired-candidate intake, overwrite an existing source, or assign `human_checked`, `verified`, final screening or source-disposition authority. `discovery acquire` always stops before intake; only a separately explicit acquired-candidate task may resume downstream processing. Review queue records are boundaries, not evidence.
