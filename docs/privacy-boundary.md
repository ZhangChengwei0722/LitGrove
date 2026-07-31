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
