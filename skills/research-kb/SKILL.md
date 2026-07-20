---
name: research-kb
description: >-
  Orchestrate the deterministic Research KB CLI for an existing workspace: initialize or validate its managed layout, ingest local primary-research PDF files, resume or inspect paper status, build and ground a Paper Card with canonical evidence, map a user-supplied or approved question, and run Guardian. Use for local primary-paper intake, evidence grounding, pipeline resume or status inspection, supplied-question mapping, or Guardian checks. Do not use for review papers, literature discovery or downloads, Step 7, manuscript writing, migration, or creating workspace configuration.
---

# Research KB

Use the existing Core CLI as the deterministic execution layer. Keep scientific reading and candidate judgment in the active Agent, while Core owns paths, hashes, IDs, validation, writes, transactions and Guardian.

## Load References

- Read [CLI contract](references/cli-contract.md) before invoking commands or interpreting diagnostics.
- Read [local intake workflow](references/local-intake-workflow.md) for intake, resume, grounding and question mapping.
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
5. Call `paper status` and `paper context` before deciding whether to resume or mutate.
6. Parse only with the explicitly available `pdfplumber` adapter, then read text through `parse show`.
7. Classify the document in task memory. Continue only for high-confidence primary research or an explicit user-supplied primary type.
8. Draft the question-independent Paper Card in memory using the ordered sections returned by intake preflight.
9. Ground factual Units to exact page, character locator and quote provenance. Send unsupported or overstrong candidates to review queue.
10. Promote Evidence and queue records before the complete Card only to obtain Core-owned IDs.
11. Recover Card Unit IDs with `paper context` and map only a user-supplied or explicitly approved question.
12. Run Guardian read-only and return the bounded task report.

Reuse current records on rerun. Do not append a candidate when an exact existing record can be reused. Stop on uncertain near-duplicates, stale upstream state, unsafe integrity or unresolved transactions.

## Hard Boundaries

Do not parse workspace or domain-profile configuration. Do not read canonical JSON or JSONL files directly. Do not allocate IDs, write canonical stores, call an LLM API, move source files or infer success from file presence.

Review processing is not implemented. Step 7 is not implemented. Discovery, acquisition, OCR, figure or table interpretation, supplementary-data processing, manuscript audit and migration are outside this Skill.

Never assign `human_checked`, `verified`, final screening or source-disposition authority. Review queue records are boundaries, not evidence.
