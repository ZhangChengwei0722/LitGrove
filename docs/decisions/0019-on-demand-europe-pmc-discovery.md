# ADR 0019: On-Demand Europe PMC Metadata Discovery

- Status: Accepted
- Date: 2026-07-21

## Decision

Add a workspace-independent `discovery search` command with a provider-neutral connector protocol and one built-in `europe-pmc` connector. The request supplies explicit inclusive dates, field-bound title/abstract keywords, `any` or `all` matching, preprint inclusion and a maximum of 1-15 results.

Core calls only the fixed Europe PMC HTTPS search endpoint. It bounds request and response bytes, timeout, pages and raw results; rejects redirects; ignores provider-returned URLs; validates provider shape; reapplies date, keyword and preprint filters locally; merges exact normalized DOI identity; and marks similar-title records without merging them.

Success emits one transient interface `1.0` JSON report with `persistent_writes: 0`. The command creates no workspace, candidate, event, journal, cache, source file or Registry record. The Portable Skill exposes a separate `on_demand_discovery` route and stops before approval persistence, acquisition or intake.

## Determinism And Trust

Public provider state is mutable. The guarantee is that the same validated request and same provider page payloads produce the same normalized bytes, not that two live searches at different times are identical. Provider metadata is untrusted external input and cannot become Paper Card, canonical Evidence or a verified claim without separately acquired and processed full text.

## Consequences

- No JSON Schema, workspace layout, ID namespace or dependency changes.
- `RKBC-032` reports connector/transport failure; `RKBC-033` reports invalid provider output.
- Capability probing advertises the connector without network access.
- Automated tests use fake transports and invented metadata only.
- Crossref, candidate persistence, user-approval records, OA resolution, downloads, browser login, version replacement, scheduled discovery and downstream intake remain separate work.
