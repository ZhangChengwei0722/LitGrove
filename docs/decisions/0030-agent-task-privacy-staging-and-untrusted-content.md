# 0030 Agent Task Privacy, Staging And Untrusted Content

Status: accepted

## Context

Codex, Claude and later workers need one portable handoff without becoming writers or receiving more content/authority than the user approved. Document text and Agent output are untrusted data, including when they contain instructions.

## Decision

### Independent privacy dimensions

`allowed_content_classes` is an explicit set; no class implies another:

```text
metadata
parsed_excerpt
canonical_evidence
paper_card_content
review_background
research_routing_context
research_synthesis
operational_context
source_document
```

`execution_scope` is independent: `cloud_allowed` or `local_only`. A locally installed cloud CLI is not a local-only executor.

The effective content set is the intersection of workspace policy, task request, user approval and executor capability. Core validates every payload item and fails closed on an unknown class, registry version, field/file projection or execution scope.

### Planned task kind coverage registry

This table freezes P4 design input; it does not create task records or schemas in P0.

| Task kind | Required input classes | Optional input classes | Explicitly forbidden fallback |
|---|---|---|---|
| `document_route_resolution` | `metadata`, `parsed_excerpt`, `operational_context` | `source_document` | Card/Review content cannot be called metadata. |
| `source_adequacy_assessment` | `metadata`, `parsed_excerpt`, `operational_context` | `source_document` | Machine hard-failure observations cannot be omitted or overridden. |
| `primary_semantic_processing` | `metadata`, `parsed_excerpt`, `operational_context` | `source_document`, `research_routing_context` | Review background cannot be promoted as Evidence. |
| `review_semantic_processing` | `metadata`, `parsed_excerpt`, `operational_context` | `source_document`, `research_routing_context` | Review Units cannot be labeled canonical Evidence. |
| `question_direction_mapping` | `paper_card_content`, `research_routing_context`, `operational_context` | `canonical_evidence`, `review_background`, `metadata` | Question/Direction context cannot be called metadata. |
| `research_synthesis_drafting` | `paper_card_content`, `canonical_evidence`, `research_routing_context`, `operational_context` | `review_background`, `research_synthesis`, `metadata` | Background cannot enter factual evidence base. |
| `semantic_review` | predecessor task's approved classes, `operational_context` | no additional class by default | A reviewer cannot expand predecessor scope. |

Adding a task kind or payload item requires a new registry version that names its content class, field/file projection, maximum count/bytes, execution eligibility and expected result contract before runtime support.

### Provisional payload envelope

Until a bounded P4 plan freezes lower values for each task kind, no single task proposal may exceed 2,000 structured items, 32 MiB total payload, 4 MiB combined excerpts or 8 source documents. `source_document` is never implicit and requires exact file preview plus user approval. These are maximum design envelopes, not permission to send that amount and not a P0 runtime gate.

### Task authority and revision lineage

- An Agent Task carries exact input refs/digests, route, expected output contract, privacy registry version, effective classes, execution scope and one lease/CAS basis.
- Agent output can only enter confined staging. Core validation cannot turn it into canonical state.
- The App previews the exact result and provenance before explicit user approval and transactional commit.
- `revision_requested` creates a successor task preserving route, predecessor task/result digest, feedback and current input refs. It does not return to document routing.
- Source, parse or active canonical revision changes invalidate the submission basis. Late/stale submit is rejected and a fresh successor is required.

### Prompt injection and rendering

PDF bytes, parsed text, metadata, Agent output and Exchange records are data. Their text cannot:

- expand allowed content or execution scope;
- request additional filesystem/network access;
- alter expected output contracts or actor identity;
- trigger commands, tools, links, embeds or writes;
- suppress validation, preview or approval.

The App renders escaped text and sanitized Markdown under CSP. Allowed links use explicit schemes and safe targets; arbitrary HTML, script, event handlers, remote embeds and path traversal are rejected. Obsidian generated views use an allowlist for Markdown/HTML, links, embeds and managed paths. Exchange content is validated in confined staging before any render.

### Portable Skill ownership

- Editable Skill source has one declared owner outside generated installation mirrors.
- A repository release contains an exact, reviewable Skill snapshot tied to a Core interface version.
- Codex/Claude installation mirrors are generated outputs and are not edited by hand.
- Authoring source, release snapshot and installed mirror fingerprints are checked separately; generation cannot silently overwrite local user-owned content.
- Skill logic orchestrates Core and Agent judgment only; it does not duplicate deterministic validation, IDs, transactions or scientific-record writes.

## Consequences

- P4 owns the first task schema/runtime and must materialize the registry only after separate approval.
- Deterministic adapter conformance is CI; bounded live Agent smoke is optional/non-deterministic and cannot gate CI on login, network, cost or model drift.
- P0 creates no Agent Task, prompt bundle or staging runtime.
