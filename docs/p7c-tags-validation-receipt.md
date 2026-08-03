# P7-C Tags Validation Receipt

- status: `passed`
- validated_at: `2026-08-03`
- baseline: `main@ee30b53ea166a72a63cda8296c6659633586c5c9`
- branch: `feature/p7c-tags`
- application_service_interface: `1.13`
- catalog_adapter_registry: `1.2`
- catalog_schema: `3`
- layout_contract: `p7c-1`

## Validation

- complete Windows suite: `993 passed, 4 expected POSIX permission skips`;
- focused Tag, transaction and Catalog matrix: `72 passed` plus `33 passed` after
  the lock-precondition review;
- real concurrent duplicate Tag and assignment tests: passed;
- `compileall src tests`: passed;
- repository privacy scan: `7 expected findings, 0 unexpected findings`;
- `git diff --check`: passed;
- package build: passed;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed.

## Reviewed Corrections

- non-Paper assignment validation now accepts canonical Direction, Field Map Entry and
  Question bundles plus the legacy-compatible Question representation;
- caller-supplied Tag IDs cannot create identities, and revisions require the current head;
- normalized vocabulary and assignment identity are rechecked inside the workspace lock;
- assignment commit also rechecks current Tag status and target availability inside the lock;
- Tag-link revision digests bind Tag, target kind, target ID and state;
- duplicate revision IDs and ambiguous active heads fail closed;
- archived Tags permit exact replay of an existing assignment but reject a new assignment;
- Catalog schema upgrades rebuild the disposable projection explicitly, while corrupt,
  wrong-workspace, newer or contract-incompatible databases fail closed;
- Tag facet count and ordered digest are verified, incremental refresh avoids the prior
  per-item N+1 query, and Registry-only delta fails closed when facets cannot be preserved.

## Artifacts

- wheel: `research_kb_core-0.1.0-py3-none-any.whl`
  - size: `383570`
  - SHA256: `4fb01fd783e6e770c6a1cc5f2b2d59f9e68ac7fdd0e26812168e9b3eb2404eee`
- sdist: `research_kb_core-0.1.0.tar.gz`
  - size: `686231`
  - SHA256: `23a06125d0de982513fbfd344a87208db028a8a5004b3f070d549499883ea3e0`

These are pre-merge validation artifacts. The App must pin a wheel rebuilt from the exact
merged Core head, not these branch artifacts.

## Boundaries

- no private scientific workspace, legacy question data or real PDF access;
- no automatic or Agent-inferred Tags;
- no Unit, Evidence, Review Memory or Research Synthesis tagging;
- no Screening, Exchange, migration, cutover or retained-workspace cleanup.
