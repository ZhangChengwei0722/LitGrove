# On-Demand Discovery Workflow

Use this workflow only for `on_demand_discovery`. Search returns a private task report and does not require a workspace. Candidate handoff is a separate, explicit user-selection phase that requires an existing workspace.

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
4. Keep the complete report in the active task and return `persistent_writes: 0` for search.

Do not create a temporary request file. Do not call Registry, Parse, Paper Card, Evidence, Question Mapping or Step 7 from search.

## Interpret The Results

- Preserve the exact date range, keyword fields, keyword mode and preprint choice.
- Show the actual `returned_result_count`, including zero.
- Do not pad a zero-result search or relax criteria to fill the requested maximum.
- Treat `paper_type` as provider metadata, not a scientific classification.
- Treat `full_text_status` as metadata only, not download authority.
- Keep `version_relationship.status: unresolved` explicit.
- Show possible duplicate result keys as candidates; never silently merge different DOI identities.
- State that results may change when the public provider updates its index.

## Explicit Candidate Handoff

Show search results first. Do not infer approval from relevance, order, `paper_type`, DOI, possible-duplicate state or full-text availability.

Only after the user explicitly names 1-15 `result_key` values:

1. Require an existing workspace config and `approved_discovery_candidate_handoff: true`. Run `workspace init --dry-run`; if initialization or `m3b-1 -> m3c-2a` upgrade is required, stop and handle that as a separate explicit workspace task before handoff.
2. Preserve the complete successful report exactly; do not submit a partial report or only selected rows.
3. If the user supplied existing question labels, resolve them through `question list/show`. New question ideas remain report-only.
4. Build one JSON selection request and pipe it through stdin:

```json
{
  "request_version": "1.0",
  "report": {"<complete_transient_report>": "..."},
  "selections": [
    {
      "result_key": "<explicitly_selected_result_key>",
      "target_question_ids": []
    }
  ]
}
```

```text
research-kb discovery select --workspace <config> --request - --actor user
```

5. Call `discovery list`, then `discovery show` for every returned candidate ID.
6. Run read-only Guardian and report created, updated and no-change candidate IDs.

Exact selection-intent reruns write nothing. A new query/question context may update the same candidate. `RKBC-034` means the same result key carries changed metadata; stop the complete batch instead of refreshing it.

## OA Resolution

Run this phase only for one persisted candidate already selected by the user and only when the active task requests an OA-route check.

1. Require `legal_oa_resolution: true` and the `discovery resolve` read command.
2. Re-read the candidate with `discovery show`; do not infer an ID from a report.
3. Call:

```text
research-kb discovery resolve --workspace <config> --candidate-id <discovery_id> --provider europe-pmc
```

4. Preserve one of `auto_acquisition_eligible`, `manual_review_required`, `institutional_browser_required` or `no_supported_oa_route` exactly as returned.
5. Require `persistent_writes: 0`. Do not store the report or treat its `provider_asset_ref` as a URL.

The command rechecks exact DOI or stored source identity and returns current provider observations. `auto_acquisition_eligible` does not authorize a download; a later acquisition contract must re-resolve under separate user authority.

## Stop Boundary

Do not download full text. Do not create a Registry record, choose a source-root destination, use a logged-in browser, post a document request or start downstream intake. `user_selected` and `auto_acquisition_eligible` are not `human_checked`, `verified`, `included` or acquisition approval.

## Failure

- `RKBC-002`: correct the bounded request; do not relax scientific criteria.
- `RKBC-032`: connector or network failure; report no partial success.
- `RKBC-033`: invalid provider output; report no partial success.
- `RKBC-034`: stored/result metadata conflict; report no partial handoff.

Never bypass the connector with an arbitrary URL, browser scrape, private API token or hidden script.
