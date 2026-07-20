# Contributor Guide

## Change Process

1. Start from an issue or approved implementation plan.
2. Keep one bounded behavior or contract change per branch.
3. Add or update deterministic tests.
4. Run the full local suite and privacy scan.
5. State compatibility, tested platform, fixture scope, and known limits in the review description.

Use the repository virtual environment so the editable package and bounded dependencies are active:

```powershell
.\.venv\Scripts\python -m pip install -e ".[test,pdf]"
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m research_kb privacy scan --root .
```

Release-resource smoke after `python -m build`:

```powershell
.\.venv\Scripts\python tests/wheel_smoke.py
.\.venv\Scripts\python tests/wheel_pdf_smoke.py
```

Schema, state, ID, path, and directory-protocol changes require explicit user approval, focused self-review, targeted tests, and the full Windows validation gate. External collaborator review is optional.

## Fixture Rules

- Author all names, text, measurements, identifiers, and relationships from scratch.
- Add `fixture_origin: synthetic_from_scratch` where the schema permits it.
- Never derive fixtures by editing private research records.
- Intentional privacy failures must be exact-file allowlisted with an expected code and count.
- Runtime fixtures must execute in a temporary copy; tests must not write canonical state into the repository fixture tree.
- Runtime fixtures must run `WorkspaceBootstrapService` before `WorkspaceLayout.load`; contract-only fixtures remain read-only and markerless.
- Record source hashes before a runtime scenario and assert that every hash is unchanged afterward.
- Generate PDF fixtures at runtime under `tmp_path` with ReportLab. Do not commit generated PDFs or derive them from real papers.
- Test blank, malformed, encrypted, all-empty, and multi-page sources with bounded diagnostics that contain no extracted text or absolute path.
- Keep the base-wheel smoke free of the PDF extra and require `RKBC-028` for explicit PDF selection; validate real PDF parsing in the separate `[pdf]` wheel smoke.

## Mutation Service Rules

- Resolve all targets through `WorkspaceLayout`; do not accept a direct `knowledge_root` override.
- Read current canonical state before composing a replacement and pass its digest to the transaction manager.
- Validate the temporary target before `os.replace`.
- Keep source assets outside the writable boundary.
- Emit no process event payload containing candidate scientific text.
- Add a failure, conflict, and source-immutability test for each new mutating service.
- For Evidence, test exact character-slice resolution, synthetic block containment, same-paper ownership, active parser/run consistency, and preservation of previous target bytes on pre-replacement failure.

## Compatibility Adapter Rules

- Keep private and domain-specific adapters outside Shared Core.
- Inject adapters explicitly in process; do not add import strings, plugin discovery, entry-point loading, or an installed default registry.
- Return bounded metadata, structured source references, digests, and public diagnostics only. Never return raw legacy payloads or absolute paths.
- Declare every protected input and test byte-identical source and knowledge state before and after a successful inspection.
- Treat compatibility output as a transient read-only report. Do not create report files, process events, journals, canonical records, or migration IDs.
- Use synthetic-from-scratch fixtures for every shared test, including adapter errors and protected-input mutation detection.

## Question Mapping Rules

- Persist mappings only for `user_supplied`, `user_approved_candidate`, or `existing_question` request contexts.
- Treat selected Paper Card Units as semantic inputs; never accept caller-supplied `evidence_ids` or `question_link_id` values.
- Derive evidence exactly from selected units and preserve every selected-unit review queue boundary.
- Keep one paper link per question, preserve existing link IDs on replace, and do not implement deletion without a later lifecycle contract.
- Add cross-paper, duplicate, stale-upstream, write-conflict, deterministic-ordering, and read-only retrieval tests.
- Keep mappings in structured JSONL. Do not add a hand-maintained Markdown mirror.

## Question Reading View Rules

- Render only records reachable through the selected validated Question Mapping; do not scan for extra same-paper evidence.
- Keep review queue boundaries in their own explicit non-evidence section and out of evidence counts.
- Build complete UTF-8/LF bytes before stdout begins. Deterministic validation and rendering failures must leave stdout empty.
- Never include wall-clock time, hostname, cwd, absolute paths, `source_ref`, or source filenames in a view.
- Treat synthetic golden Markdown as reviewed output: author its source records from scratch, compare bytes exactly, and update it only for an approved contract change.
- Rendering is read-only. Do not create a view file, cache, event, journal, report, lock, or layout directory.

## Deterministic Read And Stdin Rules

- Build each transient JSON read document completely before one UTF-8/LF stdout write.
- Repeat unchanged reads and compare exact bytes; snapshot source and managed trees before and after.
- Keep capability probing workspace-independent and distinguish implemented capability from optional dependency availability.
- Keep paper status free of source paths, statements, claims, quotes, question text, rationales and semantic next actions.
- Recheck source SHA-256 around parsed-page projection and return only the selected paper's stored records.
- Bound paper context to one selected paper, sort Evidence/queue records by canonical ID, omit source references and recheck source SHA-256 before and after projection.
- Snapshot the complete managed tree around paper context reads and test registered-only, partial-run, complete and cross-paper isolation states.
- Require an absolute path for intake inspection, resolve it to exactly one declared root, round-trip its portable source reference, and never expose its absolute path or hash.
- Match intake registration only by exact `root_id + relative_path`; test unregistered, current, stale, ambiguous and same-bytes-at-another-path states.
- Hash intake sources before and after projection, keep failure stdout empty, and prove source plus managed trees remain byte-identical.
- Read stdin as raw bytes with a command-specific limit plus one byte; never echo invalid payloads.
- Accept stdin only for Registry metadata and mutation requests, and route successful objects through existing services.
- Exercise base and `[pdf]` installed wheels so intake inspection, availability, stdin, parse reads, status and paper context do not depend on the editable tree.

## Platform Rules

Tests must include Windows-shaped and POSIX-shaped paths independent of the host. Persisted relative paths always use `/`.

Windows is the required live acceptance platform. macOS compatibility remains a design target and may be checked when available, but a live macOS run is not a release gate unless a later approved milestone says otherwise.

## Portable Skill Rules

- Keep the versioned Skill source under `skills/research-kb/`; do not package it in the Python wheel.
- Keep `SKILL.md` concise and link every detailed reference directly from it.
- Do not add Skill-local scripts, assets, schemas, state, README files or private fixtures without a later approved design.
- Keep executable command examples aligned with `capability show` and public CLI help.
- Run `tests/unit/test_portable_skill_contract.py` plus the active official `quick_validate.py`.
- Regenerate `agents/openai.yaml` with official tooling and require byte-identical output.
- Forward-test with fresh Agents, raw synthetic tasks and clean temporary workspaces; do not disclose expected routing.
- Keep forward-test outputs outside the repository and verify source bytes, record counts, events, Guardian and privacy.
- Treat repository merge and local CC Switch installation as separate gates. Never install directly into Codex or plugin-cache directories.
