# P9 Obsidian Generated Views Implementation Plan

- status: `short_review_passed`
- date: `2026-08-04`
- core_baseline: `main@050aa51`
- app_baseline: `feature/p8b-research-synthesis-work-surface@c215560`
- delivery_split: `P9-A Core managed render contract; P9-B localhost App vault sync`
- private_workspace_access_authorized: false
- real_vault_access_authorized: false
- migration_authorized: false
- cleanup_authorized_in_phase: false
- next_gate: `p9a_implementation`

## Goal

Provide a deterministic, one-way Obsidian reading projection without making Markdown a
canonical input or allowing synchronization to damage personal notes. Core owns rendered
content, logical paths, exact source dependencies, watermarks, file digests and freshness.
The localhost App owns configured vault selection, preview and the bounded copy operation.

P9 delivers only generated views and their synchronization. It does not implement reverse
Obsidian sync, a general Markdown editor, Exchange, citation graphs, migration, legacy
cutover or an embedded Agent runtime.

## Entry Conditions

- P8 is closed at Core `050aa51` and local App `c215560`.
- The public Application Service interface is `1.16`; P9-A may bump it once after the
  complete additive render service is stable.
- Canonical and operational records remain the only source of truth. SQLite, generated
  Markdown and destination manifests remain disposable projections.
- Existing Question, Research Organization, Research Synthesis and Catalog active-revision
  rules are reused. P9 must not invent a second active-record resolver.
- Tests use synthetic workspaces and synthetic TEMP vaults only.

## Delivery Structure

### P9-A: Core managed rendering

P9-A is merged and validated before P9-B pins a Core wheel. It owns no external vault path
and never writes outside the configured workspace `knowledge_root`.

P9-A also exposes a path-free streaming snapshot method over the public Application Service.
It accepts an expected active-manifest digest and a caller-owned sink callback, emits only
allowlisted logical path, content digest and verified bytes under the workspace lock, and
rechecks the active source after streaming. This lets P9-B write its own external staging
without reading a Core path or loading the complete projection into memory.

### P9-B: App preview and one-way vault synchronization

P9-B starts only from the exact merged P9-A wheel. The App receives configured target IDs,
not browser-submitted paths, and synchronizes only to a declared managed subtree.

## P9-A Core Contract

### A1. Managed projection layout

Add these derived paths through `WorkspaceLayout`:

```text
knowledge_root/
└─ views/
   └─ obsidian/
      ├─ manifest.json
      └─ generations/
         └─ <generation-id>/
            ├─ Home.md
            ├─ Papers/
            ├─ Reviews/
            ├─ Directions/
            ├─ Questions/
            ├─ Research Synthesis/
            └─ Tables/
```

`manifest.json` is the only active-generation pointer. A generation is immutable after it
becomes active. Core builds one complete temporary generation, verifies every file, renames
it to its final generation ID and atomically replaces the manifest last. A crash before the
manifest replacement leaves at most an unreferenced generated directory and cannot expose a
partial active tree.

The generation ID is `gen-` plus a digest over the render contract, selected optional tables
and sorted logical-path/content-digest pairs. It is computed before manifest serialization;
the manifest payload digest is computed afterward. The two digests never depend on each other
circularly.

P9 does not delete old generations. They are registered as generated cleanup candidates for
P11/overall closure. Full projection deletion remains recoverable by rendering again from
canonical records.

### A2. Versioned manifest and entry contract

Use one closed, versioned derived contract named
`obsidian-generated-view-manifest@1.0`. It is not a canonical scientific schema.

The manifest binds:

- workspace ID;
- render contract and renderer version;
- active generation ID;
- selected optional table IDs;
- deterministic global source watermark;
- generation creation time;
- sorted file entries;
- manifest payload digest.

Each file entry binds:

- allowlisted logical POSIX path;
- view kind and stable view ID;
- exact source dependency refs;
- dependency record digests and applicable revision IDs;
- per-file source watermark;
- rendered content SHA-256 and byte count;
- render time.

Every Markdown file repeats only the safe management facts needed for inspection in YAML
front matter: managed/generated flags, view kind/ID, render version, source watermark and
render time. Structured records and the manifest remain authoritative; front matter is not
read back as scientific state.

Serialization uses existing canonical JSON rules, UTF-8 and LF. Manifest lists and Markdown
sections have stable ordering. A no-change rerun preserves the active manifest and all file
bytes, including prior render times.

### A3. Source dependency and freshness model

Build the current record projection through existing bundle validation and active-revision
helpers. A dependency ref uses a deterministic record kind, stable record ID, current
revision ID when applicable and record digest. It never uses a filesystem path.

Dependency scope is per logical view:

- a Paper note consumes its Registry identity, active Primary Card/Evidence projection and
  applicable Tag projection;
- a Review note consumes its Registry identity, active Review Memory projection and
  applicable Tag projection;
- a Direction note consumes the active Direction record, linked Field Map summaries and
  the exact linked Paper/Review projections displayed in the note;
- a Question note consumes the active Question record and exact factual/background links
  displayed in the note;
- a Research Synthesis note consumes the active Question, displayed candidates and the
  exact Primary/Evidence/Review-background/Cross-View closure used to project freshness;
- each index/table consumes only the records represented in that index/table;
- `Home.md` consumes the exact source records needed for its current view inventory and
  counts; it never depends on generated files or another view watermark.

Read-only status compares each saved dependency ref to the current active projection and
returns `current | stale_upstream` per entry. Missing, replaced or digest-changed
dependencies stale only consuming views. Status does not rewrite the manifest or canonical
records.

Rendering a stale projection refreshes affected files. Files whose dependency watermark is
unchanged keep their existing bytes and manifest entry. The new generation remains complete,
but unrelated logical views are copied byte-for-byte from the prior verified generation.

### A4. View set and content boundary

Render these maintained views:

```text
Home.md
Papers/_index.md
Papers/<paper-id>.md
Reviews/_index.md
Reviews/<paper-id>.md
Directions/_index.md
Directions/<direction-id>.md
Questions/_index.md
Questions/<question-id>.md
Research Synthesis/_index.md
Research Synthesis/<question-id>.md
```

Paper notes present the seven configured Paper Card sections, retained Units, status and
canonical Evidence claim, PDF page, locator and short quote fields without copying raw parsed
pages or source paths. Review notes present Review Memory sections, retained Units,
page/section provenance and short excerpt or accurate paraphrase with visible
`background_only` and non-Evidence boundaries. Direction and Question notes link to generated
paper/review notes by stable ID. Research Synthesis notes render all four candidate types,
freshness, factual Evidence IDs, boundaries and separately labeled Review background.

Support exactly two optional on-demand tables in P9:

```text
library_summary
question_coverage
```

They render under `Tables/` only when selected in the render request. They are derived
reading tables, not Evidence matrices or maintained scientific records.

Generated links are internal Obsidian links built only from allowlisted logical paths and
stable IDs. P9 emits no raw HTML, executable block, embed, `file:` URL, source path or
unbounded external link. All titles, summaries, excerpts and Agent text are escaped as
untrusted data before Markdown rendering.

### A5. Integrity and edited-source handling

Before status-changing render work, Core verifies:

- the active manifest contract, workspace binding and payload digest;
- active generation confinement and regular-file types;
- exact file set and each content digest;
- absence of unsafe links, junctions and reparse points;
- no unknown file inside the active generation.

Digest mismatch or an unknown active-generation file blocks overwrite as a managed-view
integrity conflict. A read-only preview reports the logical files involved. A separately
explicit `discard_managed_edits` render continuation may rebuild from canonical records;
ordinary render cannot infer that authority. Core never imports edited Markdown.

The user-facing export-as-personal-note continuation is P9-B destination behavior. Core
does not accept a personal vault path and does not write outside the workspace.

### A6. Public service, CLI and capability

Add one public session-bound `ObsidianGeneratedViewsApplicationService` with bounded methods:

- limits/capability;
- status;
- render preview;
- render with expected preview watermark and explicit managed-edit resolution.

Responses expose logical paths, IDs, counts, digests, freshness and diagnostics only. They
omit absolute workspace paths and unrestricted record payloads. Render writes are
`persistent_writes > 0` but always `canonical_scientific_write: false`.

Add thin CLI commands over the same service:

```text
obsidian status
obsidian render --dry-run
obsidian render
```

Optional table selection is a closed repeated option. Managed-edit discard requires an
explicit flag and actor `user`; there is no force/overwrite alias. Update capability facts,
public exports and interface version only after the contract is complete.

## P9-B App Contract

### B1. Exact Core compatibility

- merge P9-A and fast-forward Core `main`;
- run post-merge validation;
- build the exact merged-head wheel and record its SHA-256;
- pin Core commit, wheel digest, package version and Application Service interface;
- fail closed when `obsidian_generated_views` is absent.

### B2. Backward-compatible local target configuration

Keep `research-kb-app-config@1.0` readable with zero configured Obsidian targets. Add a
strict `research-kb-app-config@1.1` with an `obsidian_targets` list. Each target contains:

```text
target_id
label
workspace_option_id
vault_root
managed_subtree
personal_notes_subtree
```

All paths are validated at startup. `vault_root` is one existing unlinked directory.
Subtrees are relative POSIX paths, distinct, non-overlapping and free of `.`/`..`, drive,
UNC, absolute and reserved components. Browser requests carry only `target_id`; paths never
enter HTTP responses or logs.

### B3. Sync preview and expected-state token

The App backend reads the current Core manifest through the public service and inspects only
the configured destination managed subtree. Preview returns:

- target label and managed logical root, without absolute paths;
- source manifest/generation watermark;
- create/update/no-change counts and logical paths;
- destination manifest compatibility;
- edited, unknown or collision paths;
- a bounded expected-state token binding source manifest, destination manifest and observed
  destination file digests. The token is an opaque, single-use, in-memory preview lease;
  it expires on workspace/target change and cannot be reconstructed by the browser.

First sync requires an absent or empty managed subtree. A non-empty subtree without a valid
App-owned manifest is a collision and is never adopted or overwritten. Existing sync
requires the same workspace and target binding.

### B4. Sync apply and edited-file continuations

Synchronization uses one serialized `obsidian_sync` operation. It revalidates the expected
state, stages a complete managed subtree beside the target and swaps only a previously valid
managed subtree. It never scans, deletes or overwrites outside that subtree.

If every prior managed file matches its recorded digest and there are no unknown files, an
ordinary apply may replace the prior managed subtree. If a managed file was edited or an
unknown file was added, ordinary apply stops. The App offers exactly two continuations:

```text
discard_managed_edits
export_personal_copy_then_sync
```

`discard_managed_edits` requires an explicit current user action and may replace only the
declared managed subtree. `export_personal_copy_then_sync` first writes a create-only,
collision-free copy under the configured personal-notes subtree, preserving every edited or
unknown file byte-for-byte and writing a small receipt. Only after export verification may
the managed subtree be replaced. Neither continuation writes canonical records or reverse
syncs Markdown.

Destination staging and backup names are App-owned, confined and excluded from ordinary
Obsidian links. Failed pre-swap work removes only operation-owned temporary files. A failed
swap restores the verified prior managed subtree when possible and otherwise reports a
recovery-required operation. After a successful target/manifest verification, the App may
remove only the operation-owned prior managed-subtree backup whose recorded identity still
matches. P11 will exercise process-interruption recovery at scale.

### B5. Browser work surface

Add a dedicated `Obsidian` navigation surface that provides:

- source render status and per-view freshness counts;
- maintained view inventory and optional table checkboxes;
- explicit render preview and render action;
- configured vault target selection;
- sync preview with create/update/no-change/edited counts;
- explicit sync, discard or export-copy continuation;
- last operation result and logical-path conflict list.

Use existing quiet work-surface styling, Lucide icons, escaped text and bounded lists. Do not
show absolute paths or render Markdown as unsanitized HTML. Navigation and page loading are
read-only and never render or sync implicitly.

## Tests First

### Core targeted tests

- manifest closed-contract validation, canonical serialization and payload digest;
- deterministic file ordering, UTF-8/LF and fixed-clock byte identity;
- initial render, exact rerun no-change and full rebuild after projection deletion;
- Paper, Review, Direction, Question and Research Synthesis maintained views;
- optional table selection and removal from a later generation;
- per-view dependency closure and affected-only stale projection;
- stale rerender restores only affected entries and preserves unrelated bytes/render times;
- active generation edit/unknown file blocks ordinary render;
- explicit user discard rebuilds without importing edited Markdown;
- malicious Markdown/HTML/link/embed/path text remains escaped data;
- unsafe path type, wrong workspace manifest and incompatible render contract fail closed;
- CLI/service equivalence, capability/interface projection, Guardian/Catalog regression;
- Windows and POSIX-style Unicode logical-path fixtures.

### Core full validation

```text
python -m pytest -q
python -m build
fresh-wheel install/version/service smoke
python -m research_kb privacy scan --root .
git diff --check
```

### App targeted tests

- config `@1.0` compatibility and strict `@1.1` target validation;
- target ID only, authentication, Origin/CSRF, request budgets and operation serialization;
- first-sync preview/apply and exact no-change repeat;
- source update changes only expected destination files;
- personal-note sentinel outside managed subtree remains byte-identical;
- first-sync collision and cross-workspace/destination-manifest mismatch fail closed;
- edited managed file and unknown file block ordinary sync;
- discard continuation and export-personal-copy continuation preserve expected bytes;
- expected-state race rejects apply;
- Unicode Windows/macOS path fixtures and internal Obsidian links resolve;
- browser source render, table selection, target preview and both conflict continuations;
- desktop and `390x844` screenshot, overflow and interaction checks.

### App full validation

```text
Python unit/integration/security suite
Vitest
TypeScript
ESLint
Vite production build
development and exact-wheel packaged Edge E2E
fresh-install/start/stop smoke
compatibility mismatch and privacy/path-redaction scans
git diff --check
```

## Delivery And Closure

1. Short-review and commit this plan before implementation.
2. Implement P9-A on this Core feature branch, run targeted/full validation and review diff.
3. Push, create and merge one focused Core PR; fast-forward and validate merged `main`.
4. Build and hash the exact merged Core wheel.
5. Create a sequential P9-B App feature branch and its bounded App plan, then pin that wheel.
6. Implement App target configuration, sync service, HTTP adapter and browser work surface.
7. Run complete App package/browser validation with synthetic TEMP vaults only.
8. Record validation receipts, closure manifests and generated cleanup candidates.
9. Run `neat-freak`, reconcile final design/overall plan/roadmap and begin P10 planning.

## Exit Gate

P9 closes only when Core can deterministically rebuild source-watermarked Obsidian views,
project affected-only freshness and reject edited active generated files, and the packaged
App can preview and synchronize those views into one configured synthetic vault subtree
without reverse writes, path disclosure, silent edit loss, personal-note changes or
canonical drift.

## Short Review

The synchronized short review found no architecture decision outside the approved P9 scope.
It specifically confirmed:

- immutable complete generations plus an atomic active manifest avoid partial multi-file
  projection exposure without turning the transaction journal into a Markdown store;
- generation and manifest digest derivation is acyclic;
- per-file dependencies satisfy affected-only stale propagation;
- existing App config remains readable while configured vault roots stay outside browser
  authority;
- first-sync collision, edited-file discard and personal-copy export are distinct paths;
- useful Evidence/Review provenance is rendered without raw parsed text or filesystem paths;
- P9 does not absorb Exchange, reverse sync, citation navigation, cleanup or private-data
  validation.

Implementation may begin with P9-A tests. P9-B remains gated on the exact merged P9-A wheel.

## Hard Boundaries

- no private scientific workspace, real PDF or real user vault access;
- no reverse Obsidian sync or Markdown-to-canonical import;
- no raw parsed text, source path, HTML, embed or arbitrary external link in generated views;
- no App-owned scientific rendering or direct canonical JSON/JSONL reads;
- no browser-submitted filesystem path;
- no implicit render/sync on startup, navigation, query, intake or Agent completion;
- no Exchange, citation graph, provider expansion, manuscript-review UI or Agent runtime;
- no layout migration, legacy write freeze, cutover, deployment or desktop wrapper;
- no generated-artifact cleanup before P11/overall completion.
