# P3-B Source Assets, Intake And Identity Correction Closure Manifest

- status: `implementation_validated`
- closed_at: `2026-07-30T12:54:08+08:00`
- implementation_commit: `3371edd12b062a016fc260a267ca0a5443377e04`
- branch: `feature/p3b-source-intake`
- baseline: `origin/main@176b583a193fbd88b8092d3fb662b47c627660c0`
- package: `research-kb-core==0.1.0`
- wheel_sha256: `c37f8c1b1e2d2dd147ed9d13740143ffd30b14af66e2732962ee1187480fd590`
- sdist_sha256: `a25bfcbd173676596c944e3a419ff6c7306ed390b2d9b8c6b64db0f051b41bdd`
- layout_contract: `m3c-2a` unchanged
- cleanup_status: `cleanup_deferred_until_p11_and_overall_goal_complete`

## Delivered Boundary

- optional append-only `registry/source_assets.jsonl` manifestations for `main_pdf`,
  `supplement` and `source_data`;
- read-only source reference, exact-user-authority create-only inbox copy, bounded
  watched-inbox scan/selection, association, observation and same-digest relink;
- stream-based Core copy handoff with staged, receipted and published recovery points;
- source availability/currentness projection and source-to-Parse stale behavior;
- user-only append-only Registry duplicate merge, mistaken-merge split, alias, archive
  and tombstone corrections without rewriting paper IDs;
- current Source Asset and Registry identity Catalog projections with bounded redacted
  detail;
- Guardian checks for live source identity, unassociated receipts, copy residue, exact
  Job/event authority correlation and Registry/main-source consistency;
- thin CLI commands, capability facts, schemas, installed-wheel surfaces and contributor
  documentation.

Existing Registry-only workspaces remain readable. Their Registry source is the implicit
main manifestation until a P3-B operation needs explicit history. No bulk backfill,
layout migration or historical-record rewrite was added.

## Fixed Invariants

- Every Source Asset revision is append-only, predecessor-bound and uses a closed
  transition reason. Only association adds a paper ID; only same-digest relink changes
  the portable source ref.
- Changed bytes append a non-current manifestation candidate. Missing, inaccessible or
  unsafe paths preserve history while blocking current Parse reuse.
- A known-paper active `main_pdf` manifestation matches the Registry fingerprint.
  Changed candidates may differ, but they cannot silently replace the active identity.
- Revision one retains intake ownership. Another Job cannot create a competing receipt
  or selection replay; a separately authorized association Job may append an association
  without changing that ownership.
- Copy publication is bounded and create-only. It records the Source Asset receipt before
  publication, detects source/link/publication races and never overwrites an existing
  target.
- Source and identity mutations require a current Pipeline Job with the exact authority;
  copy and identity correction additionally require exact user authority.
- Browser-facing scan, list, event and Catalog payloads contain no absolute source path or
  raw fingerprint.

## Validation

- final Windows suite: `778 passed, 4 expected POSIX skips`;
- focused P3-B source, identity, Guardian, Catalog, CLI and privacy suites:
  `106 passed`;
- `compileall`: passed;
- package build from final source: passed;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed;
- package version: `research-kb 0.1.0`;
- privacy scan: `7 expected, 0 unexpected`;
- `git diff --check`: passed before implementation commit;
- source assets, protected private workspaces and real PDFs: not accessed.

## Explicitly Not Delivered

- Source Adequacy profiles, use-specific capability decisions and deterministic trunk
  orchestration: P3-C;
- localhost App intake controls and browser acceptance: P3-D;
- Agent Task, staging/preview approval, semantic routing, Paper Card/Evidence/Review Unit
  processing and scientific `review_queue`: P4;
- Direction, Question screening, Research Synthesis, Exchange, backup, migration or
  protected legacy cutover.

P3-C may begin only after this branch is merged, the merged head passes post-merge
validation, durable roadmap state is reconciled and P3-C has its own bounded reviewed
implementation plan.
