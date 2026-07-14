# ADR 0006: Atomic Promotion And Recovery

- status: accepted_for_m1b

## Decision

Canonical JSON and JSONL stores use a workspace-wide `filelock`, same-directory fsynced temporary files, digest checks, and `os.replace`. Each mutation retains a transaction journal under `knowledge_root/.research-kb/transactions/` and appends a process event after target replacement. Existing POSIX file modes are preserved; a new canonical file is `0600`, and its newly created immediate private parent is `0700`.

The write sequence is:

```text
lock workspace
-> capture target digest and compare the caller's expected digest
-> write prepared journal
-> write and fsync same-directory temporary file
-> validate temporary canonical content
-> os.replace target
-> mark target_replaced
-> run any source-stability check required by the service
-> append process event through a non-recursive atomic rewrite
-> record the final result and mark complete
```

The event ID is reserved before mutation. A completed journal records `result: success | failure` and must bind to exactly one process event whose full deterministic content matches the journal. Guardian enforces this binding. Completed journals are retained as deterministic recovery metadata. Journals and events never include candidate scientific payloads.

If a source-dependent service detects that its source changed after target replacement but before the success event, it emits no success event and records `phase: needs_resolution` with `result: needs_resolution`. That state requires manual inspection and is never auto-completed.

## Recovery

Recovery compares the journal's before/after digests with the current target:

- current equals before: require or append the exact journal-derived failure event;
- current equals after: require or append the exact journal-derived success event;
- current equals neither: mark `needs_resolution` and do not guess;
- a missing or altered event for a completed journal requires resolution;
- any event content mismatch, reused event ID, journal/result mismatch, or existing `needs_resolution` state requires manual resolution.

`--dry-run` reports actions without changing the target, journal, or event store.

## Rejected Alternatives

Direct JSONL append cannot provide whole-store validation or reliable crash recovery. SQLite would add a second canonical model before the file contract is stable. The selected design preserves the local-first file protocol while bounding concurrent writes and partial failure.
