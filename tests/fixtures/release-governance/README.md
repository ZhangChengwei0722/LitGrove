# Release governance fixtures

These cases are `synthetic_from_scratch` contract inputs. They contain no
repository, package, user, credential, or publication data. The contract test
materializes deterministic wheel, sdist, installed `RECORD`, and capability
bytes in a temporary directory, then applies the named mutation from the JSON
case roster.

The verifier binds five identities:

- protected source commit, workflow run plus exact workflow run attempt, publishable package version,
  canonical run/attempt-bound artifact name, and candidate-scoped cache;
- wheel and sdist SHA-256 plus canonical archive-member manifests;
- installed distribution metadata, `RECORD`, payload, measured CPython runtime, Requires-Dist,
  isolated install-root, and capability output;
- a future R1-B publication tuple containing an external immutable authority manifest, accepted run
  and attempt, exact artifact ID and name, commit, final-version tag, digests, protected `pypi`
  environment, and exact Trusted Publisher workflow.

The R1 publication workflow accepts only a canonical authority manifest supplied by the authenticated
dispatch and verifies it before artifact download, OIDC, or writes. A downloaded candidate may create
an activation only after its bytes agree with that external authority; it cannot create its own
authority. Partial or unknown public states remain fail closed until same-byte recovery succeeds.

The read-only `scan-history` contract walks reachable refs, commits, tree blobs, and blob bytes with
Git read commands only. It reports only `{path, type, blob}` findings and never matched content. Its
explicit expectation manifest freezes the reachable ref boundary and exact historical findings:
`historical_boundary` is reserved for public private-question boundary statements that say access was absent, while
`credential`, `private_path`, `pdf`, and `binary` findings are unexpected unless explicitly listed.
Extra or missing findings and any ref drift fail closed.
The synthetic history contract models the current expected boundary as 15 findings: 14 public
access-absent markers and one synthetically assembled PDF magic-byte format check, with zero credential or private-path
findings.

Every negative case must fail closed. In particular, package version equality
does not make substituted bytes acceptable, a successful child or workflow step never supplies
publication authority by itself, and an activation/downloaded manifest cannot substitute for the
external expected authority manifest.
