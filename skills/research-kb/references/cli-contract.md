# CLI Contract

Use only public `research-kb` commands. Build every command result completely before making the next routing decision. A nonzero exit or structured error is a stop unless the workflow explicitly classifies it as a per-paper isolated failure.

## Command Matrix

| Command | Mode | Consume | Skill decision |
| --- | --- | --- | --- |
| `research-kb capability show` | read | `read_commands`, adapters, feature flags | Require the approved reads and an available `pdfplumber` adapter. |
| `research-kb discovery search --provider europe-pmc --request -` | external read | normalized public metadata report | Use only for discovery reported in the active task; never infer persistence or acquisition. |
| `research-kb discovery select --workspace <config> --request - --actor user` | mutation | created, updated and no-change candidate IDs | Use only after explicit result-key selection; submit the complete report and stop before acquisition. |
| `research-kb discovery list --workspace <config>` | read | bounded candidate summaries | Reconcile selected metadata candidates without reading JSONL. |
| `research-kb discovery show --workspace <config> --candidate-id <id>` | read | one complete metadata candidate | Confirm context and fixed non-evidence states. |
| `research-kb discovery resolve --workspace <config> --candidate-id <id> --provider europe-pmc` | external read | current OA routing report | Check one selected candidate; never infer acquisition authority. |
| `research-kb discovery acquire --workspace <config> --candidate-id <id> --provider europe-pmc --actor user` | source plus candidate mutation | portable receipt and write count | Use only for exact user-requested OA acquisition; stop before intake. |
| `research-kb workspace init --workspace <config> --dry-run` | read-only preflight | result, actions, diagnostics | Apply only an existing valid config with bounded safe actions. |
| `research-kb workspace init --workspace <config>` | operational mutation | result, diagnostics | Bind or validate the managed layout; never author config. |
| `research-kb intake inspect --workspace <config> --source <absolute-path>` | read | portable source, registration state, Card sections | Reuse, register or stop exactly as reported. |
| `research-kb registry add --workspace <config> --root-id <root> --relative-path <path> --metadata -` | mutation | paper ID, duplicate candidates | Use only source values returned by intake inspection. |
| `research-kb paper status --workspace <config> --paper-id <id>` | read | structural stage, freshness and integrity facts | Route resume without treating status as a semantic instruction. |
| `research-kb paper context --workspace <config> --paper-id <id>` | read | stored Card, Evidence and review queue | Recover Core-owned IDs and exact existing records. |
| `research-kb review context --workspace <config> --paper-id <id>` | read | stored Review Memory, freshness and transient exact DOI matches | Recover Review IDs without reading canonical files. |
| `research-kb parse run --workspace <config> --paper-id <id> --adapter pdfplumber` | mutation | parse run, parser identity, page count | Run only when parse is missing and mutation is safe. |
| `research-kb parse show --workspace <config> --paper-id <id>` | read | validated parsed pages | Use as the only source for scientific text and locators. |
| `research-kb record promote --workspace <config> --request - --actor agent` | mutation | promoted top-level record ID | Submit one bounded JSON mutation request through existing authority. |
| `research-kb question list --workspace <config>` | read | bounded question summaries | Find an explicitly selected existing question. |
| `research-kb question show --workspace <config> --question-id <id>` | read | one Question Mapping | Inspect an existing mapping; do not edit stores directly. |
| `research-kb question render --workspace <config> --question-id <id>` | read | disposable Markdown reading view | Use only as a non-canonical reading aid. |
| `research-kb step7 context --workspace <config> --question-id <id>` | read | mapping, candidates and current freshness | Reconcile before any Step 7 write and recover candidate IDs. |
| `research-kb step7 render --workspace <config> --question-id <id>` | read | disposable Markdown candidate view | Render only after context validation; never persist the output. |
| `research-kb guardian check --workspace <config>` | read | findings and status | Report the final deterministic integrity result. |
| `research-kb transaction recover --workspace <config> --dry-run` | read-only preflight | recovery actions | Report possible recovery; do not apply recovery in this Skill. |

## Capability Gate

Require these read commands:

```text
capability show
discovery search
discovery list
discovery resolve
discovery show
guardian check
intake inspect
paper context
paper status
parse show
question list
question render
question show
review context
step7 context
step7 render
```

For discovery search, require `on_demand_discovery: true` and an available `europe-pmc` connector. Do not require a workspace or `pdfplumber`. For explicit candidate handoff, also require `approved_discovery_candidate_handoff: true`, an existing workspace and the `discovery list/show` reads. For one selected candidate's OA check, additionally require `legal_oa_resolution: true`. For exact user-requested acquisition, require `explicit_oa_acquisition: true`, available `pdfplumber`, `discovery show/resolve` and no-change workspace preflight.

For local intake, query and Step 7 modes, require `real_pdf_parse: true`, plus `pdfplumber` with `availability: available` and a non-empty version. A declared feature with a missing optional dependency is not executable.

For query and Step 7 maintenance, use `workspace init --dry-run` only. Its successful `result` is `planned`; determine whether the workspace is already usable from `managed_actions`. Only `already_present` entries plus the planned `acquire_workspace_lock` entry are no-change preflight. Any create, marker write, adoption or upgrade action stops this route. Do not call operational init merely because the dry-run result says `planned`.

## Read Boundaries

- `intake inspect` owns absolute-path confinement, portable source projection, exact Registry matching and active Card section discovery.
- `paper status` exposes deterministic stage and integrity facts, not scientific content or a next action.
- `paper context` is the only public recovery surface for Paper Card, Evidence and queue records.
- `review context` is the only public recovery surface for Review Memory and Review Unit IDs.
- `question show` plus per-paper `paper context` are the structured inputs for question-scoped comparison.
- `question render` and `step7 render` are disposable stdout Markdown, never structured input or persistent state.
- `step7 context` is the only public recovery and freshness surface for Step 7 candidates.
- `parse show` is the only public parsed-text read surface.
- `guardian check` is read-only unless an explicit future task authorizes report persistence.
- `discovery search` reads one fixed public metadata provider and writes no local state. Its live results are mutable external input.
- `discovery list/show` validate the complete workspace and expose only persisted metadata candidates; they do not resolve live-provider staleness.
- `discovery resolve` rechecks one selected candidate through exact stored identity, returns no URL and writes no state.
- `discovery acquire` is the only source-write route; Core owns the exact inbox target, PDF validation, create-only publication and receipt.

Do not parse workspace or domain-profile configuration. Do not read canonical JSON or JSONL files directly. Do not infer IDs or canonical paths.

## Stdin Handoff

Send one UTF-8 JSON object through stdin for discovery search/selection, Registry metadata or mutation requests. Do not send YAML, exceed published limits or create temporary request files. Ordinary record mutations still use `record promote`; discovery candidate handoff uses its dedicated user-authority command.

Use `actor: agent` for ordinary record mutation. Use exact `--actor user` only to transcribe explicit discovery result-key selection or exact candidate acquisition requested by the user. Never infer user authority or request user-only review or screening states.

### Discovery request

```json
{
  "request_version": "1.0",
  "date_from": "2026-07-14",
  "date_until": "2026-07-21",
  "title_keywords": ["<title phrase>"],
  "abstract_keywords": ["<abstract phrase>"],
  "keyword_mode": "any",
  "include_preprints": true,
  "max_results": 15
}
```

Use dates resolved for the active task rather than copying the example. The request limit is 64 KiB. A successful report has `persistent_writes: 0`; it is not a candidate store or download receipt.

### Discovery selection request

Submit the complete successful report returned in the same task, not a reconstructed or selected-only subset:

```json
{
  "request_version": "1.0",
  "report": {"<complete_M3C-1_report>": "..."},
  "selections": [
    {
      "result_key": "<explicitly_selected_result_key>",
      "target_question_ids": []
    }
  ]
}
```

The selection request limit is 4 MiB. Omit `fixture_origin` in real tasks. Target questions must already exist. Selection persists only metadata candidates and never authorizes acquisition, Registry, intake, screening or scientific evidence use.

### Registry metadata

An empty object is valid when no bibliography was supplied. When metadata is available, send only bounded fields:

```json
{
  "bibliography": {
    "title": "<title or null>",
    "authors": ["<author name>"],
    "year": null,
    "doi": null
  },
  "review_status": "ai_checked"
}
```

Do not invent missing bibliography values. Omit `fixture_origin` in real tasks.

### Mutation request templates

These append templates show the complete caller-owned envelope, not the persisted record schema. Replace every angle-bracket placeholder with task data. Do not submit CLI-owned IDs, timestamps, fingerprints, canonical flags, automation status or derived Evidence links. Omit `fixture_origin` in real tasks.

Evidence:

```json
{
  "contract_version": "1.0",
  "operation": "append",
  "record_kind": "evidence",
  "target_record_id": null,
  "context": {"paper_id": "<paper_id>"},
  "payload": {
    "claim": "<narrow claim supported by the quote>",
    "evidence_type": "reported_result",
    "quote": "<exact contiguous text from parse show>",
    "source_page": {
      "pdf_page": 1,
      "printed_page": null,
      "section": "<section or null>",
      "figure_or_table": null
    },
    "locator": "page:1:char:<zero_based_start>-<exclusive_end>",
    "support_scope": "<exact supported scope>",
    "what_it_does_not_support": ["<specific unsupported extension>"],
    "review_status": "ai_checked"
  }
}
```

Use one of the public Evidence types: `reported_result`, `method`, `control`, `limitation`, `safety`, `efficacy`, `mechanism` or `other`.

Review queue boundary:

```json
{
  "contract_version": "1.0",
  "operation": "append",
  "record_kind": "review-queue",
  "target_record_id": null,
  "context": {"paper_id": "<paper_id>"},
  "payload": {
    "issue_type": "overclaim",
    "claim_candidate": "<candidate that cannot enter Evidence>",
    "reason": "<why the candidate is unsupported, ambiguous or too broad>",
    "source_page": {
      "pdf_page": 1,
      "printed_page": null,
      "section": "<section or null>",
      "figure_or_table": null
    },
    "locator": "page:1:char:<zero_based_start>-<exclusive_end>",
    "resolution_status": "needs_resolution",
    "review_status": "ai_checked"
  }
}
```

Use `null` for `source_page` and `locator` only when no exact location exists. Public issue types are `unsupported`, `ambiguous`, `overclaim`, `contradiction`, `source_reopen` and `locator_missing`.

Paper Card:

```json
{
  "contract_version": "1.0",
  "operation": "append",
  "record_kind": "paper-card",
  "target_record_id": null,
  "context": {"paper_id": "<paper_id>"},
  "payload": {
    "card_status": "calibrated",
    "review_status": "ai_checked",
    "sections": [
      {
        "section_id": "<section_id returned by intake inspect>",
        "units": [
          {
            "section_id": "<same section_id>",
            "statement": "<question-independent Card statement>",
            "statement_type": "reported_result",
            "grounding_status": "grounded",
            "evidence_ids": ["<CLI-returned evidence_id>"],
            "boundary_refs": ["<CLI-returned queue_id when applicable>"],
            "source_page": {
              "pdf_page": 1,
              "printed_page": null,
              "section": "<section or null>",
              "figure_or_table": null
            },
            "confidence": "medium"
          }
        ]
      }
    ]
  }
}
```

Include every returned Card section exactly once and in returned order, using an empty `units` array when needed. Core allocates every `unit_id`. A `grounded` or `revised` Unit requires Evidence; an `interpretive`, `background_only` or `needs_resolution` Unit has no Evidence IDs and must retain its applicable boundary.

Choose `statement_type` by the statement's meaning, not by a fixed domain section mapping:

| `statement_type` | Use for |
| --- | --- |
| `background` | Background, significance, stated research aim or research problem. |
| `method_description` | Study design, method principle, control or procedural advantage. |
| `reported_result` | Directly reported observation or measured result. |
| `author_conclusion` | The authors' explicit conclusion, application or claimed contribution. |
| `limitation` | A stated or evidence-bounded limitation. |
| `future_direction` | Proposed follow-up or future work. |
| `interpretation` | A clearly marked interpretation that is not represented as a direct result. |

There is no Card `other` type. When an aim statement also describes the experimental design, split it or use `method_description` for the design-specific Unit. Do not copy an Evidence type into `statement_type` without applying this semantic mapping.

Review Memory:

```json
{
  "contract_version": "1.0",
  "operation": "append",
  "record_kind": "review-memory",
  "target_record_id": null,
  "context": {"paper_id": "<paper_id>"},
  "payload": {
    "review_subtype": "narrative_review",
    "review_subtype_source": "agent_high_confidence",
    "review_subtype_reason": "<classification reason from parsed text>",
    "read_status": "targeted_read",
    "scope_tags": ["<generic_scope_slug>"],
    "one_sentence_reuse_value": "<specific future reuse value>",
    "memory_value": {
      "status": "reusable",
      "reason": "<why retained>"
    },
    "coverage_limits": {
      "unread_sections": ["<section not read>"],
      "weakly_read_sections": [],
      "reason": "<bounded coverage reason>"
    },
    "sections": [
      {
        "section_id": "review_objective_scope",
        "units": [
          {
            "section_id": "review_objective_scope",
            "unit_type": "field_axis",
            "content": "<actionable reusable content>",
            "source_notes": [
              {
                "pdf_page": 1,
                "printed_page": null,
                "section": "<source section>",
                "figure_or_table": null,
                "note_type": "paraphrase",
                "text": "<concise source-located paraphrase>",
                "locator": null,
                "reopen_priority": "high"
              }
            ],
            "workflow_impacts": [
              {
                "target": "primary_paper_reading",
                "action": "<concrete later action>"
              }
            ],
            "evidence_use": {
              "can_support_canonical_evidence": false,
              "can_guide_primary_grounding": true,
              "primary_grounding_required_before": ["comparative_claim"]
            },
            "reuse_quality": {
              "reuse_confidence": "medium",
              "staleness_risk": "low",
              "reason": "<reuse-quality reason>"
            },
            "primary_paper_lead": null
          }
        ]
      }
    ],
    "non_reusable_notes": [],
    "review_status": "ai_checked"
  }
}
```

Include all seven fixed Review Memory sections in the order defined by the review workflow. The example shows one section only for readability. Do not submit `review_memory_id`, `review_unit_id`, source fingerprint, parse snapshot, boundary constants, timestamps or automation status on append. Paraphrases require `locator: null`; quote excerpts require an exact character locator. A non-`reusable` memory value requires zero Units.

On explicit replace, use the current `review_memory_id` as `target_record_id`. Existing Units may retain only IDs returned by `review context`; new Units omit their ID. Review Memory cannot coexist with Paper Card or canonical Evidence for the same paper.

Question Mapping:

```json
{
  "contract_version": "1.0",
  "operation": "append",
  "record_kind": "question-mapping",
  "target_record_id": null,
  "context": {
    "paper_id": null,
    "question_origin": "user_supplied"
  },
  "payload": {
    "question_text": "<user-supplied or approved question>",
    "scope": "<bounded question scope>",
    "mapping_status": "ai_checked",
    "paper_links": [
      {
        "paper_id": "<paper_id>",
        "selected_card_unit_ids": ["<unit_id from paper context>"],
        "role_in_question": "<short role slug>",
        "relevance_rationale": "<why the selected Units address the question>",
        "boundary_refs": ["<queue_id when applicable>"]
      }
    ]
  }
}
```

Use `question_origin: user_approved_candidate` when the active user previously approved an Agent-generated candidate. Do not submit `question_link_id` or `evidence_ids`; Core derives them from the selected Card Units.

### Step 7 mutation envelopes

Use these only for `explicit_step7_maintenance` or `full_workflow_step7_refresh`. All requests require an existing Question Mapping and selected `grounded` or `revised` Card Units already present in that mapping.

Do not submit `candidate_id`, `type`, `evidence_base`, `review_queue_refs`, `input_snapshot`, timestamps, `not_fact`, `review_status` or `automation_status`. Core derives `evidence_base` and required queue-boundary closure from selected Units and owns every candidate-only field.

For replace, change `operation` to `replace` and use the current same-type candidate ID returned by `step7 context` as `target_record_id`. Submit the complete new Agent-owned payload. Never use append as version history.

Synthesis:

```json
{
  "contract_version": "1.0",
  "operation": "append",
  "record_kind": "step7-synthesis",
  "target_record_id": null,
  "context": {"paper_id": null, "question_origin": "existing_question"},
  "payload": {
    "question_id": "<existing_question_id>",
    "title": "<bounded candidate title>",
    "candidate_status": "keep",
    "analysis_operator": "aggregate",
    "paper_card_base": [
      {"paper_id": "<first_paper_id>", "card_unit_ids": ["<mapped_grounded_or_revised_unit_id>"]},
      {"paper_id": "<second_paper_id>", "card_unit_ids": ["<mapped_grounded_or_revised_unit_id>"]}
    ],
    "missing_evidence": ["<specific missing evidence>"],
    "assumptions": ["<explicit assumption>"],
    "risk": ["<candidate-level interpretation risk>"],
    "testability": "<how the synthesis could be checked>",
    "next_action": "<bounded next action>",
    "trace_status": "traceable",
    "claim": "<bounded multi-paper claim>",
    "scope": "<included scope>",
    "agreement_pattern": "<agreement or lack of agreement>",
    "conflict_pattern": "<conflict or explicit absence of direct conflict>",
    "boundary_statement": "<what the synthesis does not establish>"
  }
}
```

Review Angle:

```json
{
  "contract_version": "1.0",
  "operation": "append",
  "record_kind": "step7-review-angle",
  "target_record_id": null,
  "context": {"paper_id": null, "question_origin": "existing_question"},
  "payload": {
    "question_id": "<existing_question_id>",
    "title": "<bounded angle title>",
    "candidate_status": "keep",
    "analysis_operator": "compare",
    "paper_card_base": [
      {"paper_id": "<paper_id>", "card_unit_ids": ["<mapped_grounded_or_revised_unit_id>"]}
    ],
    "missing_evidence": ["<specific missing evidence>"],
    "assumptions": ["<explicit assumption>"],
    "risk": ["<candidate-level interpretation risk>"],
    "testability": "<how the angle could be evaluated>",
    "next_action": "<bounded next action>",
    "trace_status": "traceable",
    "thesis": "<organizing thesis>",
    "organizing_axes": ["<axis>"],
    "included_clusters": ["<included cluster>"],
    "excluded_scope": ["<excluded scope>"],
    "why_this_angle_adds_value": "<specific added value>"
  }
}
```

Insight:

```json
{
  "contract_version": "1.0",
  "operation": "append",
  "record_kind": "step7-insight",
  "target_record_id": null,
  "context": {"paper_id": null, "question_origin": "existing_question"},
  "payload": {
    "question_id": "<existing_question_id>",
    "title": "<bounded insight title>",
    "candidate_status": "keep",
    "analysis_operator": "hypothesis_generation",
    "paper_card_base": [
      {"paper_id": "<paper_id>", "card_unit_ids": ["<mapped_grounded_or_revised_unit_id>"]}
    ],
    "missing_evidence": ["<specific missing evidence>"],
    "assumptions": ["<explicit assumption>"],
    "risk": ["<candidate-level interpretation risk>"],
    "testability": "<how the insight could be tested>",
    "next_action": "<bounded next action>",
    "trace_status": "speculative",
    "insight_type": "mechanism_hypothesis",
    "hypothesis_or_idea": "<testable idea>",
    "rationale": "<Card-grounded rationale>",
    "falsification_condition": "<observation that would weaken the idea>",
    "minimum_test": "<smallest discriminating test>"
  }
}
```

Cross-View:

```json
{
  "contract_version": "1.0",
  "operation": "append",
  "record_kind": "step7-cross-view",
  "target_record_id": null,
  "context": {"paper_id": null, "question_origin": "existing_question"},
  "payload": {
    "question_id": "<existing_question_id>",
    "title": "<bounded cross-view title>",
    "candidate_status": "keep",
    "analysis_operator": "contrast",
    "paper_card_base": [
      {"paper_id": "<paper_id>", "card_unit_ids": ["<mapped_grounded_or_revised_unit_id>"]}
    ],
    "missing_evidence": ["<specific missing evidence>"],
    "assumptions": ["<explicit assumption>"],
    "risk": ["<candidate-level interpretation risk>"],
    "testability": "<how the relation could be checked>",
    "next_action": "<bounded next action>",
    "trace_status": "traceable",
    "source_views": ["<current_same_question_candidate_id>"],
    "relation_type": "complements",
    "why_interesting": "<why the relation matters>",
    "shared_dimension": "<shared comparison dimension>",
    "non_equivalence_warning": "<why the sources are not interchangeable>"
  }
}
```

Allowed `candidate_status` values are `keep`, `revise`, `rejected` and `needs_resolution`. A rejected payload must add a non-empty `rejection_rationale`; non-rejected payloads must omit it or use `null`. Use only public `analysis_operator`, `insight_type`, `relation_type` and `trace_status` values from the contract.

## Failure Output

Expect successful JSON on stdout. Structured command failures write an error diagnostic and leave stdout empty. Preserve the diagnostic code and bounded message in the private task report; never replace it with a guessed cause or expose private paths in shared artifacts.

`RKBC-032` is a discovery connector/transport stop. `RKBC-033` is invalid provider/report output. `RKBC-034` is same-result metadata conflict. None permits a partial report, partial candidate batch or direct browser/API fallback.
