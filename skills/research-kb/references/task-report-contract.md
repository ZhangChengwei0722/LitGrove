# Task Report Contract

Return one concise private task report after the invocation. The report is not canonical state and must not be persisted by the Skill.

## Invocation Summary

Always report:

```yaml
invocation_mode: on_demand_discovery | explicit_oa_acquisition | acquired_candidate_intake | local_intake | manuscript_projection | manuscript_audit | ephemeral_query | explicit_step7_maintenance | full_workflow_step7_refresh
persistent_writes:
workspace_preflight:
guardian:
capabilities_deliberately_skipped:
recommended_next_action:
```

For discovery search and `ephemeral_query`, require `persistent_writes: 0`. An explicit user-selected discovery handoff reports its actual Core write count separately.

## Discovery Result

```yaml
discovery:
  provider: europe-pmc
  date_from:
  date_until:
  title_keywords: []
  abstract_keywords: []
  keyword_mode: any | all
  include_preprints:
  requested_max_results:
  provider_hit_count:
  scanned_result_count:
  returned_result_count:
  truncated:
  results:
    - result_key:
      title:
      doi:
      first_publication_date:
      paper_type:
      matched_keywords: []
      match_location:
      full_text_status:
      version_relationship:
      possible_duplicate_result_keys: []
  unresolved_items: []
  persistent_writes: 0
```

Do not imply that a result entered Registry or a candidate store. Do not report a download, local path or paper ID. A legitimate zero-result report keeps `results: []`.

If the user explicitly selected results and candidate handoff ran, add:

```yaml
discovery_candidate_handoff:
  selected_result_keys: []
  created_candidate_ids: []
  updated_candidate_ids: []
  no_change_candidate_ids: []
  target_question_ids: []
  guardian_status:
  persistent_writes:
  acquisition_started: false
  registry_records_created: false
```

Do not report handoff for results the user did not name. `persistent_writes` is `0` for an exact no-change rerun and `1` for one atomic candidate-store replacement, regardless of candidate count.

If exact OA acquisition was requested, add one item per candidate:

```yaml
oa_acquisition:
  - candidate_id:
    provider: europe-pmc
    resolution_status:
    outcome: acquired | no_change | stopped
    source_ref:
      root_id:
      relative_path:
    source_fingerprint:
      algorithm: sha256
      value:
    content_size_bytes:
    guardian_status:
    persistent_writes:
    registry_records_created: false
    downstream_intake_started: false
```

Never report an absolute path, provider URL or paper ID from acquisition. A stopped item keeps `source_ref` and fingerprint null. `persistent_writes` is `2` for one newly published source plus candidate receipt and `0` for exact no-change.

If acquired-candidate intake was requested, add:

```yaml
acquired_candidate_intake:
  - candidate_id:
    requested_workflow_depth: registry_only | guardian | step7
    intake_state: unregistered | registered_current | registered_stale | ambiguous | stopped
    paper_id:
    registry_outcome: created | reused | stopped
    continuation_outcome: not_requested | continued | completed_no_change | stopped
    completed_stage:
    guardian_status:
    persistent_writes:
```

Do not report an absolute path or provider URL. `paper_id` is present only after Registry creation or exact current reuse. The normal per-paper result reports Parse and downstream scientific stages when the requested depth continues beyond Registry.
The acquired-candidate item includes the complete task write count, while `registry_outcome` distinguishes the Registry write from downstream writes. An explicit `registry_only` rerun may remain zero-write when it reuses `registered_current`.

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

## Manuscript Projection Result

```yaml
manuscript_projection:
  format: docx | pdf
  source_fingerprint:
    algorithm: sha256
    value:
  parser:
    adapter:
    version:
  unit_kind: paragraph | pdf_page
  unit_count:
  extracted_character_count:
  coverage_limits: []
  persistent_writes: 0
```

The active task may use the returned units, locators and style/table coordinates. In `manuscript_projection`, stop after projection; do not report audit findings, evidence matches or rewritten text.

## Manuscript Audit Result

```yaml
manuscript_audit:
  source_fingerprint:
    algorithm: sha256
    value:
  requested_criteria:
    - original_text:
      normalized_check:
  checked_scope:
    question_ids: []
    paper_ids: []
    resolution: user_supplied | task_resolved
    resolution_basis: []
    corpus_limits: []
  projection_coverage_limits: []
  findings:
    - finding_key: invocation-local
      criterion:
      manuscript_spans:
        - unit_locator:
          span_resolution: exact_slice | unit_only
          char_start:
          char_end:
          exact_text:
      assessment: meets | partially_meets | does_not_meet_in_checked_scope | not_assessable
      rationale:
      card_unit_refs: []
      evidence_ids: []
      non_evidence_boundary_ids: []
      corpus_boundary:
  unresolved_items: []
  persistent_writes: 0
```

Preserve the user's original criteria and report the exact checked local scope. For `exact_slice`, offsets and text must reproduce one projection unit; for `unit_only`, offsets and text are null. A factual support finding names canonical Evidence IDs. An absence or incomplete-corpus finding remains scope-limited and must not be reported as universal scientific truth.

This report is transient. Do not persist it, a claim map or a finding record, and do not include rewritten manuscript prose.

## Research Synthesis Maintenance Result

For explicit or full-workflow Research Synthesis work, report:

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
- Research Synthesis generation during an ordinary `ephemeral_query`;
- Research Synthesis persistence during `ephemeral_query`;
- acquisition, Registry promotion or downstream intake caused merely by discovery candidate selection;
- migration or legacy cutover;
- unsupported figure, table, OCR or supplement interpretation;
- success for a stage that Core did not validate.

When the chain is incomplete, report the exact completed stage and safe resume action rather than a generic failure.
