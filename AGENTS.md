# Shared Research KB Agent Rules

## Scope

This repository contains cross-platform Core/CLI code, public contracts, synthetic fixtures, tests, and system documentation. It does not contain private research data.

## Required Boundaries

- Never add real PDFs, parsed paper text, evidence quotes, Paper Cards, research notes, credentials, private paths, or a private workspace export.
- Fixtures must be authored from scratch and marked `synthetic_from_scratch`.
- Source assets are read-only. Core code must never move, delete, rename, overwrite, or copy a source asset without an explicit future contract and user authorization.
- The CLI performs deterministic I/O, validation, IDs, status gates, logging, rendering, and Guardian checks. It does not make scientific judgments or call an LLM.
- Agent-produced records are candidates. Agents cannot assign `human_checked`, `verified`, final screening decisions, or high-risk source operations.
- Structured records are canonical inputs. Markdown is a rendered reading view and must not overwrite structured state.

## Engineering Rules

- Use `pathlib.Path`; do not construct paths with hard-coded separators.
- Persist source locations as `root_id + relative_path` using POSIX `/`.
- Use UTF-8 and LF for repository text.
- Keep changes small and tied to an approved issue or plan.
- Schema, state, path, ID, and directory-contract changes require review from both collaborators.
- Do not add a remote, commit, push, publish, or create CI configuration without explicit user approval.
- Run targeted tests, then the full suite, before reporting completion.

## Milestone 1A Commands

```powershell
python -m pytest -q
python -m build
python -m research_kb --version
python -m research_kb privacy scan --root .
```
