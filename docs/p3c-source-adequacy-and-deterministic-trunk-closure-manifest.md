# P3-C Source Adequacy And Deterministic Trunk Closure Manifest

- status: `merged_post_merge_validated`
- validated_at: `2026-07-31T00:04:03+08:00`
- post_merge_validated_at: `2026-07-31`
- implementation_commit: `e32f9132194263bf1a2eb4cc074d06d6f6db943c`
- closure_commit: `f137d357664b613ceac9f8ecee7b46c8f4d68e17`
- merge_commit: `7df703bff6458cbb5492d58fe8408436e40b8268`
- review_pr: `https://github.com/ZhangChengwei0722/research-kb-core/pull/34`
- branch: `feature/p3c-source-adequacy`
- baseline: `main@1a504b60d07e9cfa40442de42f475adba4fcddc1`
- package: `research-kb-core==0.1.0`
- wheel_sha256: `c8cfd1380c21c6ce8ee744b893c3a0e373893a320147b1d6bdaf317d8f6f152a`
- sdist_sha256: `6a6c0b020bcb83cf3c85cdb3a54834a5ae668a7c85b7f32331bbe273793212a6`
- post_merge_wheel_sha256: `c8cfd1380c21c6ce8ee744b893c3a0e373893a320147b1d6bdaf317d8f6f152a`
- post_merge_sdist_sha256: `0989c5c2341b8db6c4a09a3fca18a355c1660057206a7a8051d5a417425ce0ed`
- layout_contract: `m3c-2a` unchanged; optional operational store added
- cleanup_status: `cleanup_deferred_until_p11_and_overall_goal_complete`

## Delivered Boundary

- optional append-only `process/source_adequacy.jsonl` Profiles bound to exact
  source manifestations, active parse identity, parser descriptor digest, parsed-output
  digest, requested operation and rule versions;
- independent capabilities for basic understanding, complete reading, continuous-text
  citation, figure/table extraction, layout-sensitive analysis and supplementary analysis;
- machine hard-failure precedence, explicit user successor decisions for non-hard
  uncertainty and read-time capability-specific stale projection;
- cross-record validation for source, parse, Profile basis, Pipeline Job authority and
  transaction/event correlation;
- a resumable deterministic trunk from source observation through parse reuse or explicit
  reparse and Source Adequacy to an explicit user-selected `primary` or `review` semantic
  boundary;
- specific Pipeline Job waits for source, parse, OCR, layout, supplement, stale and
  adequacy outcomes, with structural failures left fail-closed;
- redacted CLI, Catalog, Paper Status, Guardian and capability surfaces, including
  suppression of user-authored operational reason text from public projections;
- installed-package schema/module coverage and contributor/operator documentation.

Existing Registry-only workspaces remain readable. Their Registry source is treated as an
implicit `main_pdf`. Historical Profiles remain append-only provenance after reparse, but
cannot satisfy a current capability gate when their source, parse, parser profile, output
or rule dependency is stale.

## Fixed Invariants

- Source Adequacy measures fitness for one requested use, not scientific credibility and
  not a global pass/fail result.
- A capability is usable only from an exact current Profile whose required capability is
  `yes`; stale, absent, uncertain or inadequate state routes to a specific Pipeline Job
  wait and cannot cross the semantic boundary.
- Deterministic source, digest, locator and parse failures cannot be overridden by an
  Agent or user decision. User decisions create basis-bound successor Profiles rather
  than rewriting history.
- User decision detail remains in the canonical operational record for audit, while
  general reads and search projections expose only bounded status and generic rationale.
- A route decision rechecks the exact Profile and current gate. If either became stale
  while waiting, the Job resumes the deterministic trunk instead of completing.
- Only parser-domain or wrapped adapter-execution failures become `parse_failed`.
  Authority, schema, source-race and transaction-integrity failures propagate.
- P3-C stops at the semantic route boundary and creates no Paper Card, Evidence, Review
  Memory, scientific `review_queue`, Agent Task or App staging record.

## Validation

- final Windows suite: `813 passed, 4 expected POSIX skips`;
- focused Source Adequacy, deterministic trunk, Parse, Pipeline Job, Catalog and CLI
  regression suite: `133 passed`;
- `compileall`: passed;
- package build from final source: passed;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed;
- package version: `research-kb 0.1.0`;
- privacy scan: `7 expected, 0 unexpected`;
- `git diff --check`: passed;
- post-merge validation at `7df703bff6458cbb5492d58fe8408436e40b8268`
  repeated `compileall`, the full Windows suite, package build, both installed-wheel
  smokes, version, privacy scan and clean-tree checks with the same passing results;
- source assets, protected private workspaces and real PDFs: not accessed.

## Durable Reconciliation

- repository audiences are synchronized through `README.md`, `docs/architecture.md`,
  `docs/workflow.md`, `docs/contributor-guide.md` and this manifest;
- reconciliation date: `2026-07-31`;
- reconciliation revision: merge commit
  `7df703bff6458cbb5492d58fe8408436e40b8268`;
- the parent design, overall plan and roadmap in the external design workspace were
  rechecked and synchronized to the merged, post-merge-validated P3-C state and P3-D0
  next gate.

## Explicitly Not Delivered

- localhost App intake controls and browser acceptance: P3-D;
- Agent Task, staging/preview approval, semantic processing, Paper Card/Evidence/Review
  Memory commit and scientific `review_queue`: P4;
- Direction, Field Map, Question screening, Research Synthesis, Exchange, backup,
  migration or protected legacy cutover.

PR #34 merged as `7df703bff6458cbb5492d58fe8408436e40b8268`; the feature branch remains
available. P3-D begins from its reviewed bounded implementation plan and may not absorb
P4 semantic or Agent Task scope.
