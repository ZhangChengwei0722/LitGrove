# Changelog

All notable user-visible changes to this project will be documented in this file.
The format follows Keep a Changelog, and package versions follow Semantic Versioning as
defined in `docs/release-policy.md`.

## [0.1.1] - 2026-08-14

### Added

- Public repository governance, required cross-platform CI, security reporting, dependency
  update automation, and contribution templates.
- Apache License 2.0 project licensing and public support, contribution, and release policy.
- Risk-based L0-L4 validation, exhaustive test-shard reconciliation, machine-readable
  receipts, and a stable aggregated Windows CI gate.
- Build-once release candidates, strict accepted-byte publication authority, immutable
  release tags, CycloneDX SBOM generation, and same-byte GitHub/PyPI reconciliation.

### Changed

- Froze the public package version at `0.1.1`, Application Service interface `1.23`,
  workspace layout `p7d-1`, and CPython `3.11-3.12` support.
- Documented Windows 11 x64 as the required live acceptance platform and Linux x64 as
  CI-validated Core compatibility; macOS remains best-effort and unaccepted.

### Fixed

- Separated successful release-candidate bytes from failed-run diagnostics so a failed
  candidate cannot be mistaken for an accepted package.
- Bound publication to the authenticated release actor, exact tag ref, workflow run and
  attempt, protected source commit, GitHub artifact digest, distribution digests, and
  Trusted Publisher tuple.

### Known limitations

- This package is deterministic Core/CLI infrastructure, not the end-user GUI product.
- It does not embed an LLM, migrate private workspaces, or claim hostile-PDF sandboxing,
  process/network isolation, macOS acceptance, or physical sleep/resume support.
- Real PDF parsing is limited to trusted local files and requires the `pdf` extra.
