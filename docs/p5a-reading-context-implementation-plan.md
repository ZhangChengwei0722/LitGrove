# P5-A Reading Context Application Service Plan

- status: `approved_for_unattended_implementation`
- prepared_at: `2026-08-02`
- branch: `feature/p5a-reading-context`
- baseline_commit: `20665e2e77d0cb28fee68512db79df2466d5b163`
- baseline_tree: `91c9e56fc690b8a63aedf2fa8267f015e754a6ed`
- remote_main_equivalent: `145617b22a904566c62f6675fe23c1458cd806f3`
- current_application_service_interface: `1.6`
- target_application_service_interface: `1.7`
- next_gate: `p5a_core_validation_and_app_plan`

## Objective

Add one privacy-safe, read-only Core facade that gives the App a complete paper reading
model and exact Evidence trace descriptor without exposing local paths or introducing a
new scientific record:

```text
paper ID
-> Registry bibliography and route
-> active Primary Paper Card or Review Memory
-> source / parse / adequacy badges
-> Question context
-> Card Unit Evidence links

Evidence ID
-> owning Primary revision
-> bound source fingerprint and parse run
-> page / locator / quote
-> current source availability and parse-materialization state
```

Committed semantic content remains readable when the source is unavailable or stale. The
service must downgrade trace-back availability instead of erasing the Card/Review result.

## Changes

1. Add `ReadingApplicationService` behind an opaque `WorkspaceSession`.
2. Add `show_paper(session, paper_id)` with Registry bibliography, document route, active
   revision identity, full seven-section Paper Card or Review Memory, Source Adequacy
   projections and current Question links.
3. Add `compare_papers(session, paper_ids)` as a deterministic ordered batch of two to four
   reading contexts. It performs no semantic comparison or synthesis.
4. Add `trace_evidence(session, evidence_id)` that searches Primary bundle revisions,
   rejects ambiguous IDs, and binds the Evidence to the exact owning revision input
   snapshot, page, locator and quote.
5. Resolve current source state against the revision fingerprint. A same-digest relink may
   remain traceable; missing, inaccessible, changed or unresolved historical bytes produce
   explicit states and `trace_back_available: false`.
6. Compare the bound parse run with the materialized current parse. Never substitute the
   active parse for a historical parse; report `current`, `historical_not_materialized` or
   `unavailable`.
7. Project Unit factual admissibility explicitly: only `grounded` and `revised` Card Units
   are factual-support eligible. `interpretive`, `background_only` and `needs_resolution`
   remain visible but excluded from factual support.
8. Label every Review Memory/Unit as background-only and non-Evidence.
9. Advance the Application Service interface to `1.7`; expose the facade through package
   exports, capability facts, README/architecture/workflow and installed-wheel smoke.

## Contract Boundary

This batch adds no schema, ID namespace, canonical/operational store, workspace directory,
transaction, Agent Task kind, report output or source-write operation. Return values are
JSON-safe projections with no `Path`, `source_ref`, root ID, relative path, credential,
raw source document or raw parsed page body. Digests remain internal matching inputs; the
App receives semantic freshness states rather than local identity material.

The P4 Primary bundle is the Evidence provenance owner. Historical Evidence remains
addressable because each revision uses unique Evidence IDs and retains its input snapshot.
P5-A does not claim that a superseded parse file is still materialized; P5-B will open the
exact source PDF by the matched manifestation and use the Evidence page/quote.

## Validation

- Primary reading returns all seven configured sections in stored order;
- Review reading returns sections/Units and explicit background-only boundaries;
- legacy committed Paper Card/Review Memory remains readable through the same facade;
- source missing or digest mismatch leaves semantic content readable but disables current
  trace-back;
- one historical Evidence ID resolves to its historical revision and is never remapped to
  the active revision or active parse;
- same-digest relink remains traceable without treating a path change as scientific stale;
- changed bytes do not silently update the bound fingerprint;
- grounded/revised Units are factual eligible and every other status is excluded;
- Question context is optional and deterministic;
- compare accepts two to four unique IDs, preserves request order and rejects duplicates or
  oversized input;
- service methods perform zero persistent writes and leave workspace tree digests unchanged;
- complete Windows suite, compile, build, base/PDF wheel smoke, privacy scan and diff check
  pass.

## Stop Boundaries

Do not add PDF byte streaming, PDF.js, UPDF launch, report-only Agent Tasks, Direction or
Field Map context, Research Synthesis, Discovery, Exchange, Obsidian, and legacy private-workspace
access, migration, legacy cutover or generated-workspace cleanup.
