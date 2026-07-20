# ADR 0012: Deterministic Skill-Facing Interface

- status: accepted_for_m3a_0b

## Decision

Shared Core exposes a versioned transient interface for future Agent Skill orchestration:

```text
capability show
paper status
parse show [--page]
bounded stdin JSON handoff
```

Successful read outputs use `interface_version: "1.0"`, are completely serialized before one UTF-8/LF stdout write, and are never persisted. Core reports implemented and installed capabilities, current structured stage facts, source and transaction safety, and validated parsed pages. It does not report scientific completion, choose a next action, or maintain a workflow-run database.

`capability show` is workspace-independent. It distinguishes the implemented real-PDF contract from the installed availability of `pdfplumber`; a missing optional dependency is a successful capability report containing `RKBC-028`, not a failed probe.

`paper status` validates the complete stored bundle and projects one paper's source, parse, Paper Card, Evidence, review queue, Question Mapping freshness, Guardian findings, and transaction phases. It emits counts, IDs and status values only, with no source path, scientific statement, claim, quote, question text or rationale. The future Skill owns procedural resume decisions.

`parse show` validates the complete bundle and registered source SHA-256 before and after projection. It may emit private parsed-page text for the selected paper because it is an explicit local read surface. It creates no full-text sidecar, cache, report, event, journal or reading-view file.

## Stdin Handoff

Only these arguments accept `-`:

```text
registry add --metadata -
record promote --request -
```

Stdin accepts one strict UTF-8 JSON object, not YAML. Registry metadata is limited to 64 KiB and mutation requests to 4 MiB. The reader consumes at most `limit + 1` bytes; oversize input returns `RKBC-030`. Empty, malformed, non-object or invalid-UTF-8 input returns `RKBC-002`. Diagnostics never echo the input.

Stdin data enters the existing Registry, Record and Question Mapping services. It does not create a temporary request file or bypass schema validation, authority, CLI-owned IDs, transaction journals or source-stability checks. File-based JSON/YAML inputs retain their existing behavior.

## Limits

This decision adds no JSON Schema, canonical ID, workspace layout, dependency, persisted status, workflow state or source-copy behavior. It does not implement the Portable Skill, Review runtime, Step 7, OCR, figure/table extraction, discovery, acquisition, private compatibility work or migration.
