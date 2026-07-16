# Architecture

## Layers

```text
Shared Core + CLI
-> Portable Agent Skill
-> Separate private workspaces
```

Core owns deterministic contracts, validation, path and ID handling, structured I/O, status gates, logs, Guardian checks, and one bounded stdout reading view. The Agent layer owns scientific reading, interpretation, candidate generation, and workflow decisions. Private workspaces own papers and research records. Persisted and additional derived views remain deferred after M2B-2.

## Knowledge Flow

```text
Source Intake -> Registry -> Parse -> Paper Card Core
-> Evidence Grounding -> Question Mapping
-> Step 7 Candidate Thinking -> Guardian / Feedback
```

Canonical evidence is the provenance backbone. Paper Card Units are the semantic entry for later reasoning. Step 7 remains candidate-level and must expand back to canonical evidence.

## Milestone 1B Runtime

```text
candidate mutation request
-> trusted actor boundary
-> kind-specific normalization and CLI-owned IDs
-> schema and cross-record validation
-> workspace lock
-> same-directory fsynced temp file
-> mode-preserving, digest-checked os.replace
-> post-replacement source stability check where required
-> journal-derived process event
-> completed recovery journal with final result
```

Registry, SyntheticText Parse, Paper Card, Evidence, review queue, and Guardian services use the same workspace resolver and transaction kernel. Source references are persisted as `root_id + relative_path`; local absolute paths are never canonical data.

Guardian requires every completed journal to have exactly one matching process event. Missing or altered events and all `needs_resolution` journals fail closed instead of being inferred from target state alone.

Existing canonical records are validated with the internal `stored` context. This bypasses submitter-state checks only while reading already persisted state; it grants no mutation authority and is not exposed by CLI actor choices.

## M2A-1 Workspace Boundary

```text
existing workspace config + domain profile
-> shared semantic validation
-> read-only preflight
-> workspace lock and repeated preflight
-> exact managed directory scaffold
-> deterministic workspace identity marker
-> initialized runtime
```

`WorkspaceLayout.load` is the single initialized-workspace gate used by Registry, Parse, Record, Guardian, and Recovery commands. Bootstrap alone may resolve an unbound config. A marker mismatch, unsafe layout, unknown managed content, source/root conflict, or missing marker fails closed.

`.research-kb/workspace.json` contains only workspace/profile identities, the layout contract version, and a SHA-256 config fingerprint. It is operational metadata, not canonical scientific state, and emits no process event or transaction journal. A markerless populated M1B store is adopted only after its complete structured bundle and transaction state validate without rewriting records.

## M2A-2 Read-Only Compatibility Boundary

```text
initialized workspace + explicitly injected LegacyReaderAdapter
-> protected source snapshots
-> normalized legacy inventory and deterministic differences
-> public compatibility-report on stdout
-> protected source snapshots repeated in finally
```

Shared Core owns adapter metadata validation, source-reference confinement, difference IDs, blocking policy, report schemas, ordering, and exit codes. The adapter owns only private legacy interpretation and candidate inventory. The installed CLI has an empty adapter registry: there is no module-path loading, plugin discovery, entry-point scan, or production private adapter in this repository.

Compatibility inspection is not migration. It allocates no replacement canonical IDs, writes no report or process event, and does not alter the legacy source of truth. If declared protected input changes, disappears, changes type, or becomes unsafe during inspection, the run fails with `RKBC-026` even when the adapter also raises.

Step 7 runtime, persisted Markdown views, real PDF parsing, migration, and Agent Skill orchestration are intentionally absent.

## M2B-1 Question Mapping Boundary

```text
user-supplied or user-approved question
+ selected Paper Card Units
+ question-specific review queue boundaries
-> QuestionMappingService
-> exact evidence and boundary projection
-> questions/mappings.jsonl
```

The Agent supplies the semantic selection, role, and rationale. Core owns `question_id`, `question_link_id`, domain binding, timestamps, evidence expansion, required Card Unit boundaries, ordering, validation, and atomic persistence. One question has at most one link per paper; replace preserves existing question/link identities and cannot remove a paper link in M2B-1.

`questions/mappings.jsonl` is canonical organizational state, not canonical scientific evidence. It points back to Paper Card Units and canonical evidence rather than duplicating their scientific content. Guardian warns with `RKBC-014` when linked Card, evidence, or queue records are newer than a mapping; it never refreshes the mapping automatically.

New workspaces initialize at layout `m2b-1`. Exact `m2a-1` predecessors are runtime-blocked with `RKBC-027` and can be upgraded only through `workspace init`. The upgrade creates `questions/` and replaces operational marker metadata; it creates no empty JSONL, process event, journal, or scientific record.

## M2B-2 Question Reading View Boundary

```text
validated Question Mapping + reachable Registry/Card/Evidence/Queue records
-> deterministic in-memory projection and source snapshot digest
-> one UTF-8/LF Markdown document on stdout
```

`QuestionReadingViewService` accepts structured bundle entries rather than paths or a workspace object. It validates the complete bundle, resolves exactly one question, preserves domain-profile Card section order, expands only mapping-owned evidence and boundaries, and reuses the existing freshness diagnostic. Review queue records are rendered in a separate, explicitly non-evidence section.

The CLI completes validation, projection, hashing, and rendering before its single stdout write. It creates no `views/` directory, canonical record, cache, report, event, journal, lock, or render timestamp. The view is a one-way reading surface; JSONL remains the organizational source of truth.
