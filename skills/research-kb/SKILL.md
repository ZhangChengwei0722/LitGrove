---
name: research-kb
description: >-
  Orchestrate the deterministic Research KB CLI for an existing workspace: initialize or validate its managed layout, ingest local primary-research or review PDF files, resume paper state, build a grounded Paper Card or background-only Review Memory, map an approved primary-paper question, and run Guardian. Use for local paper intake, primary evidence grounding, review reading memory, pipeline resume, status inspection, supplied-question mapping, or Guardian checks. Do not use for literature discovery or downloads, Field Map integration, Step 7, manuscript writing, migration, or creating workspace configuration.
---

# Research KB

Use the existing Core CLI as the deterministic execution layer. Keep scientific reading and candidate judgment in the active Agent, while Core owns paths, hashes, IDs, validation, writes, transactions and Guardian.

## Load References

- Read [CLI contract](references/cli-contract.md) before invoking commands or interpreting diagnostics.
- Read [local intake workflow](references/local-intake-workflow.md) for intake, resume, grounding and question mapping.
- Read [review intake workflow](references/review-intake-workflow.md) before routing or processing a review-like document.
- Read [authority and failure boundaries](references/authority-and-failure-boundaries.md) before any mutation and whenever a command fails.
- Read [task report contract](references/task%2Dreport-contract.md) before returning results.

## Required Inputs

Require:

- an existing workspace config path;
- one or more absolute local PDF paths;
- optional bounded bibliography metadata;
- optional user-supplied document type;
- optional user-supplied or explicitly approved Research Question.

Initialize only the managed layout described by an existing config. Do not create workspace or domain-profile configuration.

## Execute

1. Read applicable project and workspace agent rules.
2. Call capability and workspace preflight before source processing.
3. Process sources sequentially; never schedule the same source concurrently.
4. Resolve each source with `intake inspect` and follow its exact registration state.
5. Call `paper status`, `paper context` and `review context` before deciding whether to resume or mutate.
6. Parse only with the explicitly available `pdfplumber` adapter, then read text through `parse show`.
7. Classify the document in task memory and route only high-confidence primary research, a supported high-confidence review subtype, or a user-supplied supported type.
8. For primary research, follow the local intake workflow to ground Evidence and persist one question-independent Paper Card.
9. For a review, follow the review intake workflow to retain only reusable, background-only Units with page/section provenance.
10. Keep the primary and review routes mutually exclusive for one paper.
11. Map questions only from primary Paper Card Units and only when user-supplied or explicitly approved.
12. Run Guardian read-only and return the route-appropriate bounded task report.

Reuse current records on rerun. Do not append a candidate when an exact existing record can be reused. Stop on uncertain near-duplicates, stale upstream state, unsafe integrity or unresolved transactions.

## Hard Boundaries

Do not parse workspace or domain-profile configuration. Do not read canonical JSON or JSONL files directly. Do not allocate IDs, write canonical stores, call an LLM API, move source files or infer success from file presence.

Review Memory is background-only and cannot become canonical Evidence, Question Mapping support or Step 7 support. Subtype-specific review schemas, Field Map integration, Review Unit Question Mapping and Step 7 are not implemented. Discovery, acquisition, OCR, figure or table interpretation, supplementary-data processing, manuscript audit and migration are outside this Skill.

Never assign `human_checked`, `verified`, final screening or source-disposition authority. Review queue records are boundaries, not evidence.
