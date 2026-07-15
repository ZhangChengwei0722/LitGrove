# ADR 0008: Explicit Read-Only Compatibility Adapters

- status: accepted_for_m2a_2

## Decision

Shared Core exposes a generic `LegacyReaderAdapter` protocol and a read-only compatibility inspection service. Adapters are supplied explicitly by a private in-process composition caller. The installed CLI starts with an empty adapter registry and provides no import-string loading, entry-point scan, plugin discovery, or production private adapter.

Every adapter declares protected inputs as `root_role + relative_path`. Core resolves those references through the initialized workspace, fingerprints regular files or deterministic directory trees before inspection, and repeats the fingerprints in a `finally` path. Nested links and reparse points fail closed. If a protected input changes, disappears, changes type, or becomes unsafe, `RKBC-026` and exit code `4` take precedence over an ordinary adapter error.

Adapters return bounded inventory and difference candidates rather than raw legacy records. Core normalizes identities and JSON pointers, computes deterministic `diff_sha256_*` IDs, applies mandatory severity and blocking policy, rejects duplicate identities or differences, validates public schemas, and emits exactly one report to stdout. Reports contain structured source references and value digests only; they contain no absolute paths or unrestricted scientific payload.

## No Persistence Or Migration

Compatibility inspection creates no report file, canonical or candidate record, process event, transaction journal, Guardian report, directory scaffold, or replacement ID. It does not modify authority or screening state. A valid report describes compatibility; it does not perform migration and does not deprecate a legacy source of truth.

Private adapters remain owned by private workspaces. Shared tests use only synthetic-from-scratch adapters and fixtures. A future migration milestone requires a separate contract, plan, validation gate, and authorization.

## Rejected Alternatives

Dynamic adapter loading would turn a deterministic CLI boundary into arbitrary code execution and could expose private modules. Persisting reports would introduce a second state lifecycle without an approved authority model. Reusing existing mutation schemas would blur read-only inspection with canonical writes. Silently merging duplicate differences would make output depend on adapter iteration order.
