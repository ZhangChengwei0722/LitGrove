# ADR 0016: Common Review Memory Runtime

- status: accepted_for_m3a_2a

## Decision

Shared Core persists at most one Review Memory for a registered review paper. Five supported review subtypes share one common seven-section contract. The record is a reusable reading decision cache, not a generic summary and not canonical Evidence.

Core owns `reviewmem_*` and `reviewunit_*` IDs, source fingerprint, active parse snapshot, timestamps, automation state and repeated non-evidence boundary constants. Every retained Unit has same-review page/section provenance and at least one concrete workflow impact.

## Runtime

Review Memory uses the existing `record promote` transaction path and is stored at:

```text
review_memories/by_paper/<paper_id>.review.json
```

Append and replace require a current immutable source and stable active parse. Replace preserves the Memory ID, creation time and submitted existing Unit IDs; new Units receive new Core-owned IDs. A paper cannot own both Review Memory and primary Paper Card/Evidence records.

`review context` is the only public Review Memory recovery surface. It returns the complete selected memory, `absent/current/stale_parse` freshness and transient exact local DOI matches. A parser change creates a Guardian warning; old source notes are never reinterpreted against the new parse.

## Agent Boundary

The existing Portable Skill gains one review-specific route. The Agent classifies the subtype in task memory, reads enough parsed pages for the declared coverage, retains only reusable Units, and submits candidates through Core. It cannot assign human-only states or turn review text into Evidence.

## Deferrals

M3A-2A does not add subtype-specific schemas, PRISMA or formal bias-assessment engines, Field Map integration, Review Unit Question Mapping, Step 7, discovery, acquisition, OCR, supplement processing, private fixtures or local Skill installation.
