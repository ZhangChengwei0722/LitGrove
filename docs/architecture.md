# Architecture

## Layers

```text
Shared Core + CLI
-> Portable Agent Skill
-> Separate private workspaces
```

Core owns deterministic contracts, validation, path and ID handling, structured I/O, status gates, logs, Guardian checks, real-PDF page extraction, Step 7/discovery candidate persistence, and bounded stdout read surfaces including manuscript projection. The Agent layer owns scientific reading, interpretation, candidate generation, and workflow decisions. Private workspaces own papers and research records. Persisted Markdown and additional derived views remain deferred.

## P2-A Read-Only Artifact Catalog Boundary

```text
trusted configured workspace option
-> Core workspace session
-> versioned current-record adapters
-> disposable SQLite/FTS projection outside workspace and source roots
-> bounded cursor search
-> authoritative current-record detail
```

`WorkspaceSessionService` accepts only backend-configured option IDs and returns redacted workspace/profile display metadata. Absolute workspace paths stay inside the trusted backend process. `CatalogProjectionService` derives every projection path under one marker-owned App state root that must not overlap the workspace root, knowledge root, local inbox or any source root. Existing state, marker, workspace projection and database paths fail closed when they are links, reparse points or the wrong filesystem type.

The catalog adapter registry covers current Registry papers, Paper Card Units, Evidence, Review Memories and Units, Question Mappings, Step 7 candidates, process events and Guardian reports. Raw parsed pages, review queue content and discovery candidates are deliberately excluded. Unknown future kinds are reported and included in the source watermark without schema guessing.

SQLite/FTS is a disposable projection, never canonical or operational authority. Full rebuild uses a temporary sibling plus atomic replacement. Incremental update removes and recreates only changed or removed source projections in one transaction, verifies source/item/FTS counts and foreign keys, and must converge to a full rebuild. The watermark binds the adapter registry version and indexed durable-record digests; upstream change makes the projection `stale` without rewriting the upstream record.

Search uses a maximum page size of 100 and an opaque cursor bound to the normalized query, filters, ordering and an existing final tuple. Results label projection freshness. Detail lookup reloads the authoritative current structured record, compares its digest with the projected row and returns `current`, `changed` or `missing`; SQLite-only scientific content is never promoted as current detail.

The public App-facing interface is `research_kb.application` version `1.0` plus services exported from `research_kb.services`. P2-A adds no CLI command, canonical schema, workspace layout, scientific mutation or Agent runtime.

## P2-B Catalog Scale Measurement Boundary

P2-B adds repository-only benchmark tooling around the existing P2-A contracts. It does not add a production schema or install benchmark commands in the wheel.

```text
named synthetic profile + fixed seed
-> absent operation-owned target outside the repository
-> current Core workspace validation
-> disposable catalog projection
-> path-redacted measurement receipt
```

`p2-catalog-generator@1.0` produces deterministic current-contract records, authored synthetic source text, canonical serialization and digest inventories. The committed `p2-small` form is an uninitialized portable seed because the Core workspace marker intentionally binds resolved runtime paths. Large generated workspaces and SQLite files remain outside Git, and benchmark execution never implies cleanup authority.

Catalog search now accepts optional exact `paper_id` and `question_id` filters. Both filters participate in opaque cursor identity and use existing SQLite indexes; omitting them preserves the P2-A response shape. A just-completed projection result may be bound to a Query Service only after its workspace, stored watermark, item count and unknown-kind set match the actual SQLite projection.

Default detail lookup resolves the exact authoritative store selected by the projection row, validates that record and compares its canonical digest before returning content. Injected loaders retain the complete-bundle fallback. This removes complete-workspace reloads for per-paper JSON records, but monolithic JSONL stores still require a bounded scan and are not claimed to meet the final R0 budget.

The fixed reference profile represents 50,000 papers, 250,000 scientific catalog items and 500,000 operational catalog items. The 250,000 figure is a projection-item count, not 250,000 independent canonical records. Preliminary Windows results are development evidence only: final budget freeze remains P2-E, where slow incremental projection and monolithic Registry detail are explicit blockers.

## P2-E Bounded R0 Projection And Detail Boundary

P2-E closes the two P2-B blockers while preserving canonical authority. Disposable
Catalog schema version 2 stores a normalized Registry store key, byte offset and byte
length. Registry detail maps that key through the active `WorkspaceLayout`, seeks the
exact canonical JSONL bytes, then validates UTF-8/LF, record ID, schema and canonical
digest. Offset drift, missing stores and changed bytes return `changed` or `missing`;
SQLite content never substitutes for canonical detail.

The optimized Registry delta is benchmark-only. It requires the current projection
watermark, Registry before/after digests and a stable writer boundary, streams and
validates the complete changed store, and applies changed/added/removed rows in one
SQLite transaction. Production `update()` still performs complete workspace loading
because a current Registry transaction also appends a process event. P3 may promote a
receipt-bound delta only after its writer receipt covers every changed store.

`CatalogProjectionService.bind_existing_projection()` performs bounded projection and
workspace-identity inspection after process restart. It conservatively reports `stale`
with `freshness_verification: unverified_after_restart`; it does not claim a current
watermark without a full rebuild, complete validation or receipt-bound update. Querying
the stale projection is allowed with the label, while each detail independently proves
current canonical bytes.

The frozen `r0-windows-catalog-v1` profile passed full rebuild, 1,000-record Registry
delta, selective FTS, authoritative Registry detail, App/Core ready-state and memory
thresholds. Raw receipts and the centralized no-deletion lifecycle report are retained
in the local App repository. No canonical schema, workspace layout, CLI command,
scientific mutation or private workspace was added.

## Knowledge Flow

```text
Source Intake -> Registry -> Parse
-> On-demand discovery: fixed public connector -> transient metadata report -> optional user-selected metadata candidate
-> Primary route: Paper Card Core -> Evidence Grounding -> Question Mapping
-> Review route: background-only Review Memory
-> Ephemeral query route: Paper Card Units -> optional Evidence trace-back -> task report
-> Candidate thinking route: mapped grounded Card Units -> Step 7 structured candidates
-> Manuscript route: exact local DOCX/PDF -> transient structured projection -> optional explicit-criteria Agent audit -> private task report
-> Guardian / Feedback
```

Canonical evidence is the provenance backbone. Paper Card Units are the semantic entry for later reasoning. Step 7 remains candidate-level and must expand back to canonical evidence. Discovery candidates are a separate metadata-only follow-up queue and never enter this evidence chain by selection alone.

## Milestone 1B Runtime

```text
candidate mutation request
-> trusted actor boundary
-> kind-specific normalization and CLI-owned IDs
-> schema and cross-record validation
-> workspace lock
-> same-directory fsynced temp file
-> mode-preserving, digest-checked os.replace
-> post-replacement source stability check where required
-> journal-derived process event
-> completed recovery journal with final result
```

Registry, SyntheticText/PdfPlumber Parse, Paper Card, Evidence, review queue, Review Memory, and Guardian services use the same workspace resolver and transaction kernel. Source references are persisted as `root_id + relative_path`; local absolute paths are never canonical data.

Guardian requires every completed journal to have exactly one matching process event. Missing or altered events and all `needs_resolution` journals fail closed instead of being inferred from target state alone.

Existing canonical records are validated with the internal `stored` context. This bypasses submitter-state checks only while reading already persisted state; it grants no mutation authority and is not exposed by CLI actor choices.

## M2A-1 Workspace Boundary

```text
existing workspace config + domain profile
-> shared semantic validation
-> read-only preflight
-> workspace lock and repeated preflight
-> exact managed directory scaffold
-> deterministic workspace identity marker
-> initialized runtime
```

`WorkspaceLayout.load` is the single initialized-workspace gate used by Registry, Parse, Record, Guardian, and Recovery commands. Bootstrap alone may resolve an unbound config. A marker mismatch, unsafe layout, unknown managed content, source/root conflict, or missing marker fails closed.

`.research-kb/workspace.json` contains only workspace/profile identities, the layout contract version, and a SHA-256 config fingerprint. It is operational metadata, not canonical scientific state, and emits no process event or transaction journal. A markerless populated M1B store is adopted only after its complete structured bundle and transaction state validate without rewriting records.

## M2A-2 Read-Only Compatibility Boundary

```text
initialized workspace + explicitly injected LegacyReaderAdapter
-> protected source snapshots
-> normalized legacy inventory and deterministic differences
-> public compatibility-report on stdout
-> protected source snapshots repeated in finally
```

Shared Core owns adapter metadata validation, source-reference confinement, difference IDs, blocking policy, report schemas, ordering, and exit codes. The adapter owns only private legacy interpretation and candidate inventory. The installed CLI has an empty adapter registry: there is no module-path loading, plugin discovery, entry-point scan, or production private adapter in this repository.

Compatibility inspection is not migration. It allocates no replacement canonical IDs, writes no report or process event, and does not alter the legacy source of truth. If declared protected input changes, disappears, changes type, or becomes unsafe during inspection, the run fails with `RKBC-026` even when the adapter also raises.

Step 7 runtime, persisted Markdown views and migration remain outside the compatibility layer. Portable Skill orchestration is a separate Agent layer and never enters a compatibility adapter.

## M2B-1 Question Mapping Boundary

```text
user-supplied or user-approved question
+ selected Paper Card Units
+ question-specific review queue boundaries
-> QuestionMappingService
-> exact evidence and boundary projection
-> questions/mappings.jsonl
```

The Agent supplies the semantic selection, role, and rationale. Core owns `question_id`, `question_link_id`, domain binding, timestamps, evidence expansion, required Card Unit boundaries, ordering, validation, and atomic persistence. One question has at most one link per paper; replace preserves existing question/link identities and cannot remove a paper link in M2B-1.

`questions/mappings.jsonl` is canonical organizational state, not canonical scientific evidence. It points back to Paper Card Units and canonical evidence rather than duplicating their scientific content. Guardian warns with `RKBC-014` when linked Card, evidence, or queue records are newer than a mapping; it never refreshes the mapping automatically.

New workspaces initialize at layout `m3c-2a`. Exact `m3b-1` predecessors are runtime-blocked with `RKBC-027` and can be upgraded only through `workspace init`. The current upgrade creates an empty `discovery/` directory and replaces operational marker metadata; it rewrites no canonical record and creates no empty JSONL store, process event or journal.

## M2B-2 Question Reading View Boundary

```text
validated Question Mapping + reachable Registry/Card/Evidence/Queue records
-> deterministic in-memory projection and source snapshot digest
-> one UTF-8/LF Markdown document on stdout
```

`QuestionReadingViewService` accepts structured bundle entries rather than paths or a workspace object. It validates the complete bundle, resolves exactly one question, preserves domain-profile Card section order, expands only mapping-owned evidence and boundaries, and reuses the existing freshness diagnostic. Review queue records are rendered in a separate, explicitly non-evidence section.

The CLI completes validation, projection, hashing, and rendering before its single stdout write. It creates no `views/` directory, canonical record, cache, report, event, journal, lock, or render timestamp. The view is a one-way reading surface; JSONL remains the organizational source of truth.

## M3A-0A Real PDF And Evidence Provenance Boundary

```text
registered immutable PDF and SHA-256
-> explicit PdfPlumberAdapter and exact installed version
-> one LF-normalized parsed-page row per PDF page
-> same-paper page/locator/quote resolution
-> Evidence promotion and Guardian enforcement
```

`pdfplumber` is an optional, lazily imported dependency. The CLI exposes two explicit deterministic profiles and never discovers or substitutes adapters. `pdfplumber` retains the original spatial profile; `pdfplumber-text-flow` uses content-stream order and a smaller horizontal tolerance for new scientific intake. Adapter name plus exact package version forms parser identity. Neither profile claims layout verification. Real Evidence uses zero-based, end-exclusive character locators over stored page text; invented synthetic fixtures retain block locators only under whitespace-normalized same-page containment.

Complete-bundle validation owns active page order, uniqueness, parse-run identity, parser identity, and Evidence resolution. `RecordService` separately owns filesystem source-stability checks before and after Evidence replacement. This adds no schema or layout state and does not make PDF extraction a scientific interpretation step.

## M3A-0B Deterministic Skill-Facing Interface

```text
public capability facts
+ validated paper-stage and safety projection
+ selected parsed-page records
+ bounded JSON stdin
-> Skill procedure without a second workflow store
```

`CapabilityService` is workspace-independent and reports both implemented adapters and installed availability. `ParseReadService` and `PaperStatusService` require an initialized workspace and complete bundle validation. They build transient interface `1.0` documents in memory and never write a cache, view, report, event, journal or lock.

Paper status derives only structural facts: source state, active parse identity, Card and Unit status counts, Evidence/queue counts, linked mapping freshness, Guardian finding codes, and transaction phase counts. It contains no scientific payload and does not prescribe a next action. Parsed-page reads may contain private page text for the explicitly selected paper and recheck source SHA-256 before returning.

Bounded stdin input is a transport boundary, not a new mutation path. Registry metadata and mutation requests enter their existing services after strict UTF-8 JSON-object and byte-limit validation. No schema, layout, ID namespace, dependency or persisted workflow state is added.

## M3A-0C Paper Context Read Boundary

```text
validated registered paper and current source fingerprint
+ stored Paper Card or null
+ same-paper canonical Evidence
+ same-paper review queue records
-> transient interface 1.0 context on stdout
```

`PaperContextService` exists so the Skill can recover CLI-owned Unit, Evidence and queue IDs after a partial or completed run without parsing canonical file paths. It validates the complete workspace bundle, filters exactly one paper, sorts canonical arrays by ID and rechecks source SHA-256 before returning.

The output may contain private scientific content for the explicitly selected paper, but never Registry source references, paths, parsed pages, Question Mappings or unrelated-paper records. It creates no record, cache, report, event, journal, lock or workflow state and does not infer document type, scientific completion or a next action.

## M3A-0D Intake Preflight Boundary

```text
absolute user-selected source path
+ initialized workspace and complete validated bundle
-> exactly one declared source root and portable source reference
-> exact-path Registry state and active Paper Card section projection
-> transient interface 1.0 response
```

`IntakeInspectService` resolves the source to a real regular file, rejects relative paths, root/link escapes and ambiguous root ownership, then round-trips the derived POSIX source reference through `WorkspaceLayout`. It hashes the source before and after reading exact Registry matches and the active domain profile.

Registration identity is exact `root_id + relative_path`. Identical bytes at another path remain unregistered for the selected path. One exact match is `registered_current` only when its stored hash matches; a changed hash is `registered_stale`; multiple exact historical owners are `ambiguous`. The output omits absolute paths, hash values, bibliography, unrelated records and semantic next actions.

Intake preflight writes nothing and does not replace `registry add`. It supports deterministic sequential reruns for the Skill, but does not provide atomic inspect-and-register or concurrent same-source deduplication.

## M3A-1 Primary-Research Portable Skill

```text
natural-language local primary-paper task
-> repo-owned procedural Skill
-> public Core reads and bounded stdin mutations
-> existing canonical stores and Guardian
-> non-canonical task report
```

`skills/research-kb/` is the reviewed Agent-layer source. Its concise `SKILL.md` routes detailed command, discovery, primary intake, review intake, query/Step 7, authority and reporting rules to seven one-level reference files. It contains no scripts, duplicate schemas, persistent state or scientific fixtures.

The Skill processes sources sequentially, uses `intake inspect` for absolute local paths or `intake inspect-acquired` for separately authorized acquired candidates before registration, reads current state through `paper status` and `paper context`, reads scientific text through `parse show`, and submits candidates through existing CLI mutation authority. It classifies document type only in task memory and stops non-primary documents before Paper Card or Evidence promotion.

The Python wheel does not package or install the Skill. A reviewed repository merge and a separately authorized CC Switch installation are distinct gates. M3A-1 itself remains the primary route; M3A-2A additively extends the same package for reviews.

## M3A-2A Common Review Memory Runtime

```text
registered review + current immutable source + active parse
-> common seven-section Review Memory candidate
-> Core-owned Memory/Unit IDs and evidence-boundary constants
-> atomic review_memories/by_paper promotion
-> review context + Guardian freshness projection
```

All five supported review subtypes share `review-memory.schema.json`. Sections may be empty; a reusable record retains at least one actionable Unit, while low-value/redundant/outdated/out-of-scope records retain zero Units and a reason. Page/section paraphrases use a null locator; quote excerpts use exact character locators over the active same-paper parse.

Review Memory and Review Unit IDs are generic record references for transactions and Guardian only. They are not Evidence IDs, Paper Card Unit IDs, Question Mapping support or Step 7 support. Primary Paper Card/Evidence and Review Memory routes are mutually exclusive per paper.

`review context 1.0` is separate from `paper context 1.0`. It returns the complete selected memory, bounded freshness and transient exact local DOI matches. A stale parse produces `RKBC-014` warning and never rebinds old provenance; broken provenance against the current snapshot is an error.

The Portable Skill adds one review route and no second deterministic implementation. Subtype-specific schemas, Field Map integration, Review Unit Question Mapping, Step 7, discovery and acquisition remain outside M3A-2A.

## M3B-1 Deterministic Step 7 Candidate Runtime

```text
current Question Mapping + selected grounded/revised Card Units
-> Agent semantic candidate
-> Core-derived Evidence and Unit-boundary closure
-> one type-specific Step 7 JSONL store
-> context / stdout Markdown / Guardian freshness
```

Four type-specific stores live under `step7/`: synthesis, review angle, insight and cross-view. `Step7CandidateService` accepts append or complete semantic replace through `record promote`. It owns candidate identity, type, exact `evidence_base`, `review_queue_refs`, `input_snapshot`, timestamps and fixed candidate-only status constants. A candidate cannot create a question, select Units outside the current mapping, use non-factual Units, accept caller-owned closure fields or use Review Memory as support.

The transaction validator reloads the current relevant mapping, Cards, Evidence, queue records and Cross-View sources while holding the workspace lock. Process events and journals contain IDs, never candidate scientific text. Cross-View sources must be current, same-question and non-rejected at promotion time.

Valid upstream changes do not corrupt an older candidate. `candidate_freshness` projects `current` or `stale_upstream` with stable reasons; Guardian emits `RKBC-014` without rewriting. Missing IDs, impossible ownership, unexplained closure mismatches and cross-question references remain errors. `step7 context` is the structured recovery surface; `step7 render` is a one-way, non-canonical UTF-8/LF Markdown view on stdout. M3B-1 does not add Agent generation/refresh orchestration, Review Unit support, Field Map integration, persisted Markdown or an LLM inside Core.

## M3B-2 Portable Skill Query And Step 7 Orchestration

```text
ephemeral paper/question query
-> grounded/revised Paper Card Units
-> canonical Evidence expansion when trace-back is requested
-> private task report with zero writes

explicit Step 7 maintenance
-> context and semantic reconciliation
-> record promote through Core authority
-> context / render / Guardian
```

The Skill classifies intent before mutation. Ordinary seven-section explanations, overview, methods, comparison, claim trace-back and research-direction discussions remain in the task report only. Review Memory may inform labeled background discussion but cannot become primary support.

Step 7 persistence requires an explicit maintenance request or an intake invocation that explicitly requests the complete workflow. Exact semantic reruns write nothing; same-candidate changes use replace; materially distinct candidates may append; uncertain near-duplicates stop. Core still owns IDs, closure, snapshots and states, while the Agent owns scientific semantics and duplicate judgment. M3B-2 adds no Python runtime, schema, layout, dependency, query-answer store or direct JSONL access.

## M3C-1 On-Demand Europe PMC Discovery

```text
explicit date and field-keyword request
-> built-in Europe PMC connector at one fixed HTTPS endpoint
-> bounded provider pages
-> local date / keyword / preprint filters
-> exact DOI deduplication and possible-title-duplicate marking
-> transient interface 1.0 task report data
```

Discovery is workspace-independent and has no mutation path. `DiscoveryRequest` closes dates, field assignments, match mode, preprint inclusion and the 1-15 result bound. The connector protocol is provider-neutral, but the installed registry contains only `europe-pmc`; there is no plugin discovery or caller-supplied endpoint.

The standard-library transport rejects redirects, bounds timeout and bytes, ignores provider-returned next-page URLs and reuses only an opaque cursor against the same endpoint. Provider metadata remains untrusted: Core validates shape and size, strips markup with an HTML parser and reapplies all user filters locally.

Exact normalized DOI identity may merge duplicate provider rows. Different or missing DOI identities are retained; similar titles only create symmetric possible-duplicate references. `paper_type`, full-text availability and unresolved version relation are metadata projections, not scientific judgment or acquisition authority.

Live provider state can change, so M3C-1 does not promise byte-identical searches across time. It guarantees that the same validated request plus the same provider page payloads produce the same normalized bytes. Any page, transport or output failure produces no partial stdout report. Browser login, downstream intake and Crossref remain separate milestones.

## M3C-2A Approved Candidate Handoff

```text
complete validated M3C-1 report
+ explicit user-selected result keys
+ optional existing Question Mapping IDs
-> deterministic selection contexts
-> discovery/candidates.jsonl
```

`DiscoveryCandidateService` validates the complete transient report without calling the provider again. Selection requires exact `actor: user`; relevance, rank, paper type and full-text status never imply approval. Only selected results persist. The report itself and all unselected metadata remain outside the workspace.

Candidate identity is the M3C-1 `result_key`; Core allocates a separate `discovery_<uuid4>` record ID. A selection context hashes provider, result key, normalized query and sorted target questions. Exact intent reruns are zero-write, a new context updates the existing candidate, and any metadata change under the same result key fails the whole batch with `RKBC-034`.

The candidate store is metadata-only organizational state. Selection creates `user_selected`, `metadata_only`, `not_started`, `not_evidence: true` and `passed_auto_checks`. Question IDs are labels pointing to existing mappings, not paper links. Events and journals contain only candidate and question IDs. `discovery list/show` validate the complete bundle and expose no absolute source path or canonical paper content.

## M3C-2B OA Resolution And Explicit Acquisition

`discovery resolve` is a zero-write live check against exact DOI or stored Europe PMC identity. It returns an opaque OA asset reference and never exposes or follows a provider URL as caller authority.

`discovery acquire` is a separate exact-user-authority mutation. `local_inbox` must already exist under exactly one declared source root. The fixed PMCID route streams at most 64 MiB into an exclusive same-directory partial, validates PDF bytes and transient parser preflight, then publishes `<candidate_id>.pdf` create-only during the candidate-store transaction.

Success changes only `acquisition_status` to `acquired` and adds a portable receipt. Existing `not_started` records need no migration. The source is still not registered, screened, parsed or evidence-bearing. Guardian verifies receipt hash/size/target and reports unreceipted finals, partials and crash journals without deleting or adopting them.

## M3C-2C Acquired Candidate Intake Handoff

`intake inspect-acquired` closes only the deterministic read gap between an acquisition receipt and existing intake. It accepts a candidate ID, verifies the exact receipt source before and after inspection, and returns portable source, registration state, domain sections and Registry bibliography input without exposing an absolute path or writing state.

The Portable Skill may pass the returned values unchanged to the existing `registry add` only when the task explicitly requests knowledge-base intake. Registry remains the sole owner of paper IDs, fingerprints, duplicate candidates and transaction semantics. The candidate stays metadata-only and no Parse or later scientific record is created by this bridge.

## M3C-2D Acquired Candidate Workflow Continuation

M3C-2D adds no Core runtime, schema, layout or capability flag. It removes a Portable Skill stop after the existing acquired-candidate Registry handoff: a separately authorized intake task now feeds the returned paper ID into the same `paper status`, context, Parse and mutually exclusive primary/review route used for local sources.

The acquisition command remains isolated and cannot chain into intake. An explicit `registry_only` intake may still stop after Registry; ordinary knowledge-base intake continues through Guardian, and Step 7 remains separately explicit and primary-question scoped. Provider paper type remains metadata rather than scientific classification.

## M3D-0A Read-Only Manuscript Projection

```text
initialized workspace + exact local DOCX/PDF under one source root
-> portable source projection + SHA-256 stability checks
-> bounded OOXML paragraph or pdfplumber page extraction
-> stable task units and coverage limits on stdout
-> zero persistence
```

DOCX projection reads only `word/document.xml` and optional `word/styles.xml`, preserves body/table paragraph order, and never follows relationships or executes embedded content. PDF projection reuses the fixed text-only `pdfplumber` policy. Both enforce source, archive, unit and text bounds and return `RKBC-035` for unsupported manuscript content while preserving `RKBC-028` for a missing PDF extra.

The source fingerprint identifies the exact inspected draft but does not register it. No manuscript store, schema, ID, event, journal, cache, claim extraction, evidence match, audit finding or rewrite is created in M3D-0A.

## M3D-1 Explicit-Criteria Manuscript Audit

```text
explicit criteria + exact current-request question/paper selectors
-> M3D-0A transient projection
-> bounded invocation-local section/claim map
-> grounded/revised Card Units
-> canonical Evidence expansion when exact factual support matters
-> scope-limited private report with zero writes
```

M3D-1 is a Portable Skill and Agent-protocol route, not a Core semantic engine. Criteria must exist before manuscript inspection and request-resolved scope may resolve only selectors already present in the request. The Agent preserves source fingerprint and projection limits, uses exact unit slices or honest unit-level fallback, and cannot use Review Memory, review queue or Step 7 as factual support.

The audit creates no schema, stable ID, workspace record, event, journal, cache, Markdown view or manuscript edit. Findings apply only to the requested criterion and checked local corpus; a local absence is not whole-field contradiction. Rewriting remains a separate task tied to the audited source fingerprint.

## P1 Shared Application-Service Facade

The CLI and future App backend share focused Python application services. P1 extracts the remaining CLI-owned validation, Question query/render, Step 7 render, named Parse and transaction-recovery composition without introducing a generic command interpreter.

```text
CLI or future host
-> focused application service
-> existing domain service / contract / transaction primitive
-> typed mapping, bytes or result + exit classification
```

`ContractValidationService`, `JsonlValidationService`, `PrivacyScanService`, `QuestionQueryService`, workspace-aware Question/Step 7 render services, `ParseApplicationService` and `TransactionRecoveryService` own the extracted rules. Existing Registry, record, discovery, capability, compatibility, intake, paper/review, Guardian and bootstrap services remain directly reusable.

The CLI owns only argument parsing, bounded stdin/file decoding, JSON/byte output, diagnostic redaction and process exit projection. Workspace-aware render services load and validate structured entries before invoking the existing pure renderers. The named Parse registry contains only explicit factories and never auto-detects, substitutes or falls back.

P1 changes no public CLI arguments or payloads, schema, layout, source-write authority or scientific semantics. It creates no App, Pipeline Job, Source Adequacy or Agent Task runtime.

## P3-A Pipeline Job Kernel

Pipeline Jobs are append-only operational state, not scientific records and not a
workflow engine. A root state captures route, depth, current node, input refs and an
authority snapshot. Transitions bind their predecessor ID and digest, preserve the
captured authority, and correlate exactly one successful process event. Current state is
a projection over the revision chain; list uses stable ordering and an opaque cursor.

Waiting, cooperative cancellation and recovery are explicit transitions. A recovery
never guesses through a stale head or changes the requested route. Guardian checks chain,
event and transaction consistency. Job state and event summaries contain no paper text,
source path, source fingerprint or Agent payload.

## P3-B Source Assets And Registry Identity

The optional `registry/source_assets.jsonl` store records append-only Source Asset state
revisions. A legacy Registry source remains the implicit main manifestation until a
current explicit `main_pdf` Source Asset exists. Current source resolution is:

```text
current explicit main Source Asset
-> otherwise legacy Registry source
-> live portable-ref and digest observation
```

Reference, local-inbox copy, bounded scan selection, observation and same-digest relink
all require a current Pipeline Job with matching authority. Copy additionally requires
the exact user actor. Core accepts a bounded binary stream; the local CLI path is only a
thin adapter that binds source identity before opening that stream. Copy stages bytes,
commits the append-only Source Asset receipt, and then publishes an operation-owned absent
target create-only. The same Job resumes a digest-bound partial, a receipt with a missing
target, or an already published target without duplicating either node. Scan is transient,
rejects an inbox larger than its inspection budget, and revalidates selected bytes through
an opaque handle. Source-path checks reject root escape, symlink/junction/reparse traversal
and detectable hard-link ambiguity, including links introduced after initial inspection.

Reference, copy and selection may append an unassociated Source Asset before Registry
identity exists. Its revision-one Job remains the owning Job. After Registry creation,
`source associate` requires exact `associate_source_asset` authority, current asset CAS,
an existing paper and a still-current available manifestation. Association appends a
revision and never rewrites the intake receipt. The owning Job cannot enter a successful
terminal state while that asset remains unassociated. `failed` and `cancelled` remain
valid terminal outcomes: they preserve committed source history and leave the incomplete
association visible to Guardian rather than undoing it.

When a paper is already known, `main_pdf` intake and later association require the exact
Registry source fingerprint. Supplements and source data do not replace that identity.
Public scan excludes every existing Registry or Source Asset ref; only selection replay
for the same Job and exact revision-one intent may reconsider a registered ref. An
unassociated asset remains owned by its revision-one Job: another Job cannot re-register
or re-select it as a new intake receipt. A separately authorized association Job may append
`paper_associated`, but that does not change revision-one ownership.

Same-digest relink retains manifestation identity and Parse currentness. Changed bytes
append a `change_candidate`; missing, inaccessible or unsafe paths append an availability
state. These states preserve historical Parse and scientific records while projecting
automatic reuse as `stale_source` or unavailable. Source list, Catalog and event outputs
omit absolute paths and source fingerprints.

Transition reasons are closed. Roots are `reference_registered` or
`copied_into_local_inbox`; only `paper_associated` adds a paper; only
`same_digest_relink` changes a portable ref while retaining the active digest;
`changed_bytes_observed` creates a candidate manifestation; and
`source_available|source_missing|source_inaccessible|source_relink_required` record
observations without rewriting identity.

The optional `registry/identity_corrections.jsonl` store records exact user decisions for
duplicate merge, mistaken-merge split, alias, archive and tombstone. Events form one
digest-bound chain and never rewrite Registry rows, paper IDs or historical references.
Current canonical-paper and active-library status are projections. Catalog indexes only
affected current identity projections and current Source Asset heads, never raw correction
events or historical source revisions.

P3-B does not create Source Adequacy, Agent Task, semantic routing or App write controls.
Those later consumers must use the current source projection rather than silently binding
historical provenance to the active parse.
