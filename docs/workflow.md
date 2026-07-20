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

Milestone 1B implements this deterministic storage lifecycle. M3A-0A adds explicit real-PDF page extraction and strict Evidence provenance validation without generating scientific content.

## Runtime Sequence

1. `workspace init --dry-run` validates an existing config and reports planned managed actions without deliberate filesystem mutation.
2. `workspace init` binds the managed root with a deterministic marker. Repeating it with the same config returns `no_change`.
3. `compatibility inspect` uses only adapters explicitly injected by a private in-process caller. It emits a read-only report to stdout and never persists compatibility state.
4. `registry add` resolves a declared source root, hashes the source read-only, and preserves exact duplicates as reciprocal candidates.
5. `parse run` uses the explicitly requested `synthetic-text` or optional `pdfplumber` adapter and writes validated page records without creating a full-text copy. It reports exact adapter/version identity and never falls back to OCR or another adapter.
6. `record promote` loads a private mutation request, injects IDs/timestamps/fingerprints, enforces actor authority, and promotes one canonical store.
7. A `question-mapping` request selects Card Units for a user-supplied or explicitly approved question. Core derives evidence and required boundaries before atomic promotion.
8. `question list/show` retrieves deterministic structured mappings without writing a reading view, report, event, or journal.
9. `question render` validates one mapping and emits its complete Markdown reading view to stdout without persisting the view or changing structured state.
10. `guardian check` is read-only by default. `--write-report` explicitly appends the report through the same transaction kernel.
11. `transaction recover --dry-run` reports digest-based recovery actions without mutation; recovery writes only when dry-run is omitted.

Every runtime command uses the same semantic validator and requires a matching workspace marker. No command accepts a raw `knowledge_root` override or a source-write operation. `local_inbox` remains user-owned and is never created or scanned by bootstrap.

Compatibility reports classify what can be read directly, projected through an adapter, left unsupported, or retained as a legacy reading view. They do not select a migration target or change authority. Core rechecks all declared protected files and trees in a `finally` path; source change takes precedence over an ordinary adapter failure.

## Authority

- `ai_draft`: Agent candidate.
- `ai_checked`: Agent review recorded under a contract that permits it.
- `passed_auto_checks`: deterministic checks passed.
- `human_checked` and `verified`: user-only.
- final screening and high-risk source operations: user-only.

`stored` is an internal validation context for existing canonical state. It is not a submitter role.

Question origins are explicit mutation context:

- `user_supplied`: the user supplied the active question;
- `user_approved_candidate`: the user explicitly approved a generated candidate;
- `existing_question`: refresh an already persisted mapping;
- an unapproved Agent-generated question stays in the task report and is not persisted.

## Failure Boundary

- Bootstrap performs a complete read-only preflight before creating its lock scaffold, then repeats mutation-sensitive checks while holding the workspace lock.
- A conflicting marker, unknown managed content, incomplete transaction, unsafe path type, or invalid markerless bundle blocks initialization without rewriting canonical records.
- An exact `m2a-1` marker requires `workspace init`; runtime commands never write through an old layout.
- Validation failure preserves the previous target bytes.
- A pre-replacement failure records a failure event when possible.
- Source-dependent services recheck source stability after replacement and before emitting success.
- Evidence promotion requires an active same-paper parsed page, a supported page-matching locator, and an exact quote slice or bounded synthetic block containment.
- Missing `research-kb-core[pdf]` returns `RKBC-028`; unsupported, malformed, encrypted, or text-unavailable PDFs return `RKBC-029` without exposing source paths or text.
- A failed post-replacement source check emits no success event, records `needs_resolution`, and requires manual inspection.
- A post-replacement event failure leaves an incomplete journal and returns a recovery-required error.
- Completed journals record their final result and must match exactly one journal-derived process event; Guardian reports missing or altered events.
- Recovery compares before/after/current digests and exact event content. It never guesses through an ambiguous or `needs_resolution` state.
- Existing POSIX target modes are preserved; new canonical files and newly created immediate private parents use `0600` and `0700` respectively.

## Reading Views

Structured JSON/YAML/JSONL records are inputs. Markdown views are one-way renders and cannot be used to rewrite structured facts.

M2B-2 renders exactly one stdout-only Question Reading View. It includes selected Paper Card Units, mapped canonical evidence, non-evidence review queue boundaries, and current freshness diagnostics. It creates no Markdown file or view store.

Evidence matrices, relations, gap maps, contradictions, persisted Question Layer Markdown, and Step 7 output remain outside the implemented runtime.
