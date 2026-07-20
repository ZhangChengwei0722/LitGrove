# CLI Contract

Use only public `research-kb` commands. Build every command result completely before making the next routing decision. A nonzero exit or structured error is a stop unless the workflow explicitly classifies it as a per-paper isolated failure.

## Command Matrix

| Command | Mode | Consume | Skill decision |
| --- | --- | --- | --- |
| `research-kb capability show` | read | `read_commands`, adapters, feature flags | Require the approved reads and an available `pdfplumber` adapter. |
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
| `research-kb guardian check --workspace <config>` | read | findings and status | Report the final deterministic integrity result. |
| `research-kb transaction recover --workspace <config> --dry-run` | read-only preflight | recovery actions | Report possible recovery; do not apply recovery in this Skill. |

## Capability Gate

Require these read commands:

```text
capability show
guardian check
intake inspect
paper context
paper status
parse show
question list
question show
review context
```

Require `real_pdf_parse: true`, plus `pdfplumber` with `availability: available` and a non-empty version. A declared feature with a missing optional dependency is not executable.

## Read Boundaries

- `intake inspect` owns absolute-path confinement, portable source projection, exact Registry matching and active Card section discovery.
- `paper status` exposes deterministic stage and integrity facts, not scientific content or a next action.
- `paper context` is the only public recovery surface for Paper Card, Evidence and queue records.
- `review context` is the only public recovery surface for Review Memory and Review Unit IDs.
- `parse show` is the only public parsed-text read surface.
- `guardian check` is read-only unless an explicit future task authorizes report persistence.

Do not parse workspace or domain-profile configuration. Do not read canonical JSON or JSONL files directly. Do not infer IDs or canonical paths.

## Stdin Handoff

Send one UTF-8 JSON object through stdin for Registry metadata or mutation requests. Do not send YAML, exceed published limits, create temporary request files or bypass `record promote`.

Use `actor: agent`. Never request user-only review or screening states.

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

## Failure Output

Expect successful JSON on stdout. Structured command failures write an error diagnostic and leave stdout empty. Preserve the diagnostic code and bounded message in the private task report; never replace it with a guessed cause or expose private paths in shared artifacts.
