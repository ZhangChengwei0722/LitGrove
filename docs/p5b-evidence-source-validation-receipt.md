# P5-B Evidence Source Access Validation Receipt

- status: `passed`
- validated_at: `2026-08-02T06:40:22+08:00`
- implementation_commit: `64c6a1528bb212daae3e5d903229277321328f17`
- implementation_tree: `4462af44a4312531492ae4f19a98a84a7e96d68a`
- branch: `feature/p5b-evidence-source`
- baseline: `4f35f3236bccca50d5b4f4b0345f1ddc235bb92a`
- package: `research-kb-core==0.1.0`
- application_service_interface: `1.8`
- wheel_sha256: `cd4ec76e89a518027307e68b4331fe9a1ccf8b12fb3261b3bc4a01a6e6bd7a23`
- sdist_sha256: `fdd730368a7897f9bb3319524e6074055302121c451cc4eff1e9f5056050c009`
- fixture_scope: `synthetic sources and generated workspaces only`

## Validation Matrix

| Check | Result |
|---|---|
| final focused source/application regressions | `23 passed` |
| complete Windows suite | `891 passed, 4 expected POSIX skips` |
| `compileall` | passed |
| source build of sdist and wheel | passed |
| isolated installed-wheel base smoke | passed |
| isolated installed-wheel PDF-extra smoke | passed |
| package version | `research-kb 0.1.0` |
| privacy scan | `7 expected, 0 unexpected` |
| `git diff --check` | passed |

## Behavior Evidence

- one Evidence ID resolves through exactly one legacy or revisioned Primary provenance
  owner and remains bound to its own revision, source fingerprint, PDF page and locator;
- a backend-only handle is bound to the workspace identity, Evidence/revision digests and
  one exact provenance source ref;
- every open reloads canonical records, rechecks handle lineage and source membership,
  rejects changed or unavailable bytes, and hashes the same descriptor later returned;
- regular-file identity, single-link/reparse safety, a `512 MiB` size ceiling and the PDF
  header are enforced before streaming authority is returned;
- active, historical and same-digest relinked sources remain accessible only when their
  exact registered bytes are available;
- changed Evidence, wrong-workspace handles, duplicate ownership, forged refs, missing or
  unsafe files, non-PDF content, oversized files and digest mismatches fail closed;
- browser-facing descriptors contain no path, portable source ref or source fingerprint,
  and covered source/knowledge trees remain byte-identical.

## Scope Notes

No canonical schema, workspace layout, operational record, source mutation, HTTP/Range
surface, browser token, PDF.js/UPDF integration, private workspace, real paper PDF,
migration or legacy cutover was added or accessed.
