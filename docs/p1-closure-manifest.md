# P1 Closure Manifest

Status: passed

Baseline commit: `0a036ee18e072afae6582f42334662d398a991c2`

Closed at: 2026-07-29

Scope: shared application-service facade and CLI behavior-preserving extraction

```text
next_gate: bounded P2 implementation plan and preflight
p2_implementation_started: false
```

## 1. Service Closure

The P0 inventory contains 29 public CLI leaf commands. P1 leaves the 20 commands that already had reusable services on those services and extracts all nine remaining CLI-owned use cases:

| Baseline use case | Shared service |
|---|---|
| contract record/bundle validation | `ContractValidationService` |
| JSONL store validation | `JsonlValidationService` |
| privacy report projection | `PrivacyScanService` |
| Question list/show | `QuestionQueryService` |
| workspace Question rendering | `WorkspaceQuestionReadingViewService` |
| workspace Step 7 rendering | `WorkspaceStep7ReadingViewService` |
| named Parse adapter selection and result projection | `ParseApplicationService` with `ParseAdapterRegistry` |
| transaction recovery classification | `TransactionRecoveryService` |

All facade services are available through the public `research_kb.services` package. The CLI retains argument parsing, bounded input decoding, output serialization, diagnostic redaction and process exit projection. It does not retain the moved bundle, validation, privacy, adapter-selection or recovery-classification composition.

## 2. Contract Preservation

| Criterion | Result | Evidence |
|---|---|---|
| public command coverage | pass | 29/29 leaf commands map to an existing or P1-extracted reusable service/use case. |
| direct service and CLI parity | pass | `tests/unit/test_application_services.py` covers validation, reads, rendering, Parse, recovery and the existing mutation/discovery/Guardian/capability classes. |
| CLI arguments and JSON fields | pass | Existing CLI tests and Portable Skill contract tests pass unchanged. |
| rendered bytes | pass | Question and Step 7 direct-service output remains byte-identical to CLI output. |
| Parse identity and fallback | pass | Adapter names use an explicit registry; an empty or unknown registry fails closed with no substitution. |
| recovery classification | pass | Manual-resolution actions retain status `needs_resolution` and exit code `4`; dry-run behavior remains read-only. |
| public Python surface | pass | All P1 facade services are exported by `research_kb.services`. |
| schema and layout | pass | No schema, contract version, workspace layout or ID namespace changed. |
| scientific and source authority | pass | No scientific judgment, source-write permission or canonical admission rule changed. |

## 3. Validation Evidence

```text
targeted application service + CLI: 91 passed
targeted cross-runtime integration: 7 passed
Portable Skill/capability contract: 18 passed
full Windows suite: 658 passed, 4 skipped
package build: sdist and wheel passed
version: research-kb 0.1.0
privacy: 7 expected findings, 0 unexpected findings
git diff --check: passed
```

The four skipped cases are the existing POSIX permission contracts. The final full-suite result was produced after the public service exports and all P1 tests were present.

## 4. Protected-State Check

| Boundary | Result |
|---|---|
| protected legacy/private workspace and PDF access | not performed |
| App repository or frontend creation | not performed |
| schema, layout, migration or provider expansion | not performed |
| embedded Agent runtime | not introduced |
| user-owned `README.md` change | preserved at SHA-256 `FB5AF898212399E53B31447B69F8B76E91DA6EA4F0163FFB04C53D9D0875A372` and excluded from P1 staging |
| user-owned `agent_protocol/README.md` change | preserved at SHA-256 `CC37BEBACD092744A550438079CB6EF034DE69DFD68D1EB274259510DA1A3594` and excluded from P1 staging |

## 5. P2 Prerequisites

P2 must begin from the merged P1 commit and a separately written, short-reviewed implementation plan. That plan must freeze:

1. the Core projection contract and dependency edge from canonical/operational records to the disposable catalog projection;
2. App repository, pinned Core version and project-level dependency boundaries;
3. loopback-only session, origin and CSRF controls plus configured-root workspace selection;
4. cursor pagination, stable ordering, maximum page size and projection rebuild/corruption behavior;
5. the existing-record-only index adapter registry and exclusion of raw parsed full text by default;
6. the synthetic catalog-scale generator materialization and the method for freezing the R0 budget profile;
7. read-only App surfaces and the canonical-tree unchanged acceptance check.

P1 does not authorize P2 to invent Direction, Field Map, Tag, Pipeline Job, Source Adequacy, Agent Task, Exchange or backup schemas. Those remain with their owning phases.

## 6. Exit Decision

P1 satisfies its exit gate. The CLI and future App can share one focused deterministic service layer without a generic command dispatcher, and the existing CLI remains compatible. P2 may proceed only after its bounded implementation plan and preflight are complete.
