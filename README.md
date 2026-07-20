# Research KB Core

Cross-platform, local-first contracts and deterministic CLI primitives for evidence-traceable research knowledge bases.

## Current Scope

Milestone 1B through the M3B-1 repository slice provide:

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
- historical layout upgrades through `m3a-2a` and the current exact `m3a-2a -> m3b-1` upgrade with no canonical-record rewrite;
- persistent, domain-neutral Question Mapping from selected Paper Card Units;
- CLI-owned question/link IDs and exact evidence/boundary projection;
- read-only `question list/show` commands and Guardian mapping freshness warnings.
- one deterministic, stdout-only Question Reading View with selected Card Units, canonical evidence trace, non-evidence boundaries, and current freshness diagnostics.
- an explicit optional `pdfplumber` adapter with exact package-version provenance and one row per PDF page;
- strict same-paper page/locator/quote validation for canonical Evidence, including bounded synthetic block compatibility.
- a versioned transient capability report, bounded one-paper status projection, and validated parsed-page read surface;
- bounded stdin JSON handoff into the existing Registry and mutation authority paths without temporary request files.
- one source-stable, paper-scoped canonical context read for Card Unit, Evidence, and review queue recovery.
- one read-only intake preflight that maps an absolute source path to its portable source reference, exact Registry state, and active Paper Card section contract.
- one repo-owned Portable Agent Skill for existing-config primary-research and common review PDF intake through mutually exclusive routes;
- one common, background-only Review Memory contract for five review subtypes, with CLI-owned Memory/Unit IDs and exact page/section provenance;
- atomic Review Memory append/replace, primary/review route exclusion, stale-parse Guardian warnings, and a separate `review context` recovery read;
- a review-specific route in the same Portable Skill, without subtype-specific schemas or downstream Field Map/Question/Step 7 integration.
- four deterministic Step 7 candidate stores with CLI-owned IDs, evidence/boundary closure and atomic append/replace;
- Question Mapping admission, stale-upstream projection, `step7 context`, stdout-only `step7 render`, and Guardian `RKBC-014` warnings.

The installed CLI contains no private adapter and performs no adapter discovery. The CLI never calls an LLM or makes scientific judgments. OCR, subtype-specific review runtime, persisted Markdown or additional derived views, Field Map integration, Review Unit Question Mapping, Agent-side Step 7 generation/refresh orchestration and migration remain later milestones.

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

## Portable Skill

The reviewed Skill source lives at `skills/research-kb/`. It orchestrates existing Core commands for mutually exclusive primary-research and common Review Memory routes, but adds no Core service, schema, ID or workflow store.

The Python wheel does not install the Skill. Local CC Switch installation is a separate, explicitly authorized post-merge operation. The Skill requires an existing workspace config and does not generate workspace/domain configuration, discover literature, integrate Review Units downstream or run Step 7.

## Runtime Commands

Initialize an existing workspace config before running workspace services:

```text
research-kb workspace init --workspace <workspace.yaml> [--dry-run]
```

Bootstrap validates source/config relationships, creates only the approved managed scaffold, and writes `.research-kb/workspace.json`. It never creates or scans `local_inbox`, changes source assets, creates canonical records, or emits a process event.

Capability probing is workspace-independent; all commands with `--workspace` resolve paths through the initialized workspace:

```text
research-kb capability show
research-kb intake inspect --workspace <workspace.yaml> --source <absolute-source-path>
research-kb compatibility inspect --workspace <workspace.yaml> --adapter <adapter_id>
research-kb registry add --workspace <workspace.yaml> --root-id <root> --relative-path <path> --metadata <metadata.json>
research-kb registry add --workspace <workspace.yaml> --root-id <root> --relative-path <path> --metadata -
research-kb parse run --workspace <workspace.yaml> --paper-id <paper_id> --adapter synthetic-text
research-kb parse run --workspace <workspace.yaml> --paper-id <paper_id> --adapter pdfplumber
research-kb parse show --workspace <workspace.yaml> --paper-id <paper_id> [--page <positive_integer>]
research-kb paper status --workspace <workspace.yaml> --paper-id <paper_id>
research-kb paper context --workspace <workspace.yaml> --paper-id <paper_id>
research-kb review context --workspace <workspace.yaml> --paper-id <paper_id>
research-kb record promote --workspace <workspace.yaml> --request <request.json> --actor <agent|cli|user>
research-kb record promote --workspace <workspace.yaml> --request - --actor <agent|cli|user>
research-kb question list --workspace <workspace.yaml>
research-kb question show --workspace <workspace.yaml> --question-id <question_id>
research-kb question render --workspace <workspace.yaml> --question-id <question_id>
research-kb step7 context --workspace <workspace.yaml> --question-id <question_id>
research-kb step7 render --workspace <workspace.yaml> --question-id <question_id>
research-kb guardian check --workspace <workspace.yaml> [--write-report]
research-kb transaction recover --workspace <workspace.yaml> [--dry-run]
```

Source assets remain read-only. Canonical writes stay under `knowledge_root` and emit a process event only after a validated atomic replacement.

`capability show`, `intake inspect`, `parse show`, `paper status`, `paper context`, `review context`, and `step7 context` emit transient interface `1.0` JSON and write no workspace state. Capability output distinguishes an implemented adapter from its installed availability. Paper status reports deterministic stage and safety facts only; it does not claim scientific completion or choose a next action. Parsed-page text appears only through the explicit local `parse show` read.

`intake inspect` accepts one absolute source path, confines it to exactly one declared source root, and returns only portable `root_id + relative_path`, exact-path registration state, and ordered Paper Card section IDs/labels. It hashes the source before and after projection, never returns the hash or absolute path, and performs no registration. The Portable Skill uses it for sequential reruns; concurrent inspect-and-register deduplication is not guaranteed.

`paper context` returns the selected paper's complete stored Paper Card or `null`, canonical Evidence records, and review queue records after complete-bundle and source-stability checks. It excludes source references, paths, parsed pages, Question Mappings, and unrelated papers. It is the public recovery surface for CLI-owned Unit, Evidence, and queue IDs, not a generic workspace export or semantic resume decision.

`review context` returns one complete Review Memory or `null`, `absent/current/stale_parse` freshness, and transient exact local DOI matches for primary-paper leads. Review Memory remains `background_only`, `can_enter_canonical_evidence: false`, and `not_fact: true`; stale notes are never rebound to a newer parse automatically.

Stdin accepts one UTF-8 JSON object only. Registry metadata is capped at 64 KiB and mutation requests at 4 MiB; YAML remains file-only. Invalid input never reaches a mutation service, and no temporary request file is created.

The PDF adapter records exact `pdfplumber` version identity and emits `page:<n>:text` page locators. Real-PDF Evidence must use `page:<n>:char:<start>-<end>` with an exact zero-based, end-exclusive slice of stored page text. Missing PDF dependencies and unsupported PDF sources fail explicitly; there is no OCR or synthetic fallback.

Question Mapping requests use `record promote`. The request selects Paper Card Unit IDs and may add question-specific review queue boundaries; Core derives `evidence_ids`, preserves required unit boundaries, allocates IDs, and stores the result in `questions/mappings.jsonl`. Unapproved Agent-generated questions remain task report candidates and cannot use a persistable `question_origin`.

`question render` validates the complete workspace bundle and emits one raw Markdown reading view to stdout. It expands only records reachable from the selected mapping, labels review queue records as non-evidence, computes freshness without rewriting the mapping, and creates no file, event, journal, report, or cache.

Step 7 requests also use `record promote`, but require `paper_id: null` and `question_origin: existing_question`. The Agent submits semantic fields and selected mapped Card Unit IDs; Core owns candidate IDs, candidate type, exact canonical Evidence and Unit-boundary closure, snapshot fields, timestamps and the fixed `not_fact: true`, `review_status: ai_draft`, `automation_status: pending` boundary. Records live in four JSONL stores under `step7/`. `step7 context` returns candidates and deterministic freshness for one question. `step7 render` emits a non-canonical Markdown reading view to stdout only. Neither command generates or scientifically judges candidates.

`compatibility inspect` is an integration seam for an adapter injected by a private caller in the same Python process. It emits one schema-valid report to stdout, snapshots every declared protected input before and after inspection, and writes no report, event, journal, or canonical record. A clean report exits `0`, blocking differences exit `1`, adapter/output errors exit `2`, and protected-input changes exit `4`.

## Contracts

JSON Schema Draft 2020-12 files live under `schemas/`. YAML inputs are parsed into mappings and validated against the same schemas. Markdown is documentation or a future rendered view, never a structured source of truth.
