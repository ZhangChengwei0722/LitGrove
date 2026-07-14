# ADR 0006: Atomic Promotion And Recovery

- status: accepted_for_m1b

## Decision

Canonical JSON and JSONL stores use a workspace-wide `filelock`, same-directory fsynced temporary files, digest checks, and `os.replace`. Each mutation retains a transaction journal under `knowledge_root/.research-kb/transactions/` and appends a process event after target replacement.

The write sequence is:

```text
lock workspace
-> capture target digest and compare the caller's expected digest
-> write prepared journal
-> write and fsync same-directory temporary file
-> validate temporary canonical content
-> os.replace target
-> mark target_replaced
-> append process event through a non-recursive atomic rewrite
-> mark complete
```

Completed journals are retained as deterministic recovery metadata. Journals and events never include candidate scientific payloads.

## Recovery

Recovery compares the journal's before/after digests with the current target:

- current equals before: append a missing failure event;
- current equals after: append a missing success event;
- current equals neither: mark `needs_resolution` and do not guess;
- an existing event with the wrong result also requires resolution.

`--dry-run` reports actions without changing the target, journal, or event store.

## Rejected Alternatives

Direct JSONL append cannot provide whole-store validation or reliable crash recovery. SQLite would add a second canonical model before the file contract is stable. The selected design preserves the local-first file protocol while bounding concurrent writes and partial failure.
