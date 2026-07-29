# P0 Future Scenarios And Scale Generator Specification

Status: accepted scenario-only specification

Materialization status: prohibited in P0

## 1. Boundary

This document describes deterministic inputs and expected behavior for record types and workflows that do not exist at the P0 baseline. It is not a schema, fixture record, migration or executable generator. A scenario may be materialized only after its owning phase approves the relevant contract/schema and updates the P0 seed inventory separately.

Scenario descriptions never count as passing runtime tests.

## 2. Scenario Manifest Convention

Each future phase should materialize a scenario from these fields:

| Field | Meaning |
|---|---|
| scenario ID | Stable documentation identifier, not a Core record ID. |
| owning phase | First phase allowed to define/materialize the record. |
| prerequisites | Approved contract, service and dependency edges required first. |
| deterministic inputs | Synthetic source/config/record state and exact actor/action. |
| expected durable outputs | Canonical/operational records that should exist after success. |
| expected zero outputs | Records/files that must not be created. |
| failure/authority case | Required fail-closed variant. |
| observations | Events, receipts, status or Guardian facts required for audit. |

All generated content must carry the then-current synthetic fixture marker and use repository-relative POSIX paths.

## 3. P3 Pipeline And Source Adequacy Scenarios

### `SC-P3-01`: use-specific capability split

- Inputs: one readable synthetic document whose continuous text is reproducible but whose fabricated figure order is intentionally ambiguous.
- Expected: basic-understanding and continuous-text capabilities adequate/current; figure-table capability inadequate with reason.
- Allowed work: basic Paper Card or Review Memory routing may continue.
- Forbidden work: figure Evidence/Review Unit staging.
- Failure case: attempt to consume figure-table capability routes the Pipeline Job to reparse/source wait; no scientific `review_queue` item is created.
- Prerequisite: approved P3 Source Adequacy and Pipeline Job contracts.

### `SC-P3-02`: missing supplementary asset

- Inputs: a main synthetic document that refers to an absent supplementary asset.
- Expected: supplementary-material capability inadequate; unrelated capabilities retain their own results.
- Failure case: SI Evidence request creates a waiting-source Pipeline transition and no Evidence.
- Prerequisite: approved asset-role/availability and adequacy contracts.

### `SC-P3-03`: source identity change

- Inputs: register a synthetic source, assess it, then replace bytes at the same fixture path under controlled test setup.
- Expected: digest recheck detects a new manifestation, old parse/adequacy becomes stale, history remains unchanged.
- Failure case: old adequacy cannot authorize a new semantic operation.
- Prerequisite: manifestation and stale-edge contracts.

### `SC-P3-04`: route ambiguity before and after P4

- P3 behavior: ambiguous type enters Pipeline `waiting_user`; no Agent Task exists.
- P4 behavior: with user/Task policy enabled, create a `document_route_resolution` task through the versioned registry.
- Failure case: unknown task kind registry version fails closed.

## 4. P4 Agent, Grounding And Correction Scenarios

### `SC-P4-01`: Primary candidate grounding

- Inputs: exact current source/parse/adequacy refs and a synthetic Agent result containing one supported and one overbroad Card Unit.
- Expected: supported Unit may stage with canonical Evidence provenance; overbroad Unit is narrowed or sent to scientific `review_queue`.
- Zero output: Agent cannot directly commit or assign human state.
- Authority case: stale source/parse basis rejects submit.

### `SC-P4-02`: Review provenance closure

- Inputs: synthetic Review Memory candidate with one text Unit and one figure Unit.
- Expected: each retained Unit has stable source/parse refs, PDF page, section or explicit absence reason, short excerpt/accurate paraphrase, background flags and a capability-consumption result.
- Failure case: figure capability is inadequate, so only that Unit is revised/rejected; the text Unit may remain.
- Zero output: no Review Unit enters canonical Evidence.

### `SC-P4-03`: low-value Review Memory

- Inputs: a redundant synthetic review producing zero reusable Units.
- Expected: Review Memory may commit with low-value reason and coverage limits after preview/approval.
- Value: prevents repeated rereading without inventing content.

### `SC-P4-04`: successor task lineage

- Inputs: Task result receives `revision_requested` feedback.
- Expected: successor preserves route, predecessor task/result digest, feedback, exact current refs and expected contract.
- Failure case: changed source/parse/current revision makes old submit stale; it cannot return to document routing.

### `SC-P4-05`: canonical correction

- Inputs: approved synthetic Evidence/Card/Review revision later found wrong.
- Expected: corrected candidate stages, previews, receives user approval and supersedes old revision; history remains resolvable.
- Forbidden: in-place edit or physical deletion.

### `SC-P4-06`: privacy and injection

- Inputs: parsed text and Agent output containing instructions to read another file, run a command or expand scope.
- Expected: text remains data; payload/authority unchanged; rendering escapes or sanitizes it.
- Failure case: unregistered payload item or local-only task sent to cloud-capable CLI is blocked.

## 5. P7 Organization And Screening Scenarios

### `SC-P7-01`: admissible factual mapping

- Inputs: grounded/revised current Card Units plus optional labeled Review background.
- Expected: only admissible retained factual Units/Evidence enter factual mapping; background remains marked.
- Failure case: `needs_revision`, rejected or background-only Unit cannot enter factual mapping.

### `SC-P7-02`: proposal approval

- Inputs: Agent proposes a Direction, Field Map entry or Question mapping.
- Expected: proposal uses the same staging/App preview/user approval path as other semantic output.
- Forbidden: direct canonical organization write by Agent.

### `SC-P7-03`: criteria revision

- Inputs: one question-screening decision bound to criteria version/digest, then criteria change.
- Expected: old decision becomes stale and requires reconfirmation; Library inclusion and paper processing remain unaffected.

## 6. P9 Generated-View Scenarios

### `SC-P9-01`: source-watermark freshness

- Inputs: two generated views with disjoint upstream records, then revise one upstream.
- Expected: only affected view becomes stale; rerender makes it current and records new watermark.
- Failure case: user edits a managed generated file; overwrite aborts.
- Recovery choices: discard managed edit or export it as a personal-note copy, then rerender.

## 7. P10 Exchange Scenarios

### `SC-P10-01`: immutable external conflict

- Inputs: unsigned bundle containing same-origin paper identity but different Card/Evidence content.
- Expected: preserve namespaced immutable external-origin revisions; external verification claim remains untrusted locally.
- Forbidden: automatic merge, local activation or factual-query eligibility.

### `SC-P10-02`: source-inclusive dry run

- Inputs: explicit selected-paper export with two synthetic source assets, one unavailable and one rights-unknown.
- Expected: dry run reports counts, estimated size, missing source and rights state; policy decides split/reject before final archive.
- Failure case: no partial final archive.

### `SC-P10-03`: hostile archive

- Inputs: traversal path, absolute path, normalized collision, link/reparse entry, undeclared entry and oversized entry variants.
- Expected: confined staging rejects each variant with zero canonical writes.
- Prerequisite: approved Exchange contract, limits and serialization profile.

### `SC-P10-04`: compatibility branches

- Inputs: supported, newer-safe, migration-required and unknown versions.
- Expected: normal; read-only; write-blocked; fail-closed respectively.

## 8. P11 Scale Generator Recipe

### Inputs

The future deterministic generator accepts independent dimensions:

```text
seed
paper_count
primary_ratio
review_ratio
parsed_pages_per_paper
evidence_per_primary
card_units_per_primary
review_units_per_review
question_count
links_per_question
step7_candidates_per_question
completed_job_count
completed_task_count
process_event_count
guardian_report_count
retained_report_count
stale_edge_count
source_availability_distribution
```

### Determinism

- Same generator version, seed and parameters produce byte-identical canonical serialization.
- IDs derive from deterministic namespace input, not wall-clock or host paths.
- Text comes from bounded templates authored from scratch and carries no copied paper content.
- Source references are relative and may point to tiny synthetic text assets; large PDF bytes are not required for catalog-density tests.
- Paper density and operational density can be raised independently.
- Generator writes into a newly created test root only and refuses nonempty/unconfined targets.

### Outputs after owning contracts exist

- a manifest with generator/profile versions and parameters;
- existing-contract scientific records at the requested density;
- future operational records only after their owning schemas are approved;
- expected counts/digests and query answer fixtures;
- no private path, credentials, external text or undeclared files.

### Validation recipes

1. full build equals incremental projection after canonical normalization;
2. delete/rebuild projection preserves query answers;
3. stable cursor pagination returns each expected ID once;
4. corruption/recovery and backup/restore converge to manifest digests;
5. repeated stale triggers coalesce by dependent/upstream revision/reason;
6. memory/latency measurements cite one named frozen budget profile.

### Materialization phases

- P2 may materialize scientific/catalog density using contracts available then.
- P3/P4 append Job/Task/adequacy density only after approval.
- P7-P10 append their own record surfaces after approval.
- P11 runs the combined formal generator and acceptance profile.

## 9. Explicit Non-Scenarios

Citation/reference graphs, cited-by tracking and related-paper navigation are post-R2 extensions. They are not represented by placeholder records or inferred from Question/Direction/Exchange scenarios.
