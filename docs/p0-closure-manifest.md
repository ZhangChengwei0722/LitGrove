# P0 Closure Manifest

Status: passed

Baseline commit: `c9a3d85e363f7c58d86992f4ad3871efc7994d3c`

Closed at: 2026-07-29

Scope: limited P0 documentation, audit and synthetic fixture only

```text
next_gate: separate P1 authorization and bounded implementation plan
implementation_authorized: false
```

## 1. Artifact Inventory

| Required artifact | Exact repository path | Result | Reason |
|---|---|---|---|
| capability/service audit | `docs/p0/capability-service-audit.md` | pass | Classifies all 29 public CLI leaf commands, current service ownership, authority/failure behavior, Core-only invariants and safe workspace loading. |
| characterization matrix | `docs/p0/cli-characterization-matrix.md` | pass | Covers read, mutation, transaction/recovery, rendering, discovery/acquisition and Guardian/capability with existing success/failure tests and P1 parity obligations. |
| App/service/authority ADR | `docs/decisions/0028-application-service-and-authority-boundary.md` | pass | Freezes separate repositories, shared facade, three storage layers, workspace session loading and localhost controls. |
| source/adequacy/dependency ADR | `docs/decisions/0029-source-identity-adequacy-and-dependency.md` | pass | Freezes digest recheck, manifestations, availability, per-use adequacy, hard failures, corrections, factual admissibility and progressive stale edges. |
| Agent/privacy/staging ADR | `docs/decisions/0030-agent-task` + `-privacy-staging-and-untrusted-content.md` | pass | Adjacent path fragments identify the exact file while avoiding a false-positive credential signature; the ADR freezes orthogonal payload classes, planned task coverage, execution scope, successor lineage, preview/approval, injection defense and Skill ownership. |
| operational lifecycle ADR | `docs/decisions/0031-operational-lifecycle-and-recovery.md` | pass | Gives every operational/staging class one lifecycle and preserves recovery/decision receipts. |
| Exchange/serialization ADR | `docs/decisions/0032-exchange-security-and-portable-serialization.md` | pass | Freezes origin/trust, source rights, safe staging, allowlisted export, canonical serialization and compatibility branches. |
| budgets/dependencies ADR | `docs/decisions/0033-versioned-acceptance-budgets-and-phase-dependencies.md` | pass | Defines provisional profile `p0-provisional-r0-v1`, freeze points, scale dimensions and real phase dependencies. |
| future scenario/generator spec | `docs/p0/future-scenarios-and-scale-generator-spec.md` | pass | Defines P3/P4/P7/P9/P10 scenarios and P11 generator recipe without materializing future records. |
| existing-contract seed fixture | `tests/fixtures/p0_seed/` | pass | Contains 16 valid records across 12 existing kinds plus two tiny invented text assets; every item is synthetic and cross-record-valid. |
| third-party reuse audit | no artifact required | not_applicable | P0/P1 proposes behavior-level inspiration only and copies no third-party code. A later code-copy proposal must run the ADR 0033 gate first. |

## 2. Seed Fixture Inventory

| Item | Path | Result |
|---|---|---|
| fixture boundary/readme | `tests/fixtures/p0_seed/README.md` | pass |
| workspace contract copy | `tests/fixtures/p0_seed/workspace.yaml` | pass |
| domain profile contract copy | `tests/fixtures/p0_seed/domain-profile.yaml` | pass |
| cross-record bundle | `tests/fixtures/p0_seed/seed-bundle.json` | pass |
| Primary synthetic source | `tests/fixtures/p0_seed/sources/alpha/primary.txt` | pass |
| Review synthetic source | `tests/fixtures/p0_seed/sources/alpha/review.txt` | pass |

The bundle materializes only baseline contract `1.0` kinds:

```text
workspace: 1
domain-profile: 1
registry-paper: 2
parsed-page: 2
evidence: 2
review-queue: 2
paper-card: 1
review-memory: 1
question-mapping: 1
step7-insight: 1
process-event: 1
guardian-report: 1
```

Source Adequacy, Pipeline Job, Agent Task, staging, Direction, Field Map, Tag, Exchange conflict, generated-view freshness and backup records are not present.

## 3. Closure Criteria

| Criterion | Result | Evidence |
|---|---|---|
| no production behavior change | pass | Git scope contains no `src/`, `schemas/`, `templates/`, packaging, App or runtime file change. |
| all current Core tests pass | pass | `643 passed, 4 skipped` on Windows; skips are the existing POSIX permission cases. |
| package remains buildable | pass | sdist and wheel built successfully for `research-kb-core 0.1.0`. |
| public version surface works | pass | `research-kb 0.1.0`. |
| seed bundle schema/cross-record validity | pass | `validate_bundle(..., actor="cli")` returned no diagnostics. |
| readable YAML copies match bundle | pass | both records validate under `1.0` and equal the bundle records. |
| source fingerprints are exact | pass | both registered SHA-256 values equal fixture asset bytes. |
| privacy | pass | repository scan reports 7 expected fixture findings and 0 unexpected findings. |
| private project/source isolation | pass | no private workspace, real source, PDF, local absolute path or private domain marker was added or accessed. |
| every public CLI command classified | pass | 29/29 commands appear in the service audit and characterization matrix. |
| accepted workflow security/capability coverage | pass | audit section 8 maps Local, Discovery, Exchange, Primary, Review, query, organization, synthesis, Obsidian, trace-back, backup and external Agent flows. |
| Core-only cross-record invariants | pass | audit section 5 lists the invariants an App/CLI adapter cannot reproduce. |
| privacy payload coverage | pass | ADR 0030 classifies every payload item of each planned initial task kind; unknown items/versions fail closed and no generic metadata fallback exists. |
| injection and untrusted rendering | pass | ADR 0030 prevents content from changing authority and requires escaped/sanitized allowlisted rendering plus CSP. |
| dependency closure | pass | ADR 0029 assigns stale edges and destinations progressively to P2/P3/P4/P7/P8/P9/P10/P11. |
| correction and identity history | pass | ADR 0029 requires successor revisions/supersede and auditable Registry merge/split/alias/archive/tombstone. |
| operational lifecycle closure | pass | ADR 0031 assigns permanent, archive/compact, cleanup or disposable policy to every listed class and preserves receipts. |
| Skill ownership | pass | ADR 0030 separates editable source, release snapshot and generated mirrors with fingerprint validation. |
| versioned budgets and actual dependencies | pass | ADR 0033 defines measurement protocol, provisional targets, freeze points and non-adjacent dependencies. |
| future fixture boundary | pass | scenario spec contains recipes only; no fabricated future record or executable generator exists. |
| seed remains small | pass | 16 structured records and two text assets; no large scale fixture or PDF. |
| third-party code reuse | not_applicable | No copied code, dependency or second backend is proposed. |
| user-owned pre-P0 edits preserved | pass | `README.md` SHA-256 remains `FB5AF898212399E53B31447B69F8B76E91DA6EA4F0163FFB04C53D9D0875A372`; `agent_protocol/README.md` remains `CC37BEBACD092744A550438079CB6EF034DE69DFD68D1EB274259510DA1A3594`. |
| no Git write | pass | Work remains uncommitted on the baseline branch; no commit, branch, push or history operation was performed. |

## 4. Validation Commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m research_kb --version
.\.venv\Scripts\python.exe -m research_kb privacy scan --root .
git diff --check
```

Additional bounded checks validated the seed bundle, YAML/bundle equality, source digests, record inventory, Markdown fence balance and authorized-path-only worktree scope.

## 5. Exit Decision

Every non-conditional limited-P0 item passes, and third-party reuse is explicitly not applicable. P1/P2 can cite exact current services, characterization tests, authority boundaries, fixture paths and provisional budget profile without inventing architecture during coding.

This closure authorizes no P1 implementation, App repository, schema/runtime change, private-workspace access, migration, provider expansion, embedded Agent or Git write.
