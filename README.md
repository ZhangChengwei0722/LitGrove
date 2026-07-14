# Research KB Core

Cross-platform, local-first contracts and deterministic CLI primitives for evidence-traceable research knowledge bases.

## Current Scope

Milestone 1A establishes:

- versioned workspace, domain, record, and candidate schemas;
- portable source references and stable IDs;
- schema, reference, status, and privacy validation;
- fully synthetic contract fixtures;
- a minimal deterministic CLI.

Operational Registry, Parse, Paper Card services, Evidence Grounding, Question Mapping, Step 7 orchestration, Guardian execution, and the Portable Agent Skill are later milestones.

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

## Contracts

JSON Schema Draft 2020-12 files live under `schemas/`. YAML inputs are parsed into mappings and validated against the same schemas. Markdown is documentation or a future rendered view, never a structured source of truth.
