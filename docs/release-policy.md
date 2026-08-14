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

A release candidate must be prepared from one exact protected `main` commit and must have:

1. all required branch checks passing on the exact commit;
2. a clean source tree and an approved version plus changelog update;
3. successful wheel and sdist builds and installed-wheel smoke tests;
4. successful CLI, privacy, Guardian, and applicable compatibility validation;
5. a successful dependency review and vulnerability audit;
6. a CycloneDX JSON SBOM and SHA-256 digests for released artifacts;
7. release notes naming compatibility, migration, platform, and known-limit impacts.

The release commit must remain the exact commit used for the accepted build. The immutable
tag is created only after the exact artifact bytes pass these gates; moving or reusing a
published tag is prohibited.

## Build-Once Transaction

Release preparation is one transaction across a protected source commit and immutable
artifact bytes:

1. Check out the exact protected `main` commit and build the wheel and sdist once.
2. Record the source commit, artifact names, SHA-256 digests, SBOM, build environment, and
   provenance inputs for that build.
3. Accept the exact bytes by digest. A filename, version, or successful rebuild is not a
   substitute for byte acceptance.
4. Create the immutable `vMAJOR.MINOR.PATCH` tag at the same protected commit only after the
   accepted bytes are known.
5. Publish only those accepted bytes. The publication step downloads and verifies them; it
   never rebuilds from the tag source or replaces them with locally built files.

Any commit, digest, run, tag, or manifest mismatch stops the transaction before external
publication. The R1 publisher runs only from the immutable accepted tag and consumes the
exact accepted candidate artifact; it never treats workflow registration or a successful
rebuild as publication authority. Before any artifact download, the authenticated dispatch
must supply the canonical external R1-B authority manifest and its SHA-256. That authority
binds the exact workflow run and attempt, source commit, artifact ID/name/service digest,
wheel/sdist digests, tag, actor and Trusted Publisher tuple. Downstream jobs download by
artifact ID with digest mismatch configured as a hard failure and check out the accepted
commit rather than resolving the tag again. Before download, the publisher also confirms
that the live protected-branch required-check set is the reviewed six-check allowlist and
that every required check completed successfully on the accepted commit. The GitHub Release
and PyPI jobs each resolve the tag to that commit again immediately before their external
write authority is used.

## Reproducible Dependency Locks

The `runtime`, `pdf`, `build`, `test`, and `audit` profiles are resolved separately for
CPython 3.11 and 3.12 on native `win_amd64` and `linux_x86_64` runners. Lock tooling is
installed from `tools/release-lock-bootstrap.txt` with hashes before resolution. Generation
uses the backtracking resolver, binary-only artifacts, hashes, stripped extras, LF output,
and no index or annotation header; the `audit` profile explicitly includes unsafe package
pins so its required `pip` version is hashed. A second generation for the same tuple must be
byte-identical.

CI disables ambient pip configuration, user site packages, and generic caches. It installs
the selected native lock with `--require-hashes --no-deps --only-binary=:all:` and builds with
the exact build profile and `--no-isolation`. A Windows lock cannot establish Linux identity,
and a lock from one CPython minor cannot substitute for another tuple.

## Distribution And Provenance

GitHub Releases are the primary public release record. PyPI publication uses the separately
reviewed `publish-accepted-release.yml` workflow, a protected `pypi` environment and OIDC
Trusted Publishing. Long-lived PyPI tokens are prohibited.

Release automation must retain the accepted source commit, checksums, SBOM, build
environment, and provenance/attestation links. It must upload the accepted bytes from the
build-once transaction and must not perform a tag-source rebuild. Its always-run
reconciliation fails closed for tag-only, GitHub-only, PyPI-only or unknown public states.
Both lightweight and annotated immutable tags are resolved to their final commit before
reconciliation; a tag object that does not resolve to the accepted commit is rejected.

## Support

Before the first stable release, security fixes target current `main` unless a release note
explicitly establishes a supported maintenance line. Supported versions and disclosure
expectations remain authoritative in `SECURITY.md`.
