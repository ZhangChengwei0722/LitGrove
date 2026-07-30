# Research KB Core

Cross-platform, local-first contracts and deterministic CLI primitives for evidence-traceable research knowledge bases.

## Current Scope

Milestone 1B through the M3D-1 repository slice provide:

- versioned workspace, domain, record, and candidate schemas;
- portable source references and stable IDs;
- schema, reference, status, and privacy validation;
- locked atomic JSON/JSONL promotion with recovery journals and process events;
- read-only source registration with SHA-256 duplicate linking;
- a synthetic text parse adapter for invented test sources;
- deterministic Paper Card, Evidence, and review queue record promotion;
- check-only or explicitly persisted Guardian reports;
- two fully synthetic cross-domain runtime fixtures;
- config-first workspace bootstrap with shared semantic validation;
- a deterministic, non-canonical workspace identity marker;
- initialized-workspace enforcement for every runtime command.
- generic read-only compatibility inspection through explicitly injected legacy adapters;
- deterministic compatibility differences, protected-input snapshots, and blocking policy without migration or persistence.
- historical layout upgrades through `m3b-1` and the current exact `m3b-1 -> m3c-2a` upgrade with no canonical-record rewrite;
- persistent, domain-neutral Question Mapping from selected Paper Card Units;
- CLI-owned question/link IDs and exact evidence/boundary projection;
- read-only `question list/show` commands and Guardian mapping freshness warnings.
- one deterministic, stdout-only Question Reading View with selected Card Units, canonical evidence trace, non-evidence boundaries, and current freshness diagnostics.
- explicit optional legacy-spatial `pdfplumber` and preferred `pdfplumber-text-flow` adapters with exact package-version provenance and one row per PDF page;
- strict same-paper page/locator/quote validation for canonical Evidence, including bounded synthetic block compatibility.
- a versioned transient capability report, bounded one-paper status projection, and validated parsed-page read surface;
- bounded stdin JSON handoff into the existing Registry and mutation authority paths without temporary request files.
- one source-stable, paper-scoped canonical context read for Card Unit, Evidence, and review queue recovery.
- one read-only intake preflight that maps an absolute source path to its portable source reference, exact Registry state, and active Paper Card section contract.
- one repo-owned Portable Agent Skill for existing-config primary-research and common review PDF intake through mutually exclusive routes;
- one common, background-only Review Memory contract for five review subtypes, with CLI-owned Memory/Unit IDs and exact page/section provenance;
- atomic Review Memory append/replace, primary/review route exclusion, stale-parse Guardian warnings, and a separate `review context` recovery read;
- a review-specific route in the same Portable Skill, without subtype-specific schemas or downstream Field Map/Question/Step 7 integration.
- four deterministic Step 7 candidate stores with CLI-owned IDs, evidence/boundary closure and atomic append/replace;
- Question Mapping admission, stale-upstream projection, `step7 context`, stdout-only `step7 render`, and Guardian `RKBC-014` warnings.
- Portable Skill routes for read-only paper/question queries, canonical claim trace-back and explicitly gated Step 7 maintenance.
- one workspace-independent Europe PMC metadata connector that reports only in the active task, with bounded local filtering and DOI deduplication.
- explicit user-only handoff of selected discovery results into an idempotent `metadata_only` candidate store, plus separate zero-write OA resolution and create-only Europe PMC acquisition contracts.
- a read-only acquired-candidate intake projection and Portable Skill continuation into the existing primary/review workflow through Guardian.
- a bounded, stdout-only DOCX/PDF manuscript projection with source fingerprints, stable paragraph/page locators and explicit coverage limits.
- a Portable Skill-only explicit-criteria manuscript audit route over transient projection and existing knowledge reads, with scope-limited findings and zero persistence.
- append-only Pipeline Jobs with captured authority, bounded current-state listing, cooperative cancellation, recovery transitions, correlated events and Guardian checks;
- append-only Source Asset manifestations for reference, create-only local-inbox copy, bounded inbox selection, observation and same-digest relink;
- user-authorized Registry identity corrections for duplicate merge, mistaken-merge split, alias, archive and tombstone without rewriting paper IDs or historical records;
- use-specific Source Adequacy profiles bound to exact source and parse snapshots, with independent capability gates, stale projection and hard-failure precedence;
- a resumable deterministic trunk from current source through Parse and Source Adequacy to an explicit user-selected primary/review semantic boundary, without creating scientific records.

The installed CLI contains no private adapter and performs no adapter or connector discovery. The CLI never calls an LLM or makes scientific judgments. M3D-1 audit semantics remain Agent-owned in the Portable Skill; Core still only projects manuscripts and exposes existing reads. OCR, manuscript rewriting, subtype-specific review runtime, persisted Markdown or additional derived views, Field Map integration, Review Unit Question Mapping, institutional/browser acquisition and migration remain later milestones.

## Privacy Boundary

This repository contains no real paper data or private workspace content. See [docs/privacy-boundary.md](docs/privacy-boundary.md).

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m research_kb --version
.\.venv\Scripts\python -m research_kb privacy scan --root .
```

For real local PDF parsing, install the bounded optional extra in the repository environment:

```powershell
.\.venv\Scripts\python -m pip install -e ".[test,pdf]"
```

On macOS, use `.venv/bin/python` instead.

## Portable Skill

The reviewed Skill source lives at `skills/research-kb/`. It orchestrates bounded on-demand metadata discovery, explicit user-selected candidate handoff, exact-user-authority Europe PMC OA acquisition, separately requested acquired-candidate intake through the existing primary/review workflow, exact-path manuscript projection, explicit-criteria manuscript audit, read-only knowledge queries and explicitly gated Step 7 maintenance. It adds no schema, ID or workflow store of its own.

The Python wheel does not install the Skill. Local CC Switch installation is a separate, explicitly authorized post-merge operation. Discovery search is workspace-independent; candidate handoff, resolution, acquisition, manuscript projection/audit and all intake/query/Step 7 modes require an existing workspace config. The Skill does not generate workspace/domain configuration or integrate Review Units downstream. It acquires a source only through the exact-user-authority `explicit_oa_acquisition` route. Discovery search, manuscript projection/audit and ordinary queries remain non-persistent; only candidate handoff, explicit OA acquisition, explicit Step 7 maintenance or an explicitly complete intake workflow may write through Core.

## Runtime Commands

Initialize an existing workspace config before running workspace services:

```text
research-kb workspace init --workspace <workspace.yaml> [--dry-run]
```

Bootstrap validates source/config relationships, creates only the approved managed scaffold, and writes `.research-kb/workspace.json`. It never creates or scans `local_inbox`, changes source assets, creates canonical records, or emits a process event.

Capability probing is workspace-independent; all commands with `--workspace` resolve paths through the initialized workspace:

```text
research-kb capability show
research-kb discovery search --provider europe-pmc --request <request.json>
research-kb discovery search --provider europe-pmc --request -
research-kb discovery select --workspace <workspace.yaml> --request <selection.json> --actor user
research-kb discovery select --workspace <workspace.yaml> --request - --actor user
research-kb discovery list --workspace <workspace.yaml>
research-kb discovery show --workspace <workspace.yaml> --candidate-id <discovery_id>
research-kb discovery resolve --workspace <workspace.yaml> --candidate-id <discovery_id> --provider europe-pmc
research-kb discovery acquire --workspace <workspace.yaml> --candidate-id <discovery_id> --provider europe-pmc --actor user
research-kb intake inspect --workspace <workspace.yaml> --source <absolute-source-path>
research-kb intake inspect-acquired --workspace <workspace.yaml> --candidate-id <discovery_id>
research-kb manuscript inspect --workspace <workspace.yaml> --source <absolute.docx|absolute.pdf>
research-kb compatibility inspect --workspace <workspace.yaml> --adapter <adapter_id>
research-kb registry add --workspace <workspace.yaml> --root-id <root> --relative-path <path> --metadata <metadata.json>
research-kb registry add --workspace <workspace.yaml> --root-id <root> --relative-path <path> --metadata -
research-kb job create --workspace <workspace.yaml> --request <request.json> --actor <agent|cli|user>
research-kb job list --workspace <workspace.yaml> [--page-size <n>] [--cursor <opaque-cursor>]
research-kb job show --workspace <workspace.yaml> --job-id <job_id>
research-kb job transition --workspace <workspace.yaml> --job-id <job_id> --request <request.json> --actor <agent|cli|user>
research-kb job cancel --workspace <workspace.yaml> --job-id <job_id> --request <request.json> --actor <agent|cli|user>
research-kb job recover --workspace <workspace.yaml> --job-id <job_id> --request <request.json> --actor <agent|cli|user>
research-kb source list --workspace <workspace.yaml>
research-kb source scan --workspace <workspace.yaml> [--max-entries <n>] [--min-stable-age-seconds <n>]
research-kb source reference --workspace <workspace.yaml> --request <request.json> --actor <cli|user>
research-kb source copy --workspace <workspace.yaml> --request <request.json> --actor user
research-kb source select --workspace <workspace.yaml> --request <request.json> --actor <cli|user>
research-kb source associate --workspace <workspace.yaml> --request <request.json> --actor <cli|user>
research-kb source observe --workspace <workspace.yaml> --request <request.json> --actor <cli|user>
research-kb source relink --workspace <workspace.yaml> --request <request.json> --actor <cli|user>
research-kb identity list --workspace <workspace.yaml>
research-kb identity correct --workspace <workspace.yaml> --request <request.json> --actor user
research-kb parse run --workspace <workspace.yaml> --paper-id <paper_id> --adapter synthetic-text
research-kb parse run --workspace <workspace.yaml> --paper-id <paper_id> --adapter pdfplumber
research-kb parse run --workspace <workspace.yaml> --paper-id <paper_id> --adapter pdfplumber-text-flow
research-kb parse show --workspace <workspace.yaml> --paper-id <paper_id> [--page <positive_integer>]
research-kb adequacy assess --workspace <workspace.yaml> --request <request.json> --actor <cli|user>
research-kb adequacy show --workspace <workspace.yaml> --paper-id <paper_id> [--operation <operation>]
research-kb adequacy gate --workspace <workspace.yaml> --paper-id <paper_id> --operation <operation>
research-kb trunk advance --workspace <workspace.yaml> --request <request.json> --actor <cli|user>
research-kb paper status --workspace <workspace.yaml> --paper-id <paper_id>
research-kb paper context --workspace <workspace.yaml> --paper-id <paper_id>
research-kb review context --workspace <workspace.yaml> --paper-id <paper_id>
research-kb record promote --workspace <workspace.yaml> --request <request.json> --actor <agent|cli|user>
research-kb record promote --workspace <workspace.yaml> --request - --actor <agent|cli|user>
research-kb question list --workspace <workspace.yaml>
research-kb question show --workspace <workspace.yaml> --question-id <question_id>
research-kb question render --workspace <workspace.yaml> --question-id <question_id>
research-kb step7 context --workspace <workspace.yaml> --question-id <question_id>
research-kb step7 render --workspace <workspace.yaml> --question-id <question_id>
research-kb guardian check --workspace <workspace.yaml> [--write-report]
research-kb transaction recover --workspace <workspace.yaml> [--dry-run]
```

Existing source assets remain immutable. The source-write exceptions are exact-user-authority `discovery acquire` and `source copy`; each may create only one previously absent PDF under the configured, uniquely addressable `local_inbox`. Neither may overwrite, move, rename or delete a user source. Canonical writes stay under `knowledge_root` and emit a process event only after a validated atomic replacement.

`capability show`, `discovery search`, `discovery list`, `discovery show`, `discovery resolve`, `intake inspect`, `intake inspect-acquired`, `manuscript inspect`, `parse show`, `adequacy show`, `adequacy gate`, `paper status`, `paper context`, `review context`, and `step7 context` emit bounded JSON and write no workspace state. Capability output distinguishes installed adapters and built-in connectors without calling the network. Paper status reports deterministic stage and safety facts only; it does not claim scientific completion or choose a next action. Parsed-page text appears only through the explicit local `parse show` read.

`discovery search` accepts an explicit date range, field-bound keywords, preprint choice and maximum of 1-15 results. It calls only the built-in fixed Europe PMC HTTPS endpoint, reapplies filters locally, merges exact DOI identity and marks possible title duplicates without merging them. It creates no workspace, candidate, event, report file or downloaded source. Public provider state is mutable; identical requests and provider page payloads produce identical normalized bytes.

`discovery select` accepts the complete validated transient report plus 1-15 result keys chosen explicitly by the user. It persists only those results to `discovery/candidates.jsonl`, allocates `discovery_<uuid4>` IDs, records deterministic selection contexts and optionally links existing Question Mapping IDs for organization. Exact intent reruns write nothing; new contexts update the same candidate; changed metadata for the same result key fails the complete batch with `RKBC-034`. Selection assigns only `user_selected`, `metadata_only`, `not_started` and `not_evidence: true`; it does not register, include, verify, acquire or download a paper. `discovery list/show` validate the complete workspace and expose no paths or paper content.

`discovery resolve` rechecks one selected candidate against the fixed Europe PMC search endpoint. Exact DOI or stored source identity is used; the report returns an opaque OA asset reference and `persistent_writes: 0`, never a provider URL. `auto_acquisition_eligible` is a routing fact only and does not authorize download, Registry intake or screening.

`discovery acquire` requires exact `actor: user`, re-runs resolution, accepts only one repository-OA PDF, and creates `<local_inbox>/<candidate_id>.pdf` without overwrite. The candidate gains a portable receipt but remains `metadata_only` and `not_evidence: true`. Exact reruns are zero-write. Missing/changed receipts, existing unreceipted targets, partials and crash states stop or become Guardian findings; acquisition never chains into Registry or Parse.

`intake inspect` accepts one absolute source path, confines it to exactly one declared source root, and returns only portable `root_id + relative_path`, exact-path registration state, and ordered Paper Card section IDs/labels. It hashes the source before and after projection, never returns the hash or absolute path, and performs no registration. The Portable Skill uses it for sequential reruns; concurrent inspect-and-register deduplication is not guaranteed.

`intake inspect-acquired` accepts one acquired discovery candidate ID, verifies its receipt against the exact current inbox PDF, and emits the same intake projection plus deterministic Registry bibliography input. It writes nothing and performs no provider request. A separately requested Skill route may pass that projection to the existing `registry add`; acquisition alone still stops before Registry. Unless that later task explicitly requests `registry_only`, the returned paper ID resumes the same status, Parse and mutually exclusive primary/review workflow used by local-path intake.

`manuscript inspect` accepts one exact absolute `.docx` or `.pdf` path under exactly one declared source root. It fingerprints the source before and after bounded extraction, returns stable paragraph or page units plus parser identity and coverage limits, and writes nothing. DOCX uses a standard-library OOXML reader; PDF uses the installed `pdfplumber` policy. The output is private task context, not Registry, Evidence, a claim map or an audit result.

The Portable Skill's separate `manuscript_audit` mode requires criteria and exact current-task question/paper selectors before inspection. It preserves the requested dimensions, limits every finding to the reported local corpus, expands exact factual support from Card Units to canonical Evidence and returns only a private report with `persistent_writes: 0`. It creates no Core audit command, stored claim map, finding record or rewritten manuscript.

`paper context` returns the selected paper's complete stored Paper Card or `null`, canonical Evidence records, and review queue records after complete-bundle and source-stability checks. It excludes source references, paths, parsed pages, Question Mappings, and unrelated papers. It is the public recovery surface for CLI-owned Unit, Evidence, and queue IDs, not a generic workspace export or semantic resume decision.

`review context` returns one complete Review Memory or `null`, `absent/current/stale_parse` freshness, and transient exact local DOI matches for primary-paper leads. Review Memory remains `background_only`, `can_enter_canonical_evidence: false`, and `not_fact: true`; stale notes are never rebound to a newer parse automatically.

Stdin accepts one UTF-8 JSON object only. Discovery requests and Registry metadata are capped at 64 KiB; mutation requests are capped at 4 MiB. YAML remains file-only. Invalid input never reaches its service, and no temporary request file is created.

Pipeline Job mutation requests are capped at 64 KiB and bind each state transition to a captured authority snapshot. Source mutations require a current Job with the exact operation authority. `source scan` is bounded, transient and non-daemon; selection revalidates the opaque candidate against current bytes. Reference, copy or selection may first create an unassociated Source Asset; after `registry add`, `source associate` binds that exact current asset state to the new paper through CAS. A Job cannot end as `completed` or `completed_with_findings` while a Source Asset it created remains unassociated. Failed or cancelled work retains the source receipt and exposes the incomplete association through Guardian. Source and identity list responses expose only current projections, never absolute paths or source fingerprints.

The Core copy handoff is `LocalSourceIntakeService.copy_stream`, which consumes a bounded binary stream and never requires a browser to submit a server path. `source copy` is the local CLI adapter: it opens one exact absolute PDF, binds its size/digest/file identity, and delegates to the same stream service. The operation stages bytes, commits the Source Asset receipt, then publishes one absent inbox target create-only. The same Job can resume a matching staged partial, a receipted-but-missing target, or an already published target without duplicating the file or asset. Partial recovery requires the original expected digest. A bounded inbox scan rejects a directory with more than 1,000 entries rather than traversing an unbounded listing.

Source Asset revisions preserve every manifestation. Same-digest relink changes only the portable location and keeps Parse reusable. Changed, missing, inaccessible, hard-linked or reparse-point sources become explicit non-current states; historical Parse remains present but cannot be reused as current. Registry identity correction is user-only and append-only: aliases and merges affect projections, split supersedes an earlier merge, and archive/tombstone affects active-library visibility without deleting Registry rows or source files.

An explicit `main_pdf` may bind a known Registry paper only when its SHA-256 equals that paper's registered fingerprint; a Source Asset cannot silently replace Registry source identity. Public inbox scan excludes every Registry or Source Asset ref. The only registered-file replay is an exact `source select` retry for the same Job, revision-one intent, paper argument and role. Source state reasons are a closed vocabulary: only `paper_associated` adds a paper ID, only `same_digest_relink` changes the portable ref, and observation transitions cannot rewrite either identity.

Both PDF adapters record exact `pdfplumber` package version identity and emit `page:<n>:text` page locators. Their distinct adapter names preserve extraction-profile identity: `pdfplumber` is the legacy spatial profile, while `pdfplumber-text-flow` uses `x_tolerance=1` and content-stream order for new scientific intake. Text-flow is not layout verification; unresolved columns, spacing, tables or OCR remain stop conditions. Real-PDF Evidence must use `page:<n>:char:<start>-<end>` with an exact zero-based, end-exclusive slice of stored page text. Missing PDF dependencies and unsupported PDF sources fail explicitly; there is no OCR or synthetic fallback.

Question Mapping requests use `record promote`. The request selects Paper Card Unit IDs and may add question-specific review queue boundaries; Core derives `evidence_ids`, preserves required unit boundaries, allocates IDs, and stores the result in `questions/mappings.jsonl`. Unapproved Agent-generated questions remain task report candidates and cannot use a persistable `question_origin`.

`question render` validates the complete workspace bundle and emits one raw Markdown reading view to stdout. It expands only records reachable from the selected mapping, labels review queue records as non-evidence, computes freshness without rewriting the mapping, and creates no file, event, journal, report, or cache.

Step 7 requests also use `record promote`, but require `paper_id: null` and `question_origin: existing_question`. The Agent submits semantic fields and selected mapped Card Unit IDs; Core owns candidate IDs, candidate type, exact canonical Evidence and Unit-boundary closure, snapshot fields, timestamps and the fixed `not_fact: true`, `review_status: ai_draft`, `automation_status: pending` boundary. Records live in four JSONL stores under `step7/`. `step7 context` returns candidates and deterministic freshness for one question. `step7 render` emits a non-canonical Markdown reading view to stdout only. Neither command generates or scientifically judges candidates.

`compatibility inspect` is an integration seam for an adapter injected by a private caller in the same Python process. It emits one schema-valid report to stdout, snapshots every declared protected input before and after inspection, and writes no report, event, journal, or canonical record. A clean report exits `0`, blocking differences exit `1`, adapter/output errors exit `2`, and protected-input changes exit `4`.

## Contracts

JSON Schema Draft 2020-12 files live under `schemas/`. YAML canonical inputs are parsed into mappings and validated against the same schemas. The closed discovery search request and report remain transient interface `1.0` documents; only explicitly selected metadata is normalized into the public `discovery-candidate` schema. Markdown is documentation or a future rendered view, never a structured source of truth.
