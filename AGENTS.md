# Shared Research KB Agent Rules

## Scope

This repository contains cross-platform Core/CLI code, public contracts, synthetic fixtures, tests, and system documentation. It does not contain private research data.

## Required Boundaries

- Never add real PDFs, parsed paper text, evidence quotes, Paper Cards, research notes, credentials, private paths, or a private workspace export.
- Fixtures must be authored from scratch and marked `synthetic_from_scratch`.
- Existing source assets are immutable. Core code must never move, delete, rename, overwrite or edit an existing source asset. Reading an existing source for explicit create-only inbox import does not grant mutation authority over that source.
- Source writes are limited to exact-user-authority `discovery acquire` and `copy_into_local_inbox`. Each may create only a previously absent file under the configured, uniquely addressable `local_inbox`; neither may overwrite, move, rename or delete an existing user file. Ordinary failure cleanup may unlink only a temp or final file created by that same operation while its recorded file identity still matches. Acquisition or copy never authorizes Registry intake or downstream scientific records.
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
- Run targeted tests, then the risk-appropriate L2-L4 validation. Schema, authority,
  storage, transaction, recovery, merge, and release changes require complete L3 plus L4.

## Validation Commands

```powershell
python tools/run_validation.py --level L2 --receipt .validation/l2.json
python tools/run_validation.py --verify --collect-nodeids --receipt .validation/manifest.json
python tools/run_validation.py --level L3 --shard all --receipt .validation/l3.json
python tools/run_validation.py --level L4 --shard scale --receipt .validation/l4.json
python -m build
python -m research_kb --version
python -m research_kb privacy scan --root .
```

## Repository Governance

- The accepted single-maintainer exception keeps required approvals at `0`; it does not
  remove required checks, administrator enforcement, conversation resolution, or the
  prohibition on force-push and branch deletion.
- The G1 risk classifier is shadow-only. Its suggested L0-L4 level never skips the existing
  Windows, Linux, dependency, security, or full L3/L4 validation gates. Unknown, renamed,
  governance, release, security, privacy, schema, storage, transaction, recovery, authority,
  or scale changes remain high-risk until independently classified.
- Release artifacts follow a build-once transaction: build from the exact protected `main`
  commit, accept the exact artifact bytes, create an immutable `v*` tag at that same commit,
  and publish those accepted bytes without rebuilding from the tag. G1 creates no tag,
  GitHub Release, PyPI publication, or Trusted Publisher configuration.
