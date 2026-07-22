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

Milestone 1B implements this deterministic storage lifecycle. M3A-0A adds explicit real-PDF page extraction and strict Evidence provenance validation without generating scientific content.

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
22. A Step 7 `record promote` request selects grounded/revised Card Units already admitted by one current Question Mapping. Core derives exact Evidence and Unit-boundary closure before atomic promotion.
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

M2B-2 renders exactly one stdout-only Question Reading View. M3B-1 adds a separate stdout-only Step 7 Reading View grouped by candidate type. Both are built from validated structured records, label queue references as non-evidence, and create no Markdown file or view store.

Evidence matrices, relations, gap maps, contradictions, persisted Question Layer Markdown, persisted Step 7 Markdown and additional derived views remain outside the implemented runtime.

## Skill-Facing Read And Handoff Boundary

The transient JSON read interface is version `1.0`. Core reports capability and current state; the Portable Skill decides the procedural resume step and the Agent remains responsible for scientific interpretation. No read command persists a status snapshot or workflow run.

`intake inspect` and `manuscript inspect` are the only absolute-path-facing reads. `intake inspect-acquired` is the candidate-ID bridge for a separately authorized acquired-source task. The Skill must inspect before `registry add`, reuse the sole returned paper ID for `registered_current`, register only `unregistered`, and stop on `registered_stale` or `ambiguous`. It must use returned source and domain values rather than parsing workspace, profile or Registry files. Manuscript projection is independent of Registry and stops after its stdout report; only the separate Agent-owned `manuscript_audit` route may consume that report for current-task reasoning. This is sequential routing, not an atomic concurrency guarantee.

`paper context` is the primary-route read that returns stored Card, Evidence and queue scientific content together. `review context` is the separate review-route read and returns a complete background-only Review Memory. Both are bounded to an explicit paper, omit paths and unrelated records, and exist to recover Core-owned IDs without direct store access.

Stdin accepts JSON only for discovery search/selection requests, Registry metadata and mutation requests. Empty, invalid, non-object or oversized input fails before service dispatch. Discovery file input is JSON-only; canonical file requests retain existing JSON/YAML support. YAML is never accepted through stdin.

## Portable Skill Workflow

The repo-owned `research-kb` Skill routes workspace-independent on-demand discovery, optional explicit candidate handoff, separately requested OA acquisition, local or already-acquired PDF intake, exact-path manuscript projection/audit, or an existing-workspace knowledge query. Discovery resolves explicit dates and field-bound keywords and reports 0-15 metadata results with zero writes. Candidate selection and acquisition each require separate exact user authority. Acquisition stops after reporting its portable source reference. A later explicit acquired-candidate intake resolves Registry state and, unless `registry_only` was requested, resumes the same explicit-`pdfplumber`, mutually exclusive primary/review route as local-path intake.

`manuscript_projection` returns stable units only and stops. `manuscript_audit` separately requires one or more criteria and exact current-task knowledge selectors before inspection. It preserves criterion wording, resolves no adjacent corpus, expands exact factual support to canonical Evidence and returns a scope-limited private report with `persistent_writes: 0`. No Core semantic service, claim-map store or rewrite is involved.

The Skill maintains no checkpoint. Reruns recover state through `intake inspect` or `intake inspect-acquired`, then `paper status` and `paper context`; exact existing records are reused, while stale state, ambiguous sources and uncertain near-duplicates stop. Paper-local unsupported-PDF or document-type failures may be isolated, but workspace/transaction integrity failures stop the batch.

Document classification and the final report remain local to the active task. Supported high-confidence reviews use the common Review Memory route; ambiguous, mixed and unsupported types stop before mutation. Knowledge queries start from grounded/revised Paper Card Units and expand to canonical Evidence for trace-back. Discovery search and ordinary queries write nothing; explicit selection writes metadata candidates and explicit OA acquisition may add a source receipt. Explicit Step 7 maintenance and an explicitly complete intake workflow may use the deterministic Core runtime. Subtype-specific review schemas, Field Map integration, Review Unit Question Mapping, institutional/browser acquisition, OCR, migration and workspace-config generation are not implemented.

## Step 7 Candidate Flow

```text
current Question Mapping
-> selected grounded/revised Card Units
-> Agent semantic candidate
-> Core-derived Evidence and Unit-boundary closure
-> append/replace in one Step 7 JSONL store
-> context / stdout render / Guardian
```

Step 7 cannot create a question. The request uses `paper_id: null` and `question_origin: existing_question`; callers must not submit candidate IDs, evidence/queue closure, snapshots or status constants. Synthesis spans at least two papers. Cross-View sources are same-question, current and admissible. Upstream drift leaves a valid candidate readable as `stale_upstream`; structural corruption still blocks the bundle.

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

Ordinary reruns reuse a current memory without writing. A stale parse requires rereading before explicit refresh. A low-value review may persist zero Units with a reason. Review-derived content remains non-evidence and receives no Field Map, Question Mapping or Step 7 identity in M3A-2A.
