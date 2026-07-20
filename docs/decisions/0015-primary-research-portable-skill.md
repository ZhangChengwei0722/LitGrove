# ADR 0015: Primary-Research Portable Skill

- status: accepted_for_m3a_1

## Decision

Shared Core repository owns a Portable Agent Skill at `skills/research-kb/`. The first slice orchestrates one existing-config local primary-research PDF workflow through public Core commands and returns a non-canonical task report.

The Skill is procedural Agent guidance. It does not call an LLM API, implement deterministic storage logic, allocate IDs, write canonical files directly or maintain workflow state.

## Package

The package contains `SKILL.md`, `agents/openai.yaml` and four one-level references covering:

```text
CLI contract
local intake workflow
authority and failure boundaries
task reporting
```

It contains no scripts, assets, duplicate schemas, README, changelog or private fixtures. Detailed rules use one-level progressive disclosure from the concise `SKILL.md`.

## Workflow Boundary

The Skill requires an existing workspace config and absolute PDF paths. It validates public capability, initializes only the supplied config, resolves exact registration with `intake inspect`, recovers current state, parses through explicit `pdfplumber`, grounds factual Card Units to exact page/locator/quote provenance, promotes through existing authority, maps only a supplied or approved question and runs Guardian read-only.

Sources run sequentially. Reruns reuse current exact records and Core-owned IDs. Stale sources, ambiguous ownership, unsafe integrity and uncertain semantic duplicates stop rather than append or rewrite.

Document type remains local to the active task. Review-like or low-confidence documents stop before primary Paper Card or Evidence promotion.

## Distribution Boundary

The Python wheel does not package or install the Skill. The repository is the reviewed versioned source. Local CC Switch installation occurs only after merge under separate authorization and must be byte-identical to the merged tree. Platform-owned Codex and plugin-cache directories are not installation targets.

## Limits

M3A-1 does not add workspace/domain config generation, Review runtime, Step 7, discovery, acquisition, OCR, figure/table/supplement interpretation, manuscript audit, migration, private-workspace integration or a Core runtime change.
