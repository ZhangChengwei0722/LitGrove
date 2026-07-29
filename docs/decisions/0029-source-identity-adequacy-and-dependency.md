# 0029 Source Identity, Adequacy And Dependency

Status: accepted

## Context

Scientific trace-back depends on exact source bytes and exact parse output. A path is not an identity, one global parse pass/fail bit is insufficient, and stale results must not silently support new factual answers.

## Decision

### Source manifestations

- A source manifestation is identified by its digest plus its declared asset role and source reference.
- Before Parse, Evidence/Review trace-back, semantic commit and Guardian source checks, Core recomputes the digest of the actual source bytes.
- Same path and same digest preserves the manifestation. Same path with changed bytes creates a new manifestation and marks dependent parse/adequacy/scientific records stale; the old fingerprint is never edited in place.
- Relink is permitted only after exact digest equality. It updates availability/location association without rewriting historical provenance.
- Availability is projected as `available`, `missing`, `inaccessible` or `relink_required`. Missing bytes do not delete historical Evidence, but factual use must expose that current trace-back is unavailable.
- External roots remain read-only. Copying into `local_inbox` is separately authorized, create-only and records a new local manifestation.

### Source Adequacy

Source Adequacy answers whether exact source assets and one active parse can support a requested use. It does not judge scientific credibility.

An assessment is bound to requested use, source references/digests, parse identity, parser name/version, parse-output digest, rule version and assessment time. A changed source, active parse, parser identity, parse output or rule version makes the assessment stale. Adequacy for one use cannot be reused for another.

Capabilities are independent. Initial semantics include:

- `basic_paper_understanding`;
- `continuous_text_traceback`;
- `figure_table_traceback`;
- `formula_layout_analysis`;
- `supplementary_material_analysis`.

Missing supplementary material or unreliable figure order blocks only operations that consume those capabilities; it does not block an otherwise adequate basic Card/Review Memory.

### Multiple assessors and hard failures

Assessment retains three distinct layers:

1. machine observations: digest, readability, page count, locator reproducibility, parser identity and deterministic extraction checks;
2. Agent assessment: reading order, layout quality and use-specific semantic sufficiency;
3. user decision: explicit override or routing decision where policy allows.

Deterministic hard failures such as digest mismatch, unreadable file, missing required asset or irreproducible locator cannot be changed to adequate by an Agent. A user may choose a different source/parse or knowingly stop the task, but cannot rewrite the observation. Final capability is the most restrictive applicable decision plus a reason trail.

### Consumption gates

Every Evidence operation declares its requested capability before staging. `current + adequate` permits staging; `no`, `uncertain` or `stale` routes to a Pipeline Job waiting for source or reparse and creates no Evidence or scientific review-queue entry.

Review Memory may use `basic_paper_understanding` for basic orientation. Every retained Review Unit additionally consumes the capability required by its source note: continuous text, figure/table, formula/layout or supplementary-material trace-back. An inadequate Unit is revised, narrowed or rejected before staging; the rest of the Review Memory may continue.

### Progressive dependency graph

The dependency graph is semantic and phase-owned, not inferred from directory timestamps:

| Owning phase | Required edge closure |
|---|---|
| P0 | Freeze node/edge ownership, reasons and destinations. |
| P2 | canonical/operational records -> projection rows and read models. |
| P3 | source manifestation -> parse -> Source Adequacy capability. |
| P4 | source/parse/adequacy -> Evidence or retained Review Unit -> Paper Card/Review Memory revision. |
| P7 | admissible Card Unit/Evidence/Review background -> Question/Direction mappings and criteria-bound screening. |
| P8 | Question/Card/Evidence/background inputs -> Research Synthesis candidate. |
| P9 | canonical source watermark -> generated view. |
| P10 | external-origin revision/trust/availability -> local import projection only until approved. |
| P11 | lazy freshness projection and deduplicated maintenance scheduling. |

Each new writer must implement and test its outgoing stale edges before its phase exits. Invalidations preserve historical records, set factual eligibility appropriately, and route work to the owning Pipeline Job, Agent Task, Guardian finding or generated-view refresh. No generic `needs_resolution` sink is allowed.

### Canonical and Registry correction

Approved Paper Card, Evidence and Review Memory corrections create a successor revision through staging, App preview and user approval. The successor supersedes the old revision; history is neither overwritten nor deleted.

Registry identity correction supports auditable duplicate merge, mistaken-merge split, alias, Library archive and tombstone. Historical IDs and references remain resolvable, and no correction physically deletes or rewrites identity history.

### Knowledge Query default admissibility

```text
local + committed + admissible + current
-> may support factual output

stale_upstream or external_unreviewed
-> may be displayed with status, but does not support facts by default

Review Memory
-> labeled background only
```

Evidence uses its own historical `source_ref + parse_ref + locator`. An active parse is used only for freshness comparison or explicit remapping, never as a substitute for provenance.

## Consequences

- P3 must introduce the bounded Source Adequacy contract before automatic semantic routing.
- P4 tests include basic-Card adequacy with blocked figure Evidence and missing-SI rejection.
- P1/P2 do not materialize Source Adequacy or future dependency records.
