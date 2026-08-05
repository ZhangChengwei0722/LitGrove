# Pilot exFAT Publish Remediation Validation Receipt

- date: `2026-08-05`
- defect: `PILOT-DEFECT-001`
- status: `candidate_validated`
- base: `d646c052e6a62c237e6392b9832555f571e2428c`
- application_service_interface: `1.18` unchanged
- fixture_scope: `synthetic_from_scratch`

## Candidate Behavior

- hard links remain the preferred create-only publication path;
- Windows `winerror` `1` and `50` alone permit same-directory `os.rename` fallback;
- destination races, rename failures, unexpected hard-link failures and non-Windows hosts
  fail closed without overwrite;
- publication revalidates staged identity and final identity plus SHA-256;
- an owning Pipeline Job can publish its already receipted partial through `resume()`
  without receiving the original upload stream again;
- an existing changed final source remains on the established source-change path and is not
  mistaken for a recoverable missing publication.

## Validation

Focused regression:

```text
7 passed
```

Complete synthetic Windows population, executed as exhaustive stable shards:

```text
unit A:                              52 passed
unit B-C:                           159 passed
unit D-F:                           166 passed
unit G-M:                           123 passed, 2 skipped
unit N-P:                           166 passed
unit Q-S:                           206 passed
unit T-Z:                            77 passed, 2 skipped
contract/integration/privacy/bench: 166 passed
aggregate:                         1115 passed, 4 skipped
```

The four skips are the expected POSIX permission contracts on Windows. An earlier coarse
`unit A-F` command did not complete within its 15-minute executor timeout and was not
reported as passed or failed; the smaller exhaustive shards above replaced it.

Additional checks:

- `compileall src tests`: passed;
- package build: passed;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed;
- version smoke: `research-kb 0.1.0`;
- privacy scan: `7 expected findings, 0 unexpected findings`;
- `git diff --check`: passed.

## Candidate Artifacts

- wheel: `research_kb_core-0.1.0-py3-none-any.whl`
  - size: `490509` bytes
  - SHA-256: `a2e8cfcf59d2cc56dc431fbb0f0dc8ab3cea3d978aebf75e4d8daab0bf0596df`
- sdist: `research_kb_core-0.1.0.tar.gz`
  - size: `849443` bytes
  - SHA-256: `e7e33fd2fc06cc93847e7c2ddb627a72a9d0947be4786882a1dfff858611c2f3`

These are pre-merge candidate artifacts and are not the Pilot runtime identity. The exact
wheel must be rebuilt from the merged head before the interrupted diagnostic Job is
resumed.

## Remaining Gate

Candidate validation does not close the defect. Closure requires an exact merged-package
rebuild, compatibility-pin refresh, execution-manifest revision and a real exFAT diagnostic
replay that removes the operation-owned partial, reaches the semantic gate and leaves no
related Guardian finding. Untouched acceptance cases remain blocked until that replay
passes.
