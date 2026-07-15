# Architecture

## Layers

```text
Shared Core + CLI
-> Portable Agent Skill
-> Separate private workspaces
```

Core owns deterministic contracts, validation, path and ID handling, structured I/O, status gates, logs, and Guardian checks. The Agent layer owns scientific reading, interpretation, candidate generation, and workflow decisions. Private workspaces own papers and research records. Rendering remains deferred after M2A-1.

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

Question Mapping, Step 7 runtime, Markdown rendering, real PDF parsing, and Agent Skill orchestration are intentionally absent.
