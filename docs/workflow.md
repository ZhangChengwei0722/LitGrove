# Workflow Contract

## Deterministic Boundary

Agent-created structured content follows this intended lifecycle:

```text
candidate input
-> schema/reference/status validation
-> locked atomic promotion
-> process event
-> Guardian
```

Milestone 1B implements this deterministic storage lifecycle for Registry, synthetic Parse, Paper Card Core, Evidence, and review queue records. It does not generate scientific content.

## Runtime Sequence

1. `workspace init --dry-run` validates an existing config and reports planned managed actions without deliberate filesystem mutation.
2. `workspace init` binds the managed root with a deterministic marker. Repeating it with the same config returns `no_change`.
3. `registry add` resolves a declared source root, hashes the source read-only, and preserves exact duplicates as reciprocal candidates.
4. `parse run` uses only `SyntheticTextAdapter` in M1B and writes validated page records without creating a full-text copy.
5. `record promote` loads a private mutation request, injects IDs/timestamps/fingerprints, enforces actor authority, and promotes one canonical store.
6. `guardian check` is read-only by default. `--write-report` explicitly appends the report through the same transaction kernel.
7. `transaction recover --dry-run` reports digest-based recovery actions without mutation; recovery writes only when dry-run is omitted.

Every runtime command uses the same semantic validator and requires a matching workspace marker. No command accepts a raw `knowledge_root` override or a source-write operation. `local_inbox` remains user-owned and is never created or scanned by bootstrap.

## Authority

- `ai_draft`: Agent candidate.
- `ai_checked`: Agent review recorded under a contract that permits it.
- `passed_auto_checks`: deterministic checks passed.
- `human_checked` and `verified`: user-only.
- final screening and high-risk source operations: user-only.

`stored` is an internal validation context for existing canonical state. It is not a submitter role.

## Failure Boundary

- Bootstrap performs a complete read-only preflight before creating its lock scaffold, then repeats mutation-sensitive checks while holding the workspace lock.
- A conflicting marker, unknown managed content, incomplete transaction, unsafe path type, or invalid markerless bundle blocks initialization without rewriting canonical records.
- Validation failure preserves the previous target bytes.
- A pre-replacement failure records a failure event when possible.
- Source-dependent services recheck source stability after replacement and before emitting success.
- A failed post-replacement source check emits no success event, records `needs_resolution`, and requires manual inspection.
- A post-replacement event failure leaves an incomplete journal and returns a recovery-required error.
- Completed journals record their final result and must match exactly one journal-derived process event; Guardian reports missing or altered events.
- Recovery compares before/after/current digests and exact event content. It never guesses through an ambiguous or `needs_resolution` state.
- Existing POSIX target modes are preserved; new canonical files and newly created immediate private parents use `0600` and `0700` respectively.

## Reading Views

Structured JSON/YAML/JSONL records are inputs. Markdown views are one-way renders and cannot be used to rewrite structured facts.
