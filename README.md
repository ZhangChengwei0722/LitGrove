# Research KB Core

Cross-platform, local-first contracts and deterministic CLI primitives for evidence-traceable research knowledge bases.

## Current Scope

Milestone 1B through M3A-0C provide:

- versioned workspace, domain, record, and candidate schemas;
- portable source references and stable IDs;
- schema, reference, status, and privacy validation;
- locked atomic JSON/JSONL promotion with recovery journals and process events;
- read-only source registration with SHA-256 duplicate linking;
- a synthetic text parse adapter for invented test sources;
- deterministic Paper Card, Evidence, and review queue record promotion;
- check-only or explicitly persisted Guardian reports;
- two fully synthetic cross-domain runtime fixtures;
- config-first workspace bootstrap with shared semantic validation;
- a deterministic, non-canonical workspace identity marker;
- initialized-workspace enforcement for every runtime command.
- generic read-only compatibility inspection through explicitly injected legacy adapters;
- deterministic compatibility differences, protected-input snapshots, and blocking policy without migration or persistence.
- an explicit `m2a-1 -> m2b-1` workspace layout upgrade with no canonical-record rewrite;
- persistent, domain-neutral Question Mapping from selected Paper Card Units;
- CLI-owned question/link IDs and exact evidence/boundary projection;
- read-only `question list/show` commands and Guardian mapping freshness warnings.
- one deterministic, stdout-only Question Reading View with selected Card Units, canonical evidence trace, non-evidence boundaries, and current freshness diagnostics.
- an explicit optional `pdfplumber` adapter with exact package-version provenance and one row per PDF page;
- strict same-paper page/locator/quote validation for canonical Evidence, including bounded synthetic block compatibility.
- a versioned transient capability report, bounded one-paper status projection, and validated parsed-page read surface;
- bounded stdin JSON handoff into the existing Registry and mutation authority paths without temporary request files.
- one source-stable, paper-scoped canonical context read for Card Unit, Evidence, and review queue recovery.

The installed CLI contains no private adapter and performs no adapter discovery. Scientific claim generation, OCR, Review runtime, persisted or additional derived views, Step 7 runtime, migration, and the Portable Agent Skill remain later milestones. The CLI never calls an LLM or makes scientific judgments.

## Privacy Boundary

This repository contains no real paper data or private workspace content. See [docs/privacy-boundary.md](docs/privacy-boundary.md).

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m research_kb --version
.\.venv\Scripts\python -m research_kb privacy scan --root .
```

For real local PDF parsing, install the bounded optional extra in the repository environment:

```powershell
.\.venv\Scripts\python -m pip install -e ".[test,pdf]"
```

On macOS, use `.venv/bin/python` instead.

## Runtime Commands

Initialize an existing workspace config before running workspace services:

```text
research-kb workspace init --workspace <workspace.yaml> [--dry-run]
```

Bootstrap validates source/config relationships, creates only the approved managed scaffold, and writes `.research-kb/workspace.json`. It never creates or scans `local_inbox`, changes source assets, creates canonical records, or emits a process event.

Capability probing is workspace-independent; all commands with `--workspace` resolve paths through the initialized workspace:

```text
research-kb capability show
research-kb compatibility inspect --workspace <workspace.yaml> --adapter <adapter_id>
research-kb registry add --workspace <workspace.yaml> --root-id <root> --relative-path <path> --metadata <metadata.json>
research-kb registry add --workspace <workspace.yaml> --root-id <root> --relative-path <path> --metadata -
research-kb parse run --workspace <workspace.yaml> --paper-id <paper_id> --adapter synthetic-text
research-kb parse run --workspace <workspace.yaml> --paper-id <paper_id> --adapter pdfplumber
research-kb parse show --workspace <workspace.yaml> --paper-id <paper_id> [--page <positive_integer>]
research-kb paper status --workspace <workspace.yaml> --paper-id <paper_id>
research-kb paper context --workspace <workspace.yaml> --paper-id <paper_id>
research-kb record promote --workspace <workspace.yaml> --request <request.json> --actor <agent|cli|user>
research-kb record promote --workspace <workspace.yaml> --request - --actor <agent|cli|user>
research-kb question list --workspace <workspace.yaml>
research-kb question show --workspace <workspace.yaml> --question-id <question_id>
research-kb question render --workspace <workspace.yaml> --question-id <question_id>
research-kb guardian check --workspace <workspace.yaml> [--write-report]
research-kb transaction recover --workspace <workspace.yaml> [--dry-run]
```

Source assets remain read-only. Canonical writes stay under `knowledge_root` and emit a process event only after a validated atomic replacement.

`capability show`, `parse show`, `paper status`, and `paper context` emit transient interface `1.0` JSON and write no workspace state. Capability output distinguishes an implemented adapter from its installed availability. Paper status reports deterministic stage and safety facts only; it does not claim scientific completion or choose a next action. Parsed-page text appears only through the explicit local `parse show` read.

`paper context` returns the selected paper's complete stored Paper Card or `null`, canonical Evidence records, and review queue records after complete-bundle and source-stability checks. It excludes source references, paths, parsed pages, Question Mappings, and unrelated papers. It is the public recovery surface for CLI-owned Unit, Evidence, and queue IDs, not a generic workspace export or semantic resume decision.

Stdin accepts one UTF-8 JSON object only. Registry metadata is capped at 64 KiB and mutation requests at 4 MiB; YAML remains file-only. Invalid input never reaches a mutation service, and no temporary request file is created.

The PDF adapter records exact `pdfplumber` version identity and emits `page:<n>:text` page locators. Real-PDF Evidence must use `page:<n>:char:<start>-<end>` with an exact zero-based, end-exclusive slice of stored page text. Missing PDF dependencies and unsupported PDF sources fail explicitly; there is no OCR or synthetic fallback.

Question Mapping requests use `record promote`. The request selects Paper Card Unit IDs and may add question-specific review queue boundaries; Core derives `evidence_ids`, preserves required unit boundaries, allocates IDs, and stores the result in `questions/mappings.jsonl`. Unapproved Agent-generated questions remain task report candidates and cannot use a persistable `question_origin`.

`question render` validates the complete workspace bundle and emits one raw Markdown reading view to stdout. It expands only records reachable from the selected mapping, labels review queue records as non-evidence, computes freshness without rewriting the mapping, and creates no file, event, journal, report, or cache.

`compatibility inspect` is an integration seam for an adapter injected by a private caller in the same Python process. It emits one schema-valid report to stdout, snapshots every declared protected input before and after inspection, and writes no report, event, journal, or canonical record. A clean report exits `0`, blocking differences exit `1`, adapter/output errors exit `2`, and protected-input changes exit `4`.

## Contracts

JSON Schema Draft 2020-12 files live under `schemas/`. YAML inputs are parsed into mappings and validated against the same schemas. Markdown is documentation or a future rendered view, never a structured source of truth.
