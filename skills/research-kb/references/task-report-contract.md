# Task Report Contract

Return one concise private task report after the invocation. The report is not canonical state and must not be persisted by the Skill.

## Invocation Summary

Always report:

```yaml
invocation_mode: local_intake | ephemeral_query | explicit_step7_maintenance | full_workflow_step7_refresh
persistent_writes:
workspace_preflight:
guardian:
capabilities_deliberately_skipped:
recommended_next_action:
```

For `ephemeral_query`, require `persistent_writes: 0`.

## Batch Summary

Report:

- requested source count and deduplicated source count;
- completed, no-change, stopped and failed counts;
- workspace and capability preflight outcome;
- Guardian status;
- capabilities deliberately skipped;
- recommended next action.

## Per-Paper Result

For each source, include:

```yaml
source_label:
paper_id:
outcome:
completed_stage:
registry_state:
source_state:
parser:
  adapter:
  version:
  page_count:
document_type:
  value:
  confidence:
  routing_decision:
paper_card:
  present:
  section_count:
  unit_count:
  grounded_unit_count:
evidence_count:
review_queue_count:
review_memory:
  present:
  review_memory_id:
  freshness:
  review_subtype:
  read_status:
  memory_value_status:
  section_count:
  reusable_unit_count:
  primary_paper_lead_count:
  non_reusable_note_count:
  coverage_limit_summary:
question_mapping:
  status:
guardian:
  status:
  finding_codes:
failure:
  diagnostic_code:
  failed_stage:
  resume_possible:
  safe_next_action:
```

Use `null` for fields that do not apply. Keep stable machine-readable outcome labels from the authority reference and explain them briefly in natural language.

Use `completed` when this run newly reaches Guardian, and `completed_no_change` when the current chain was already complete and no canonical write was needed.

## Ephemeral Query Result

For an ordinary knowledge query, report:

```yaml
query:
  query_type: seven_section | overview | methods | comparison | claim_trace_back | research_directions | review_gaps
  selector:
  paper_ids:
  question_id:
  card_unit_base:
  canonical_evidence_expanded:
  evidence_ids:
  non_evidence_boundary_ids:
  answer:
  unresolved_items:
  persistent_writes: 0
```

Keep research-direction and new-question ideas `report-only` unless a separately explicit persistence action is active.

## Step 7 Maintenance Result

For explicit or full-workflow Step 7 work, report:

```yaml
step7_maintenance:
  question_id:
  freshness_before:
  appended_candidate_ids:
  replaced_candidate_ids:
  no_change_candidate_ids:
  near_duplicate_pairs:
  freshness_after:
  render_status:
  guardian_status:
  guardian_finding_codes:
```

Do not claim a write when `record promote` did not return the candidate ID. Exact reruns belong in `no_change_candidate_ids` and produce no process event.

## Privacy And Detail

The active private task may repeat a path the user supplied when needed to distinguish sources. Never copy an absolute path, paper text, quote, Card content or task report into the shared repository, Skill package, test log or persistent shared artifact.

Do not dump parsed pages, every Evidence quote, every queue item or every Review Unit. Retrieve primary details through `paper context` and `parse show`; retrieve review memory through `review context`.

## Claims

Do not claim:

- human review or verification;
- final screening;
- review-derived canonical Evidence, Field Map integration or Review Unit Question Mapping;
- Step 7 generation during an ordinary `ephemeral_query`;
- Step 7 persistence during `ephemeral_query`;
- migration or legacy cutover;
- unsupported figure, table, OCR or supplement interpretation;
- success for a stage that Core did not validate.

When the chain is incomplete, report the exact completed stage and safe resume action rather than a generic failure.
