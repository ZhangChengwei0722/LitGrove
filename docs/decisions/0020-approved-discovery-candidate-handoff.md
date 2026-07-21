# ADR 0020: Explicit Approved Discovery Candidate Handoff

- Status: Accepted
- Date: 2026-07-21

## Decision

Add a dedicated `discovery select` mutation that accepts a complete validated M3C-1 report, 1-15 result keys explicitly selected by the user and optional existing Question Mapping IDs. Only exact `actor: user` is accepted. Core never derives selection from relevance, rank, paper type, DOI or full-text status.

Persist selected metadata to `discovery/candidates.jsonl` under layout `m3c-2a`. Core owns `discovery_<uuid4>` IDs, deterministic selection-context hashes, timestamps, fixed non-evidence states, complete-bundle validation and one atomic JSONL replacement. `discovery list/show` are the only public read surfaces.

The complete report is validated but not persisted. Unselected results are not persisted. Exact selection-intent reruns write nothing; a new query or question context updates the same candidate; changed metadata under an existing result key raises `RKBC-034` and rejects the complete batch. Events and journals contain only candidate and question IDs.

## Boundary

`user_selected` records follow-up interest only. Candidates remain `metadata_only`, `acquisition_status: not_started`, `not_evidence: true` and `automation_status: passed_auto_checks`. Selection does not create a Registry record, Question Mapping paper link, canonical Evidence, screening decision, verification, acquisition approval or source file.

The exact `m3b-1 -> m3c-2a` upgrade creates only an empty `discovery/` directory and replaces the workspace marker. It creates no empty candidate store, event or journal.

## Consequences

- Add the public `discovery-candidate` schema and `discovery` ID namespace.
- Add `RKBC-034` for same-result metadata conflict.
- Include candidates in bundle, Guardian, transaction recovery and privacy checks.
- Require synthetic-from-scratch report fixtures and installed-wheel handoff smoke coverage.
- Defer metadata refresh, candidate deletion, OA resolution, acquisition, source-root writes and Registry/intake chaining.
