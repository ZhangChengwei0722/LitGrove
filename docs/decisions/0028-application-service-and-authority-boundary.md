# 0028 Application Service And Authority Boundary

Status: accepted

## Context

The accepted product is a localhost workspace application backed by Shared Core. The existing CLI already exposes deterministic capabilities, but several commands still compose reads or result policy directly. Building an App against CLI subprocess output or duplicating those rules would create two authorities.

## Decision

### Repository and ownership

- Shared Core and the future App use separate repositories and release versions.
- Shared Core owns contracts, IDs, workspace/config loading, source references, transactions, provenance, validation, deterministic pipeline stages, projections, rendering primitives and Guardian rules.
- The App owns local interaction, preview, selection and visualization. It does not own scientific records or reproduce Core invariants.
- CLI and App backend call the same application-service facade. The CLI becomes a thin adapter for arguments, bounded stdin/file decoding, output serialization and process exit codes.
- An App release pins a compatible Core interface/version. It cannot vendor a second copy of business rules.

### Storage authority

| Layer | Authority | Rule |
|---|---|---|
| canonical scientific records | Shared Core contracts and transactional services | Versioned, provenance-bearing, revisioned; never projection-only. |
| operational records | Shared Core operational services | Jobs, tasks, events, receipts, staging and recovery state never masquerade as scientific truth. |
| projections/read views | Rebuildable Core projection/render services | Disposable; deletion and rebuild cannot lose durable knowledge. |

Only one writer lease exists per workspace. Browser requests, CLI invocations and external Agents do not bypass that lease.

### Workspace session loading

The App backend opens a workspace through one Core service using a user-selected root. The service validates marker/config, contract/layout compatibility, source roots and path confinement, then returns an opaque session-bound handle and redacted display metadata. Browser clients never submit arbitrary filesystem paths after the session is established.

The App must not parse workspace configuration independently, cache unchecked roots, follow undeclared links, or construct storage paths from record IDs.

### Localhost security

The first App release:

- binds to loopback only and chooses an available port automatically;
- generates an in-memory high-entropy startup token and exchanges it for a `Secure`-where-applicable, `HttpOnly`, `SameSite=Strict` session cookie;
- accepts only the exact startup origin and rejects wildcard CORS, foreign `Origin`/`Host`, and missing anti-CSRF proof on mutations;
- keeps filesystem roots and authorization tokens out of URLs and browser storage;
- uses CSP, escaped text, sanitized Markdown and no inline arbitrary script;
- exposes explicit normal shutdown and releases writer/session handles on exit.

Loopback is a transport boundary, not authentication by itself.

### Mutation authority

Deterministic App actions may invoke Core services directly. Semantic Agent output always follows:

```text
Agent result -> confined staging -> Core validation -> App preview
-> explicit user approval -> transactional Core commit
```

Staging is not canonical state. The App may display structural/provenance checks but cannot label semantic content as human-verified unless the user performs that action.

## Consequences

- P1 extracts the service facade and preserves the P0 characterization matrix.
- P2 may build a read-only App against that facade but cannot invent missing schema or write paths.
- Embedded Agent execution is not required; Codex and Claude remain external workers through portable handoff contracts.
- No App repository, server or workspace session runtime is created by this ADR.

## Rejected Alternatives

- App business logic duplicated from CLI handlers.
- Browser calls arbitrary CLI commands or supplies arbitrary local paths.
- SQLite/FTS or rendered Markdown as a second canonical store.
- Loopback binding without session, origin and CSRF controls.
