# ADR 0018: Portable Skill Knowledge Query And Step 7 Orchestration

- Status: Accepted
- Date: 2026-07-21

## Decision

Extend the repo-owned Portable Agent Skill with two separate behaviors:

```text
ordinary knowledge query -> private task report with zero writes
explicit/full-workflow Step 7 maintenance -> M3B-1 Core authority
```

Single-paper explanation, overview, methods, comparison, claim trace-back and research-direction discussion start from grounded/revised Paper Card Units. Canonical Evidence is expanded when exact provenance is requested or persistence is proposed. Review Memory may inform labeled background only and cannot become primary support.

Step 7 maintenance requires an existing Question Mapping and an explicit persistence intent. The Skill calls `step7 context`, reconciles semantic candidates, promotes only through `record promote`, optionally renders, and finishes with Guardian. Exact reruns produce no write, same-candidate changes use replace, materially distinct candidates may append and uncertain near-duplicates stop.

Query and maintenance preflight calls `workspace init --dry-run` only. The dry-run result is always `planned`; only `already_present` actions plus the planned lock acquisition are accepted as no-change. Bootstrap, adoption or layout upgrade is a separate operation and cannot be hidden inside a query or candidate rerun.

## Rationale

Using Paper Card Units as the semantic entry avoids repeating Evidence extraction during every question. Keeping canonical Evidence as the trace-back backbone preserves paper/page/locator/quote auditability. Separating ephemeral answers from durable candidate maintenance prevents ordinary conversation from producing uncontrolled Step 7 churn.

## Consequences

- The Skill, not Core, owns scientific selection, analysis and semantic duplicate judgment.
- Core continues to own candidate IDs, support/boundary closure, snapshots, states, atomic writes and freshness.
- No query-answer store, persisted Markdown, direct JSONL access, Core LLM provider or new dependency is added.
- New Research Question ideas remain report-only until explicit approval.
- Review Unit Step 7, Field Map integration, discovery, acquisition, manuscript audit, migration and local Skill installation remain separate work.
