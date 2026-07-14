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

1. `registry add` resolves a declared source root, hashes the source read-only, and preserves exact duplicates as reciprocal candidates.
2. `parse run` uses only `SyntheticTextAdapter` in M1B and writes validated page records without creating a full-text copy.
3. `record promote` loads a private mutation request, injects IDs/timestamps/fingerprints, enforces actor authority, and promotes one canonical store.
4. `guardian check` is read-only by default. `--write-report` explicitly appends the report through the same transaction kernel.
5. `transaction recover --dry-run` reports digest-based recovery actions without mutation; recovery writes only when dry-run is omitted.

Every mutation uses an explicit workspace config. No command accepts a raw `knowledge_root` override or a source-write operation.

## Authority

- `ai_draft`: Agent candidate.
- `ai_checked`: Agent review recorded under a contract that permits it.
- `passed_auto_checks`: deterministic checks passed.
- `human_checked` and `verified`: user-only.
- final screening and high-risk source operations: user-only.

`stored` is an internal validation context for existing canonical state. It is not a submitter role.

## Failure Boundary

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
