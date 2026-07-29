# P0 CLI Characterization Matrix

Status: accepted baseline characterization

Baseline: `c9a3d85e363f7c58d86992f4ad3871efc7994d3c`

Purpose: freeze observable behavior before P1 service-facade extraction

## 1. Characterization Rule

P1 must preserve command arguments, bounded input handling, stdout/stderr shape, redaction, exit code, mutation set, source-byte state and transaction outcome. Service/CLI parity is measured on normalized structured results; byte-for-byte comparison is required for rendered Markdown and stable JSON serialization surfaces that already promise deterministic bytes.

Each service class below has at least one existing success case and one validation, authority or recovery failure case. P0 adds no tests because it changes no behavior; the cited tests are the executable baseline that P1 must retain and extend with direct-service parity assertions.

## 2. Minimum Service-Class Matrix

| Service class | Success baseline | Failure/authority baseline | P1 parity obligation |
|---|---|---|---|
| read | `test_parse_show_cli_emits_all_or_one_page_without_writes`; `test_paper_status_cli_is_deterministic_bounded_and_read_only`; `test_question_list_and_show_are_deterministic_and_read_only` | `test_parse_show_cli_invalid_page_is_structured_failure`; `test_paper_status_cli_unknown_paper_has_empty_stdout`; `test_question_show_missing_id_is_redacted_reference_error` | Same fields, order, redaction, no writes and exit codes through service and CLI. |
| mutation | `test_m1b_cli_runs_registry_parse_record_and_guardian`; `test_cli_accepts_registry_metadata_and_mutation_request_from_stdin` | `test_registry_stdin_failure_is_bounded_and_preserves_registry`; `test_mutation_stdin_failure_preserves_the_complete_workspace` | Same canonical targets, events, source bytes, actor gates and rollback. |
| transaction/recovery | `test_transaction_recover_cli_dry_run_is_read_only` | `test_transaction_recover_cli_reports_missing_completed_event_as_needs_resolution` | Same action classification, dry-run mutation set, status and exit `4` for manual resolution. |
| rendering | `test_question_render_emits_raw_markdown_and_changes_no_workspace_file`; `test_two_domains_run_two_papers_through_all_step7_types_and_reads` | `test_question_render_missing_id_has_empty_stdout`; `test_step7_read_missing_id_has_empty_stdout` | Same rendered bytes, reference failures and zero workspace writes. |
| discovery/acquisition handoff | `test_discovery_search_cli_stdin_and_file_are_equal_and_read_only`; `test_discovery_acquire_cli_creates_only_source_and_receipt` | `test_discovery_search_cli_failure_has_empty_stdout`; `test_discovery_acquire_cli_non_user_failure_has_empty_stdout` | Preserve transient/persisted/create-only boundaries, exact actor authority and source-byte effects. |
| Guardian/capability | `test_capability_show_cli_is_workspace_independent`; successful Guardian path in `test_m1b_cli_runs_registry_parse_record_and_guardian` | `test_guardian_cli_returns_findings_exit_for_changed_source`; `test_workspace_init_blocked_output_is_redacted_and_uses_exit_four` | Same capability fields, finding semantics, redaction and optional report write. |

## 3. Command Coverage

| Command | Current success characterization | Current failure/authority characterization | P1 direct-service parity needed |
|---|---|---|---|
| `capability show` | `test_capability_show_cli_is_workspace_independent` | workspace independence itself is the boundary | yes |
| `compatibility inspect` | `test_two_synthetic_domains_use_same_read_only_compatibility_service`; `test_compatibility_cli_supports_explicit_in_process_injection` | `test_compatibility_cli_returns_one_for_blocking_report`; `test_compatibility_cli_returns_four_when_protected_input_changes` | yes |
| `contract validate` | `test_contract_validate_cli`; `test_cli_validates_cross_record_bundle` | `test_unknown_record_kind_returns_contract_registry_exit`; `test_cli_exit_codes_distinguish_validation_version_and_input_errors` | yes, after service extraction |
| `data check-jsonl` | `test_data_check_jsonl_reports_records_and_format_failures` success branch | same test format/validation failure branches | yes, after service extraction |
| `discovery acquire` | `test_discovery_acquire_cli_creates_only_source_and_receipt` | `test_discovery_acquire_cli_non_user_failure_has_empty_stdout` | yes |
| `discovery list` | `test_cli_select_list_show_and_guardian_preserve_unselected_metadata_and_sources` | uninitialized workspace matrix in `test_every_runtime_cli_command_requires_initialized_workspace` | yes |
| `discovery resolve` | `test_discovery_resolve_cli_is_deterministic_and_zero_write` | `test_discovery_resolve_cli_failure_has_empty_stdout` | yes |
| `discovery search` | `test_discovery_search_cli_stdin_and_file_are_equal_and_read_only` | `test_discovery_search_cli_failure_has_empty_stdout` | yes |
| `discovery select` | `test_cli_select_list_show_and_guardian_preserve_unselected_metadata_and_sources` | `test_cli_non_user_selection_has_empty_stdout_and_no_candidate_store` | yes |
| `discovery show` | `test_cli_select_list_show_and_guardian_preserve_unselected_metadata_and_sources` | uninitialized workspace matrix | yes |
| `guardian check` | `test_m1b_cli_runs_registry_parse_record_and_guardian` | `test_guardian_cli_returns_findings_exit_for_changed_source` | yes |
| `intake inspect` | `test_intake_inspect_cli_is_deterministic_bounded_and_read_only` | `test_intake_inspect_cli_failure_has_empty_stdout_and_no_mutation` | yes |
| `intake inspect-acquired` | acquired candidate downstream Primary/Review runtime tests | uninitialized workspace matrix and unresolved candidate service tests | yes |
| `manuscript inspect` | `test_manuscript_inspect_cli_emits_one_read_only_docx_report` | `test_manuscript_inspect_cli_failure_keeps_stdout_empty` | yes |
| `paper context` | `test_two_domains_run_same_core_from_intake_to_guardian`; Review route non-leak assertion in `test_review_runtime_persists_reusable_and_low_value_memories_without_primary_leak` | uninitialized workspace matrix | yes |
| `paper status` | `test_paper_status_cli_is_deterministic_bounded_and_read_only` | `test_paper_status_cli_unknown_paper_has_empty_stdout` | yes |
| `parse run` | `test_parse_cli_dispatches_pdfplumber_and_reports_exact_identity`; text-flow counterpart | wrong-type and unavailable-extra tests | yes, including adapter registry |
| `parse show` | `test_parse_show_cli_emits_all_or_one_page_without_writes` | `test_parse_show_cli_invalid_page_is_structured_failure` | yes |
| `privacy scan` | `test_privacy_scan_cli` | repository privacy suite unexpected-finding cases | yes, after service extraction |
| `question list` | `test_question_list_and_show_are_deterministic_and_read_only` | uninitialized workspace matrix | yes, after service extraction |
| `question render` | `test_question_render_emits_raw_markdown_and_changes_no_workspace_file` | `test_question_render_missing_id_has_empty_stdout` | yes, including entry loading |
| `question show` | `test_question_list_and_show_are_deterministic_and_read_only` | `test_question_show_missing_id_is_redacted_reference_error` | yes, after service extraction |
| `record promote` | `test_cli_accepts_registry_metadata_and_mutation_request_from_stdin`; two-domain runtime | `test_mutation_stdin_failure_preserves_the_complete_workspace`; actor contract tests | yes |
| `registry add` | `test_m1b_cli_runs_registry_parse_record_and_guardian` | `test_registry_stdin_failure_is_bounded_and_preserves_registry` | yes |
| `review context` | `test_review_memory_stdin_promotion_and_context_cli_are_deterministic`; Review runtime integration | Primary/Review mutual-exclusion contract tests and uninitialized matrix | yes |
| `step7 context` | `test_two_domains_run_two_papers_through_all_step7_types_and_reads` | `test_step7_read_missing_id_has_empty_stdout` | yes |
| `step7 render` | same Step 7 runtime integration | `test_step7_read_missing_id_has_empty_stdout` | yes, including entry loading |
| `transaction recover` | `test_transaction_recover_cli_dry_run_is_read_only` | `test_transaction_recover_cli_reports_missing_completed_event_as_needs_resolution` | yes, after recovery report extraction |
| `workspace init` | `test_workspace_init_cli_dry_run_apply_and_no_change` | blocked, input/version and redaction tests | yes |

## 4. P1 Acceptance Additions

For each row marked `yes`, P1 adds one direct-service call beside the existing CLI call and compares:

- normalized structured result and diagnostic codes;
- stdout/stderr and exit-code projection at the CLI boundary;
- created/changed/deleted workspace path set;
- source-byte digests before and after;
- event/journal/recovery outcome for mutations;
- exact rendered bytes for rendering;
- zero-write guarantees for read/discovery resolution paths.

No command may be declared migrated merely because the CLI instantiates a class. The use-case rule, validation aggregation, sorting/filtering, authority decision and result classification must all be owned by the shared application service.

## 5. Deferred Characterization

Pipeline Job, Source Adequacy, Agent Task, App preview/approval, Direction/Field Map/Tag, Exchange, managed Obsidian writes, backup and large-workspace maintenance have no baseline CLI behavior. Their scenario specifications are design inputs, not characterization tests, and cannot be counted as P1 parity coverage.

## 6. P1 Closure Status

All rows marked for extraction now have a focused application service. `tests/unit/test_application_services.py` compares direct service and CLI behavior across all six service classes and enforces that `cli.py` no longer imports the moved validators, bundle composition, privacy scanner, transaction manager or parse adapter classes.

The twenty baseline reusable commands retain their original services and characterization. P1 does not wrap them in another command-dispatch backend.
