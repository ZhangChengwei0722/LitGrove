# Architecture

## Layers

```text
Shared Core + CLI
-> Portable Agent Skill
-> Separate private workspaces
```

Core owns deterministic contracts, validation, path and ID handling, structured I/O, status gates, logs, Guardian checks, real-PDF page extraction, Step 7 candidate persistence, and bounded stdout read surfaces. The Agent layer owns scientific reading, interpretation, candidate generation, and workflow decisions. Private workspaces own papers and research records. Persisted Markdown and additional derived views remain deferred.

## Knowledge Flow

```text
Source Intake -> Registry -> Parse
-> Primary route: Paper Card Core -> Evidence Grounding -> Question Mapping
-> Review route: background-only Review Memory
-> Ephemeral query route: Paper Card Units -> optional Evidence trace-back -> task report
-> Candidate thinking route: mapped grounded Card Units -> Step 7 structured candidates
-> Guardian / Feedback
```

Canonical evidence is the provenance backbone. Paper Card Units are the semantic entry for later reasoning. Step 7 remains candidate-level and must expand back to canonical evidence.

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

New workspaces initialize at layout `m3b-1`. Exact `m3a-2a` predecessors are runtime-blocked with `RKBC-027` and can be upgraded only through `workspace init`. The current upgrade creates an empty `step7/` directory and replaces operational marker metadata; it rewrites no canonical record and creates no empty JSONL store, process event or journal.

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

`pdfplumber` is an optional, lazily imported dependency. The CLI never discovers adapters and never falls back from an explicit `pdfplumber` request. Real Evidence uses zero-based, end-exclusive character locators over stored page text; invented synthetic fixtures retain block locators only under whitespace-normalized same-page containment.

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

`skills/research-kb/` is the reviewed Agent-layer source. Its concise `SKILL.md` routes detailed command, primary intake, review intake, query/Step 7, authority and reporting rules to six one-level reference files. It contains no scripts, duplicate schemas, persistent state or scientific fixtures.

The Skill processes sources sequentially, uses `intake inspect` before registration, reads current state through `paper status` and `paper context`, reads scientific text through `parse show`, and submits candidates through existing CLI mutation authority. It classifies document type only in task memory and stops non-primary documents before Paper Card or Evidence promotion.

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
