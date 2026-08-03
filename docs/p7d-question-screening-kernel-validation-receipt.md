# P7-D1 Question Screening Kernel Validation Receipt

- status: `passed`
- validated_at: `2026-08-03`
- baseline: `main@59d4c004a84acd7f87bc204907882b11fa590f64`
- branch: `feature/p7d-question-screening`
- application_service_interface: `1.14`
- layout_contract: `p7d-1`

## Validation

- complete deterministic Windows suite, sharded under the established validation policy:
  `1004 passed, 4 expected POSIX permission skips`;
- final affected screening, Catalog, Question Mapping, Research Organization and Guardian
  matrix after review corrections: `73 passed`;
- final screening contract/schema check after requiring non-empty criterion rationale:
  `13 passed`;
- contract and integration closure: `141 passed` within the complete sharded run;
- privacy suite: `4 passed`;
- benchmark suite: `16 passed`;
- `compileall src tests`: passed;
- repository privacy scan: `7 expected findings, 0 unexpected findings`;
- `git diff --check`: passed;
- package build: passed;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed.

Two monolithic suite attempts exceeded the execution timeout without failure output. The
same test population was therefore completed in deterministic shards. The final review
corrections were then covered by the affected `73 passed` matrix.

## Reviewed Corrections

- criteria, criterion, criteria-revision, decision and decision-revision identities are
  Core-owned and globally closed;
- caller-supplied criterion IDs are rejected even on initial criteria creation;
- one active criteria set governs a Question, and one stable decision owns a
  Question-Paper pair;
- every decision consumes the exact current criteria revision/digest and records that
  revision in its transaction input references;
- criteria successors project dependent decisions and existing mappings as stale without
  rewriting either record;
- factual mapping is unchanged when no criteria exist and requires `current + included`
  only when active criteria exist;
- Catalog status-label filters are cursor-bound, and exact decision detail reloads current
  workspace context before reporting freshness;
- Guardian reports unavailable or stale decisions while retaining append-only history.

## Artifacts

- wheel: `research_kb_core-0.1.0-py3-none-any.whl`
  - size: `397852`
  - SHA256: `97518abb4b3a5f0d120a03575a4ea15039063e205a3222ae588f146133f888a9`
- sdist: `research_kb_core-0.1.0.tar.gz`
  - size: `706195`
  - SHA256: `9f5ee2bb5cb38862276e047706312c977ee7d966c356509e196e5132719c45db`

These are pre-merge validation artifacts. The App must pin a wheel rebuilt from the exact
merged Core head, not these branch artifacts.

## Boundaries

- no Agent proposal task or localhost screening work surface;
- no scientific credibility scoring, Evidence creation or mandatory Library screening;
- no private scientific workspace, legacy question data or real PDF access;
- no migration, cutover, Research Synthesis, Exchange or retained-workspace cleanup.
