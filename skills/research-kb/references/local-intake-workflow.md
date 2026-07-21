# Local Primary-Research Intake Workflow

## Contents

1. Batch preparation
2. Capability and workspace preflight
3. Source and paper routing
4. Parse and classification
5. Card and provenance grounding
6. Question mapping and Guardian
7. Resume rules
8. Batch isolation

## 1. Batch Preparation

Accept an existing workspace config and either absolute PDF source paths or exact acquired candidate IDs under the separately explicit `acquired_candidate_intake` route. Preserve user order, remove repeated inputs and process one source at a time. Do not scan a directory unless the user explicitly supplied that bounded directory as the task input.

Keep source assets read-only. Never move, copy, rename, delete or rewrite them.

## 2. Capability And Workspace Preflight

Call `capability show`. Require the public read commands listed in the CLI contract and an available versioned `pdfplumber` adapter.

Call `workspace init --dry-run` with the supplied config. If the existing config is valid and initialization is required, call `workspace init`. If the config is absent, invalid, identity-conflicting or would require authoring configuration, stop with `config_required` or `integrity_blocked`.

Do not parse workspace or domain-profile configuration. Treat the initialized Core response as authority.

## 3. Source And Paper Routing

Call `intake inspect` for the absolute source path:

```text
unregistered
-> call registry add with returned root_id and relative_path
-> capture the returned paper_id

registered_current
-> require exactly one returned paper_id
-> reuse it

registered_stale
-> stop with source_stale

ambiguous
-> stop with source_ambiguous
```

Do not use same-content matches at other paths as the selected paper. Do not call `registry add` twice for the same inspected path.

For `acquired_candidate_intake`, call `discovery show` and `intake inspect-acquired` instead of reconstructing an absolute path. Pass the returned `source` and `registry_metadata` unchanged to the same `registry add` command only for `unregistered`. Reuse `registered_current`; stop on `registered_stale` or `ambiguous`. Run Guardian and stop after Registry in this bounded route.

After resolving a paper ID, call `paper status` and `paper context`. Stop when source state is not current, mutation safety is false or transaction state needs resolution.

## 4. Parse And Classification

Use status to route parsing:

- missing parse and mutation-safe paper: run `parse run --adapter pdfplumber`;
- current parse: reuse it;
- stale or inconsistent parse: stop and report;
- existing grounded downstream records with a changed parser context: stop rather than automatically reparse.

Call `parse show` after a current parse exists. Read the complete relevant pages before scientific drafting.

Classify the document in task memory as `primary_research`, `review`, `other` or `unknown`, with confidence and reason. Proceed only for high-confidence primary research or an explicit user-supplied primary type. Review, commentary, perspective, protocol, methods-only and low-confidence documents stop before Paper Card or Evidence promotion.

Classification is task output only. Do not persist a classification record.

## 5. Card And Provenance Grounding

Use the ordered `paper_card_sections` returned by intake inspection. Do not hardcode one domain's section IDs.

Build one question-independent Paper Card in memory. For the default seven-section profile, cover background/significance, research problem, method principle/advantages, conclusions/applications, innovation, limitations and future outlook. Follow a different approved profile exactly when returned.

For each factual Unit:

1. locate support in `parse show` output;
2. select one exact contiguous quote;
3. calculate the zero-based, end-exclusive character range on that stored page text;
4. submit the same paper ID, PDF page, locator and quote;
5. narrow `support_scope`;
6. state `what_it_does_not_support`;
7. use candidate review status permitted to the Agent.

Do not ground figure, table, supplement, OCR or non-contiguous claims from surrounding prose. Send unsupported, ambiguous or overstrong candidate judgments to review queue when their contract can be satisfied; otherwise keep them in the task report.

Preserve scientific order while satisfying storage dependencies:

```text
draft Card and Units in memory
-> ground factual Units
-> promote new Evidence and queue records
-> collect returned IDs
-> promote one complete Card
-> recover Card Unit IDs through paper context
```

Never persist an ungrounded placeholder Card. On rerun, compare the candidate to records returned by `paper context`. Reuse an exact existing record and its ID. Stop on a possible near-duplicate that cannot be matched exactly.

## 6. Question Mapping And Guardian

Map only:

- a question supplied directly in the active task;
- a question candidate the user explicitly approved earlier;
- an existing persisted question selected by the user.

Use Card Unit IDs returned by `paper context`. Never submit Evidence IDs or question-link IDs as caller-owned values. Keep Agent-generated unapproved questions in the task report.

Run `guardian check` without report persistence. Report its status and finding codes. Do not call a repair path or apply transaction recovery.

## 7. Resume Rules

Use current Core state rather than a Skill-owned checkpoint:

- registered only: continue to parse;
- parsed only: classify, ground and build Card;
- partial Evidence or queue without Card: reuse exact records and finish Card;
- current Card without approved mapping: map only when authorized;
- completed current chain: return a no-change result;
- stale source, stale parse or unresolved integrity: stop;
- uncertain duplicate: stop;
- unsupported document type: remain stopped before primary records.

Do not automatically replace an existing Paper Card or refresh grounded downstream records in M3A-1.

## 8. Batch Isolation

Continue to the next source after a paper-local failure such as unsupported PDF, document-type stop, unavailable provenance or record validation confined to that paper. Stop the entire batch only for workspace identity/layout conflict, incomplete transaction, mutation-unsafe state or Guardian integrity failure that may affect shared state.

Record each paper's failed stage, diagnostic, whether resume is possible and the exact safe next action.
