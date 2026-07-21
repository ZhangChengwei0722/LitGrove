# Shared Research KB Agent Rules

## Scope

This repository contains cross-platform Core/CLI code, public contracts, synthetic fixtures, tests, and system documentation. It does not contain private research data.

## Required Boundaries

- Never add real PDFs, parsed paper text, evidence quotes, Paper Cards, research notes, credentials, private paths, or a private workspace export.
- Fixtures must be authored from scratch and marked `synthetic_from_scratch`.
- Existing source assets are immutable. Core code must never move, delete, rename, overwrite, edit, or copy an existing source asset.
- The only source-write exception is exact-user-authority `discovery acquire`: it may create one previously absent file under the configured, uniquely addressable `local_inbox`. Ordinary failure cleanup may unlink only a temp or final file created by that same operation while its recorded file identity still matches. Acquisition never authorizes Registry intake or downstream scientific records.
- The CLI performs deterministic I/O, validation, IDs, status gates, logging, rendering, and Guardian checks. It does not make scientific judgments or call an LLM.
- Agent-produced records are candidates. Agents cannot assign `human_checked`, `verified`, final screening decisions, or high-risk source operations.
- Discovery selection requires explicit user authority and creates metadata-only, non-evidence candidates; it never authorizes acquisition, Registry intake, screening, or verification.
- Structured records are canonical inputs. Markdown is a rendered reading view and must not overwrite structured state.

## Engineering Rules

- Use `pathlib.Path`; do not construct paths with hard-coded separators.
- Persist source locations as `root_id + relative_path` using POSIX `/`.
- Use UTF-8 and LF for repository text.
- Keep changes small and tied to an approved issue or plan.
- Schema, state, path, ID, and directory-contract changes require explicit user approval, a focused review, and targeted plus full validation. External collaborator review is optional and is not an acceptance gate.
- Windows is the required live acceptance platform. Keep host-independent POSIX path tests and `pathlib` portability; live macOS validation is best-effort unless a future milestone explicitly requires it.
- Do not add a remote, commit, push, publish, or create CI configuration without explicit user approval.
- Run targeted tests, then the full suite, before reporting completion.

## Validation Commands

```powershell
python -m pytest -q
python -m build
python -m research_kb --version
python -m research_kb privacy scan --root .
```
