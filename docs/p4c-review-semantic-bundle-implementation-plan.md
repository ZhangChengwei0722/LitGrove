# P4-C Review Semantic Bundle Implementation Plan

- status: `reviewed_unattended`
- prepared_at: `2026-08-01`
- branch: `feature/p4c-review-semantic-bundle`
- baseline: `origin/main@8342de9`
- required_application_service_interface: `1.4`
- required_agent_task_registry: `p4c-v1`
- implementation_authorized: `standing unattended authorization after bounded phase plan`
- next_gate: `p4c_implementation_validation_and_diff_review`

## 1. Objective

Deliver the Review/mixed semantic path from a completed deterministic Review gate to one
user-approved, traceable and correction-capable background-only Review Memory:

```text
completed review_semantic_gate or review_semantic_gate_mixed_document
-> explicit Review semantic Agent Task request
-> independent review_semantic_processing Pipeline Job
-> basic Review Memory Source Adequacy
-> bounded external Agent handoff
-> same-review provenance candidate
-> per-source-note adequacy and provenance validation
-> non-canonical preview
-> explicit user approval
-> one atomic Review Semantic Bundle revision
-> completed semantic Job and approved Task receipts
```

Every retained Review Unit remains secondary-source background. This phase creates no
canonical Evidence, factual Question Mapping, Field Map entry or Research Synthesis
candidate.

## 2. Design-To-Implementation Review

The approved final design and current Core agree on the route, authority and evidence
boundary. P4-C must adapt the design to current runtime facts as follows:

1. Reuse the existing common seven-section `review-memory` contract and its source-note
   provenance validator. Do not create subtype-specific Review schemas.
2. Add a thin physical revision bundle; do not replace the Review Memory logical contract
   or edit the legacy Review Memory file in place.
3. Keep current operation names: `basic_review_memory`, `continuous_text_evidence`,
   `figure_table_evidence`, `formula_layout_analysis` and `supplementary_analysis`.
   Review use does not make those operations canonical Evidence.
4. Extend source-note compatibility only enough to permit `section: null` with a
   non-empty `section_missing_reason`. Existing records with a section and no reason
   remain valid.
5. Record each retained note's consumed operation/Profile binding in the bundle revision.
   Do not duplicate source or Parse digests inside every Review Unit.
6. Leave the existing direct `ReviewMemoryService` as legacy authority. A paper may use
   either that store or a P4-C bundle, never both.

No unresolved decision requires a new product or scientific policy.

## 3. Fixed Authority And Storage

### 3.1 Independent semantic Job

The terminal deterministic intake Job remains unchanged. An explicit user Task request
creates an idempotent child Job:

```text
requested_route: semantic_processing
requested_depth: review_semantic_bundle
current_node: review_semantic_processing
authority: assess_source_adequacy + commit_review_semantic_bundle
```

Only completed `review_semantic_gate` and `review_semantic_gate_mixed_document` origins
are accepted. A mixed document always uses this route. Basic inadequacy moves only this
semantic Job to the exact source/user/reparse wait; it does not reopen routing.

### 3.2 Versioned Task registry

- retain `p4a-v1` and `p4b-v1` behavior;
- add `p4c-v1` with `review_semantic_processing` available;
- require `metadata`, `parsed_excerpt` and `operational_context`;
- allow `review_background` only as an explicitly approved optional correction context;
- keep `source_document` unavailable for these configured external CLI executors;
- result contract: `p4c-review-semantic-candidate@1.0`;
- manifest contract: `p4c-agent-handoff@1.0`.

Core does not locate, launch, authenticate or supervise Codex CLI or Claude Code CLI.

### 3.3 One physical canonical bundle

Add one per-paper file:

```text
knowledge/review_bundles/by_paper/<paper_id>.review-bundle.json
```

Each bundle contains ordered immutable revisions. A revision owns:

- revision ID/number, predecessor ID/digest and approval receipt;
- exact source, Parse and five Source Adequacy snapshots;
- note-level consumed operation/Profile bindings;
- one schema-valid existing `review-memory` child.

Only the active child enters existing Review Context, Catalog and background reads.
Historical revision, Memory and Unit IDs remain audit-resolvable but are not current
background inputs. Every approved correction allocates a new Memory ID and new Unit IDs,
so downstream background links can become deterministically stale rather than silently
changing meaning.

Legacy `review_memories/by_paper/*.review.json` remains readable. P4-C rejects a bundle
when a legacy Review Memory exists for the same paper, and direct legacy promotion rejects
a paper already owned by a Review bundle. Primary legacy records and Primary bundles
remain mutually exclusive with either Review authority.

The optional `review_bundles/by_paper` store advances the layout contract to `p4c-1` by
the existing additive bootstrap/upgrade path. No canonical record migration occurs.

## 4. Candidate And Provenance Contract

The Agent submits semantic fields only. It cannot submit Memory/Unit/revision IDs,
fingerprints, Parse snapshots, Profile IDs, timestamps, status constants or approval
fields.

The candidate contains:

- one supported common review subtype and classification reason;
- `skimmed`, `targeted_read` or `deep_read` status;
- scope tags, one-sentence reuse value, memory value and coverage limits;
- exactly the common seven ordered Review sections;
- zero or more retained Units and explicit non-reusable notes;
- for every retained Unit, at least one source note and one concrete workflow impact;
- for every source note, one task-local `requested_operation`.

Core enforces:

- basic candidate processing consumes current/adequate `basic_review_memory`;
- quote excerpts are short exact slices with page/character locator on the Task-bound
  Parse;
- accurate paraphrases have no character locator but retain reproducible PDF page and
  section, or an explicit missing-section reason;
- figure/table, formula/layout and supplementary notes declare and pass their matching
  operation rather than masquerading as continuous text;
- every included Unit is fully retained and grounded at review level; revision-required
  or rejected material belongs in non-reusable notes and cannot enter the staged Memory;
- one inadequate/uncertain/stale consumed operation rejects staging of the closed
  candidate and routes the semantic Job to remediation;
- an unrelated figure/SI limitation does not block a zero-Unit or text-only Memory whose
  actual consumed operations are adequate;
- zero Units are valid only when `memory_value` is non-reusable/low-value and the reason,
  read scope and coverage limits are explicit;
- `background_only=true`, `can_enter_canonical_evidence=false` and `not_fact=true` are
  Core-owned constants.

The source-note text proves only that a Unit came from the named review location. It is
never an Evidence quote and cannot support an experimental claim.

## 5. Application Service Flow

Extend the session-bound Agent Task facade:

```text
create_from_pipeline(... review_semantic_processing ...)
prepare_handoff(...)
submit_result(...)
preview_result(...)
request_revision(...) / reject_result(...)
refresh_review_task(...)
approve_review_result(...)
```

Creation assesses all five registered operations and binds their Profile IDs/digests.
Task creation requires only the basic gate. Handoff includes bounded parsed excerpts,
common section IDs, capability outcomes and non-authority instructions. For a correction,
the active Review Memory may appear only when `review_background` is effectively allowed;
otherwise the Task remains valid and rereads bounded parsed excerpts.

Submission validates the candidate, current source bytes, Parse, every consumed Profile
and existing Review provenance before staging. A blocked source note creates no Review
Unit, no Review Memory, no Evidence and no scientific review-queue row.

Preview returns a complete escaped structured diff against the active Memory or an initial
creation view. It does not expose paths, fingerprints, raw Task refs or unapproved content.

Approval allocates canonical IDs, constructs the complete existing Review Memory child,
validates a temporary bundle plus active projection, checks source/Parse/Profile/bundle
head again and atomically replaces only the per-paper bundle file. It then completes the
semantic Job and appends the approved Task receipt. Crash replay completes missing
receipts without creating a duplicate revision.

Created, leased or submitted stale Review Tasks are superseded only by reciprocal
successor lineage. Refresh stays on the Review route and preserves prior handoff/result
digest and feedback.

## 6. Implementation Batches

### P4-C1 contracts, layout and active projection

- add Review candidate and bundle schemas plus revision ID namespace;
- add the optional Review bundle layout and additive `p4c-1` bootstrap upgrade;
- project only the active embedded `review-memory` child;
- retain historical IDs for audit resolution and active-only Catalog/background reads;
- block legacy/P4-C and Primary/Review mixed authority;
- add the backward-compatible `section_missing_reason` source-note rule.

### P4-C2 semantic Job, Task and adequacy flow

- add `review_semantic_processing`, `p4c-v1` and portable handoff;
- create/recover the independent Review semantic Job;
- bind all five Profiles and basic-gate Task creation;
- validate note-level consumed operations and route blocked candidates with zero
  scientific write;
- add stale created/leased/submitted Review Task refresh lineage.

### P4-C3 approval, correction and Guardian

- allocate Review revision/Memory/Unit IDs and strip task-local operation fields;
- atomically commit/recover one complete bundle revision;
- preserve historical revisions and activate only the approved successor;
- validate provenance bindings, source/Parse/Profile snapshots and Task approval receipts;
- ensure Review change never stales or supplies canonical Evidence.

### P4-C4 integration and closure

- update capability, architecture, workflow and contributor contracts;
- extend bundle, Review Context, Catalog, Guardian, privacy and installed-wheel coverage;
- run focused, full Windows, build, installed-wheel, privacy and diff validation;
- write validation receipt, ADR/closure manifest and complete `neat-freak` reconciliation;
- retain generated workspaces until P11 and overall completion.

## 7. Validation Matrix

At minimum verify:

- `p4a-v1` and `p4b-v1` behavior remains compatible; unknown `p4c` contracts fail closed;
- only completed Review/mixed semantic gates create Review semantic Jobs;
- mixed documents cannot enter Primary semantic processing;
- basic Review capability can pass while unrelated figure/SI capability fails;
- a Unit consuming failed figure/SI capability prevents staging and writes no scientific
  record or queue boundary;
- exact quote excerpt resolves on the Task-bound page/locator;
- accurate paraphrase allows no locator and requires section or missing-section reason;
- every retained Unit has source note, workflow impact and a valid consumed-operation
  binding;
- zero-Unit low-value/redundant Memory commits with explicit reason and coverage limits;
- approval exposes one complete active Review Memory and no canonical Evidence;
- rejection or injected pre-replace failure produces zero canonical scientific write;
- crash after bundle replacement recovers Job/Task receipts without a second revision;
- correction preserves revision 1 bytes/digest and activates new Memory/Unit IDs only;
- old Unit IDs remain audit-resolvable but cannot be selected as current background;
- direct legacy Review promotion and P4-C bundle cannot coexist for one paper;
- Primary/Review route exclusion covers legacy and bundle combinations;
- source, Parse, Profile, semantic Job or bundle-head drift rejects submit/approval;
- Guardian rejects missing provenance bindings or incomplete approval receipts;
- Review Context and Catalog expose only the active child and correct freshness;
- full Windows suite, package build, installed-wheel smoke, privacy scan and
  `git diff --check` pass.

## 8. Stop And Defer Boundaries

Do not add in P4-C:

- App Agent Task UI, HTTP endpoints, prompt paste/upload or browser diff surface;
- Portable Skill installation/mirror mutation or embedded Agent execution;
- subtype-specific Review schema, PRISMA/risk-of-bias engine or automatic review scoring;
- Field Map/Direction integration, Review Unit Question Mapping or question-seed commit;
- Review viewpoint conflict database or cross-review provenance replay;
- canonical Evidence from Review content or scientific review queue for source failure;
- Research Synthesis drafting, discovery/acquisition, PDF reader, Obsidian renderer,
  Exchange, backup, migration or legacy cutover;
- private workspaces, real PDFs or private scientific records.

GitHub delivery failure may defer remote closure but does not expand scope or block local
implementation/validation. P4-D remains the separate App/host work-surface milestone.
