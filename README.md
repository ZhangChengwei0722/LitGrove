# Research KB Core

Cross-platform, local-first contracts and deterministic CLI primitives for evidence-traceable research knowledge bases.

## Current Scope

Milestone 1B and M2A-1 provide:

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

Real PDF parsing, scientific claim generation, Question Mapping runtime, Step 7 runtime, Markdown rendering, and the Portable Agent Skill remain later milestones. The CLI never calls an LLM or makes scientific judgments.

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

On macOS, use `.venv/bin/python` instead.

## Runtime Commands

Initialize an existing workspace config before running workspace services:

```text
research-kb workspace init --workspace <workspace.yaml> [--dry-run]
```

Bootstrap validates source/config relationships, creates only the approved managed scaffold, and writes `.research-kb/workspace.json`. It never creates or scans `local_inbox`, changes source assets, creates canonical records, or emits a process event.

All runtime commands then resolve paths through the initialized workspace:

```text
research-kb registry add --workspace <workspace.yaml> --root-id <root> --relative-path <path> --metadata <metadata.json>
research-kb parse run --workspace <workspace.yaml> --paper-id <paper_id> --adapter synthetic-text
research-kb record promote --workspace <workspace.yaml> --request <request.json> --actor <agent|cli|user>
research-kb guardian check --workspace <workspace.yaml> [--write-report]
research-kb transaction recover --workspace <workspace.yaml> [--dry-run]
```

Source assets remain read-only. Canonical writes stay under `knowledge_root` and emit a process event only after a validated atomic replacement.

## Contracts

JSON Schema Draft 2020-12 files live under `schemas/`. YAML inputs are parsed into mappings and validated against the same schemas. Markdown is documentation or a future rendered view, never a structured source of truth.
