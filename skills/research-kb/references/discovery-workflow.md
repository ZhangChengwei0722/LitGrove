# On-Demand Discovery Workflow

Use this workflow only for `on_demand_discovery`. It searches public metadata through the Core connector and returns a private task report. It does not require or initialize a workspace.

## Resolve The Request

Convert relative dates such as `last seven days` into explicit inclusive ISO dates before calling Core. Do not let Core infer the current date.

Build one closed request:

```yaml
request_version: "1.0"
date_from: YYYY-MM-DD
date_until: YYYY-MM-DD
title_keywords:
  - bounded phrase assigned to title
abstract_keywords:
  - bounded phrase assigned to abstract
keyword_mode: any | all
include_preprints: true | false
max_results: 1
```

The user may request any maximum from 1 through 15. Report the range as `0-15` when the requested maximum is 15. At least one title or abstract keyword is required.

Do not silently move a keyword between fields. `any` accepts one matching field-keyword pair. `all` requires every title keyword in the title and every abstract keyword in the abstract.

## Execute

1. Call `capability show` and require `on_demand_discovery: true`, `discovery search`, and an available `europe-pmc` connector.
2. Send the request through stdin:

```text
research-kb discovery search --provider europe-pmc --request -
```

3. Accept only a successful transient interface `1.0` report.
4. Keep the report in the active task only and return `persistent_writes: 0`.

Do not create a temporary request file. Do not call `workspace init`, Registry, Parse, Paper Card, Evidence, Question Mapping, Step 7 or Guardian from this route.

## Interpret The Results

- Preserve the exact date range, keyword fields, keyword mode and preprint choice.
- Show the actual `returned_result_count`, including zero.
- Do not pad a zero-result search or relax criteria to fill the requested maximum.
- Treat `paper_type` as provider metadata, not a scientific classification.
- Treat `full_text_status` as metadata only, not download authority.
- Keep `version_relationship.status: unresolved` explicit.
- Show possible duplicate result keys as candidates; never silently merge different DOI identities.
- State that results may change when the public provider updates its index.

## Stop Boundary

Every result is report-only in M3C-1.

Do not persist a discovery candidate. Do not download full text. Do not create a Registry record, choose a source-root destination, use a logged-in browser, post a document request or start downstream intake.

When the user approves results, report that approved-candidate persistence and legal acquisition require the separately implemented next route. A new Research Question idea also remains report-only.

## Failure

- `RKBC-002`: correct the bounded request; do not relax scientific criteria.
- `RKBC-032`: connector or network failure; report no partial success.
- `RKBC-033`: invalid provider output; report no partial success.

Never bypass the connector with an arbitrary URL, browser scrape, private API token or hidden script.
