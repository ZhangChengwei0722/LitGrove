# 0032 Exchange Security And Portable Serialization

Status: accepted

## Context

Knowledge workspaces must be exchangeable across users and operating systems, optionally including PDFs, without treating an archive digest as sender trust or allowing imported content to become local canonical truth automatically.

## Decision

### Export authority and allowlist

Exchange supports four explicit selection scopes: selected records, selected papers, selected Question/project scope, and whole workspace. Source/PDF inclusion is a separate explicit choice subject to recorded rights status.

The allowlist includes only selected canonical records, required provenance/identity records, compatibility metadata, receipts needed to verify the export, and explicitly approved source assets. It excludes Agent prompts, full task payloads, report-only output, logs, local absolute paths, credentials and projection databases by default.

Before writing, dry run reports file count, PDF count, estimated bytes, missing sources and rights state. Over-budget exports are split or rejected. Archive creation is all-or-nothing; failure leaves no partial final archive.

### Trust and identity

- Cross-workspace identity is namespaced by `origin_workspace_id + origin_record_id`.
- External Paper Card/Evidence conflicts remain immutable external-origin records. They are not local active canonical records and do not support factual query/synthesis until local review and approved promotion create a local revision.
- External `verified` or `human_checked` is retained as an external claim; local review state starts unreviewed.
- Archive/per-entry digests prove byte integrity, not sender identity. An unsigned bundle has unverified origin and review claims.

### Safe archive and import staging

Export and import reject absolute paths, `..`, duplicate normalized paths, case-fold collisions, links/reparse points, device names, nested archives beyond policy, undeclared files and size/count/decompression limits. Entries use POSIX bundle paths and extract only into a newly created confined staging directory.

Import validates outer digest, per-entry digest, compatibility, contract, identity/trust, rights and source inventory before preview. User approval precedes one transactional commit. Failure produces zero partial canonical import.

### Canonical serialization

Portable records use:

- UTF-8 without BOM;
- LF line endings;
- UTC RFC3339 timestamps;
- canonical JSON with deterministic key ordering and number/string encoding;
- POSIX `/` bundle paths;
- explicit contract, serialization and compatibility versions.

### Compatibility behavior

| Bundle state | Behavior |
|---|---|
| supported | normal validated read/import |
| newer but declared safe-compatible | read-only inspection only |
| migration required | block writes and require a separately approved migration |
| unknown or incompatible | fail closed |

### Backup is not Exchange

Default backup is inventory-only for external sources. Source-inclusive backup/export is separately explicit. Backup preserves one workspace's recoverable state; Exchange applies origin/trust/conflict policy and never inherits local authority.

## Consequences

- P10 owns Exchange contracts/runtime after a separate bounded design.
- P0 materializes no Exchange conflict records or archives.
- Citation/reference graphs and related-paper navigation remain a post-R2 extension, not an Exchange side effect.
