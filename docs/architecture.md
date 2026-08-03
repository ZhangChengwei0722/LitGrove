# Architecture

## Layers

```text
Shared Core + CLI
-> Portable Agent Skill
-> Separate private workspaces
```

Core owns deterministic contracts, validation, path and ID handling, structured I/O, status gates, logs, Guardian checks, real-PDF page extraction, Research Synthesis/discovery candidate persistence, and bounded stdout read surfaces including manuscript projection. The Agent layer owns scientific reading, interpretation, candidate generation, and workflow decisions. Private workspaces own papers and research records. Persisted Markdown and additional derived views remain deferred.

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

The catalog adapter registry covers current Registry papers, Paper Card Units, Evidence, Review Memories and Units, Question Mappings, Tags, Research Synthesis candidates, process events and Guardian reports. Raw parsed pages, review queue content and discovery candidates are deliberately excluded. Unknown future kinds are reported and included in the source watermark without schema guessing.

SQLite/FTS is a disposable projection, never canonical or operational authority. Full rebuild uses a temporary sibling plus atomic replacement. Incremental update removes and recreates only changed or removed source projections in one transaction, verifies source/item/FTS/facet counts, ordered Tag-facet integrity and foreign keys, and must converge to a full rebuild. The watermark binds the adapter registry version and indexed durable-record digests; upstream change makes the projection `stale` without rewriting the upstream record.

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
-> Candidate thinking route: mapped grounded Card Units -> Research Synthesis structured candidates
-> Manuscript route: exact local DOCX/PDF -> transient structured projection -> optional explicit-criteria Agent audit -> private task report
-> Guardian / Feedback
```

Canonical evidence is the provenance backbone. Paper Card Units are the semantic entry for later reasoning. Research Synthesis remains candidate-level and must expand back to canonical evidence. Discovery candidates are a separate metadata-only follow-up queue and never enter this evidence chain by selection alone.

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

Research Synthesis runtime, persisted Markdown views and migration remain outside the compatibility layer. Portable Skill orchestration is a separate Agent layer and never enters a compatibility adapter.

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

`skills/research-kb/` is the reviewed Agent-layer source. Its concise `SKILL.md` routes detailed command, discovery, primary intake, review intake, query/Research Synthesis, authority and reporting rules to one-level reference files. It contains no scripts, duplicate schemas, persistent state or scientific fixtures.

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

Review Memory and Review Unit IDs are generic record references for transactions and Guardian only. They are not Evidence IDs, Paper Card Unit IDs, factual Question Mapping support or Research Synthesis evidence support. Primary Paper Card/Evidence and Review Memory routes are mutually exclusive per paper.

`review context 1.0` is separate from `paper context 1.0`. It returns the complete selected memory, bounded freshness and transient exact local DOI matches. A stale parse produces `RKBC-014` warning and never rebinds old provenance; broken provenance against the current snapshot is an error.

The Portable Skill adds one review route and no second deterministic implementation. Subtype-specific schemas, Field Map integration, Review Unit Question Mapping, Research Synthesis, discovery and acquisition remain outside M3A-2A.

## M3B-1 Deterministic Research Synthesis Candidate Runtime

```text
current Question Mapping + selected grounded/revised Card Units
-> Agent semantic candidate
-> Core-derived Evidence and Unit-boundary closure
-> one type-specific Research Synthesis JSONL store
-> context / stdout Markdown / Guardian freshness
```

Four type-specific stores live under `step7/`: synthesis, review angle, insight and cross-view. `Step7CandidateService` accepts append or complete semantic replace through `record promote`. It owns candidate identity, type, exact `evidence_base`, `review_queue_refs`, `input_snapshot`, timestamps and fixed candidate-only status constants. A candidate cannot create a question, select Units outside the current mapping, use non-factual Units, accept caller-owned closure fields or use Review Memory as support.

The transaction validator reloads the current relevant mapping, Cards, Evidence, queue records and Cross-View sources while holding the workspace lock. Process events and journals contain IDs, never candidate scientific text. Cross-View sources must be current, same-question and non-rejected at promotion time.

Valid upstream changes do not corrupt an older candidate. `candidate_freshness` projects `current` or `stale_upstream` with stable reasons; Guardian emits `RKBC-014` without rewriting. Missing IDs, impossible ownership, unexplained closure mismatches and cross-question references remain errors. `step7 context` is the structured recovery surface; `step7 render` is a one-way, non-canonical UTF-8/LF Markdown view on stdout. M3B-1 does not add Agent generation/refresh orchestration, Review Unit support, Field Map integration, persisted Markdown or an LLM inside Core.

## M3B-2 Portable Skill Query And Research Synthesis Orchestration

```text
ephemeral paper/question query
-> grounded/revised Paper Card Units
-> canonical Evidence expansion when trace-back is requested
-> private task report with zero writes

explicit Research Synthesis maintenance
-> context and semantic reconciliation
-> record promote through Core authority
-> context / render / Guardian
```

The Skill classifies intent before mutation. Ordinary seven-section explanations, overview, methods, comparison, claim trace-back and research-direction discussions remain in the task report only. Review Memory may inform labeled background discussion but cannot become primary support.

Research Synthesis persistence requires an explicit maintenance request or an intake invocation that explicitly requests the complete workflow. Exact semantic reruns write nothing; same-candidate changes use replace; materially distinct candidates may append; uncertain near-duplicates stop. Core still owns IDs, closure, snapshots and states, while the Agent owns scientific semantics and duplicate judgment. M3B-2 adds no Python runtime, schema, layout, dependency, query-answer store or direct JSONL access.

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

The acquisition command remains isolated and cannot chain into intake. An explicit `registry_only` intake may still stop after Registry; ordinary knowledge-base intake continues through Guardian, and Research Synthesis remains separately explicit and Question scoped. Provider paper type remains metadata rather than scientific classification.

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

M3D-1 is a Portable Skill and Agent-protocol route, not a Core semantic engine. Criteria must exist before manuscript inspection and request-resolved scope may resolve only selectors already present in the request. The Agent preserves source fingerprint and projection limits, uses exact unit slices or honest unit-level fallback, and cannot use Review Memory, review queue or Research Synthesis as factual support.

The audit creates no schema, stable ID, workspace record, event, journal, cache, Markdown view or manuscript edit. Findings apply only to the requested criterion and checked local corpus; a local absence is not whole-field contradiction. Rewriting remains a separate task tied to the audited source fingerprint.

## P1 Shared Application-Service Facade

The CLI and future App backend share focused Python application services. P1 extracts the remaining CLI-owned validation, Question query/render, Research Synthesis render, named Parse and transaction-recovery composition without introducing a generic command interpreter.

```text
CLI or future host
-> focused application service
-> existing domain service / contract / transaction primitive
-> typed mapping, bytes or result + exit classification
```

`ContractValidationService`, `JsonlValidationService`, `PrivacyScanService`, `QuestionQueryService`, workspace-aware Question/Research Synthesis render services, `ParseApplicationService` and `TransactionRecoveryService` own the extracted rules. Existing Registry, record, discovery, capability, compatibility, intake, paper/review, Guardian and bootstrap services remain directly reusable.

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

P3-B itself does not create Source Adequacy, Agent Task, semantic routing or App write
controls. P3-C consumes its current source projection rather than silently binding
historical provenance to the active parse.

## P3-C Source Adequacy And Deterministic Trunk

The optional `process/source_adequacy.jsonl` store is append-only operational history.
Each Profile binds one registered paper and Pipeline Job to the exact source role set,
manifestation IDs, parse run, parser adapter/version/profile digest, authoritative parsed
page bundle digest, requested operation and rule versions. A legacy Registry source is an
implicit `main_pdf`; its ref, fingerprint-derived manifestation and role must match the
Registry row. A parser profile digest is recomputed from the registered descriptor during
bundle validation rather than trusted from stored input.

Source Adequacy answers fitness for a requested use, not scientific credibility. Its
public capability vocabulary separates basic understanding, complete reading, continuous
text citation, figure/table extraction, layout-sensitive analysis and supplementary
analysis. Deterministic hard failures cannot be overridden. Non-hard uncertainty may have
an explicit user successor decision; P3 rejects Agent-authored assessments.

Freshness is read-time and capability-specific. Changed source bytes, parse run, parser
profile, parse output or rule version make affected uses stale without rewriting old
Profiles. Historical Profiles remain valid after the active parsed-page projection is
replaced: when their old pages are no longer active, their event and self-describing parser
digest remain provenance, while they cannot pass a current gate.

The deterministic trunk advances one authorized `local_source / semantic_gate` Pipeline
Job through source observation, parse reuse or explicit reparse, Source Adequacy assessment
and a zero-write capability gate. Missing sources, supplements, OCR/layout/reparse choices
and non-hard uncertainty route to their specific Pipeline Job wait reason. Only parser
domain or adapter-execution failures become `parse_failed`; authority, schema, source race
and transaction-integrity errors fail closed.

An allowed use stops at `route_ambiguous` until an explicit user decision selects
`primary` or `review`; `mixed_document` is preserved as a review-route reason. P3-C creates
no Paper Card, Evidence, Review Memory, scientific review queue or Agent Task. Those
semantic and App-preview responsibilities remain in P4 and P3-D respectively.

## P3-D0 Session-Bound Deterministic Intake Facade

`DeterministicIntakeApplicationService` is the sole Core-owned composition boundary for
the first App intake workflow. It accepts an opaque `WorkspaceSession`; only Core reads
the session layout. The facade supports bounded inbox scan, upload-stream start,
watched-inbox start, resume, cancel, list, detail and authoritative limits. It adds no
CLI command, browser transport, schema or App workflow database.

One start request creates or exactly replays a `local_source / semantic_gate` Pipeline
Job with a closed authority set for its ingress mode. Upload requires a backend-computed
size and SHA-256 and uses the existing create-only local-inbox stream boundary. Watched
inbox requires both candidate selection and reference-registration authority. Browser
inputs cannot add operations or submit a server filesystem path.

Recovery is receipt-based. The facade requires exactly one mode-matching Source Asset
root and exactly one correlated success event containing that root and state ID. Registry
and association steps likewise reuse one correlated receipt. A crash after any committed
substep appends only the missing Job transition; it never repeats Registry identity
creation or moves the deterministic intake node backwards.

P3-D0 permits `running -> running` only as deterministic progress: `current_node` must
change, committed outputs must be a strict superset, and wait, retry and recovery state
must remain unchanged. The same rule validates stored history so tampered chains fail
closed. Public results omit source refs, paths, fingerprints, idempotency keys, authority
snapshots, raw parsed text and user-authored reason text.

The facade stops at an explicit wait or completed primary/review semantic gate. It creates
no Paper Card, Evidence, Review Memory, scientific review queue, Agent Task or staging
record. P3-D1 owns the secure localhost backend and operation coordinator; P3-D2 owns the
browser UI; P3-D3 owns integrated browser acceptance.

## P4-A External Agent Task And Semantic Preview Kernel

Application Service interface `1.2` adds a versioned, deny-by-default Agent Task boundary
without embedding an Agent runtime. The App selects an external executor, explicitly
approves content classes, exports a portable prompt manifest and imports one bounded JSON
result. Core does not locate executables, manage credentials, spawn a process or call a
model API.

`document_route_resolution` is the only available task kind. Every Task binds the exact
paper, Pipeline Job state, live source digest, Parse output and current Source Adequacy
profile. Prompt preparation issues a CAS lease; a changed input basis rejects late
submission. Submitted output remains untrusted non-canonical staging until the App shows
an escaped preview and the user revises, rejects or approves it. Approval advances only
the deterministic primary/review gate and creates no scientific record.

Task states are append-only and transactionally correlated with process events. Revision
creates a successor Task with reciprocal lineage, prior result digest, user feedback and
fresh inputs. Privacy classes are explicit sets rather than an implied hierarchy; workspace
policy, task-kind policy, user approval and executor capability must all admit every
required class. Guardian validates state chains, event ownership and route-approval
receipts.

## P4-B Primary Semantic Bundle

Application Service interface `1.3` adds `primary_semantic_processing` without changing
the external-Agent boundary. An explicit user request creates an independent semantic
Pipeline Job from a completed `primary_semantic_gate`; the terminal deterministic intake
Job is never reopened and does not authorize scientific writes.

The Task basis binds the paper record and live source digests, one Parse run and output
digest, five operation-specific Source Adequacy Profiles, the semantic Job state and the
current Primary bundle head. Handoff payloads contain only approved metadata, parsed
excerpts and operational context. The Agent returns task-local aliases. Core validates
the seven ordered sections, exact quote/page/locator trace-back and the capability needed
by each requested Evidence operation before it writes non-canonical staging.

User approval allocates canonical IDs and promotes one complete file:

```text
knowledge/primary_bundles/by_paper/<paper_id>.primary.json
```

Each file contains an immutable revision chain and one active revision. That active
revision projects one Paper Card, zero or more Evidence records and zero or more
scientific review-queue boundaries into existing logical reads. Historical revisions
remain canonical audit history and reference targets but do not enter Catalog or factual
downstream reads. A correction therefore leaves old Question Mapping and Research
Synthesis references structurally resolvable while their active-projection freshness
becomes stale.
Legacy per-kind Primary records remain readable, but a paper cannot use legacy and P4-B
authority at the same time.

Source, Parse, Profile and bundle-head identity are checked again before replacement.
The file replacement, semantic Job completion and Task approval use idempotent receipts:
a retry after bundle replacement completes only missing Job or Task state. Corrections
append a successor revision; stale or deliberately refreshed Tasks are superseded by a
new Task with reciprocal lineage. No path edits an approved scientific revision in place.

## P4-C Review Semantic Bundle

Application Service interface `1.4` adds `review_semantic_processing` under the versioned
`p4c-v1` Agent Task registry. Only a completed `review_semantic_gate` or
`review_semantic_gate_mixed_document` may create the independent Review semantic Job.
The Task binds the same source, Parse, five use-specific Source Adequacy, Job and bundle
head identities as the Primary route, but consumes `basic_review_memory` for its base
gate and note-specific capabilities only for retained source notes.

User approval promotes one complete file:

```text
knowledge/review_bundles/by_paper/<paper_id>.review-bundle.json
```

Every revision contains a newly allocated Review Memory and newly allocated Review Unit
IDs, the exact source/Parse/Profile input snapshot, one provenance binding for every
retained source note and the user-approved Task receipt. Only the active Memory enters
Review Context and Catalog. Historical Memory and Unit IDs remain audit-resolvable.
Legacy Review Memory and P4-C bundle authority cannot coexist for one paper.

Review content does not become factual through approval. Every retained Unit remains
`background_only=true`, `can_enter_canonical_evidence=false` and `not_fact=true`.
A blocked figure, formula or supplementary source note returns the semantic Job to its
specific source/reparse wait and creates no Memory, Unit, Evidence or scientific review
queue row. A zero-Unit low-value or redundant Memory is valid when its reason and
coverage limits are explicit. Crash replay completes missing Job/Task receipts without
creating a duplicate revision; corrections append and never edit historical revisions.

## P4-D0 App Handoff Inspection And Recovery

Application Service interface `1.5` adds a zero-write `inspect_handoff` read before prompt
creation. It returns the exact already privacy-filtered payload, effective content classes,
result contract and byte facts, but no prompt or lease. The App can therefore show what
will leave the workspace before the user creates an external Codex/Claude handoff.

`prepare_handoff` also accepts the exact current leased state as an idempotent recovery
request. Core rebuilds the manifest from current inputs, rechecks source/Parse/Profile and
bundle-head freshness, and requires the stored handoff digest to match. The original
predecessor-state replay remains valid for a lost first response. Neither recovery form
creates another lease or persistent write.

Application Service interface `1.6` makes the manual external handoff self-contained by
adding a fully resolved `result_contract_schema` to the handoff manifest. It contains
only schema fragments reachable from the declared result contract, carries no source
path or lease and remains inside the existing prompt-byte budget. This preserves Core as
the schema authority while allowing Codex CLI or Claude Code CLI to return valid JSON
without copying scientific contracts into the Portable Skill.

## P5-A Reading Context Application Service

Application Service interface `1.7` adds a read-only `ReadingApplicationService` behind
the same opaque `WorkspaceSession`. `show_paper` returns the whole seven-section Paper
Card or Review Memory, current source/Parse/Source Adequacy states and optional Question
links. `compare_papers` only batches two to four ordered paper reading inputs; it performs
no semantic comparison. `trace_evidence` locates an Evidence ID in the exact Primary
revision that owns it and returns page, locator, quote, revision state and safe source/Parse
availability.

The reading surface deliberately separates semantic readability from current trace-back.
A committed Card or Review Memory remains visible when source bytes are missing, changed
or relink-required, while `trace_back_available` becomes false. A same-digest relink can
remain current. A superseded Evidence ID stays bound to its historical revision and parse
run; the materialized active Parse is reported separately and never substituted.

The service is JSON-safe and zero-write. It exposes no local path, portable source ref,
source fingerprint, raw parsed page body, Agent lease or mutation authority. Review Memory
is always background-only. Only `grounded` and `revised` Primary Card Units are projected
as eligible factual inputs; interpretation, background and unresolved Units remain visible
but excluded.

## P5-B Evidence Source Access

Application Service interface `1.8` adds a backend-only, non-persistent Evidence source
handle. Core resolves the exact Evidence owner and immutable Primary revision, binds the
workspace identity, expected source fingerprint, source ref, PDF page and locator, and
returns only a redacted descriptor for App use. Historical Evidence may use an available
exact historical manifestation; an active head never substitutes different bytes.

Every source open reloads canonical records, rechecks Evidence/revision/source lineage,
validates the live source ref, opens a regular file, enforces the `512 MiB` budget, hashes
the opened descriptor, requires the expected SHA-256 and PDF header signature, and rewinds
that same descriptor. The backend path is available only on the opened Core value for an
explicit trusted local-reader adapter. No handle, path, source ref or fingerprint is
durable or browser-authoritative.

## P5-C Knowledge Query Agent Tasks

Application Service interface `1.9` adds one `knowledge_query_report` Task kind under the
additive `p5c-v1` registry. It supports single-paper explanation, seven-section overview,
methods, selected-paper comparison, trend/problem discussion and Evidence finding through
one state machine and one `p5c-knowledge-query-report@1.0` result contract.

Knowledge Query Tasks are created from one to four ordered Library paper IDs and are not
owned by a Pipeline Job. Core projects Registry identity first: archived, tombstoned or
aliased-away records cannot enter factual support. It then binds exact paper, active
Primary/Review revision, Card, Evidence, Review Memory, Question Mapping and live source
digests. Only grounded/revised active Card Units whose complete Evidence closure remains
canonical and traceable enter the factual allowlist. Review Units may enter only an
explicit `background_only` allowlist. Stale or unavailable records expose reason-only
descriptors without their scientific content.

The handoff contains no source ref, path, fingerprint, parsed page body, PDF bytes, lease
or writer authority. Submitted factual and cross-paper blocks must close over exact
`paper_id + card_unit_id + evidence_ids` entries; background blocks must close over exact
Review Unit entries. Unresolved and zero-match reports are valid. A changed input basis
rejects inspection, lease, submission, revision and acceptance rather than remapping to a
new active revision.

User acceptance appends only the terminal Agent Task receipt with
`retention_class=current_task_report`, `persistence_status=report_only` and
`canonical_scientific_write=false`. It has no applied Job state and does not write Paper
Card, Evidence, Review Memory, Question Mapping or Research Synthesis. Archive, compaction
and closed-task payload cleanup remain P11 lifecycle work.

## P6 Discovery Application Service

Application Service interface `1.10` adds `DiscoveryApplicationService` as a thin,
session-bound product facade over the existing Europe PMC discovery contracts. It owns
no schema, connector discovery or second persistence implementation. Production
registration remains closed to `EuropePmcConnector`, `EuropePmcResolver` and
`EuropePmcPdfTransport`; tests may inject protocol-conforming fakes.

The facade preserves three separate authority transitions:

```text
search -> transient report, zero workspace writes
explicit user selection -> metadata-only discovery candidate
explicit user acquisition -> create-only inbox PDF plus receipt, then stop
```

Candidate listing adds stable candidate-ID cursor pagination for the App without changing
the legacy CLI list contract. `inspect_acquired` is a read-only handoff into the already
defined intake contract; it does not register, parse or create a Pipeline Job. Discovery
candidates remain excluded from Catalog projection, so selection and acquisition do not
invalidate or rebuild Catalog.

## P7-A Research Organization Kernel

Application Service interface `1.11` adds generic Direction, Field Map Entry and Question
revision stores plus session-bound bounded reads. Each target has one stable ID and an
append-only revision chain. Core owns target, revision and link identities; corrections
append and never rewrite accepted history.

Organization links resolve only active semantic Units. Factual Primary links require a
current grounded or revised Unit and derive their canonical Evidence closure inside Core.
Primary interpretation and Review Units may be linked only as explicit background;
Review links remain `background_only=true`, `can_enter_canonical_evidence=false` and
`not_fact=true`. Field Map Direction references bind exact Direction revisions, and all
links project current or stale-upstream state without rewriting canonical records.

The P7-A writer accepts only explicit user-authored or user-approved payloads. It does not
run an Agent, infer Tags, create Screening decisions or generate Research Synthesis.

## P7-B Organization Proposal Agent Tasks

Application Service interface `1.12` and additive privacy registry `p7b-v1` add one direct
`organization_proposal` Task kind. It has no Pipeline Job owner. One Task binds exactly one
new or existing Direction, Field Map Entry or Question, one ordered selection of up to 25
papers and an optional background-only Review context choice.

Core constructs the handoff from active Primary/Review semantic revisions, current
admissible Units, derived Evidence closure and bounded organization context. Existing
Field Map Direction references are prioritized before context truncation. The Agent sees
no paths, source refs, parsed pages, leases or writer authority and cannot allocate
canonical IDs.

Submission and approval both require the exact Task basis to remain current. Returned
source links must close over the handoff allowlist; target-specific link roles are schema
closed. Unresolved conflicts can be previewed but block approval. Explicit user approval
calls exactly one P7-A writer. A semantic duplicate writes no organization revision but
does retain an append-only Task receipt; replay resolves the basis-bound historical
revision. A canonical write completed before its Task receipt is recovered by exact Task
and result digests without creating another revision.

## P7-C Deterministic Tags

Application Service interface `1.13` and layout `p7c-1` add a local user-owned Tag
vocabulary. A Tag has a stable Core ID and append-only definition revisions. Rename keeps
the previous name as an alias, archive retains history and blocks new assignments, and
duplicate normalized names or aliases fail closed. Tags are not scientific claims and do
not carry Paper Card Units, Review Units or Evidence.

Assignments have independent stable IDs and append-only `assigned` / `removed` revisions.
One assignment binds a Tag to one Paper, Direction, Field Map Entry or Question identity.
Target successor revisions do not rewrite the assignment; unavailable or non-canonical
targets are projected and reported without deleting history. Existing Review Memory
`scope_tags` remain document metadata and are never silently promoted into this vocabulary.

Tag definition and assignment mutations are deterministic explicit-user App operations,
not Agent Tasks. Canonical bundles remain the authority. SQLite stores only rebuildable Tag
search documents and target facets; Tag/link changes participate in the Catalog watermark
and incremental refresh. Cross-file vocabulary and assignment uniqueness are rechecked
inside the workspace transaction lock. P7-C does not add hierarchy, inferred synonyms, automatic merge,
automatic tagging, Screening or Research Synthesis.

## P7-D1 Deterministic Question Screening

Application Service interface `1.14` and layout `p7d-1` add optional Question-specific
screening without changing Library inclusion or ordinary Paper processing. One active
criteria bundle may govern a Question. Criteria and criterion identities are Core-owned;
criteria revisions are append-only, user-approved and may be archived without deleting
history.

One stable decision owns each Question-Paper pair. Every final decision is user-only,
binds the exact current criteria revision and digest, and records a disposition for every
criterion. A criteria successor leaves the older decision intact but projects it as
`stale_criteria`. Registry corrections and unavailable Questions or Papers likewise affect
freshness without rewriting accepted revisions.

When no active criteria exist, Question Mapping and Research Organization preserve their
existing behavior. When active criteria exist, new factual links require a current
`included` decision for every linked Paper; excluded, missing or stale decisions fail
closed. Screening remains organization metadata, never Evidence or a scientific
credibility judgment. Catalog stores only rebuildable criteria/decision search documents
and status labels, while Guardian checks identity, revision, reference, digest, freshness
and transaction closure. Agent proposals and the localhost App work surface remain P7-D2.

## P7-D2 Question Screening Proposals

Application Service interface `1.15` and Agent privacy registry `p7d-v1` add two direct,
no-Pipeline-Job proposal Tasks: `question_screening_criteria_proposal` and
`question_screening_decision_proposal`. Core builds bounded Question, criteria, Paper
metadata and optional current Paper Card context; the App exports a portable handoff for an
external Codex CLI or Claude Code CLI and never launches an Agent.

Criterion identities are represented to the Agent only by deterministic task-local aliases.
Criteria candidates may retain an alias, omit it or add identity-free text. Decision
candidates must close over every supplied alias exactly once and may return `uncertain`, but
`uncertain` cannot be promoted. Canonical criteria and included/excluded decisions remain
explicit-user authority. Approved Agent results use
`origin: user_approved_agent_proposal`; they are never relabeled `user_authored`.

Question, Paper, criteria, prior decision and optional Paper Card revisions are exact Task
basis. Any successor makes lease, submission or approval stale. Canonical screening writes
reuse the P7-D1 writer, preserve Core-owned IDs, recover a write-before-Task-receipt crash by
Task/result digest, and retain a no-change approval receipt without inventing a new canonical
revision. Guardian checks both Task-to-revision and revision-to-Task closure.
