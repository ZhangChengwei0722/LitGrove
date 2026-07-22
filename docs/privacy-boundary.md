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
