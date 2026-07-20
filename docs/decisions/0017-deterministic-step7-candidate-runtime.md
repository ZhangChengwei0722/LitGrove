# ADR 0017: Deterministic Step 7 Candidate Runtime

- Status: Accepted
- Date: 2026-07-21

## Decision

Persist Synthesis, Review Angle, Insight and Cross-View candidates in four type-specific JSONL stores under `step7/`. Use the existing `record promote` mutation boundary for append and complete semantic replace. Require an existing current Question Mapping and selected grounded/revised Paper Card Units.

The Agent owns scientific candidate content. Core owns candidate identity and type, exact canonical Evidence and selected-Unit review queue closure, the input snapshot, timestamps and fixed `not_fact: true`, `review_status: ai_draft`, `automation_status: pending` states. Review queue records remain non-evidence, and Review Memory cannot support Step 7.

Expose `step7 context` as the structured recovery surface and `step7 render` as a one-way stdout-only Markdown reading view. Guardian reports valid upstream drift with `RKBC-014` and does not refresh records. Missing references, impossible ownership, unexplained closure mismatch and cross-question Cross-View references remain integrity errors.

Advance the workspace layout from exact predecessor `m3a-2a` to `m3b-1` by creating only an empty `step7/` directory and replacing operational marker metadata.

## Rationale

Paper Card Units are the reusable semantic entry, while canonical Evidence remains the provenance backbone. Deriving closure in Core avoids a second Agent-maintained evidence list and keeps candidate reasoning traceable without claiming that Core can judge scientific correctness.

Stale records must remain readable because upstream grounding evolves. Treating all drift as corruption would block unrelated work; treating malformed references as mere staleness would hide damage. The runtime therefore separates deterministic freshness projection from structural validation.

## Consequences

- Candidate scientific text is stored only in its Step 7 JSONL record and explicit context/render output, never in events or journals.
- Markdown remains disposable and cannot be edited back into structured state.
- Synthesis requires two distinct papers. Cross-View sources must be current, admissible and from the same question at promotion time.
- Core does not call an LLM, generate candidates, judge novelty, merge duplicates or choose refresh actions.
- Review Unit support, Field Map integration, persisted Markdown, discovery, acquisition, migration and Portable Skill Step 7 orchestration remain separate work.
