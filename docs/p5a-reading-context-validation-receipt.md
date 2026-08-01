# P5-A Reading Context Validation Receipt

- status: `passed`
- validated_at: `2026-08-02T03:57:15+08:00`
- implementation_commit: `aeefc3eaf3a4e006e0472bd45e68933706bc3719`
- implementation_tree: `d9a99324fc77b6936636ef5870da657e6fdef11d`
- branch: `feature/p5a-reading-context`
- baseline: `61d0ee904fcb3b6efdec27d5e8eec6aa2ce6e286`
- package: `research-kb-core==0.1.0`
- application_service_interface: `1.7`
- wheel_sha256: `e17ad7d1de2c91bb044f436fa196d0428d02a7fbd3b144e3e71233d25e498753`
- sdist_sha256: `ba24b60c9a9905c81176d206cb021957b4b4465cb28aaf1ac6661d1edb14ec56`
- fixture_scope: `synthetic sources and generated workspaces only`

## Validation Matrix

| Check | Result |
|---|---|
| final focused reading/application regressions | `28 passed` |
| active-head live-digest regressions | `3 passed` |
| complete Windows suite | `881 passed, 4 expected POSIX skips` |
| `compileall` | passed |
| source build of sdist and wheel | passed |
| isolated installed-wheel base smoke | passed |
| isolated installed-wheel PDF-extra smoke | passed |
| package version | `research-kb 0.1.0` |
| privacy scan | `7 expected, 0 unexpected` |
| `git diff --check` | passed |

## Behavior Evidence

- Primary reads return the complete seven-section Paper Card and explicit Unit status
  admissibility without creating a record or filesystem side effect;
- Review reads retain their complete reusable-unit content while remaining explicitly
  background-only and ineligible for canonical Evidence;
- Evidence IDs resolve to exactly one owning Primary revision and retain that revision's
  source fingerprint, parse run, page, locator and quote binding;
- missing or changed source bytes do not erase committed semantic content, but current
  trace-back and factual eligibility are disabled;
- a same-digest relink remains current, while a live-changed active source cannot hide
  behind an older exact copy;
- ordered comparisons accept two to four unique paper IDs and perform no synthesis;
- App-facing values contain no local path, source reference, fingerprint or parsed page
  body.

## Scope Notes

No canonical schema, workspace layout, operational record, Agent Task kind, PDF byte
streaming, PDF.js/UPDF integration, private workspace, real PDF, migration or legacy
cutover was added or accessed.
