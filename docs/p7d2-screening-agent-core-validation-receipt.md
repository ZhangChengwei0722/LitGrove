# P7-D2A Screening Agent Core Validation Receipt

- status: `passed`
- validated_at: `2026-08-03`
- baseline: `main@7bf01ea4e4b64a891590035df1c9940c7e669ab2`
- branch: `feature/p7d2-screening-proposals`
- application_service_interface: `1.15`
- agent_task_registry: `p7d-v1`
- layout_contract: `p7d-1`

## Delivered Contract

- two direct, no-Pipeline-Job screening proposal Tasks for criteria and decisions;
- bounded Question, criteria, Paper metadata and optional Paper Card context;
- task-local criterion aliases with no Agent-owned canonical criterion identity;
- `included | excluded | uncertain` candidate outcomes with `uncertain` approval blocked;
- explicit dedicated user approval through the P7-D1 deterministic writer;
- canonical provenance `origin: user_approved_agent_proposal`;
- exact Question, Paper, criteria, decision and optional Paper Card stale-basis checks;
- revision-request lineage, exact replay, no-change receipt and write-before-receipt recovery;
- Guardian and cross-record closure in both Task-to-revision directions;
- capability feature `question_screening_agent_tasks: true`.

## Validation

The monolithic suite exceeded the 60-minute command budget without failure output. The same
test population was completed using the repository's established deterministic shards:

```text
unit A-M:                446 passed, 2 skipped
unit N-Z:                401 passed, 2 skipped
contract + integration:  141 passed
privacy + benchmark:       20 passed
aggregate:               1008 passed, 4 skipped
```

The four skips are the expected POSIX permission contracts on Windows. Additional checks:

- affected P7-D matrix: `84 passed`;
- final all-Task privacy-registry/Guardian matrix after fail-closed review correction:
  `68 passed`;
- final screening proposal file: `2 passed` within the complete N-Z shard;
- `compileall src tests`: passed;
- privacy scan: `7 expected findings, 0 unexpected findings`;
- `git diff --check`: passed;
- package build: passed;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed.

The first base-wheel smoke found only a test fixture mismatch: the smoke workspace still
selected `p7b-v1` while asserting `p7d-v1`. The fixture was advanced to the additive latest
registry and both installed-wheel smokes then passed.

## Artifacts

- `research_kb_core-0.1.0-py3-none-any.whl`
  - size: `406356`
  - SHA256: `e4bb484b812424ef6affc87e2410a7d616fa7a3e0a91e4afdcf7a222fd331578`
- `research_kb_core-0.1.0.tar.gz`
  - size: `721747`
  - SHA256: `5285fd036782412c9ccfdecbbbbfa55e7730d5176250713f3abbeca802eb1ebe`

These are pre-merge artifacts. P7-D2B must pin a wheel rebuilt from the exact merged Core
head, not this branch artifact.

## Boundaries

- no embedded Agent execution or model/provider API;
- no mandatory screening, credibility scoring or Evidence generation;
- no private scientific workspace, legacy Question data or real PDF access;
- no Research Synthesis, Exchange, migration, cutover or cleanup deletion.
