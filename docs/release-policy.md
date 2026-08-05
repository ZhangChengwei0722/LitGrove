# Release Policy

## Versioning

The Python package follows Semantic Versioning. Tags use `vMAJOR.MINOR.PATCH`; the package
metadata uses the same version without the `v` prefix.

While the package is below `1.0.0`, incompatible public changes increment `MINOR`. A `1.x`
release treats incompatible changes to documented Python APIs, CLI behavior, schemas,
serialized contracts, stable IDs, exit codes, or supported workspace compatibility as a
`MAJOR` change. Compatible functionality increments `MINOR`; compatible fixes increment
`PATCH`.

Internal schema, interface, and workspace-layout versions remain explicit contract versions.
A package version does not replace those identifiers.

## Compatibility And Deprecation

- Every incompatible change requires an approved design, a migration or fail-closed
  compatibility path, targeted tests, the complete high-risk validation level, and a
  `CHANGELOG.md` entry.
- A public feature should be deprecated in one minor release before removal when a safe
  compatibility period is technically possible.
- Security, privacy, data-integrity, or source-immutability defects may require immediate
  removal. The release notes must state the reason and the affected versions.
- Acceptance thresholds cannot be relaxed after a failing release candidate merely to make
  the release pass.

## Release Gates

A release candidate must be built from a protected `main` commit and must have:

1. all required branch checks passing on the exact commit;
2. a clean source tree and an approved version plus changelog update;
3. successful wheel and sdist builds and installed-wheel smoke tests;
4. successful CLI, privacy, Guardian, and applicable compatibility validation;
5. a successful dependency review and vulnerability audit;
6. a CycloneDX JSON SBOM and SHA-256 digests for released artifacts;
7. release notes naming compatibility, migration, platform, and known-limit impacts.

The tag is created only after the release commit passes these gates. Moving or reusing a
published tag is prohibited.

## Distribution And Provenance

GitHub Releases are the first supported release record. Publishing to PyPI is disabled until
a separate reviewed workflow establishes the project identity, trusted publishing through
OIDC, environment protection, and artifact attestations. Do not use a long-lived PyPI token.

Release automation must build artifacts from the tagged source rather than upload locally
built files. The release record must retain the source commit, checksums, SBOM, build
environment, and provenance/attestation links.

## Support

Before the first stable release, security fixes target current `main` unless a release note
explicitly establishes a supported maintenance line. Supported versions and disclosure
expectations remain authoritative in `SECURITY.md`.
