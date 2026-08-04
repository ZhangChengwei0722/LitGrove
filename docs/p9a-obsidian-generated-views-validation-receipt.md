# P9-A Obsidian Generated Views Validation Receipt

- status: `passed`
- validated_at: `2026-08-04`
- baseline: `main@050aa51`
- branch: `feature/p9-obsidian-generated-views`
- application_service_interface: `1.17`
- fixture_scope: `synthetic_from_scratch`

## Delivered Contract

P9-A adds one deterministic, one-way generated Obsidian projection under the workspace
`knowledge/views/obsidian` boundary. Core owns immutable complete generations, one atomic
active manifest, exact per-view dependencies, source watermarks, current/stale projection,
and explicit edited-file rejection. Generated Markdown remains disposable and cannot become
canonical scientific input.

The maintained view set covers Home, Papers, Reviews, Directions, Questions and
`Research Synthesis / 科研综合与启发`, plus the closed optional `library_summary` and
`question_coverage` tables. User-visible naming is `Research Synthesis`; internal `step7`
commands, record kinds, stores and classes remain compatibility identifiers.

## Targeted Validation

```text
tests/unit/test_obsidian_generated_views_application_service.py
-> 20 passed
```

The targeted suite covers deterministic generation and exact rerun, manifest validation,
complete rebuild, optional-table removal, per-view dependency scope, affected-only stale
projection, edited and unknown managed-file blocking, explicit user discard, untrusted
Markdown/link/path escaping, unsafe filesystem links, CLI/service equivalence, pagination,
Guardian/Catalog isolation and Unicode rendering.

## Full Windows Validation

The suite was executed in bounded shards because the command host terminates a single
process after ten minutes. The shards cover the same complete repository test collection.

```text
unit A-M:                 451 passed, 2 skipped
unit O-P:                 154 passed
unit Q:                    38 passed
unit R:                    98 passed
unit S:                    70 passed
unit T-W:                  77 passed, 2 skipped
contract + integration:   141 passed
privacy + benchmark:       20 passed
aggregate:               1049 passed, 4 skipped
```

The four skips are the expected POSIX permission contracts on Windows.

Additional checks:

- `compileall src tests`: passed;
- package build: passed;
- `research-kb --version`: `0.1.0`;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed;
- final privacy scan: `7 expected / 0 unexpected`;
- final `git diff --check`: passed.

## Candidate Artifacts

- wheel SHA-256: `9502a7799495eb738ad9e27fdd69e02398839c29ce65c9aab462227b3ba2f76d`;
- wheel size: `434297` bytes;
- sdist SHA-256: `0a0ed39f21f3cc24f7f1b8ba70aff4625baa82201c39c51f26b4f4e2bff15a9a`;
- sdist size: `765382` bytes.

These are pre-merge candidates. P9-B must pin a wheel rebuilt from the exact merged P9-A
head and record that commit and digest separately.

## Diff Review

The final review found no unresolved correctness or boundary issue. In particular:

- manifest and generation digest derivation is acyclic and canonical;
- the active manifest is replaced only after a complete generation verifies;
- unchanged views retain bytes and render timestamps while changed dependencies stale only
  their consuming views;
- a managed-file edit or unknown file cannot be overwritten without explicit `user`
  discard authority;
- logical paths use a closed allowlist and filesystem link/reparse traversal fails closed;
- generated files contain no source paths, raw parsed pages, executable embeds or canonical
  mutation authority;
- generated views do not change Guardian findings or Catalog source watermarks.

Two pre-existing documentation files were adjusted only to replace a private project marker
with `private scientific workspace`. The privacy-safe wording preserves their meaning and is
part of this focused P9-A diff.

## Boundaries

- no private scientific workspace, real PDF or real Obsidian vault access;
- no reverse sync, Markdown import, Exchange, citation graph or embedded Agent runtime;
- no schema migration, legacy cutover, deployment or desktop wrapper;
- no generated-artifact cleanup before P11 or overall closure.
