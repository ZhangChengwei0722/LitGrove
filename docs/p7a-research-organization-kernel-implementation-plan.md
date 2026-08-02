# P7-A Research Organization Kernel Implementation Plan

- status: `approved_for_unattended_implementation`
- prepared_at: `2026-08-03`
- branch: `feature/p7a-organization-kernel`
- baseline: `origin/main@d37f42540a683b7c02b624ef04a0fbd1dc8c5fce`
- parent_design: `bounded P7 organization and screening design maintained outside this shared repository`
- target_application_service_interface: `1.11`
- agent_registry_change: false
- canonical_schema_change: true
- workspace_layout_change: `additive_optional_organization_stores`
- migration: false
- q001_or_private_workspace_access: false
- next_gate: `tests_then_p7a_core_implementation`

## 1. Objective

Deliver the deterministic P7 organization kernel before any Agent proposal or App writer is
enabled:

```text
current Primary/Review semantic Units
-> strict factual/background admissibility
-> append-only Direction or Field Map target revision
-> append-only Question successor revision over legacy-compatible base
-> lazy freshness
-> Catalog / Guardian / session-bound reads
```

The kernel owns IDs, validation, atomic revisions, dependency checks and projections. It
does not decide scientific mappings and does not expose a browser mutation route.

## 2. Design-To-Implementation Review

The bounded P7 design maps to the current Core as follows:

1. Reuse the current active Primary/Review bundle projections. Historical Unit IDs remain
   audit material and are not current mapping inputs.
2. Treat only Primary `grounded` and `revised` Units with current Evidence closure as
   factual. Existing direct Question promotion must reject `interpretive`,
   `background_only` and `needs_resolution` as new factual inputs.
3. Keep legacy `knowledge/questions/mappings.jsonl` immutable as a compatibility base.
   P7 Question successors live in a separate per-Question revision bundle; no migration or
   duplicate stable ID is appended to the legacy JSONL.
4. Use one atomic per-target bundle for each Direction, Field Map Entry or P7 Question.
   A bundle contains immutable ordered revisions and projects exactly one active revision.
5. Embed sparse Unit links in the owning target revision. Do not create one generic global
   relationship store or copy Unit text.
6. Expose reads through a new session-bound Research Organization Application Service.
   Canonical promotion remains Core-only until P7-B adds Agent staging and App approval.
7. Register Catalog adapters through the existing adapter registry. Do not change Catalog
   authority or index absent future P7 types before their contracts exist.

No unresolved design decision blocks P7-A.

## 3. Contract And ID Slice

Add stable namespaces for:

- Direction;
- Field Map Entry;
- organization revision;
- organization Unit link;
- Question revision;
- Question background link.

Add closed schemas for:

- Direction bundle and revision;
- Field Map Entry bundle and revision;
- P7 Question revision bundle;
- shared organization Unit-link shapes where exact reuse removes duplication.

Every bundle binds stable target ID, ordered revision number, revision ID, predecessor
ID/digest, canonical content digest, approval receipt and timestamps. Revisions are
append-only; exact reruns are no-change.

Primary factual links contain source paper/Card Unit IDs and Core-derived Evidence IDs.
Primary contextual links contain no Evidence IDs and are visibly non-factual. Review links
contain source paper/Review Memory/Review Unit IDs and Core-owned
`background_only=true`, `can_enter_canonical_evidence=false` and `not_fact=true`.

## 4. Storage And Compatibility Slice

Add optional stores beneath a dedicated organization root:

```text
knowledge/organization/directions/by_id/<direction_id>.direction-bundle.json
knowledge/organization/field_map/by_id/<field_map_entry_id>.field-map-bundle.json
knowledge/organization/questions/by_id/<question_id>.question-revision-bundle.json
```

The additive layout upgrade creates empty directories only through existing bootstrap or
upgrade authority. Existing workspaces remain readable without immediate materialization.
No legacy Question record is copied, edited or deleted.

Question current-read precedence is deterministic:

1. current P7 Question revision when a bundle exists;
2. otherwise the validated legacy Question Mapping record;
3. conflicting duplicate P7 owners fail closed.

The first P7 successor of a legacy Question records the legacy record digest as predecessor
basis. A new P7 Question allocates one stable Question ID and its first revision in one
transaction.

Once a P7 bundle exists for a Question, the direct legacy `QuestionMappingService` writer
must reject further mutation of that stable Question ID. This prevents split authority
between the legacy base and its P7 successor chain.

## 5. Admissibility And Freshness Slice

Before any new factual link is staged for promotion, Core verifies:

- the paper exists and has one current active Primary authority;
- the selected Card Unit belongs to the active revision;
- `grounding_status` is `grounded` or `revised`;
- every Core-derived Evidence ID exists, belongs to the same paper and is current;
- no caller-supplied Evidence union differs from Core derivation.

Review background links verify current Review authority, retained Unit membership,
same-review provenance closure and background constants. They never derive Evidence IDs.
Primary contextual links may use only current `interpretive` or `background_only` Units,
must carry a non-factual role and contain no Evidence IDs. `needs_resolution`, rejected and
superseded Primary Units are rejected for every current link role.

Lazy freshness reasons cover:

- source Unit superseded;
- Evidence missing, stale or inadmissible;
- Review provenance/source revision superseded;
- predecessor or target-head mismatch;
- linked Direction revision unavailable for a Field Map Entry.

Freshness does not rewrite canonical bundles. Catalog and Application Service reads project
`current`, `stale_upstream` or `unavailable` with bounded reasons.

## 6. Deterministic Services

Implement focused Core services:

- promote/read/list Direction bundles;
- promote/read/list Field Map Entry bundles;
- create/succeed/read/list P7 Question revisions;
- project current organization context for one or more source papers;
- report duplicate/corroborating links as no-change;
- expose current and stale links without treating stale factual links as support.

Promotion accepts only a complete already-approved semantic payload with explicit
`actor: user` and a user-approved origin. P7-A tests this lower-level authority but adds no
Agent Task, App endpoint or CLI mutation command. P7-B will be the only product route that
converts an Agent proposal into this approved payload.

Advance the public Application Service interface to `1.11` with read-only session-bound
methods for:

- limits;
- list/show Directions;
- list/show Field Map Entries;
- list/show current Questions with compatibility source;
- show organization context by paper/target IDs.

The facade returns IDs, definitions, roles, rationales, freshness and bounded counts. It
does not return paths, fingerprints, raw stores, transaction handles or writer authority.

## 7. Catalog And Guardian Slice

Register versioned adapters only after each P7-A contract exists:

- Direction item;
- Field Map Entry item;
- current Question projection, preferring P7 revision over legacy base.

Catalog stores derived searchable definitions and stable IDs, not copied Card/Review text.
Legacy and P7 representations of one stable Question must project one stable Catalog item
ID and never appear as duplicate rows or change cursor identity merely because a successor
revision exists.
Rebuild remains disposable and canonical trees remain byte-identical.

Guardian checks:

- bundle revision order, predecessor digest and one active head;
- target/link ID uniqueness;
- factual/background admissibility and Core-derived Evidence closure;
- active Unit/revision ownership;
- legacy/P7 Question precedence and conflicting owners;
- Field Map-to-Direction references;
- stale links are excluded from current factual support but retained for audit.

## 8. Tests First

Write focused failing tests in this order:

1. ID, schema and additive layout contracts;
2. active Primary/Review Unit resolution and strict factual/background admissibility;
3. Direction append/revision/idempotency and stale source Unit behavior;
4. Field Map Entry links, duplicate corroboration and Direction refs;
5. legacy Question read, first successor, new Question and active revision precedence;
6. direct legacy Question promotion rejection for non-admissible factual Units;
7. Evidence/revision/provenance stale edges;
8. Catalog adapters, cursor pagination and delete/rebuild equivalence;
9. Guardian findings and Application Service browser-safe projections;
10. installed-wheel capability/interface smoke and privacy scan.

Existing P6 behavior and all older schema/layout versions remain regression coverage.

## 9. Validation And Delivery

- focused contract/unit/integration tests;
- complete Windows pytest, sharded only if the monolithic process exceeds the tool timebox;
- `compileall`;
- sdist/wheel build;
- base and PDF installed-wheel smoke;
- package/version/Application Service capability checks;
- privacy scan with zero unexpected findings;
- synthetic canonical/projection digest checks;
- `git diff --check` and full diff review;
- implementation commit followed by validation receipt and closure manifest;
- push/PR/merge when GitHub is available, then exact merged-wheel artifact receipt;
- mandatory `neat-freak` before P7-A closure reporting.

Generated workspaces remain retained until P11 and overall project completion.

## 10. Stop Boundary

Do not add in P7-A:

- Agent Task registry/result contracts or Portable Skill changes;
- App HTTP routes or UI;
- personal Tags;
- Screening Sets, criteria, recommendations or decisions;
- Research Synthesis writes or refresh;
- Obsidian, Exchange, citation graph or related-paper navigation;
- discovery/provider changes;
- persistent review-conflict database;
- legacy scientific workspace or real PDF access;
- migration, legacy cutover, cleanup or deletion.

If a legacy Question cannot be represented without migration, fail closed and record the
case for P7-A compatibility follow-up; do not rewrite it in place.
