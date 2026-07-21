---
name: research-kb
description: >-
  Orchestrate the deterministic Research KB CLI to search public paper metadata on demand, persist explicitly user-selected discovery candidates, explicitly acquire eligible Europe PMC OA PDFs into a configured local_inbox, or operate an existing workspace: ingest primary-research or review PDF files, resume paper status, build evidence-traceable records, map approved questions, answer read-only knowledge queries, run paper comparison and claim trace-back, discuss research directions, maintain Step 7 candidates, and run Guardian. Use for bounded literature discovery, approved candidate handoff or OA acquisition, local intake, Paper Card, Review Memory, Evidence Grounding, question-scoped query work, or controlled Step 7 refresh. Do not use for arbitrary or institutional downloads, Field Map integration, manuscript writing, migration, or creating workspace configuration.
---

# Research KB

Use Core CLI as the deterministic execution layer. Keep scientific reading, comparison and candidate judgment in the active Agent. Core owns paths, hashes, IDs, validation, writes, transactions, provenance closure and Guardian.

## Load References

- Read [CLI contract](references/cli-contract.md) before invoking commands or interpreting diagnostics.
- Read [on-demand discovery workflow](references/discovery-workflow.md) before public metadata search.
- Read [local intake workflow](references/local-intake-workflow.md) for intake, resume, grounding and question mapping.
- Read [review intake workflow](references/review-intake-workflow.md) before routing or processing a review-like document.
- Read [knowledge query and Step 7 workflow](references/knowledge-query-and-step7-workflow.md) before answering from an existing workspace or maintaining Step 7.
- Read [authority and failure boundaries](references/authority-and-failure-boundaries.md) before mutation and whenever a command fails.
- Read [task report contract](references/task%2Dreport-contract.md) before returning results.

## Select One Mode

Classify the invocation mode before any mutation:

- `local_intake`: one or more absolute local PDF paths, with an optional supplied or approved question.
- `on_demand_discovery`: one explicit date range and bounded title/abstract keywords; search remains report-only unless the user explicitly selects result keys for candidate handoff.
- `explicit_oa_acquisition`: exact already-selected candidate IDs that the user explicitly asks to acquire through the supported Europe PMC OA route.
- `ephemeral_query`: one paper, selected papers or one existing question; all answers remain in the task report only.
- `explicit_step7_maintenance`: the user explicitly requests Step 7 create, refresh, revise, reject or render work for one existing question.
- `full_workflow_step7_refresh`: an intake request explicitly asks for the complete workflow through Step 7 and Guardian.

If persistence intent is unclear, use `ephemeral_query` or intake without Step 7 persistence. Ordinary knowledge queries never persist a Question Mapping, Step 7 candidate, Markdown view, answer, cache or report.

## Required Inputs

Require an existing workspace config for intake, query, Step 7, approved discovery-candidate handoff and explicit OA acquisition. Discovery search is workspace-independent.

For intake, require absolute PDF paths and accept bounded bibliography, a supplied document type and a supplied or explicitly approved question. For queries, require a paper ID, ordered paper IDs, an existing question ID or an equivalent selector already resolved in the active task. For Step 7 maintenance, require one existing question ID.

For discovery, require explicit inclusive dates, field-bound title/abstract keywords, `any` or `all`, a preprint choice and `max_results` from 1 through 15. Resolve relative dates in the Agent before CLI invocation.

Initialize only the managed layout described by an existing config. Do not create workspace or domain-profile configuration.

## Execute Discovery

Call `capability show`, require the built-in `europe-pmc` connector, then send one bounded JSON request to `discovery search --provider europe-pmc --request -`. Return 0-15 normalized metadata results exactly as filtered, including a true zero-result outcome. Search keeps `persistent_writes: 0`.

Show the results before any write. If and only if the user explicitly names selected `result_key` values, preserve the complete report, require an existing workspace, resolve optional question labels through `question list/show`, and call `discovery select --request - --actor user`. Re-read through `discovery list/show`.

When the active task asks only whether one selected candidate has a supported OA route, require `legal_oa_resolution: true`, call `discovery resolve --provider europe-pmc`, report the status with `persistent_writes: 0`, and stop.

For `explicit_oa_acquisition`, require exact candidate IDs named by the user, `explicit_oa_acquisition: true`, an available `pdfplumber`, and a no-change workspace preflight. Re-read each candidate, call `discovery resolve`, then call `discovery acquire --provider europe-pmc --actor user`; Core re-resolves again before writing. Re-read the candidate, run Guardian, report only the portable `source_ref` and receipt facts, and stop before Registry or intake. Never infer this authority from selection or `auto_acquisition_eligible` alone.

## Execute Intake

1. Read applicable project and workspace rules.
2. Call capability and workspace preflight.
3. Process sources sequentially and resolve each with `intake inspect`.
4. Call `paper status`, `paper context` and `review context` before resume or mutation decisions.
5. Parse only through explicit available `pdfplumber`, then read through `parse show`.
6. Classify in task memory and choose one mutually exclusive primary or review route.
7. Ground a question-independent primary Card or retain a background-only Review Memory.
8. Map only a user-supplied or explicitly approved question from primary Card Units.
9. Run Step 7 only for `full_workflow_step7_refresh`; otherwise stop after Guardian/reporting.
10. Run Guardian read-only and return the route-appropriate report.

## Execute Queries And Step 7

For query and maintenance modes, call capability plus `workspace init --dry-run`. Allow only `already_present` plus the planned `acquire_workspace_lock` action; the dry-run result itself remains `planned`. Never call operational `workspace init` from these modes. Stop if initialization, adoption, upgrade or repair would be required.

For `ephemeral_query`, start from grounded/revised Paper Card Units returned by `paper context`. Expand to canonical Evidence for trace-back or when exact support matters. Review Memory may inform labeled background discussion but cannot become primary support. Return `persistent_writes: 0`.

For persisted maintenance, call `step7 context` first, select only grounded/revised Units in the current Question Mapping, reconcile against existing candidates, and use `record promote --request - --actor agent`. Exact reruns write nothing. Replace an existing candidate when the same idea changes; append only a materially distinct candidate; stop on uncertain near-duplicates. Re-read context, optionally call `step7 render`, then run Guardian.

## Hard Boundaries

Do not parse workspace/domain-profile configuration or read canonical JSON/JSONL directly. Do not allocate IDs, write canonical stores, call an LLM API from Core, move source files or infer success from file presence.

Review Memory is background-only and cannot support canonical Evidence, Question Mapping or persisted Step 7. Review Unit Question Mapping, Field Map integration and subtype-specific review schemas are not implemented. New question candidates remain report-only until explicit approval.

Arbitrary connectors, institutional/browser acquisition, metadata refresh/deletion, OCR, figure/table interpretation, supplementary-data processing, manuscript audit and migration remain outside this Skill. Never infer discovery selection or acquisition, overwrite an existing source, or assign `human_checked`, `verified`, final screening or source-disposition authority. Review queue records are boundaries, not evidence.
