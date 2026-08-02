# P7-A Research Organization Kernel Validation Receipt

- status: `passed`
- validated_at: `2026-08-03`
- implementation_commit: `1175adc`
- baseline: `origin/main@d37f42540a683b7c02b624ef04a0fbd1dc8c5fce`
- branch: `feature/p7a-organization-kernel`
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

## Artifacts

- wheel: `research_kb_core-0.1.0-py3-none-any.whl`
  - size: `361888`
  - SHA256: `869224c046431fc57b06b08290f119b75dbea2a252c2d95738b8bf56f2326426`
- sdist: `research_kb_core-0.1.0.tar.gz`
  - size: `642546`
  - SHA256: `28f7a6e40a1622b2041a7d8737e7072ce7bfbeb266772c87b937ecbcd5e422f7`

Artifacts are temporary validation outputs and are not committed.

## Boundaries

- no legacy scientific workspace, private workspace or real PDF access;
- no Agent Task registry or App mutation route;
- no Tags, Screening or Research Synthesis implementation;
- no migration, cutover or cleanup.
