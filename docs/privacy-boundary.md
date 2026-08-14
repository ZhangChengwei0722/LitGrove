# Privacy Boundary

## Allowed

- Core and CLI source;
- public schemas and templates;
- system documentation and Agent protocol;
- tests and fully synthetic fixtures.

## Prohibited

- real or parsed paper content;
- real evidence, Paper Cards, questions, discovery reports/candidates, candidate insights, manuscripts, manuscript projections, manuscript audit reports, or research notes;
- credentials, authorization files, tokens, or institution-restricted content;
- local absolute paths, usernames, or a private directory inventory;
- private workspace exports or disguised copies.

## Enforcement

The privacy scanner checks normal repository files, fixtures, and build artifacts. Intentional negative fixtures are exact-file allowlisted and must produce only their declared findings.

External Review Agent handoffs use the same explicit content-class intersection as other
Agent Tasks. Required payload classes are metadata, bounded parsed excerpts and
operational context. Existing Review background is optional and appears only when the
workspace policy, task definition, executor and current user approval all allow
`review_background`. Source paths, fingerprints, credentials, unbounded source documents
and unrelated workspace content are never included. PDF text and Agent output remain
untrusted data and cannot expand Task authority.

## History, License And NOTICE

- Reachable Git history, the selected release tree, package inputs, and generated release
  metadata are subject to the same privacy and private-path review. A finding in history is
  not cleared merely because the current tree is clean.
- This repository is distributed under the Apache License 2.0. Contributions and generated
  attribution records must remain compatible with that license and must not introduce
  institution-restricted or private material.
- `NOTICE` is conditional: it is added only when a recorded attribution review shows that an
  additional notice is required. The absence of `NOTICE` means only that the reviewed inputs
  did not require one; it is not permission to copy third-party or private content.
