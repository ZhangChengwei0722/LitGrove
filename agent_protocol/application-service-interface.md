# Application Service Interface

Status: implemented in P1

## Purpose

This interface lets the CLI and a future localhost App backend call the same deterministic Core behavior. It is a Python service boundary, not a network API, Agent protocol, schema version or permission grant.

## Result Forms

| Use case | Service result |
|---|---|
| ordinary read | JSON-compatible mapping |
| deterministic render | final UTF-8 bytes |
| expected validation/findings | immutable result with `to_dict()` and `exit_code` |
| canonical mutation | domain result and transaction receipt, or an immutable projection of them |
| exceptional failure | `ResearchKBError(Diagnostic)` |

Hosts may project these values into CLI stdout/stderr or an App response. They may not infer human review, scientific credibility or broader filesystem authority.

## P1 Extracted Services

```text
ContractValidationService
JsonlValidationService
PrivacyScanService
QuestionQueryService
WorkspaceQuestionReadingViewService
WorkspaceStep7ReadingViewService
ParseApplicationService
TransactionRecoveryService
```

`ParseAdapterRegistry` is explicit and exact-name only. An unknown or unregistered adapter fails with `RKBC-028`; no adapter is auto-discovered or substituted.

## Host Responsibilities

- decode transport input within the host's documented size limits;
- open a validated `WorkspaceLayout` through Core rules;
- call one focused service method;
- serialize the returned mapping/bytes once;
- redact diagnostics through Core's existing projection;
- preserve the service's exit/status classification;
- never access canonical stores directly to reproduce service behavior.

## Stable CLI Compatibility

P1 preserves existing command names, arguments, JSON fields, Markdown bytes, exit codes and mutation effects. CLI file/stdin decoding remains an adapter concern. Service methods do not expose a stringly typed `execute(command, args)` endpoint.

## Deferred

The interface does not define localhost HTTP routes, App sessions, Pipeline Jobs, Source Adequacy, Agent Tasks, staging, Exchange or backup. Those require their owning phase contracts.
