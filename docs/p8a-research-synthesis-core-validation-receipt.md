# P8-A Research Synthesis Core Validation Receipt

- status: `passed`
- validated_at: `2026-08-03`
- baseline: `main@16013d5c58cf4f592129bb93f07425adf522b32d`
- branch: `feature/p8-research-synthesis`
- application_service_interface: `1.16`
- agent_task_registry: `p8-v1`
- plan_commit: `89c301b`

## Delivered Contract

- user-facing `Research Synthesis` terminology with internal `step7-*` stores, schemas,
  commands and class names retained for compatibility;
- all four candidate types through one dedicated external-Agent Task route;
- one current Question, one candidate type and one explicit `append | replace` intent per
  Task;
- bounded Primary Card Unit and projected canonical Evidence context, with existing
  candidate context required for duplicate comparison;
- optional current Review Units in a separately labeled background closure that never
  enters `evidence_base`;
- dedicated preview and user approval, uncertain-near-duplicate blocking, exact replay and
  write-before-receipt recovery;
- active P7 Question revision binding and stale-submit rejection;
- additive `p8-v1` privacy registry and packaged proposal schema;
- public zero-write `ResearchSynthesisApplicationService` reads;
- approval receipts that require user authority and cannot be inherited or bypassed by a
  legacy direct-Agent replacement;
- Guardian/cross-record references for Question, Review background and approval Task
  closure.

## Validation

The complete deterministic Windows suite was executed using the established unit shards:

```text
unit A-M:                446 passed, 2 skipped
unit N-Z:                417 passed, 2 skipped
contract + integration:  141 passed
privacy + benchmark:       20 passed
aggregate:               1024 passed, 4 skipped
```

The four skips are the expected POSIX permission contracts on Windows. After the final
authority/privacy/cross-record review tightened the implementation, a delta matrix covering
all affected services plus the complete contract, integration, privacy and benchmark suites
passed `315 passed`. The final focused Task/candidate/freshness/schema rerun passed
`42 passed`.

Additional checks:

- `compileall src tests`: passed;
- all `schemas/1.0/*.json` parse as JSON: passed;
- privacy scan: `7 expected findings, 0 unexpected findings`;
- `git diff --check`: passed;
- user-facing naming audit over `src/research_kb`, `skills/research-kb`, `agent_protocol`
  and the P8 plan: no legacy display-name occurrence remains;
- package build: passed;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed.

## Candidate Artifacts

- `research_kb_core-0.1.0-py3-none-any.whl`
  - size: `416956`
  - SHA256: `174377f21169233d55ce07efc5fa8c4aef6ad1d28c6df1e16ef2f977d75246f5`
- `research_kb_core-0.1.0.tar.gz`
  - size: `736438`
  - SHA256: `2726a5c97cddabce82e756471f48971a88792747137174cb6cedee8aaebbbff7`

These are reviewed pre-merge artifacts. P8-B must pin a wheel rebuilt from the exact merged
Core head, not this candidate.

## Boundaries

- no private scientific workspace, legacy private scientific record or real PDF access;
- no store/schema/ID migration or legacy cutover;
- no embedded Agent execution, provider credential or deployment;
- no Review Memory in canonical Evidence;
- no automatic Research Synthesis on intake, query, navigation or startup;
- no Obsidian, Exchange, citation graph or generated-artifact deletion.
