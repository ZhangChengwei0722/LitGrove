# P5-B Evidence Source Access Implementation Plan

- status: `approved_for_unattended_implementation`
- prepared_at: `2026-08-02`
- branch: `feature/p5b-evidence-source`
- baseline: `c932ba9f72709a109f71351b73d86acce4b32b6c`
- target_application_service_interface: `1.8`
- canonical_schema_change: false
- workspace_layout_change: false
- next_gate: `tests_then_implementation`

## Objective

Extend the read-only `ReadingApplicationService` with a backend-only Evidence source
handle and revalidated PDF read boundary. The App may stream or explicitly hand the
validated source to a local reader without receiving path authority from the browser.

## Contract

- resolve one Evidence ID through its exact legacy or revisioned Primary owner;
- bind an immutable in-memory handle to Evidence/revision digests, source fingerprint,
  exact source ref, PDF page and locator;
- allow an exact historical manifestation when the active source head has changed;
- on each read, reload canonical records, verify handle lineage, open the regular file,
  enforce `512 MiB`, hash the opened bytes, require the expected digest and PDF header
  signature, then rewind the same descriptor;
- expose no browser response, durable record, schema, ID namespace or write path;
- keep source ref, fingerprint and absolute path backend-only;
- advance Application Service interface/capability to `1.8`.

## Tests First

- active Evidence opens its generated synthetic PDF with exact page and locator;
- historical Evidence opens its own exact source/parse lineage;
- same-digest relink remains usable;
- missing, unsafe, changed, non-PDF, oversized and ambiguous sources fail closed;
- changed Evidence or revision after handle issue rejects stale reuse;
- descriptor bytes match the registered source and knowledge/source trees remain
  byte-identical;
- focused, full, build, base/PDF wheel, privacy and diff validation pass.

## Stop Boundary

Do not add HTTP, Range parsing, browser tokens, PDF.js, UPDF launching, source mutation,
canonical schema, migration, private fixtures, Knowledge Query or later-phase features.
