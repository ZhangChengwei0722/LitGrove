# P0 Capability And Service Audit

Status: accepted P0 baseline audit

Baseline: `c9a3d85e363f7c58d86992f4ad3871efc7994d3c`

Audit date: 2026-07-29

Scope: documentation only; no production behavior, schema, layout or private-workspace change

## 1. Baseline Capability Report

The installed Core reports contract version `1.0`, layout version `m3c-2a`, and package version `0.1.0`. The following feature flags are currently public:

| Feature | Baseline |
|---|---|
| approved discovery candidate handoff | enabled |
| explicit OA acquisition | enabled |
| legal OA resolution | enabled |
| manuscript projection | enabled |
| on-demand discovery | enabled |
| real PDF parse | enabled |
| Review Memory runtime | enabled |
| stdin JSON handoff | enabled |
| Step 7 candidate runtime | enabled |

These flags describe deterministic Core capabilities. They do not imply an App, Source Adequacy, Pipeline Job, Agent Task, Exchange runtime, embedded Agent, or migration support.

## 2. Classification

| Classification | Meaning |
|---|---|
| `already_reusable_service` | A callable service owns the use-case rule; CLI mainly loads arguments and projects output. |
| `cli_orchestration_to_extract` | Reusable primitives exist, but CLI still owns composition or result policy that a future App must not duplicate. |
| `missing_query_service` | CLI composes reads/validation directly and P1 must introduce one application service without changing behavior. |
| `missing_mutation_service` | No reusable mutation facade exists. No current command falls wholly in this class. |
| `deferred` | Outside P1/P2 or blocked on a later approved contract. |

## 3. Public Command Inventory

The baseline has 29 public leaf commands.

| Command | Service or current owner | Service class | Classification | P1 action and authority boundary |
|---|---|---|---|---|
| `capability show` | `CapabilityService` | Guardian/capability | `already_reusable_service` | Reuse unchanged; workspace-independent and read-only. |
| `compatibility inspect` | `CompatibilityInspectionService` plus CLI adapter registry | read | `already_reusable_service` | Keep explicit adapter injection; never dynamically discover or mutate legacy data. |
| `contract validate` | `SchemaRegistry`, `validate_record`, `validate_bundle`, CLI diagnostic deduplication | read | `missing_query_service` | Extract validation report service, including deduplication and exit classification. |
| `data check-jsonl` | `read_jsonl`, `validate_record`, CLI aggregation | read | `missing_query_service` | Extract bounded store-validation service; preserve missing/format/version behavior. |
| `discovery acquire` | `DiscoveryAcquisitionService` plus resolver/transport registries | discovery/acquisition | `already_reusable_service` | Reuse; exact user actor and create-only `local_inbox` authority remain mandatory. |
| `discovery list` | `DiscoveryCandidateService` | read | `already_reusable_service` | Reuse deterministic metadata-only projection. |
| `discovery resolve` | `DiscoveryResolutionService` plus resolver registry | discovery/acquisition | `already_reusable_service` | Reuse zero-download, zero-canonical-write resolution. |
| `discovery search` | `DiscoveryService` plus connector registry | discovery/acquisition | `already_reusable_service` | Reuse transient search; CLI request decoding stays an adapter concern. |
| `discovery select` | `DiscoveryCandidateService` | mutation | `already_reusable_service` | Reuse; only explicit user selection may persist metadata-only candidates. |
| `discovery show` | `DiscoveryCandidateService` | read | `already_reusable_service` | Reuse deterministic candidate read. |
| `guardian check` | `GuardianService` | Guardian/capability | `already_reusable_service` | Reuse; optional report write remains explicit and transactional. |
| `intake inspect` | `IntakeInspectService` | read | `already_reusable_service` | Reuse bounded, read-only source projection. |
| `intake inspect-acquired` | `AcquiredCandidateIntakeService` | read | `already_reusable_service` | Reuse; acquisition receipt is not Registry authority. |
| `manuscript inspect` | `ManuscriptProjectionService` | read | `already_reusable_service` | Reuse read-only projection; document review remains deferred. |
| `paper context` | `PaperContextService` | read | `already_reusable_service` | Reuse canonical Paper Card/Evidence/review-queue context. |
| `paper status` | `PaperStatusService` | read | `already_reusable_service` | Reuse bounded status projection. |
| `parse run` | `ParseService`; CLI selects adapter class | mutation | `cli_orchestration_to_extract` | Move the versioned adapter registry/selection behind the service facade; never auto-substitute an adapter. |
| `parse show` | `ParseReadService` | read | `already_reusable_service` | Reuse exact stored parse reads. |
| `privacy scan` | `scan_repository`; CLI report projection | Guardian/capability | `missing_query_service` | Extract a stable privacy-scan report service. |
| `question list` | CLI loads/validates bundle and projects mappings | read | `missing_query_service` | Add a query service; App must not reproduce bundle invariants or sorting. |
| `question render` | CLI loads entries, then `QuestionReadingViewService` | rendering | `cli_orchestration_to_extract` | Service must own loading, validation and render input selection. |
| `question show` | CLI loads/validates/filter mappings | read | `missing_query_service` | Add exact-ID query service with current error semantics. |
| `record promote` | `RecordService` | mutation | `already_reusable_service` | Reuse transactional promotion and actor/state gates. |
| `registry add` | `RegistryService` | mutation | `already_reusable_service` | Reuse reference-only registration and immutable-source checks. |
| `review context` | `ReviewContextService` | read | `already_reusable_service` | Reuse Review Memory-only context and parse freshness. |
| `step7 context` | `Step7ContextService` | read | `already_reusable_service` | Reuse candidate context; candidates remain non-facts. |
| `step7 render` | CLI loads entries, then `Step7ReadingViewService` | rendering | `cli_orchestration_to_extract` | Service must own loading, validation and render input selection. |
| `transaction recover` | `TransactionManager`; CLI classifies manual-resolution actions | transaction/recovery | `cli_orchestration_to_extract` | Extract recovery report service including action classification and exit policy. |
| `workspace init` | `WorkspaceBootstrapService` | mutation | `already_reusable_service` | Reuse validation, dry run and apply behavior; App may not handcraft layout. |

Summary:

```text
already_reusable_service: 20
cli_orchestration_to_extract: 4
missing_query_service: 5
missing_mutation_service: 0
deferred public commands: 0
```

The original P0 prose summary miscounted one `missing_query_service` row. The command table and 29-command total were correct; this numerical correction does not change the P0 classification or exit decision.

The `main()` dispatch tree, bounded stdin/file decoding, JSON/byte output, redaction and process exit-code mapping remain CLI adapter concerns. Business result classification must move into the relevant service only where the table identifies current CLI ownership.

## 4. Current Failure And Authority Snapshot

| Boundary | Current behavior to preserve |
|---|---|
| Exit codes | `0` success; `1` valid request with findings/validation failure; `2` malformed input or ordinary Core error; `3` unsupported contract/schema version; `4` authority, protected-input, layout, recovery or workspace block. |
| Stdout/stderr | Success emits exactly one JSON object or one byte-rendered view. Structured failures keep stdout empty where the command contract promises it and write redacted diagnostics to stderr. |
| Workspace | Runtime commands require an initialized compatible layout. Old or conflicting layouts fail closed. |
| Source assets | Registry and Parse read declared assets; they do not move, rename, overwrite or delete them. Acquisition is the sole create-only exception and does not authorize Registry. |
| Actors | Agent cannot assign human states; discovery persistence and acquisition require explicit user authority. |
| Transactions | Canonical mutations use transaction journals and digest-checked recovery. A failed mutation must preserve the complete prior workspace. |
| Discovery | Search and resolution are transient; selection persists metadata only; acquisition creates a source plus receipt only. |
| Review | Review Memory is mutually exclusive with the Primary route for one paper and cannot enter canonical Evidence. |
| Rendering | Markdown is a derived read surface; current `question render` and `step7 render` do not write workspace files. |
| Privacy | Absolute/home paths are redacted from diagnostics and repository privacy scanning fails on unexpected private material. |

## 5. Cross-Record Invariants Reserved To Core

The App and CLI adapters may display these outcomes but must never reimplement or weaken them:

1. Contract version, schema kind, ID namespace, actor and review-status authorization.
2. Workspace/root confinement and canonical POSIX relative source references.
3. Registry source fingerprint equality across Evidence and Review Memory.
4. One current Paper Card or Review Memory per paper, with Primary and Review routes mutually exclusive.
5. Grounded/revised Card Units require same-paper canonical Evidence; interpretive/background/needs-resolution Units cannot carry Evidence.
6. Review Units retain review-source provenance while remaining `background_only`, `not_fact` and excluded from canonical Evidence.
7. Question links resolve to same-paper Card Units, Evidence and boundary records.
8. Step 7 candidates resolve their Card Units/Evidence/boundaries, preserve input snapshots, and never treat review-queue IDs as Evidence.
9. Guardian status agrees with finding severity and source-byte state.
10. Transaction idempotency, expected digests, atomic promotion, journal recovery and source immutability.

## 6. Safe Workspace Loading Decision

The future App backend must receive one user-selected workspace root and open it through a Core application service. It must not parse `workspace.yaml`, resolve roots, inspect compatibility, or construct `WorkspaceLayout` independently.

The service boundary must:

1. resolve the selected root once without following an unapproved alternate path;
2. load and validate the workspace marker/config through existing Core rules;
3. verify supported layout/contract versions and source-root confinement;
4. return an opaque session-bound workspace handle plus redacted display metadata;
5. reject arbitrary browser-supplied filesystem paths after session creation;
6. close the handle before another writer or backup barrier is granted.

P1 owns this application-service facade. P2 may consume it but may not add a second workspace loader.

## 7. Explicit Deferrals

Source Adequacy, Pipeline Job, Agent Task, staging/preview, Direction/Field Map/Tag, generated-view freshness, Exchange import/export, backup, and scale maintenance do not exist in the P0 runtime. They are represented only by ADR decisions and scenario/generator specifications until their owning phase approves contracts.

## 8. Product Workflow Security And Capability Matrix

| Accepted workflow | Current callable basis | Owning phase for missing surface | Authority/security rule |
|---|---|---|---|
| open workspace and inspect status | bootstrap, capability, paper/review/question/Step 7 reads | P1 facade, P2 App | Core-only workspace loader; opaque session; loopback/session/origin/CSRF controls. |
| local user-provided source | intake, Registry, Parse | P3 pipeline/adequacy | declared root, immutable source, digest recheck; Library inclusion does not require Question screening. |
| public discovery and user download handoff | search/select/resolve/acquire/intake-acquired | P6 UI/date/retraction policy | search/resolve transient; selection and acquisition separately user-authorized; acquisition create-only. |
| Exchange import | no current runtime | P10 | confined staging, origin namespace, external-unreviewed default, preview and all-or-nothing commit. |
| Primary semantic processing | Paper Card/Evidence/review-queue contracts and promotion | P4 Agent Task/staging | use-specific adequacy first; Agent output stages; user approves; canonical Evidence only from Primary. |
| Review semantic processing | Review Memory contract and promotion | P4 Agent Task/staging | retained Unit provenance plus consumed adequacy capability; background only; zero canonical Evidence. |
| single-paper/cross-paper factual query | current contexts and mappings | P5 Knowledge Query | local + committed + admissible + current only; zero canonical scientific write. |
| Direction/Field Map/Question/Tag organization | Question Mapping only | P7 | Agent proposals use staging/preview; factual mapping excludes non-admissible/background Units. |
| Research Synthesis | current Step 7 candidate substrate | P8 | explicit persistence request; evidence base canonical; Review Memory labeled background. |
| generated Obsidian views | current stdout renderers | P9 | one-way managed subtree, source watermark, sanitized allowlist, managed-edit overwrite abort. |
| PDF trace-back | exact current parse/Evidence locators | P5 App viewer/handoff | Evidence's historical source/parse/locator controls; active parse cannot substitute. |
| backup/recovery/scale | transaction recovery and Guardian baseline | P11 | writer barrier/watermark, validated restore, independent operational density and deduplicated maintenance. |
| external Codex/Claude work | deterministic stdin/context and Portable Skill baseline | P4 versioned tasks | explicit content classes and execution scope; untrusted content is data; no direct write. |

No row authorizes an embedded Agent runtime, a second provider, a private-workspace migration or legacy cutover.

## 9. P1 Migration Result

P1 extracted all nine non-reusable baseline rows:

| Baseline composition | P1 service |
|---|---|
| contract record/bundle validation | `ContractValidationService` |
| JSONL store validation | `JsonlValidationService` |
| privacy report projection | `PrivacyScanService` |
| Question list/show | `QuestionQueryService` |
| workspace Question rendering | `WorkspaceQuestionReadingViewService` |
| workspace Step 7 rendering | `WorkspaceStep7ReadingViewService` |
| named parse adapter selection/receipt | `ParseApplicationService` and `ParseAdapterRegistry` |
| transaction recovery classification | `TransactionRecoveryService` |

The CLI retains argument parsing, bounded input decoding, output serialization, redaction and process exit projection only. No P1 service adds a record type, schema, layout, App or semantic decision.
