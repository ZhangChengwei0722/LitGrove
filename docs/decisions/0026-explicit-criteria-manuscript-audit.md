# 0026: Keep Explicit-Criteria Manuscript Audit In The Agent Layer

## Decision

M3D-1 adds one Portable Skill mode, `manuscript_audit`, over the existing read-only M3D-0A projection and public knowledge reads. The route requires one or more criteria stated for the current invocation and exact user-supplied or current-request-resolved question/paper selectors before manuscript inspection. It returns one private scope-limited report with `persistent_writes: 0`.

## Rationale

Criteria interpretation, section/claim decomposition, comparison and writing judgment are semantic Agent responsibilities. Core already owns the deterministic inputs needed for this task: source confinement, fingerprints, stable manuscript units, Card Units, canonical Evidence and non-evidence boundaries. A Core audit engine or persisted claim map would add provider coupling, schema lifecycle, stale-source semantics and private state without a demonstrated need.

## Boundaries

The Agent preserves original criterion wording, does not add default dimensions or widen the corpus, and degrades uncertain character spans to stable unit locators. Exact factual support expands from grounded/revised Card Units to canonical Evidence. Review Memory, review queue and Step 7 cannot support factual findings. No CLI command, capability flag, schema, ID, layout, store, event, journal, cache, Markdown view, manuscript rewrite, private-workspace access or real manuscript fixture is added.
