# P7-A Research Organization Kernel Validation Receipt

- status: `passed`
- validated_at: `2026-08-03`
- implementation_commit: `1175adc`
- baseline: `origin/main@d37f42540a683b7c02b624ef04a0fbd1dc8c5fce`
- branch: `feature/p7a-organization-kernel`
- merge_commit: `0b32c80d3d8791eaffda69d638c348f062691dfa`
- application_service_interface: `1.11`
- catalog_registry: `1.1`
- layout_contract: `p7a-1`

## Validation

- full Windows pytest: `948 passed, 4 expected POSIX permission skips`
- focused P7-A, Guardian, Catalog and privacy tests: `90 passed`
- `compileall`: passed
- repository privacy scan: `0 unexpected findings`
- `git diff --check`: passed
- base installed-wheel smoke: passed, `pip check` clean
- PDF-extra installed-wheel smoke: passed, `pip check` clean

## Post-Merge Validation

- exact merge head: `main@0b32c80d3d8791eaffda69d638c348f062691dfa`
- full Windows pytest: `948 passed, 4 expected POSIX permission skips`
- focused P7-A, organization, Question compatibility, workspace, capability and
  Portable Skill tests: `98 passed, 2 expected POSIX permission skips`
- `compileall`: passed
- repository privacy scan: `7 expected findings, 0 unexpected findings`
- `git diff --check`: passed
- base installed-wheel smoke: passed after correcting stale smoke expectations from
  layout `p4c-1` / Application Service `1.10` to `p7a-1` / `1.11`
- PDF-extra installed-wheel smoke: passed

## Artifacts

- wheel: `research_kb_core-0.1.0-py3-none-any.whl`
  - size: `361888`
  - SHA256: `869224c046431fc57b06b08290f119b75dbea2a252c2d95738b8bf56f2326426`
- sdist: `research_kb_core-0.1.0.tar.gz`
  - size: `643314`
  - SHA256: `3f80badae3d74179f4e50755757f5dca98447cb5a44da407cf8bc6a4451679ec`

These artifact hashes were built from exact merge head `0b32c80`; artifacts are
temporary validation outputs and are not committed.

## Boundaries

- no legacy scientific workspace, private workspace or real PDF access;
- no Agent Task registry or App mutation route;
- no Tags, Screening or Research Synthesis implementation;
- no migration, cutover or cleanup.
