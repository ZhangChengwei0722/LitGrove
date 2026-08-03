# P7-C Tags Implementation Plan

- status: `approved_for_unattended_implementation`
- prepared_at: `2026-08-03`
- core_baseline: `main@ee30b53ea166a72a63cda8296c6659633586c5c9`
- app_baseline: `feature/p7b-organization-work-surface@ec666dc`
- core_branch: `feature/p7c-tags`
- app_branch: `feature/p7c-tags-work-surface`
- parent_design: `bounded P7 organization and screening design maintained outside this shared repository`
- canonical_schema_change: true
- operational_schema_change: false
- migration: false
- private_scientific_workspace_access: false
- next_gate: `tests_then_core_implementation`

## 1. Objective

Deliver a deterministic, local Tag vocabulary and assignment surface for large workspaces:

```text
user-defined Tag identity
-> append-only definition revision
-> explicit target assignment or removal
-> Core validation and atomic commit
-> Catalog facet projection and Guardian checks
-> App browse, filter and edit surface
```

Tags are user-owned organization metadata. They do not express scientific support, do not
carry Evidence, and do not require an Agent Task. Existing Review Memory `scope_tags` remain
document metadata and are not imported, aliased or promoted into this Tag vocabulary.

## 2. Design Decisions

1. A Tag has a stable Core-allocated ID and an append-only definition bundle. Rename,
   description change and archive create successor revisions; no canonical record is edited
   in place.
2. A Tag assignment has its own stable Core-allocated link ID and append-only state bundle.
   The active state is `assigned` or `removed`. Exact replays are no-change.
3. One assignment binds exactly one Tag to exactly one current target identity. Initial
   target kinds are `paper`, `direction`, `field_map_entry` and `question`.
4. Assignment does not copy target text, source paths, Paper Card Units, Review Units or
   Evidence. Target revisions may change without invalidating the assignment identity.
5. Tag definition and assignment writes require explicit App user action. Agent-generated
   Tag inference, automatic tagging and organization-proposal Tag payloads remain excluded.
6. Tags are local authority in P7-C. Exchange identity, external-origin Tags and cross-
   workspace merge policy remain P10 concerns.

## 3. Definition Semantics

Each current Tag definition contains a display name, normalized lookup key, optional short
description, aliases and `active | archived` status. Core owns normalization and rejects:

- empty or over-budget names and descriptions;
- duplicate normalized active names or aliases across stable Tag identities;
- aliases equal to the current name or duplicated after normalization;
- new assignments to an archived Tag.

Rename preserves the previous display name as an alias unless that would violate the
closed uniqueness rules. Duplicate Tags are never merged automatically. P7-C reports the
conflicting stable ID and requires the user to reuse it or choose a distinct name. Archive
retains history and assignments; it only removes the Tag from default active choices and
blocks new assignments. Physical deletion is not implemented.

## 4. Assignment And Freshness Semantics

Tag assignment targets are validated by Core:

- `paper`: one Registry identity that is not tombstoned;
- `direction`: one existing Direction bundle;
- `field_map_entry`: one existing Field Map Entry bundle;
- `question`: one current legacy-compatible or P7 Question identity.

An assignment remains attached to stable target identity when a target receives a successor
revision. Reads project bounded target availability separately from assignment state. A
missing, split, tombstoned or structurally invalid target projects `target_unavailable` and
is reported by Guardian; history is not rewritten. An archived Tag remains readable and
does not make old assignments structurally stale.

Assign and remove operations are idempotent and optimistic: callers supply the expected
current assignment state when one exists. Core allocates IDs and timestamps, validates the
current Tag/target basis, and commits one definition or assignment bundle atomically.

## 5. Storage And Contracts

Add optional stores under the existing organization root:

```text
knowledge/organization/tags/by_id/<tag_id>.tag-bundle.json
knowledge/organization/tag_links/by_id/<tag_link_id>.tag-link-bundle.json
```

The schema slice defines only the fields required by the decisions above. Bundles use the
existing canonical serialization, immutable revision, predecessor-digest, approval and
transaction conventions. Bootstrap/upgrade creates empty optional directories only under
existing workspace authority. Existing workspaces remain readable without migration.

Add stable ID namespaces for Tag, Tag definition revision, Tag link and Tag link revision.
Do not reuse Review Memory slugs or derive stable IDs from display names.

## 6. Core Services, Catalog And Guardian

Add a focused Tag service for create/revise/archive/list/show and assign/remove/list by Tag
or target. Add a session-bound Tag Application Service with closed request objects, cursor
pagination, stable sorting, bounded page sizes and path-free responses. Advance the public
Application Service interface only after the new methods and capability are complete.

Catalog integration must:

- project Tag definitions as searchable organization items;
- attach current active Tag IDs/names as disposable facets to taggable target projections;
- rebuild entirely from canonical Tag and assignment bundles;
- preserve target item identity and cursor stability when Tags change;
- refresh only affected projection records for incremental mutation.

Guardian must check revision order/digests, one active head, stable ID ownership, normalized
name/alias uniqueness, assignment uniqueness, target existence and state, archived-Tag
assignment rules, Catalog freshness and transaction recovery. Guardian reports findings;
it does not delete, merge or silently repair Tags.

## 7. App Work Surface

After Core merge and exact wheel pinning, add a first-class Tag surface and lightweight
facets to existing Library and Research Organization reads. The App supports:

- list, search and inspect active or archived Tags;
- create, rename/revise and archive with escaped preview;
- assign or remove a Tag from one Paper, Direction, Field Map Entry or Question;
- show target availability, archived state, no-change and conflict outcomes;
- filter supported Library/organization lists by selected current Tag.

All mutations use session authentication, exact Host/Origin, CSRF, strict bodies and the
existing operation coordinator. The browser never allocates IDs, writes canonical files or
uses untrusted Markdown. No Agent selector or handoff is shown for deterministic Tag work.

## 8. Tests First

### Core

1. schema and stable ID contract tests for both bundle kinds;
2. create, exact no-change, revise, rename alias preservation and archive;
3. normalized duplicate name/alias rejection and archived assignment rejection;
4. assign/remove/reassign idempotency for all four target kinds;
5. target successor revision continuity and unavailable-target projection;
6. concurrent/stale expected-state rejection and transaction recovery;
7. Catalog search/facet rebuild and bounded incremental refresh;
8. Guardian structural, uniqueness, target and projection findings;
9. session-bound privacy/redaction, installed-wheel and compatibility smokes.

### App

1. Core compatibility and capability fail-closed tests;
2. strict Tag read/mutation API security and error-redaction tests;
3. React states for empty, active, archived, duplicate, no-change and unavailable target;
4. assignment/removal plus Library and organization facet filtering;
5. desktop/mobile Playwright flow on synthetic fixtures;
6. packaged fresh-install production-start smoke.

## 9. Delivery Sequence

### P7-C1 Core

- branch from `main@ee30b53`;
- write focused failing tests, then contracts/storage/service/Application Service;
- add Catalog and Guardian integration only after canonical behavior passes;
- run focused and complete Windows validation, compile, build, installed-wheel smokes,
  privacy scan and diff review;
- commit, push, open and merge a small PR when GitHub is available;
- record the exact merged Core commit and wheel digest.

### P7-C2 App

- branch from App `ec666dc` after exact Core wheel pinning;
- implement backend routes before the Tag UI and list facets;
- run Python, Vitest, TypeScript, ESLint, Vite, Playwright, package and fresh-install
  validation;
- commit locally. The App repository has no remote, so no push or PR is invented.

### P7-C3 Closure

- write Core/App validation receipts and closure manifests;
- synchronize repository architecture, contributor and milestone records;
- retry the parent `E:` design/overall-plan status sync without blocking Core/App closure;
- run mandatory `neat-freak` and update durable project records.

Generated test/build workspaces remain retained until P11 and overall completion.

## 10. Stop Boundary

P7-C does not add:

- automatic or Agent-inferred Tags;
- Unit-, Evidence-, Review Memory- or Research Synthesis-level tagging;
- Tag hierarchy, ontology reasoning, synonym inference or automatic merge;
- Question-specific Screening, criteria or decisions;
- Research Synthesis generation or persistence;
- Exchange identity/merge policy, Obsidian runtime or citation graph;
- legacy/private-workspace access, real PDFs, migration or legacy cutover;
- cleanup or deletion of retained benchmark/test workspaces.

Question-specific Screening remains P7-D. Research Synthesis remains P8.
