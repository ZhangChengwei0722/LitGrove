# P4-B Primary Semantic Bundle Implementation Plan

- status: `approved_unattended`
- prepared_at: `2026-07-31`
- branch: `feature/p4b-primary-semantic-bundle`
- local_baseline: `3f28d36`
- upstream_p4a_merge: `e5399a1`
- upstream_p4a_post_merge_pr: `40 merged as b47b4394; local fetch pending after github.com:443 timeout`
- required_application_service_interface: `1.3`
- implementation_authorized: `standing unattended authorization after bounded phase plan`
- next_gate: `p4b_implementation_validation_and_diff_review`

## 1. Objective

Deliver the Primary semantic path from a completed deterministic intake gate to one
user-approved, traceable and correction-capable canonical result:

```text
completed primary_semantic_gate
-> explicit Primary semantic Agent Task request
-> independent semantic_processing Pipeline Job
-> operation-specific Source Adequacy profiles
-> bounded external Agent handoff
-> alias-based Primary candidate
-> use-specific adequacy and provenance validation
-> non-canonical preview
-> explicit user approval
-> one atomic Primary Semantic Bundle revision
-> completed semantic Job and approved Task receipts
```

The bundle contains one seven-section Paper Card, canonical Evidence and scientific
review-queue boundaries for exactly one paper. No scientific child record is visible
before approval, and approval cannot leave a partial Card/Evidence/queue combination.

## 2. Fixed Architecture

### 2.1 Independent semantic Job

The P3 deterministic intake Job is terminal after reaching `primary_semantic_gate`; it
cannot authorize later Source Adequacy or scientific writes. P4-B therefore creates one
idempotent child Pipeline Job under the explicit user Task-creation action:

```text
requested_route: semantic_processing
requested_depth: primary_semantic_bundle
current_node: primary_semantic_processing
input_refs: origin Job + paper
authority: assess_source_adequacy + commit_primary_semantic_bundle
```

The child advances directly from `created` to `waiting_agent`. Source inadequacy moves
this child to `waiting_source` or `waiting_user`; it never reopens the terminal intake
Job. A changed source/parse invalidates the old Task basis and requires a successor Task
after remediation.

### 2.2 Versioned Task registry

- retain `p4a-v1` for route-only workspaces and exact backward compatibility;
- add `p4b-v1`, enabling `primary_semantic_processing` while retaining route resolution;
- require `metadata`, `parsed_excerpt` and `operational_context`; allow the already
  registered routing context class only when explicitly approved;
- keep Codex CLI and Claude Code CLI as external manual handoffs;
- do not locate, launch, authenticate or supervise either Agent.

The Primary result contract is `p4b-primary-semantic-candidate@1.0`. Unknown registry
versions, task kinds, result versions, aliases or fields fail closed.

### 2.3 One physical canonical bundle

Add one per-paper canonical file:

```text
knowledge/primary_bundles/by_paper/<paper_id>.primary.json
```

It is the atomic authority for P4-B-created Primary records. One append-only revision
chain stores complete immutable revision snapshots; the last non-superseded revision is
the active head. Each revision owns:

- revision ID, number, predecessor ID/digest and approval receipt;
- exact source, parse and Source Adequacy snapshots;
- one schema-valid seven-section Paper Card;
- zero or more canonical Evidence records;
- zero or more scientific review-queue boundaries.

Workspace loading projects only the active child records into the existing logical
`paper-card`, `evidence` and `review-queue` kinds. Historical revisions remain canonical
and Guardian-verifiable but cannot enter factual query, mapping or Catalog results.

Legacy per-kind Primary stores remain readable and unchanged. P4-B refuses mixed
authority for a paper: it cannot create a bundle when legacy Card/Evidence/queue records
exist, and legacy `RecordService` cannot mutate a P4-B bundle. Legacy adoption or
migration is outside this phase.

### 2.4 Canonical correction

An initial approval creates revision 1. A later correction Task against a P4-B bundle
creates revision N+1 with the prior revision ID/digest and then supersedes the prior head
by append-only revision semantics. It never edits or deletes a historical revision.
Correction remains subject to staging, preview, user approval, source freshness and
operation-specific Source Adequacy.

## 3. Candidate Contract

The Agent returns task-local aliases, never canonical IDs:

- Evidence candidates: alias, claim, type, quote, pdf/printed page, locator,
  support scope, non-support boundaries and requested Evidence operation;
- scientific boundaries: alias, issue type, candidate claim, reason, optional locator,
  and resolution status;
- exactly the domain profile's seven ordered sections;
- Card Units: statement, statement type, grounding status, Evidence aliases, boundary
  aliases, source page and confidence.

Validation rules:

- aliases are unique within their class and every reference resolves locally;
- `grounded`/`revised` Units require Evidence and cannot cite queue aliases;
- `needs_resolution` Units require at least one boundary and no Evidence;
- `interpretive`/`background_only` Units carry no Evidence;
- every Evidence quote/page/locator resolves against the Task-bound parse;
- every Evidence operation consumes its own current `yes` Source Adequacy capability;
- blocked Evidence prevents staging the candidate and cannot be converted into a
  scientific queue record merely to bypass source remediation;
- Card construction itself requires current `basic_paper_card` adequacy;
- Agent-supplied IDs, timestamps, fingerprints, source refs, review state, automation
  state and canonical flags are rejected.

## 4. Service Flow

Extend the session-bound Agent Task service without exposing stores:

```text
create_from_pipeline(... primary_semantic_processing ...)
prepare_handoff(...)
submit_result(...)
preview_result(...)
request_revision(...) / reject_result(...)
approve_primary_result(...)
```

Creation accepts only a completed Primary intake Job and creates/reuses the semantic
child Job. It deterministically assesses all registered Primary Evidence operations and
binds their profile IDs/digests into the Task input basis. The handoff includes only
approved parsed excerpts, the seven section IDs, capability outcomes and explicit
non-authority instructions.

Submission validates the candidate, source digest, parse digest and every consumed
adequacy profile before writing staging. If a consumed capability is not current and
adequate, submission writes no staging and transitions the semantic Job to the exact
Source Adequacy wait route. Scientific review queue remains untouched.

Approval allocates all canonical IDs, resolves aliases, builds the complete bundle head,
validates the temporary bundle plus active logical projection, verifies source digest
again and atomically replaces only the per-paper bundle file. It then completes the
semantic Job and appends the approved Task receipt. Replays recover deterministically
when the bundle or Job commit succeeded before the Task receipt.

## 5. Implementation Batches

### P4-B1 contracts and storage projection

- add the Primary candidate and Primary bundle contracts;
- add revision and Primary bundle identifier namespaces;
- add the managed bundle path, bootstrap/layout validation and bundle loading;
- add active-child projection, cross-record validation and Guardian checks;
- block legacy/P4-B mixed authority.

### P4-B2 semantic Job and Task flow

- add semantic Pipeline Job route/depth and transitions;
- add registry `p4b-v1` and backward-compatible `p4a-v1` resolution;
- generalize Task input basis/handoff/result validation by task kind;
- create deterministic operation-specific Source Adequacy profiles;
- add blocked-capability wait routing with zero staging/scientific writes.

### P4-B3 canonical approval and correction

- allocate IDs and resolve task-local aliases;
- commit one atomic bundle revision;
- complete semantic Job and Task receipts with crash-safe replay;
- create successor revisions for corrections without overwriting history;
- expose bounded preview and active context projections.

### P4-B4 integration and closure

- update capability, architecture, workflow and contributor contracts;
- extend bundle/Guardian/privacy/install-wheel coverage;
- run focused, full, build, installed-wheel, privacy and diff validation;
- record package digests, generated workspace retention and next P4-C gate;
- run `neat-freak` reconciliation before closure.

## 6. Validation Matrix

At minimum verify:

- `p4a-v1` route Tasks remain byte/behavior compatible;
- `p4b-v1` is required for Primary Tasks and unknown versions fail closed;
- only a completed `primary_semantic_gate` origin can create the child Job;
- exact Task creation is idempotent and different intent conflicts;
- prompt injection text remains data and private refs never enter preview;
- basic Card adequacy can pass while figure/SI Evidence is blocked;
- blocked figure/SI operations transition only the semantic Job, write no staging and
  create no Evidence or scientific queue record;
- continuous-text Evidence quote/page/locator trace-back succeeds only on the bound parse;
- alias errors, cross-paper refs, duplicate sections and invalid Unit grounding fail;
- approval writes one file and exposes a complete logical Card/Evidence/queue set;
- injected failure before replacement leaves zero canonical scientific write;
- crash after bundle replacement recovers Job/Task receipts without a second revision;
- correction preserves revision 1 bytes/digest and activates revision 2 only;
- legacy Primary records and P4-B bundles cannot coexist for one paper;
- stale source, parse, adequacy or bundle head rejects submit/approval;
- Question Mapping and Research Synthesis continue to consume only active logical records;
- full Windows suite, package build, installed-wheel smoke, privacy scan and
  `git diff --check` pass.

## 7. Stop And Defer Boundaries

Do not add in P4-B:

- App Agent Task UI, prompt paste/upload, preview or approval endpoints;
- Review Memory semantic bundles or mixed/review processing;
- Field Map, Direction, Question proposal processing or Research Synthesis drafting;
- PDF.js, UPDF, Obsidian, discovery UI, Exchange, backup or migration;
- embedded Agent execution, API credentials or live-model CI;
- real PDFs, private workspaces or legacy scientific-data mutation.

GitHub delivery failures and the unavailable external design/roadmap workspace are
recorded and bypassed. They do not authorize copying or reconstructing private content.
