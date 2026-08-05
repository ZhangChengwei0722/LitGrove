# Workflow Contract

## Deterministic Boundary

Agent-created structured content follows this intended lifecycle:

```text
candidate input
-> schema/reference/status validation
-> locked atomic promotion
-> process event
-> Guardian
```

Milestone 1B established this deterministic storage lifecycle, and later M3/P0-P11 slices
extended it through the current R3 product contract. Historical milestone labels below
identify where a behavior entered the cumulative workflow; they are not active gates.
P0-P11 is delivered at Application Service interface `1.18` while semantic judgment
remains outside Core.

## Runtime Sequence

1. `capability show` reports public Core contracts, Review runtime, adapter availability and built-in discovery connectors without loading a workspace or calling the network.
2. `discovery search` sends one bounded request to the fixed Europe PMC endpoint, locally refilters normalized metadata and emits a transient zero-write report.
3. After the user explicitly chooses result keys, `discovery select --actor user` validates the complete report and atomically persists only those metadata candidates; `discovery list/show` are read-only.
4. `discovery resolve` rechecks one selected candidate through a fixed Europe PMC identity and emits a transient OA-policy report without download or persistence.
5. `discovery acquire --actor user` re-runs resolution and may create one new PDF in an exact configured `local_inbox`, then atomically receipts the candidate without Registry chaining.
6. `intake inspect-acquired` verifies one stored acquisition receipt and maps its exact inbox PDF to portable intake and Registry metadata without network or writes.
7. `workspace init --dry-run` validates an existing config and reports planned managed actions without deliberate filesystem mutation.
8. `workspace init` binds the managed root with a deterministic marker. Repeating it with the same config returns `no_change`.
9. `intake inspect` maps one absolute source path to a portable source reference, exact Registry state and active Card sections without writing or registering it.
10. `manuscript inspect` projects one exact local DOCX/PDF into bounded stable task units and coverage limits, then stops with zero writes.
11. `compatibility inspect` uses only adapters explicitly injected by a private in-process caller. It emits a read-only report to stdout and never persists compatibility state.
12. `registry add` resolves a declared source root, hashes the source read-only, and preserves exact duplicates as reciprocal candidates. `--metadata -` accepts one bounded JSON object through stdin.
13. `parse run` uses the explicitly requested `synthetic-text`, legacy-spatial `pdfplumber` or preferred scientific-intake `pdfplumber-text-flow` adapter and writes validated page records without creating a full-text copy. It reports exact adapter/version identity and never falls back to OCR or another adapter.
14. `parse show` emits all validated active pages or one positive PDF page after source-fingerprint checks, without creating a full-text copy or read artifact.
15. `record promote` loads a file request or bounded stdin JSON object, injects IDs/timestamps/fingerprints, enforces actor authority, and promotes one canonical store.
16. `paper context` returns the selected primary paper's stored Card, Evidence and review queue context after source-stability checks, without exposing canonical paths or creating a read artifact.
17. `review context` separately returns one Review Memory, freshness and transient exact DOI matches without changing `paper context 1.0`.
18. A `question-mapping` request selects Card Units for a user-supplied or explicitly approved question. Core derives evidence and required boundaries before atomic promotion.
19. `paper status` projects deterministic stage, freshness, Guardian and transaction safety facts without scientific content or a resume decision.
20. `question list/show` retrieves deterministic structured mappings without writing a reading view, report, event, or journal.
21. `question render` validates one mapping and emits its complete Markdown reading view to stdout without persisting the view or changing structured state.
22. A Research Synthesis `record promote` request selects grounded/revised Card Units already admitted by one current Question Mapping. Core derives exact Evidence and Unit-boundary closure before atomic promotion.
23. `step7 context` returns only one question's complete candidates plus deterministic current/stale projections; `step7 render` emits the corresponding non-canonical Markdown reading view to stdout.
24. `guardian check` is read-only by default. `--write-report` explicitly appends the report through the same transaction kernel.
25. `transaction recover --dry-run` reports digest-based recovery actions without mutation; recovery writes only when dry-run is omitted.

Every workspace-bound runtime command uses the same semantic validator and requires a matching workspace marker. `capability show` and `discovery search` are workspace-independent. No command accepts a raw `knowledge_root` or output-path override. `discovery resolve` is an external read against one persisted candidate. `local_inbox` remains user-owned and is never created by bootstrap; only explicit acquisition scans exact operation names and creates one absent candidate PDF.

Compatibility reports classify what can be read directly, projected through an adapter, left unsupported, or retained as a legacy reading view. They do not select a migration target or change authority. Core rechecks all declared protected files and trees in a `finally` path; source change takes precedence over an ordinary adapter failure.

## Authority

- `ai_draft`: Agent candidate.
- `ai_checked`: Agent review recorded under a contract that permits it.
- `passed_auto_checks`: deterministic checks passed.
- `human_checked` and `verified`: user-only.
- final screening and high-risk source operations: user-only.

`stored` is an internal validation context for existing canonical state. It is not a submitter role.

Question origins are explicit mutation context:

- `user_supplied`: the user supplied the active question;
- `user_approved_candidate`: the user explicitly approved a generated candidate;
- `existing_question`: refresh an already persisted mapping;
- an unapproved Agent-generated question stays in the task report and is not persisted.

Discovery candidate authority is separate: only exact `actor: user` may select report results. Selection records follow-up intent only and does not grant acquisition. A later exact `discovery acquire --actor user` is a separate authority event and still grants no `human_checked`, `verified`, screening or Registry authority.

## Failure Boundary

- Bootstrap performs a complete read-only preflight before creating its lock scaffold, then repeats mutation-sensitive checks while holding the workspace lock.
- A conflicting marker, unknown managed content, incomplete transaction, unsafe path type, or invalid markerless bundle blocks initialization without rewriting canonical records.
- An exact `m3b-1` marker requires `workspace init`; runtime commands never write through an old layout.
- Validation failure preserves the previous target bytes.
- A pre-replacement failure records a failure event when possible.
- Source-dependent services recheck source stability after replacement and before emitting success.
- Evidence promotion requires an active same-paper parsed page, a supported page-matching locator, and an exact quote slice or bounded synthetic block containment.
- Missing `research-kb-core[pdf]` returns `RKBC-028`; unsupported, malformed, encrypted, or text-unavailable PDFs return `RKBC-029` without exposing source paths or text.
- Manuscript projection preserves `RKBC-028` for a missing PDF extra and uses `RKBC-035` for unsupported DOCX/PDF manuscript content; every structured failure leaves stdout empty.
- A failed post-replacement source check emits no success event, records `needs_resolution`, and requires manual inspection.
- A post-replacement event failure leaves an incomplete journal and returns a recovery-required error.
- Completed journals record their final result and must match exactly one journal-derived process event; Guardian reports missing or altered events.
- Recovery compares before/after/current digests and exact event content. It never guesses through an ambiguous or `needs_resolution` state.
- Existing POSIX target modes are preserved; new canonical files and newly created immediate private parents use `0600` and `0700` respectively.

## Reading Views

Structured JSON/YAML/JSONL records are inputs. Markdown views are one-way renders and cannot be used to rewrite structured facts.

M2B-2 renders exactly one stdout-only Question Reading View. M3B-1 adds a separate stdout-only Research Synthesis Reading View grouped by candidate type. Both are built from validated structured records, label queue references as non-evidence, and create no Markdown file or view store.

Evidence matrices, relations, gap maps, contradictions, persisted Question Layer Markdown, persisted Research Synthesis Markdown and additional derived views remain outside the implemented runtime.

## Skill-Facing Read And Handoff Boundary

The transient JSON read interface is version `1.0`. Core reports capability and current state; the Portable Skill decides the procedural resume step and the Agent remains responsible for scientific interpretation. No read command persists a status snapshot or workflow run.

`intake inspect` and `manuscript inspect` are the only absolute-path-facing reads. `intake inspect-acquired` is the candidate-ID bridge for a separately authorized acquired-source task. The Skill must inspect before `registry add`, reuse the sole returned paper ID for `registered_current`, register only `unregistered`, and stop on `registered_stale` or `ambiguous`. It must use returned source and domain values rather than parsing workspace, profile or Registry files. Manuscript projection is independent of Registry and stops after its stdout report; only the separate Agent-owned `manuscript_audit` route may consume that report for current-task reasoning. This is sequential routing, not an atomic concurrency guarantee.

`paper context` is the primary-route read that returns stored Card, Evidence and queue scientific content together. `review context` is the separate review-route read and returns a complete background-only Review Memory. Both are bounded to an explicit paper, omit paths and unrelated records, and exist to recover Core-owned IDs without direct store access.

Stdin accepts JSON only for discovery search/selection requests, Registry metadata and mutation requests. Empty, invalid, non-object or oversized input fails before service dispatch. Discovery file input is JSON-only; canonical file requests retain existing JSON/YAML support. YAML is never accepted through stdin.

## Portable Skill Workflow

The repo-owned `research-kb` Skill routes workspace-independent on-demand discovery, optional explicit candidate handoff, separately requested OA acquisition, local or already-acquired PDF intake, exact-path manuscript projection/audit, or an existing-workspace knowledge query. Discovery resolves explicit dates and field-bound keywords and reports 0-15 metadata results with zero writes. Candidate selection and acquisition each require separate exact user authority. Acquisition stops after reporting its portable source reference. A later explicit acquired-candidate intake resolves Registry state and, unless `registry_only` was requested, resumes the same preferred `pdfplumber-text-flow`, mutually exclusive primary/review route as local-path intake; legacy `pdfplumber` identity remains available only for compatibility.

`manuscript_projection` returns stable units only and stops. `manuscript_audit` separately requires one or more criteria and exact current-task knowledge selectors before inspection. It preserves criterion wording, resolves no adjacent corpus, expands exact factual support to canonical Evidence and returns a scope-limited private report with `persistent_writes: 0`. No Core semantic service, claim-map store or rewrite is involved.

The Skill maintains no checkpoint. Reruns recover state through `intake inspect` or `intake inspect-acquired`, then `paper status` and `paper context`; exact existing records are reused, while stale state, ambiguous sources and uncertain near-duplicates stop. Paper-local unsupported-PDF or document-type failures may be isolated, but workspace/transaction integrity failures stop the batch.

Document classification and the final report remain local to the active task. Supported high-confidence reviews use the common Review Memory route; ambiguous documents stop before mutation, while genuine mixed documents use the Review route. Knowledge queries start from grounded/revised Paper Card Units and expand to canonical Evidence for trace-back. Discovery search and ordinary queries write nothing; explicit selection writes metadata candidates and explicit OA acquisition may add a source receipt. Explicit Research Synthesis maintenance and an explicitly complete intake workflow may use the deterministic Core runtime. Approved organization proposals may link Review background into Field Map entries, but subtype-specific review schemas, Review Unit factual Question Mapping, automatic organization expansion, institutional/browser acquisition, OCR, migration and workspace-config generation remain outside the delivered runtime.

## Research Synthesis Candidate Flow

```text
current Question Mapping
-> selected grounded/revised Card Units
-> Agent semantic candidate
-> Core-derived Evidence and Unit-boundary closure
-> append/replace in one internal `step7` JSONL store
-> context / stdout render / Guardian
```

Research Synthesis cannot create a question. The request uses `paper_id: null` and `question_origin: existing_question`; callers must not submit candidate IDs, evidence/queue closure, snapshots or status constants. Synthesis spans at least two papers. Cross-View sources are same-question, current and admissible. Upstream drift leaves a valid candidate readable as `stale_upstream`; structural corruption still blocks the bundle.

The Skill owns candidate generation, duplicate judgment, scientific assessment and refresh decisions. It calls `step7 context` before mutation, writes only through `record promote`, treats exact reruns as no-change, stops on uncertain near-duplicates, and finishes with context/render/Guardian. Core remains deterministic and makes no scientific judgment.

## Review Memory Flow

```text
current parse + supported review classification
-> fixed seven-section reusable memory draft
-> same-review page/section provenance
-> append or explicit AI-owned replace
-> review context ID recovery
-> Guardian read-only
```

Ordinary reruns reuse a current memory without writing. A stale parse requires rereading before explicit refresh. A low-value review may persist zero Units with a reason. Review-derived content remains non-evidence and receives no Field Map, Question Mapping or Research Synthesis identity in M3A-2A.

## P1 Host-Neutral Service Workflow

Every current CLI leaf command now reaches one reusable service/use case. Hosts call those services directly; they do not invoke `main()`, parse CLI stdout or read JSONL stores themselves.

```text
host input
-> host performs its own transport decoding
-> Core application service validates authority and workspace state
-> service returns mapping / bytes / typed result
-> host projects transport-specific output
```

Expected validation findings return a typed result and exit classification. Exceptional authority, version, path, integrity and unresolved-reference failures raise `ResearchKBError` with one `Diagnostic`. Mutation services continue to return transaction receipts, and rendering returns final bytes. This is a host boundary only; it does not grant a browser or Agent additional filesystem or write authority.

The CLI compatibility surface remains unchanged. A future App backend must use these services through a Core-controlled workspace session and may not duplicate bundle validation, parse adapter selection, recovery classification or Question filtering.

## P2-A App Catalog Workflow

The localhost App backend uses the public Core interface directly; the browser never submits a filesystem path:

```text
configured option ID
-> WorkspaceSessionService.open
-> CatalogProjectionService.status
-> explicit rebuild or incremental update when needed
-> CatalogQueryService.search
-> CatalogQueryService.detail
```

`status` reports `missing`, `current`, `stale`, `corrupt` or `incompatible`. Only `current` and explicitly labeled `stale` projections are queryable. Rebuild and update may write under the confined disposable App state root only. Session opening, status, search and detail create no workspace record, event, journal or canonical scientific write.

Catalog snippets are search projections. A detail response is usable as current record data only when `current_record_status` is `current`; `changed` and `missing` require projection maintenance or upstream inspection. Parsed-page text remains available only through the existing explicit paper-scoped Parse read surface and is not a catalog fallback.

Exact paper and Question filters may be combined with query text and item-kind filters. A cursor is valid only for the same normalized query, item kinds, paper ID, Question ID and ordering. App hosts may pass a successful rebuild/update result to `bind_projection_result` to avoid recomputing the workspace watermark; Core accepts it only when it matches the stored disposable projection.

## P2-B Repository Benchmark Workflow

P2-B benchmark commands run from a source checkout and are not production CLI commands:

```text
profile
-> generate one absent external target
-> inspect counts and digests
-> build/update/query/detail measurement
-> preserve a path-redacted receipt
```

Use `python -m benchmarks.p2_catalog_scale profile` for count-only inspection, `generate` for materialization, `inspect` for digest verification, `estimate` for pilot-based disk preflight and `measure` for Core observations. `generate-measure` additionally requires a matching passing preflight and rechecks current free space. Output receipts must be new files under an existing parent.

`p2-small` is materialized through `WorkspaceBootstrapService` before use. The large `p2-pilot-v1` and `p2-r0-scale-v1` targets remain outside the repository. Measurements temporarily revise generated Registry rows, call the ordinary projection update, restore original payload bytes and report whether the projection was restored or intentionally left stale. Cleanup remains a separate destructive operation and is not part of these commands.

The preliminary reference measurement is not a release acceptance result. Full build and selective query observations met provisional targets, while 1,000-record incremental projection and monolithic Registry detail did not. P2-E must fix and remeasure both before freezing the R0 Windows budget.

## P2-E R0 Catalog Workflow

P2-E adds three source-checkout benchmark commands:

```text
measure-projection-rebuild
measure-registry-delta
measure-catalog-reads
```

The Registry delta command is not a production CLI or App mutation. It binds one
benchmark-owned changed store to the current projection watermark and before/after
digests, validates the complete Registry JSONL, applies the exact delta transactionally,
restores generated payload bytes and records a path-redacted receipt. Ordinary
`CatalogProjectionService.update()` remains the complete production path.

After restart, an App backend should use:

```text
configured option ID
-> WorkspaceSessionService.open
-> CatalogProjectionService.bind_existing_projection
-> cache stale / unverified_after_restart status
-> CatalogQueryService.search
-> CatalogQueryService.detail
```

Health and polling reuse the inspected status instead of repeatedly walking the
workspace. Search may use the stale projection with explicit labeling. Registry detail
seeks and validates the exact canonical bytes at the stored locator before returning
`current`; a stale locator or digest never falls back to projected content.

The final R0 workload and receipts are synthetic and path-redacted. Cleanup remains a
separate explicitly authorized lifecycle operation; benchmark completion alone deletes
nothing.

## P3 Source Intake Workflow

Create one Pipeline Job before a deterministic source mutation. The Job authority must
name the exact operation; an Agent or CLI actor cannot enlarge captured user authority.

```text
job create
-> source reference | source copy | source scan + source select
-> optional unassociated Source Asset
-> Registry add when paper identity does not yet exist
-> source associate
-> current associated Source Asset projection
-> source observe or same-digest source relink as needed
-> job transition / wait / recover / cancel
-> Guardian
```

`source reference` reads one declared portable source and writes no source bytes. The Core
copy service consumes a bounded binary stream. `source copy` is its separate
exact-user-authority local CLI adapter: it verifies and opens the declared absolute PDF,
then delegates to the same stream route. Copy stages bytes, commits the Source Asset
receipt and creates one absent target under the exact configured inbox. A retry under the
same Job resumes a digest-bound partial, a receipted missing target or an already published
target. It cannot overwrite, move, rename or delete a source. A failed operation may remove
only its own still-matching temporary identity.

`source scan` reads a bounded stable snapshot of `local_inbox` and writes nothing. It
rejects an inbox with more than 1,000 entries, and returns redacted display metadata plus
an opaque candidate handle. `source select`
re-scans and rejects changed, recent, unsafe, already-associated or ineligible entries
before appending a reference state. It is not a daemon and does not infer document type or
start Parse.

The public scan excludes every portable ref already present in Registry or Source Asset
history. A registered ref is reconsidered only for an exact `source select` replay whose
Job, revision-one receipt, paper argument and role match. That replay cannot adopt another
Job's unassociated asset.

Reference, copy or selection can receive a known `paper_id`; otherwise it creates an
unassociated Source Asset owned by the Job that created revision one. `source associate`
is the only direct follow-up: it consumes exact `associate_source_asset` authority, the
current state ID and digest, an existing Registry paper and a still-current available
manifestation. That authority may belong to a later recovery Job, but the appended state
does not transfer revision-one ownership. A successful Job terminal receipt is rejected until each owned Source
Asset is associated. Cancelling or failing the Job does not delete or rewrite the source
receipt; Guardian continues to show the unassociated state.

For `main_pdf`, known-paper intake and later association require the exact Registry
fingerprint. This prevents a Source Asset operation from silently changing the paper's
registered identity. Source transition reasons are finite and validated: association is
the only paper-ID transition, relink is the only portable-ref transition, and observation
states cannot rewrite either.

`source observe` compares the current portable ref to its stored manifestation. Same
bytes are a no-op. Changed bytes append a candidate manifestation and make Parse reads
`stale_source`; missing, inaccessible or unsafe paths preserve historical records while
making current trace-back unavailable. `source relink` accepts only the active digest at a
new safe portable ref and does not stale Parse.

Registry identity correction is a separate user decision:

```text
identity list
-> user confirms merge | split | alias | archive | tombstone
-> identity correct
-> current identity projection / Catalog / Guardian
```

Merge and alias redirect current identity without rewriting references. Split must
supersede an earlier duplicate merge. Archive and tombstone change active-library
projection only. Every Registry row, paper ID, source and scientific record remains
resolvable. P3-B ends at the current source projection; P3-C consumes it as follows.

### Source Adequacy and deterministic trunk

```text
current registered source manifestation
-> current parse reuse or explicit registered adapter run
-> Source Adequacy assessment for one requested operation
-> current + adequate capability: semantic route wait
-> no / uncertain / stale: specific Pipeline Job wait
-> explicit user primary | review route
-> completed semantic-gate boundary
```

Use `adequacy assess` only with a current Job that grants
`assess_source_adequacy`. `adequacy show` returns redacted Profile projections;
`adequacy gate` is zero-write. A Profile for `basic_paper_card` cannot authorize figure,
formula or supplementary work. Main source, active parse, parser identity/profile and
parsed output are rechecked before reuse. A reparse-related user wait always runs the
adapter named in the resumed request; it does not silently reuse the parse that caused the
wait.

`trunk advance` requires one `local_source / semantic_gate` Job granting source
observation, parse, adequacy and trunk authority. It may reuse an exact current parse and
Profile on replay. It sends file/parse/capability problems to explicit wait reasons and
leaves structural failures to fail closed and Guardian. It never infers document type or
creates scientific records. A user-selected mixed document must use the review route.

### App-facing deterministic intake facade

The App backend uses one Core session facade rather than replaying CLI commands:

```text
configured workspace option
-> WorkspaceSessionService.open
-> facade limits / bounded inbox scan
-> explicit upload-stream or watched-inbox start
-> one closed-authority Pipeline Job
-> Source Asset receipt and matching success event
-> Registry receipt
-> Source Asset association receipt
-> deterministic trunk
-> explicit wait or completed semantic gate
```

An exact start replay is zero-write after all committed steps are present. After a crash,
the facade reconciles Source Asset, Registry and association receipts and advances only
the missing transition. Intake progress nodes are monotonic; a resume cannot move from
association or trunk back to Registry. Changed intent under the same client idempotency
key, ambiguous receipts, stale CAS or a source/event mismatch fails closed.

Upload callers provide an already-open bounded stream plus trusted backend-computed size
and digest. Watched-inbox callers provide one opaque stable candidate handle. Neither
route accepts an arbitrary filesystem path through the application facade. List, detail,
limits and mutation results are bounded App projections without source path, portable
reference, fingerprint, raw parse text or free-text operational reason.

P3-D0 is a Core surface only. The localhost HTTP server, multipart spool ownership,
operation coordinator, Catalog rebuild scheduling and processing UI remain P3-D1/D2.

### External Agent route-resolution handoff

After a route-ambiguous deterministic intake, the App uses the P4-A service flow:

```text
waiting_user route_ambiguous Job
-> user selects executor and exact content classes
-> Agent Task created; Job becomes waiting_agent
-> portable manifest with exact payload and resolved result schema prepared under one CAS lease
-> external Codex CLI or Claude Code CLI returns bounded JSON
-> Core rejects stale basis or stages the untrusted result
-> App renders escaped preview
-> user requests revision, rejects or approves
-> approval advances the deterministic primary/review semantic gate
```

No method launches an Agent or exposes a source path. A Task is bound to one exact Job
state, paper record, live source digest, Parse output and current Source Adequacy profile.
The user transfers the complete manifest rather than a prompt-only fragment; the
external Agent preserves `task_id` and `input_basis_digest`, follows the embedded schema
and returns one bare candidate JSON object to the App.
Revision creates a new Task with reciprocal lineage and refreshed inputs. Staging is
operational and non-canonical; P4-A route approval creates no Paper Card, Evidence,
Review Memory or scientific review-queue item.

### Primary semantic processing and approval

After deterministic intake reaches a completed Primary gate, the App uses the P4-B
service flow:

```text
completed primary_semantic_gate
-> user requests a Primary semantic Task and selects an external executor
-> Core creates an independent semantic Job and assesses five requested uses
-> basic Paper Card capability allowed: Task and bounded handoff are created
-> external Agent returns aliases, seven Card sections, Evidence and boundaries
-> Core validates aliases, exact provenance and each consumed capability
-> non-canonical App preview
-> user revises, refreshes, rejects or approves
-> Core allocates canonical IDs and atomically appends one Primary bundle revision
-> semantic Job and Task receipts complete or recover idempotently
```

An inadequate requested Evidence use moves only the semantic Job to the matching source
or reparse wait and writes no scientific staging. Basic Paper Card processing can proceed
when figure, formula or supplementary capabilities are unavailable, provided the
candidate does not request those operations. A changed source, Parse output, Profile or
bundle head rejects stale submission or approval; refresh supersedes the old Task and
creates a successor without returning to document routing.

Only the active bundle revision is projected into ordinary Paper Card, Evidence and
review-queue reads. Corrections repeat staging and preview, then append a new revision.
Existing Question Mapping and Research Synthesis references to the superseded revision
remain auditable but become stale and cannot provide current factual support until they
are remapped.
Legacy Primary records are never silently adopted or combined with a Primary bundle.

### Review semantic processing and approval

After deterministic intake reaches a completed Review or mixed-document Review gate,
the App uses the P4-C service flow:

```text
completed review_semantic_gate | review_semantic_gate_mixed_document
-> user requests a Review semantic Task and selects an external executor
-> Core creates an independent semantic Job and assesses five requested uses
-> basic Review Memory capability allowed: Task is created
-> App inspects the exact zero-write payload and user confirms the handoff
-> Core prepares or idempotently recovers the bounded prompt and lease
-> external Agent returns seven Review sections and same-review source notes
-> Core validates the common Review contract, exact quote/page provenance and consumed capabilities
-> non-canonical App preview
-> user revises, refreshes, rejects or approves
-> Core allocates new Memory/Unit IDs and atomically appends one Review bundle revision
-> semantic Job and Task receipts complete or recover idempotently
```

Unavailable figure, formula or supplementary capability blocks only a retained note that
requests that operation. It does not block a text-only or zero-Unit Memory. Every retained
Unit must have at least one reproducible source note and one concrete workflow impact.
Quote excerpts must equal the Task-bound page-text slice; paraphrases retain page and
section, or an explicit section-missing reason, without a character locator.

Only the active Review Memory enters ordinary background reads and Catalog. Corrections
allocate a new Memory ID and new Unit IDs while preserving older revisions for audit.
Review content remains background-only after user approval and cannot create Evidence,
factual Question Mapping, Research Synthesis support or a scientific review-queue row.
Legacy Review Memory is never silently adopted or combined with Review bundle authority.

The same inspect-before-prepare order applies to document-route and Primary semantic
Tasks. Inspection never returns a prompt or lease. If the App restarts after leasing, it
may prepare again from the current leased state; Core returns the identical manifest and
lease only while the Task input basis and stored handoff digest remain current.

### Reading and Evidence trace context

P5-A adds a deterministic read path after Primary or Review approval:

```text
paper ID
-> complete Paper Card or Review Memory
-> source / parse / adequacy badges
-> optional Question context
-> Card Unit Evidence IDs
-> revision-bound Evidence trace descriptor
```

Reading never creates a canonical or operational record. Missing or changed source bytes
do not hide an already committed semantic record, but they disable current trace-back.
Evidence lookup searches every Primary bundle revision and reports whether that revision is
active or historical. The bound parse run remains visible even when only a newer parse is
materialized. The result contains no path, source reference, fingerprint or source body;
P5-B adds the separate trusted-backend flow:

```text
Evidence ID
-> exact owning Primary revision
-> exact source manifestation
-> non-persistent Core handle
-> canonical/source lineage revalidation
-> opened and hashed PDF descriptor
-> App-owned opaque session handle or explicit local-reader handoff
```

The browser never supplies or receives a source path. Each open revalidates the Core handle
and returns the already-open descriptor so a later path reopen cannot bypass the checked
fingerprint, regular-file, size or PDF-signature boundary.

## P6-P8 Discovery, Organization And Research Synthesis

```text
explicit Europe PMC query
-> transient metadata results
-> explicit user selection
-> metadata-only candidate
-> optional explicit OA acquisition into an absent local_inbox target
-> stop before Registry and intake
```

Organization and Research Synthesis are separate later actions. Direction, Field Map,
Question, Tag and Question-screening proposals use bounded external-Agent Tasks, App preview
and dedicated user approval. Factual organization links admit only current grounded/revised
Primary Units and derive Evidence closure in Core; Review Memory remains labeled background.

```text
current Question + admissible mapped Primary Units
-> explicit Research Synthesis Task
-> external Agent candidate
-> App preview
-> user revision/reject/approve
-> append or replace one candidate
```

Ordinary query, navigation and intake do not create or refresh Research Synthesis. Internal
`step7-*` names remain compatibility-only.

## P9-P10 Generated Views And Exchange

```text
canonical records + source watermark
-> Core render preview
-> managed-file conflict check
-> explicit one-way Obsidian sync
```

Edited managed output stops overwrite. Continue only by explicit discard or create-only
personal-note export. Reverse Markdown import is not supported.

```text
explicit export scope
-> Core closure and dry run
-> optional rights-asserted source inclusion
-> deterministic archive
-> safe import preflight
-> App preview and user approval
-> immutable external-origin package
```

Imported records remain external and unreviewed. They do not become local factual query or
Research Synthesis support without a separate future review route.

## P11 Backup And Operational Maintenance

```text
backup preview under writer barrier
-> explicit create to absent archive
-> safe inspect
-> restore into confined staging
-> reference/journal/Guardian/source-inventory validation
-> projection rebuild check
-> publish absent restored root
```

Source-free backup records external-source inventory without copying source bytes.
Source-inclusive backup requires explicit user authority and exact digest revalidation.
Neither mode overwrites a workspace, source or archive.

```text
eligible settled journals
-> archive preview
-> explicit user archive
-> immutable segment + durable receipt
-> digest-checked active-journal cleanup
```

Explicit maintenance triggers coalesce by
`(dependent_id, upstream_revision, reason)` and preserve all trigger refs. Freshness remains
lazy by default. P11 acceptance selected `retain_current_layout`; migration and private
legacy-workspace cutover remain separate decisions.

