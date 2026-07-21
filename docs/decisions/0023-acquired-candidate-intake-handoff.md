# ADR 0023: Acquired Candidate Intake Handoff

## Decision

Add `intake inspect-acquired` as a network-free, zero-write bridge from one acquired discovery candidate to the existing intake projection. It validates the receipt source before and after inspection and returns portable source fields plus deterministic Registry bibliography input.

## Rationale

The acquisition receipt already owns the source identity, while `RegistryService` already owns paper IDs, hashing, duplicate links and transaction semantics. A candidate-specific read command removes absolute-path reconstruction from the Portable Skill without creating another registration implementation.

## Boundary

Acquisition-only tasks still stop before Registry. A separate explicit intake request may pass the projection unchanged to `registry add`. The candidate remains `metadata_only` and `not_evidence: true`; this bridge creates no Parse, Paper Card, Review Memory, Evidence, Question Mapping or Step 7 record.

## Consequences

No schema, layout or migration is required. The candidate and Registry paper remain linked deterministically through the same `source_ref` and fingerprint rather than a new foreign-key field. Exact reruns use `registered_current` and write nothing.
