# Authority And Failure Boundaries

Read this file before mutation and whenever a command fails. Fail closed; never repair by bypassing Core.

## Human Authority

The Agent may create or check candidates only where the public contract permits. Never assign:

- `human_checked`;
- `verified`;
- final `included` or `excluded` screening;
- source deletion, replacement or disposition;
- migration or legacy write-freeze completion.

A generated Research Question remains report-only until the user approves it.

Discovery search results remain report-only until the user explicitly names result keys. That selection may create metadata-only candidates through M3C-2A, but it does not authorize a download, choose a destination path, register a paper or assign review/screening status.

Ordinary knowledge queries are read-only. If persistence intent is ambiguous, use `ephemeral_query` and report `persistent_writes: 0`.

Query and Step 7 maintenance preflight is dry-run-only. A workspace action must be handled as a separate authorized intake/bootstrap task, never hidden inside a query or candidate rerun.

## Discovery Authority

Use only `discovery search` with the built-in `europe-pmc` connector. The Agent resolves exact dates and field-bound keywords; Core validates, searches, normalizes and locally refilters.

Do not pad zero results, accept an arbitrary endpoint, follow a provider full-text link, download a source or chain into intake. `full_text_status` is metadata only. A connector or later-page failure invalidates the complete discovery response.

Candidate handoff requires the complete report and exact `actor: user` after explicit result-key selection. Do not infer selection, drop unselected rows from the submitted report, create a question, or interpret `user_selected` as `human_checked`, `verified`, `included` or acquisition approval. `RKBC-034` blocks the complete batch; never refresh changed metadata silently.

`discovery resolve` may check exactly one selected candidate through the fixed Europe PMC route. It returns transient access-policy observations only. Never persist the report, expose or follow a provider URL, or treat `auto_acquisition_eligible` as download authority.

## Evidence Boundary

Canonical Evidence requires a current same-paper source, active parsed page, exact page/character locator and exact quote slice. Narrow every claim to what the source actually supports.

Review queue records are not evidence. Never cite them as support, count them as canonical Evidence or silently promote them.

Review Memory is background-only. Review source notes, exact excerpts, Review Memory IDs and Review Unit IDs never support canonical Evidence, Question Mapping or Step 7. Keep the review route mutually exclusive from Paper Card and Evidence for the same paper.

Review Memory may inform a labeled ephemeral background discussion, but it cannot become primary support.

## Step 7 Authority

Persist only for `explicit_step7_maintenance` or `full_workflow_step7_refresh`. Use `step7 context` before mutation and `record promote` for every append or replace. Never write Step 7 JSONL directly.

Use only grounded/revised Card Units already selected by the current Question Mapping. Exact reruns write nothing. Replace the same candidate when its meaning/support changes, append only a materially distinct candidate and stop on an uncertain near-duplicate.

Refresh Cross-View only after all source candidates are current and admissible. Staleness requires reassessment; it does not authorize automatic scientific replacement.

## Source And Parse Stops

Stop for:

- relative, missing, directory or out-of-root source paths;
- link or root escape;
- stale or ambiguous exact registration;
- changed source fingerprint;
- unavailable PDF adapter;
- malformed, encrypted, image-only or text-unavailable PDF;
- stale or inconsistent parse identity;
- claims requiring OCR, geometry, figures, tables, supplements or non-contiguous excerpts.

Never move, copy, rename, delete or edit a source asset.

## Integrity Stops

Stop the complete batch for:

- workspace identity or layout conflict;
- unsupported layout version;
- unresolved or incomplete transaction;
- mutation safety reported false;
- complete-bundle validation failure;
- Guardian findings that indicate shared-state integrity failure.

`transaction recover --dry-run` may explain possible actions. Do not apply recovery in this Skill.

## Semantic Stops

Stop the selected paper for:

- unsupported, mixed, protocol, methods-only or low-confidence document type;
- primary/review route conflict or uncertain review subtype;
- stale Review Memory that has not been reread against the current parse;
- a possible duplicate record that cannot be matched exactly;
- a claim whose wording exceeds its quote;
- an unsupported Card Unit with no valid queue representation;
- a requested Question Mapping without user supply or approval;
- an existing Card or grounded chain that would require automatic rewrite.
- ambiguous Step 7 persistence intent;
- an uncertain near-duplicate Step 7 candidate;
- a Cross-View whose source candidate is stale, rejected, missing or cross-question.

## Task Outcomes

Use these only in the non-canonical task report:

| Outcome | Meaning |
| --- | --- |
| `completed` | The requested primary or review route newly reached read-only Guardian. |
| `unsupported_for_now` | Required runtime capability is outside the current Skill. |
| `config_required` | An existing valid workspace config was not supplied. |
| `source_stale` | Exact registration exists but source bytes changed. |
| `source_ambiguous` | More than one paper owns the exact source reference. |
| `document_type_stop` | The source is not eligible for the primary route. |
| `review_classification_uncertain` | A review-like source cannot be assigned a supported subtype confidently. |
| `route_conflict` | The paper already owns records from the mutually exclusive route. |
| `review_memory_stale` | The stored Review Memory refers to an older parse snapshot. |
| `low_value_recorded` | A valid zero-Unit memory prevents unnecessary rereading. |
| `provenance_unavailable` | Exact page, locator or quote support cannot be produced. |
| `integrity_blocked` | Workspace, transaction or Guardian state is unsafe. |
| `needs_user_approval` | The next action requires explicit user authority. |
| `resume_available` | Current state supports a deterministic next incomplete stage. |
| `completed_no_change` | The current chain is already complete and current. |
| `query_completed_no_write` | An ephemeral query returned a bounded answer with zero persistent writes. |
| `discovery_completed_no_write` | Public metadata discovery returned 0-15 results with zero persistent writes. |
| `discovery_candidates_recorded` | Explicitly selected metadata candidates were created or gained a new selection context. |
| `discovery_candidates_no_change` | Every explicit selection intent already existed and no write occurred. |
| `discovery_candidate_conflict` | Same-result metadata changed and the complete handoff batch was rejected. |
| `discovery_failed` | The bounded request, connector or provider output failed before a complete report. |
| `step7_no_change` | An exact existing candidate satisfied the requested maintenance without a write. |
| `step7_near_duplicate_stop` | Semantic overlap could not be resolved safely before append/replace. |

These labels never become stored statuses.

## No Fallbacks

Do not parse workspace or domain-profile configuration. Do not read canonical JSON or JSONL files directly. Outside the explicit `discovery search` route, do not call a private legacy CLI, fallback parser, browser, network service or hidden script. Report the boundary and stop.
