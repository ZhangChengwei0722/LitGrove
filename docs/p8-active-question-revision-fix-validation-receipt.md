# P8 Active Question Revision Fix Validation Receipt

- status: `passed`
- validated_at: `2026-08-04`
- baseline: `main@0142e6d96595796a525e0f79ebae157017c44e8d`
- branch: `fix/p8-active-question-revision`
- affected_surface: `Research Synthesis support closure`

## Defect And Fix

`derive_support_closure` indexed raw legacy `question-mapping` rows instead of resolving
the active record. When a P7 `question-revision-bundle` superseded a stale legacy mapping,
current Research Synthesis work could therefore be rejected as stale.

The Question index now uses the canonical active-record resolver. The regression fixture
makes the legacy mapping genuinely stale before creating the current successor and proves
that support closure binds to the active revision while concurrent later revisions still
reject stale submission.

## Validation

```text
unit A-M:                446 passed, 2 skipped
unit N-Z:                417 passed, 2 skipped
contract + integration:  141 passed
privacy + benchmark:       20 passed
aggregate:               1024 passed, 4 skipped
```

The four skips are the expected POSIX permission contracts on Windows.

Additional checks:

- `compileall src tests`: passed;
- all 49 `schemas/1.0/*.json` files parse as JSON: passed;
- `git diff --check`: passed;
- package build: passed;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed.

## Candidate Artifacts

- wheel SHA256: `74ba76af5d5749bc1526b79dd2c11b25080bedd3c1d2dddf4b56bc71b39245b8`
- wheel size: `416967` bytes
- sdist SHA256: `20e5f81e5e7f7e736792427637f0a772d470b0a26366ec39f9cbb6cde94f989b`
- sdist size: `738153` bytes

These are pre-merge candidates. The App must pin a wheel rebuilt from the exact merged
fix head.

## Boundaries

- no contract, schema, ID, store or workspace-layout change;
- no private scientific workspace or real PDF access;
- no compatibility rename of internal `step7-*` identifiers;
- no migration, cutover, embedded Agent runtime or deployment.
