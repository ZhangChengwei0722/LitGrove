# P2-A Core Catalog Closure Manifest

Status: passed

Baseline commit: `fa75f19c900a11bd82c7c95a56919e765b27aacf`

Closed at: 2026-07-29

Scope: Core workspace session, versioned Artifact Catalog adapters and disposable SQLite/FTS projection

```text
next_gate: merge P2-A, then execute P2-B synthetic scale generator
app_repository_created: false
canonical_scientific_writes_added: false
```

## 1. Delivered Surface

| Surface | Result |
|---|---|
| App-facing interface identity | `research_kb.application` exposes version `1.0`. |
| workspace session | Trusted configured option IDs open initialized workspaces and return redacted display metadata without browser-facing paths. |
| adapter registry | Current Registry, Card Unit, Evidence, Review Memory/Unit, Question, Step 7, process-event and Guardian kinds have versioned adapters. |
| deliberate exclusions | Parsed pages, review queue records and discovery candidates are not indexed; unknown future kinds are reported without guessing. |
| projection ownership | SQLite/FTS lives under a marker-owned App state root outside the complete workspace, local inbox and source roots. |
| projection integrity | Full build is atomically published; incremental update changes only affected sources and verifies source/item/FTS counts and foreign keys. |
| freshness | Adapter registry identity and indexed source digests form the source watermark; upstream changes project `stale`. |
| search | Stable title/kind/ID ordering, query-bound cursor, existing-position validation and page size `1..100`. |
| authoritative detail | Detail reloads current structured records and returns `current`, `changed` or `missing`; SQLite is never scientific authority. |
| public package | Catalog/session services are exported through `research_kb.services` and verified from the built wheel. |

## 2. Contract Boundaries

P2-A creates no CLI command, canonical schema, workspace layout, ID namespace, process event, transaction journal or scientific record. It adds no Direction, Field Map, Tag, Pipeline Job, Source Adequacy, Agent Task, Exchange, backup, provider or embedded-Agent behavior.

The projection may be deleted and rebuilt without losing durable knowledge. Stale search snippets remain explicitly labeled; a detail response is current only when its source digest still matches the authoritative structured record. Raw parsed-page text remains available only through the existing paper-scoped Parse read service.

## 3. Validation Evidence

```text
focused catalog/session tests: 21 passed
full Windows suite: 679 passed, 4 skipped
compileall src/tests: passed
package build: sdist and wheel passed
built-wheel smoke including P2-A imports: passed
version: research-kb 0.1.0
privacy: 7 expected findings, 0 unexpected findings
git diff --check: passed
```

The four skipped cases are the existing POSIX permission contracts. The final full-suite run includes marker-ownership enforcement and all P2-A tests.

## 4. Protected-State Check

| Boundary | Result |
|---|---|
| private scientific workspaces, legacy records and real PDFs | not accessed |
| canonical/source mutation by catalog operations | byte-preservation integration test passed |
| App repository or frontend | not created |
| schema/layout migration | not performed |
| global dependency installation | not performed |
| user-owned `README.md` change | preserved at SHA-256 `FB5AF898212399E53B31447B69F8B76E91DA6EA4F0163FFB04C53D9D0875A372` and excluded from P2-A staging |
| user-owned `agent_protocol/README.md` change | preserved at SHA-256 `CC37BEBACD092744A550438079CB6EF034DE69DFD68D1EB274259510DA1A3594` and excluded from P2-A staging |

## 5. Exit Decision

P2-A satisfies its bounded exit gate. The future localhost backend can select one configured workspace, build or refresh a disposable catalog, perform bounded search and resolve current details through public Core services without invoking the CLI or reading stores directly.

P2-B may begin only after this Core slice is merged. P2-B remains limited to deterministic synthetic generation and preliminary Core-only measurement; it does not create the App repository or future operational/scientific record types.
