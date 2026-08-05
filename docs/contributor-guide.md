# Contributor Guide

## Change Process

1. Read the root `CONTRIBUTING.md`, then start from an issue or approved implementation plan.
2. Keep one bounded behavior or contract change per branch.
3. Add or update deterministic tests.
4. Run the full local suite and privacy scan.
5. State compatibility, tested platform, fixture scope, and known limits in the review description.

Use the repository virtual environment so the editable package and bounded dependencies are active:

```powershell
.\.venv\Scripts\python -m pip install -e ".[test,pdf]"
.\.venv\Scripts\python tools/run_validation.py --level L2 --receipt .validation/l2.json
.\.venv\Scripts\python -m research_kb privacy scan --root .
```

Release-resource smoke after `python -m build`:

```powershell
.\.venv\Scripts\python tests/wheel_smoke.py
.\.venv\Scripts\python tests/wheel_pdf_smoke.py
```

## Continuous Integration

Every pull request and push to `main` runs two platform gates plus dependency security:

- `Windows validation` aggregates exhaustive L3 shards, separate L4 scale validation,
  collection reconciliation, source compilation, distribution build, installed-wheel
  smoke tests, CLI smoke and privacy scan on Python 3.12. This is the required live
  acceptance platform.
- `Linux validation` uses Python 3.11 for the full test suite, CLI smoke and privacy scan so
  host-independent POSIX behavior is exercised.

GitHub Actions are pinned to immutable commit SHAs. Dependabot proposes weekly Python and
Actions updates; review and merge those updates through the same validation gates.

Dependency changes must also pass `Dependency review` and `Python dependency audit`.
The audit uploads a CycloneDX JSON SBOM for inspection. A dependency alert must be fixed,
explicitly dismissed with a repository-visible rationale, or kept blocked; do not weaken
the audit command or hide a vulnerable dependency to make a check pass.

Package versions follow `docs/release-policy.md`. Record user-visible changes, deprecations,
security fixes, and compatibility changes in `CHANGELOG.md` under `Unreleased`.

## Validation Levels

Use the repository-owned runner and retain its JSON receipt:

```powershell
# Documentation-only change
.\.venv\Scripts\python tools/run_validation.py --level L0 --receipt .validation/l0.json

# One bounded behavior
.\.venv\Scripts\python tools/run_validation.py --level L1 --selector tests/unit/test_example.py --receipt .validation/l1.json

# Normal feature feedback
.\.venv\Scripts\python tools/run_validation.py --level L2 --receipt .validation/l2.json

# Complete high-risk acceptance and scale
.\.venv\Scripts\python tools/run_validation.py --verify --collect-nodeids --receipt .validation/manifest.json
.\.venv\Scripts\python tools/run_validation.py --level L3 --shard all --receipt .validation/l3.json
.\.venv\Scripts\python tools/run_validation.py --level L4 --shard scale --receipt .validation/l4.json
```

See `docs/test-validation.md` for marker ownership, shard coverage, serial/slow inventory,
receipts, and performance budgets. Targeted or L2 validation does not replace L3/L4 for a
schema, authority, storage, transaction, recovery, merge, release, privacy-boundary, or
source-write change.

Schema, state, ID, path, and directory-protocol changes require explicit user approval, focused self-review, targeted tests, and the full Windows validation gate. External collaborator review is optional.

## Fixture Rules

- Author all names, text, measurements, identifiers, and relationships from scratch.
- Add `fixture_origin: synthetic_from_scratch` where the schema permits it.
- Never derive fixtures by editing private research records.
- Intentional privacy failures must be exact-file allowlisted with an expected code and count.
- Runtime fixtures must execute in a temporary copy; tests must not write canonical state into the repository fixture tree.
- Runtime fixtures must run `WorkspaceBootstrapService` before `WorkspaceLayout.load`; contract-only fixtures remain read-only and markerless.
- Record source hashes before a runtime scenario and assert that every hash is unchanged afterward.
- Generate PDF fixtures at runtime under `tmp_path` with ReportLab. Do not commit generated PDFs or derive them from real papers.
- Test blank, malformed, encrypted, all-empty, and multi-page sources with bounded diagnostics that contain no extracted text or absolute path.
- Keep the base-wheel smoke free of the PDF extra and require `RKBC-028` for explicit PDF selection; validate real PDF parsing in the separate `[pdf]` wheel smoke.
- Build DOCX fixtures from scratch as minimal OOXML archives; never commit or derive a real manuscript fixture.

## Mutation Service Rules

- Resolve all targets through `WorkspaceLayout`; do not accept a direct `knowledge_root` override.
- Read current canonical state before composing a replacement and pass its digest to the transaction manager.
- Validate the temporary target before `os.replace`.
- Keep every existing source asset outside the writable boundary. The only exception is the approved create-only `local_inbox` acquisition contract.
- Emit no process event payload containing candidate scientific text.
- Add a failure, conflict, and source-immutability test for each new mutating service.
- For Evidence, test exact character-slice resolution, synthetic block containment, same-paper ownership, active parser/run consistency, and preservation of previous target bytes on pre-replacement failure.
- For Review Memory, test CLI-owned Memory/Unit IDs, source and parse stability, exact quote-excerpt slices, paraphrase null locators, route exclusion, zero-Unit low-value records, stable replace IDs, stale warnings and previous-target preservation.

## Compatibility Adapter Rules

- Keep private and domain-specific adapters outside Shared Core.
- Inject adapters explicitly in process; do not add import strings, plugin discovery, entry-point loading, or an installed default registry.
- Return bounded metadata, structured source references, digests, and public diagnostics only. Never return raw legacy payloads or absolute paths.
- Declare every protected input and test byte-identical source and knowledge state before and after a successful inspection.
- Treat compatibility output as a transient read-only report. Do not create report files, process events, journals, canonical records, or migration IDs.
- Use synthetic-from-scratch fixtures for every shared test, including adapter errors and protected-input mutation detection.

## Question Mapping Rules

- Persist mappings only for `user_supplied`, `user_approved_candidate`, or `existing_question` request contexts.
- Treat selected Paper Card Units as semantic inputs; never accept caller-supplied `evidence_ids` or `question_link_id` values.
- Derive evidence exactly from selected units and preserve every selected-unit review queue boundary.
- Keep one paper link per question, preserve existing link IDs on replace, and do not implement deletion without a later lifecycle contract.
- Add cross-paper, duplicate, stale-upstream, write-conflict, deterministic-ordering, and read-only retrieval tests.
- Keep mappings in structured JSONL. Do not add a hand-maintained Markdown mirror.

## Question Reading View Rules

- Render only records reachable through the selected validated Question Mapping; do not scan for extra same-paper evidence.
- Keep review queue boundaries in their own explicit non-evidence section and out of evidence counts.
- Build complete UTF-8/LF bytes before stdout begins. Deterministic validation and rendering failures must leave stdout empty.
- Never include wall-clock time, hostname, cwd, absolute paths, `source_ref`, or source filenames in a view.
- Treat synthetic golden Markdown as reviewed output: author its source records from scratch, compare bytes exactly, and update it only for an approved contract change.
- Rendering is read-only. Do not create a view file, cache, event, journal, report, lock, or layout directory.

## Research Synthesis Runtime Rules

- Admit only grounded/revised Card Units already selected by one current Question Mapping.
- Treat candidate semantics as Agent-owned and IDs, type, Evidence/boundary closure, snapshots, timestamps and candidate-only status constants as Core-owned.
- Keep review queue IDs out of `evidence_base`; Review Memory and Review Unit IDs cannot support canonical Evidence or the factual synthesis base. Current linked Review Units may appear only in the labeled Review-background closure.
- Distinguish explained upstream staleness from structural corruption. Guardian may warn with `RKBC-014`, but it must never refresh a candidate.
- Revalidate relevant upstream records inside the transaction lock and keep candidate text out of journals and process events.
- Test all four types, Cross-View same-question/current-source gates, stale readability, unrelated mutation, pre-replacement failure and exact golden Markdown for two synthetic domains.

## Research Synthesis Skill Orchestration Rules

- Classify persistence intent before mutation. Ordinary explanations, comparisons, trace-back and research discussions must remain read-only task reports.
- Keep query/maintenance workspace preflight dry-run-only. Treat `already_present` plus planned `acquire_workspace_lock` as no-change; do not confuse dry-run `result: planned` with a need for operational init.
- Start semantic reasoning from grounded/revised Paper Card Units and expand to canonical Evidence for provenance or promotion.
- Call `step7 context` before every maintenance decision and use `record promote`; never read or write internal Research Synthesis JSONL directly.
- Exact semantic reruns write nothing. Replace the same candidate, append only a materially distinct one and stop on uncertain near-duplicates.
- Keep Review Memory out of factual support. Review Units may persist only through the current labeled Review-background closure and never enter `evidence_base`.
- Forward-test query ephemerality by snapshotting the complete managed tree and test explicit persistence through Core plus Guardian.

## Deterministic Read And Stdin Rules

- Build each transient JSON read document completely before one UTF-8/LF stdout write.
- Repeat unchanged reads and compare exact bytes; snapshot source and managed trees before and after.
- Keep capability probing workspace-independent and distinguish implemented capability from optional dependency availability.
- Keep paper status free of source paths, statements, claims, quotes, question text, rationales and semantic next actions.
- Recheck source SHA-256 around parsed-page projection and return only the selected paper's stored records.
- Bound paper context to one selected paper, sort Evidence/queue records by canonical ID, omit source references and recheck source SHA-256 before and after projection.
- Keep review context separate from paper context; return the complete selected memory, bounded freshness and transient exact DOI matches without paths or writes.
- Snapshot the complete managed tree around paper context reads and test registered-only, partial-run, complete and cross-paper isolation states.
- Require an absolute path for intake inspection, resolve it to exactly one declared root, round-trip its portable source reference, and never expose its absolute path or hash.
- Match intake registration only by exact `root_id + relative_path`; test unregistered, current, stale, ambiguous and same-bytes-at-another-path states.
- Hash intake sources before and after projection, keep failure stdout empty, and prove source plus managed trees remain byte-identical.
- Read stdin as raw bytes with a command-specific limit plus one byte; never echo invalid payloads.
- Accept stdin only for discovery search/selection requests, Registry metadata and mutation requests, and route successful objects through existing services.
- Exercise base and `[pdf]` installed wheels so intake inspection, availability, stdin, parse reads, status and paper context do not depend on the editable tree.

## Manuscript Projection Rules

- Accept only an exact absolute `.docx` or `.pdf` path under exactly one configured source root and recheck SHA-256 after extraction.
- Keep the report transient and stdout-only. Do not create a manuscript store, schema, ID, event, journal, cache, Markdown file or Registry record.
- Bound source bytes, OOXML entries/uncompressed bytes, projected units and extracted characters before success output.
- Read only WordprocessingML document/styles parts; do not follow relationships, execute macros or include deleted revisions, drawings, text boxes or embedded content.
- Reuse the fixed `pdfplumber` text policy and preserve `RKBC-028`; map unsupported manuscript content to `RKBC-035` without exposing paths or text.
- Test exact repeatability, body/table paragraph order, style/heading projection, PDF page order, malformed and empty inputs, source change, empty failure stdout and byte-identical source/managed trees.
- Keep semantic claim extraction, criteria evaluation, evidence matching and rewriting outside M3D-0A.

## Manuscript Audit Skill Rules

- Keep M3D-1 in the Portable Skill and Agent protocol; do not add a Core audit command, semantic service, provider call or persisted contract.
- Require at least one explicit criterion and exact current-task question/paper selectors before `manuscript inspect`; never infer default audit dimensions or a broad corpus from topic similarity.
- Preserve original criterion wording and report selector resolution basis, projection coverage limits and checked-corpus limits.
- Use exact zero-based/end-exclusive unit slices only when reproduced byte-for-text; otherwise degrade to a stable unit locator with null offsets and text.
- Start from grounded/revised Card Units and expand to canonical Evidence for exact factual support. Review Memory, review queue and Research Synthesis remain background or boundaries, never support.
- Keep reports transient with `persistent_writes: 0`; do not persist claim maps, findings, caches, events, journals, reports, Markdown or manuscript edits.
- Contract-test these boundaries with invented text only. Static Skill tests verify the published route contract; they do not claim to verify scientific judgment.

## Discovery Connector Rules

- Register public connectors explicitly in Core; do not add arbitrary URL input, plugin discovery, credentials or environment-driven endpoints.
- Keep M3C-1 workspace-independent and persistence-free. A discovery report is external metadata, not Registry, Evidence or candidate state.
- Bound request bytes, date span, keywords, provider pages, raw results, response bytes and timeout.
- Reject redirects and reuse provider cursors only against the fixed endpoint; never fetch a provider-returned next-page or full-text URL.
- Reapply date, field-keyword and preprint filters locally. Exact DOI may deduplicate; title similarity may only mark a candidate pair.
- Use structured HTML parsing for provider markup and validate every provider field before output.
- Treat later-page failure as whole-request failure and leave stdout empty.
- Write fake transport tests from scratch. Automated tests must not call a live provider or contain copied real metadata.
- State the reproducibility boundary: identical request and provider payloads normalize identically, while live provider state may change.

## Discovery Candidate Rules

- Require the complete validated M3C-1 report and exact `actor: user`; never infer selection from rank, relevance, paper type or full-text status.
- Persist only selected result keys. Keep the report and unselected results out of the workspace.
- Use `discovery_<uuid4>` record IDs and deterministic selection-context hashes; preserve candidate identity across new query or Question Mapping contexts.
- Treat same-key metadata drift as `RKBC-034` and roll back the complete batch. Do not refresh silently.
- Selection fixes `user_selected`, `metadata_only`, `not_started`, `not_evidence: true` and `passed_auto_checks`; only explicit acquisition may change `not_started` to `acquired` and add its receipt.
- Keep titles, abstracts, queries and report digests out of process events and transaction journals.
- Cover layout upgrade, idempotence, context append, conflict rollback, complete-bundle validation, deterministic list/show, Guardian, stdin and installed-wheel behavior with `synthetic_from_scratch` data.
- Candidate selection never implies acquisition, Registry/intake chaining, deletion or provider refresh.

## Discovery Acquisition Rules

- Require exact `actor: user`, one selected candidate, live eligible resolution and available `pdfplumber` preflight.
- Write only a previously absent `<candidate_id>.pdf` under an existing `local_inbox` owned by exactly one source root.
- Use the fixed Europe PMC PMCID GET, reject redirects and credentials, and enforce status, media type, 64 MiB, signature, SHA-256 and parser bounds.
- Publish with same-directory operation-owned partials and exclusive creation; never overwrite or adopt an unreceipted target.
- Ordinary cleanup may unlink only the current operation's still-matching file identity. Guardian reports crash residues and receipt mismatch without deleting them.
- Keep receipt persistence and source publication separate from Registry, Parse and scientific records.

## Acquired Candidate Intake Handoff Rules

- Keep `intake inspect-acquired` read-only and network-free; acquisition-only tasks still stop before intake.
- Require one stored `acquired` candidate and verify its deterministic receipt target, regular-file type, size, SHA-256 and PDF signature before and after inspection.
- Project Registry bibliography from stored candidate metadata without creating a second Registry mutation path.
- Use the existing `registry add` command for an explicitly requested intake; exact reruns recover through `registered_current`.
- Keep the M3C-2C bridge itself stopped after Registry; it does not own Parse or scientific records.

## Acquired Candidate Workflow Continuation Rules

- Keep `discovery acquire` stopped before intake. Only a later explicit acquired-candidate task may continue.
- Reuse the returned paper ID with existing status, Parse, primary/review and Guardian services; do not add a workflow runner or second mutation path.
- Allow an explicit `registry_only` depth, otherwise continue ordinary knowledge-base intake through Guardian.
- Treat provider paper type as metadata and classify from parsed document content before scientific promotion.
- Keep Research Synthesis separately explicit and scoped to an existing or approved question. Factual support remains primary-only; current linked Review Units may be labeled background only.

## Pipeline Job Rules

- Treat Pipeline Jobs as append-only operational coordination, never as scientific truth or a generic unresolved queue.
- Capture route, depth and authority at creation; transitions may consume but never enlarge that authority.
- Bind every successor to the current state ID and digest, and correlate each committed state with exactly one success event.
- Keep list/show output bounded, deterministic and free of source paths, fingerprints, paper text and task payloads.
- Represent waiting, cancellation and recovery explicitly; never restart routing or guess through a stale head.

## Source Asset And Identity Rules

- Preserve legacy Registry source behavior when no explicit main Source Asset exists; do not bulk backfill old workspaces.
- Append Source Asset revisions and identity-correction events. Never rewrite a prior digest, paper ID, Registry row or historical reference.
- Require a current Pipeline Job with the exact operation authority for every source mutation; require exact `actor: user` for copy and identity correction.
- Reject path escape, symlink/junction/reparse traversal, detectable hard-link ambiguity and changed observations. Revalidate source bytes inside the canonical transaction.
- Keep inbox scan bounded and transient, revalidate its opaque handle at selection, and never expose an absolute path or raw source digest in browser-facing or Catalog output.
- Treat revision one as the Source Asset's owning Job. Require exact authority, current CAS, an existing Registry paper and current available bytes before appending an association.
- Require every known-paper `main_pdf` to match the Registry fingerprint. Never let a later source operation silently replace paper identity.
- Exclude all registered refs from public inbox scan. Permit a registered candidate only for an exact same-Job selection replay; another Job cannot create a competing intake receipt. A later Job with exact association authority may append `paper_associated` without changing revision-one ownership.
- Keep transition reasons closed: only association adds a paper ID, only same-digest relink changes the ref, and observation transitions preserve identity fields.
- Block `completed` and `completed_with_findings` while an owning Job still has an unassociated Source Asset. Preserve receipts on `failed` or `cancelled` and leave them Guardian-visible.
- Delete a failed copy artifact only while its captured file identity and digest still match. Leave uncertain residue for Guardian rather than deleting by filename.
- Keep the Core copy boundary stream-based. A local path belongs only to the CLI adapter; browser/App callers must not submit a server path. Commit the Source Asset receipt before create-only publication so the same Job can resume each interruption point.
- Bound the total inbox entries inspected, not only the number returned. Reject an over-budget inbox without hashing an unbounded directory.
- Keep same-digest relink current. Project changed bytes as `stale_source` and missing, inaccessible or unsafe paths as unavailable without deleting historical Parse or scientific records.
- Keep merge/split/alias/archive/tombstone user-authorized, acyclic and projection-only. A split must identify the earlier merge it supersedes.
- Index only current Source Asset and affected identity projections; do not index historical revisions, raw correction events, source refs or fingerprints.
- Do not add Source Adequacy, Agent Task, semantic route or App mutation behavior in P3-B.

## Source Adequacy And Deterministic Trunk Rules

- Treat Source Adequacy as use-specific operational history, not scientific credibility or a global pass/fail label.
- Bind every Profile to exact source roles/manifestations, one parse run, parser descriptor digest, parse-output digest, requested operation and rule versions.
- Recompute implicit Registry-main and parser-profile identity during bundle validation; never trust stored digests by shape alone.
- Preserve old Profiles after reparse and project them stale. Do not require historical parsed pages to remain in the active parsed-page store.
- Keep hard failures machine-owned. P3 permits explicit user successor decisions only for non-hard uncertainty and rejects Agent assessment writes.
- Require the matching current capability before downstream staging. Block only the requested use and route it to the specific Pipeline Job wait reason.
- Resume `ocr_required`, `layout_parse_required` and `reparse_required` by running the newly selected registered adapter even when active pages already exist.
- Convert only parser-domain or wrapped adapter-execution failures to `parse_failed`; authority, schema, source-race and transaction failures must propagate.
- Keep the trunk restartable and Job-correlated. It may stop at an explicit primary/review semantic boundary but must not create Paper Card, Evidence, Review Memory, scientific review-queue or Agent Task records.

## Deterministic Intake Application Facade Rules

- Accept only an opaque `WorkspaceSession`; App code and callers must not read `session._layout`, workspace config or canonical stores.
- Keep upload and watched-inbox authority registries closed. A request cannot add operations, and watched selection requires both selection and reference-registration authority.
- Bind upload to backend-computed size and SHA-256. Do not accept a browser-submitted server path, filename as authority or unbounded stream.
- Require exactly one mode-matching Source Asset root and one correlated success event containing its asset and state IDs before resuming downstream work.
- Reconcile Registry and Source Asset association from Job-correlated receipts. Never repeat `RegistryService.add` after its receipt exists.
- Permit `running -> running` only when the node advances, outputs strictly grow and wait/retry/recovery fields remain unchanged. Apply the same rule to stored-chain diagnostics.
- Keep deterministic intake nodes monotonic during recovery. Resume may append a missing transition but cannot move a later node back to Registry or association.
- Return only bounded App projections. Never expose source refs, paths, fingerprints, authority snapshots, idempotency keys, raw page text or user-authored operational reason text.
- Stop at waits or semantic gates. Do not create Paper Card, Evidence, Review Memory, scientific review-queue, Agent Task or staging records in P3-D0.
- Exercise the public facade from both base and PDF-extra installed wheels; source-checkout imports alone are insufficient.

## External Agent Task And Staging Rules

- Keep Core free of embedded Agent execution, credentials, executable discovery and model API calls. Export only a portable, explicit-user-action handoff manifest.
- Register every task kind, result contract, content class and executor profile by version. Unknown and deferred kinds fail closed.
- Treat privacy classes as explicit sets. Workspace policy, task-kind allowance, user approval and executor capability must all admit every required class.
- Bind each Task to the exact Job state, paper digest, live source digest, Parse output and current Source Adequacy profile. Reject late results after any basis change.
- Use append-only Task states, CAS-bound leases and transaction-correlated events. Exact replay is zero-write; changed intent conflicts.
- Keep submitted output untrusted and non-canonical. App preview must be escaped and cannot expose source refs, paths, fingerprints, raw authority or private payloads.
- Require explicit user revision, rejection or approval. Revision creates a reciprocal successor lineage with prior result digest, feedback and refreshed inputs.
- P4-A may advance only document routing. It must not create Paper Card, Evidence, Review Memory, scientific review-queue or Research Synthesis records.
- Exercise registry, handoff, submit, preview and approval from the installed wheel; live model availability is never a deterministic CI condition.
- Keep `p5c-v1` additive: existing Pipeline-bound kinds remain available, while `knowledge_query_report` has no Job owner and no scientific commit path.
- Build Knowledge Query payloads only from active Registry identities, current source manifestations, grounded/revised active Card Units and their complete canonical Evidence closure. Review Units are an explicit background-only allowlist.
- Bind the query input basis to exact selected paper, revision, Card, Evidence, Review Memory, routing and source-currentness digests. Reject stale handoff, submission, revision and acceptance; P5-C has no refresh transition.
- Validate every returned support/background ref against the exact handoff payload. A zero-match or unresolved report is valid; excluded context never becomes support.
- Accepting a query result may append only Agent Task operational state with `report_accepted`; it cannot name an applied Job or write a scientific store.
- Keep `p7b-v1` additive and register `organization_proposal` as a direct no-Job Task. One Task targets exactly one Direction, Field Map Entry or Question.
- Preserve the caller's ordered, unique paper selection. Build all Unit text, Evidence closure and organization context inside Core; browser or Agent input may reference only allowlisted IDs.
- Bind existing targets, selected semantic revisions, Card/Review Units, Evidence closure and referenced Directions in the Task basis. Reject stale handoff, submission, revision and approval.
- Keep Review Units background-only. Direction and Field Map links allow factual Primary examples or background context; Question background links require `question_background`; Review content never becomes Evidence.
- Promote through exactly one `ResearchOrganizationService` writer after explicit user approval. Core allocates target, revision and link IDs and derives factual Evidence IDs.
- Treat exact semantic duplicates as no canonical write while still retaining the Task approval receipt. Replay must resolve the basis-bound historical revision even when no Task-authored canonical revision exists.
- Keep unresolved conflict notes previewable but approval-blocking. Organization proposals cannot carry Tags, Screening decisions or Research Synthesis candidates.
- Keep `p7d-v1` additive. Screening criteria and decision proposals are direct no-Job Tasks; do not route them through a semantic Pipeline Job.
- Expose criterion identities to an Agent only through task-local aliases. Criteria revisions may retain existing aliases; decisions must close over every alias exactly once.
- Keep Question screening membership separate from scientific credibility. Do not add Evidence or source-document payload merely to justify a screening proposal.
- Promote screening proposals only through `approve_question_screening_result` after explicit user approval. `uncertain` is previewable but never promotable.
- Preserve `user_approved_agent_proposal` provenance, exact stale-basis rejection, canonical-write recovery and no-change Task receipts.
- Keep P7-C Tags deterministic and user-authored. Do not route create, rename, archive, assign or remove through an Agent Task.
- Allocate stable Tag and assignment IDs inside Core and append successor revisions. Never edit or delete accepted Tag history in place.
- Treat Review Memory `scope_tags` as separate document metadata. Do not import or infer P7-C Tag vocabulary from them.
- Tag only Paper, Direction, Field Map Entry or Question identities in P7-C. Do not attach Tags to Units, Evidence, Review Memory or Research Synthesis.
- Keep Tag facets disposable: every Catalog Tag result must rebuild from canonical Tag and assignment bundles, and every assignment change must affect projection freshness.

## Primary Semantic Bundle Rules

- Start Primary semantic work only from a completed `primary_semantic_gate`; create a separate semantic Job with the exact scientific-write authority.
- Keep `p4a-v1` route behavior compatible. Register Primary processing only under the versioned `p4b-v1` privacy/task registry.
- Bind every Primary Task to exact paper, source, Parse, Source Adequacy, semantic Job and bundle-head digests. Reject stale submission and approval.
- Require `basic_paper_card` for Card construction and the exact operation-specific capability for every Evidence candidate. A blocked use creates no scientific staging or review-queue substitute.
- Accept only task-local aliases from Agents. Core owns Paper Card Unit, Evidence, queue and Primary revision IDs.
- Validate Evidence against the Task-bound Parse, not whichever Parse is currently active at read time.
- Keep preview operational and non-canonical. Only explicit user approval may write the complete Primary bundle revision.
- Treat the per-paper Primary bundle as the sole P4-B authority. Reject legacy/P4-B coexistence and block legacy Record Service mutation after a bundle exists.
- Preserve every approved revision and predecessor digest. Corrections append; refresh supersedes a stale Task with reciprocal successor lineage.
- Keep historical Primary child IDs resolvable for audit and stale propagation, but expose only active-revision children to Catalog and factual downstream reads.
- Recover bundle, Job and Task receipts idempotently after partial process failure. Never create a second revision for the same approved Task result.

## Review Semantic Bundle Rules

- Start Review semantic work only from a completed Review or mixed-document Review gate; never reopen the deterministic intake Job.
- Keep `p4a-v1` and `p4b-v1` behavior compatible. Register Review processing under `p4c-v1`; `p5c-v1` must continue exposing all three earlier available Task kinds.
- Bind five Review operations, but block staging only when the base `basic_review_memory` gate or an operation actually consumed by a retained source note is inadequate.
- Validate every retained source note against the Task-bound Parse. Exact quotes require a character slice; paraphrases require a resolvable page and section or explicit missing-section reason.
- Accept semantic Review fields only. Core owns revision, Review Memory and Review Unit IDs plus all background/non-evidence constants.
- Permit zero reusable Units only for an explicitly low-value, redundant, outdated or outside-scope Memory with a concrete reason and coverage limits.
- Keep preview operational and non-canonical. Explicit user approval may write one Review bundle revision, but it never upgrades Review content into Evidence or factual support.
- Reject legacy/P4-C coexistence and direct legacy Review mutation after a Review bundle exists.
- Keep App handoff inspection zero-write and lease-free. Leased replay must reproduce the
  stored handoff digest and must not allocate a second lease.
- Preserve every approved revision and predecessor digest. Corrections allocate new Memory and Unit IDs; only the active child enters Review Context and Catalog.
- Recover bundle, Job and Task receipts idempotently. A blocked Review use creates no scientific review-queue substitute.

## Platform Rules

Tests must include Windows-shaped and POSIX-shaped paths independent of the host. Persisted relative paths always use `/`.

Windows is the required live acceptance platform. macOS compatibility remains a design target and may be checked when available, but a live macOS run is not a release gate unless a later approved milestone says otherwise.

## Portable Skill Rules

- Keep the versioned Skill source under `skills/research-kb/`; do not package it in the Python wheel.
- Keep `SKILL.md` concise and link every detailed reference directly from it.
- Do not add Skill-local scripts, assets, schemas, state, README files or private fixtures without a later approved design.
- Keep executable command examples aligned with `capability show` and public CLI help.
- Refresh the repo-owned release snapshot only with `python tools/sync_portable_skill.py --source <authoring-source> --apply`; use `--check` in review and record the normalized tree digest.
- Run `tests/unit/test_portable_skill_contract.py` plus the active official `quick_validate.py`.
- Regenerate `agents/openai.yaml` with official tooling and require byte-identical output.
- Forward-test with fresh Agents, raw synthetic tasks and clean temporary workspaces; do not disclose expected routing.
- Keep forward-test outputs outside the repository and verify source bytes, record counts, events, Guardian and privacy.
- Treat repository merge and local CC Switch installation as separate gates. Never install directly into Codex or plugin-cache directories.
