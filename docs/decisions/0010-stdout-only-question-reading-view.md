# ADR 0010: Stdout-Only Question Reading View

- status: accepted_for_m2b_2

## Decision

Shared Core renders one deterministic Question Reading View from one validated Question Mapping and its reachable structured records. The view combines question scope, mapping state, linked papers and selected Card Units, canonical evidence trace, review queue boundaries, and freshness diagnostics in one Markdown document.

The CLI command is `research-kb question render --workspace <workspace> --question-id <question_id>`. Success writes raw UTF-8/LF Markdown to stdout. The renderer constructs the complete byte sequence in memory before the first write and never adds a JSON envelope, progress message, render timestamp, local path, or source filename.

The view expands only the Card Units, canonical evidence IDs, and boundary IDs already present in the selected mapping. Queue boundaries remain visibly non-evidence. A SHA-256 digest identifies the exact reachable structured inputs, and the existing mapping freshness diagnostic supplies the independent `current` or `stale` display state.

## Storage And Authority

M2B-2 creates no `views/` store, Markdown file, cache, view manifest, process event, transaction journal, Guardian report, schema, ID namespace, or layout version. Structured JSON/YAML/JSONL records remain canonical inputs. The Markdown is a generated reading surface with `canonical: false` and `editable_source: false`; it cannot be edited back into structured state.

## Rejected And Deferred Alternatives

An overview/evidence split was rejected because one complete question view is sufficient for the first usage test and avoids duplicate rendering contracts. Persisted Markdown was deferred because it would require export paths, freshness ownership, Guardian behavior, and lifecycle rules. A generic renderer registry was deferred until a second shared renderer demonstrates a real abstraction need.

Evidence matrices, relation views, persisted exports, Step 7 rendering, private legacy compatibility comparison, template selection, and a Portable Agent Skill remain later, separately approved work.
