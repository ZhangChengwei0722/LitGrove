# Manuscript Audit Workflow

Use this route only when the user asks to audit one exact DOCX/PDF against one or more criteria stated for the current task. The audit is Agent-owned reasoning over deterministic reads; Core does not contain a semantic audit engine.

## Required Authority And Inputs

Require all of:

```yaml
workspace: existing initialized workspace config
source: exact absolute DOCX or PDF path
criteria:
  - one or more non-empty checks from this invocation
knowledge_scope:
  question_ids: []
  paper_ids: []
  resolution: user_supplied | task_resolved
  resolution_basis: []
```

Preserve each criterion's original wording. The Agent may normalize it for invocation-local reasoning, but must not silently add topic coverage, evidence support, citation consistency, wording strength or another audit dimension.

If criteria are absent or materially ambiguous, stop before `manuscript inspect`. If knowledge comparison is requested but a non-empty unambiguous scope cannot be established, stop before manuscript inspection or return `not_assessable` when the limitation appears only after projection.

## Resolve The Checked Scope

For `user_supplied`, use only the supplied existing question and paper IDs. For `task_resolved`, resolve only exact selectors already present in the current request, such as a named existing question, paper ID or unambiguous paper reference. Use `question list/show` and `paper context`; do not read canonical files directly.

The Agent must not infer a broad corpus from manuscript topic similarity, add adjacent questions or papers, or claim that the checked local scope represents the field. Record the originating request text or selector for every resolved ID in `resolution_basis`. An absence finding is always limited to this checked scope.

## Project And Map The Manuscript

Run `capability show` and `workspace init --dry-run`. Accept only the existing no-change state, require `manuscript_projection: true`, then call:

```text
research-kb manuscript inspect --workspace <config> --source <absolute.docx|absolute.pdf>
```

Retain its SHA-256 fingerprint, parser identity, stable units and coverage limits. Build only the invocation-local sections and claims needed by the requested criteria.

For an exact manuscript span:

- use zero-based, end-exclusive `char_start` and `char_end` within one returned unit;
- set `span_resolution: exact_slice` only after `exact_text` equals that unit slice;
- if the slice cannot be reproduced exactly, use `span_resolution: unit_only`, keep offsets and `exact_text` null, and cite only the stable unit locator.

Never guess character precision. PDF sections inferred from page text remain `agent_inferred`; missing layout, OCR, figures, tables, comments, revisions, footnotes and external relationships remain explicit coverage limits.

## Compare With Knowledge

Start from grounded/revised Paper Card Units returned by public reads. Expand to canonical Evidence IDs whenever a finding asserts exact factual support, checks a citation or judges wording strength. A Card Unit is the semantic entry, not a replacement for exact Evidence.

Review Memory may provide labeled background orientation. Review Memory, review queue and Step 7 cannot become factual support: queue IDs are non-evidence boundaries, and Step 7 remains candidate thinking. Do not treat silence as contradiction or a local miss as a universal unsupported claim.

## Produce The Report

Use the task report contract. Every finding is tied to one requested criterion and uses:

```text
meets | partially_meets | does_not_meet_in_checked_scope | not_assessable
```

A text-specific finding cites at least one exact or unit-level manuscript span. An absence finding may have no span only when its rationale states the bounded search performed and that no matching span was found. Factual support findings name canonical Evidence IDs; criterion-irrelevant reference arrays may remain empty.

Return `persistent_writes: 0`. Do not create a manuscript store, claim map, finding record, cache, event, journal, report file or Markdown view. Recommendations may identify what to inspect or add, but the audit must not output rewritten prose or modify the source. Rewriting is a separate task tied to the audited source fingerprint.

## Stop Conditions

Stop or mark `not_assessable` for:

- missing or ambiguous criteria or selectors;
- unsupported source, source change or relevant projection coverage limits;
- exact support requiring unavailable OCR, geometry, figures, tables, supplements, comments, revisions, footnotes or external relationships;
- a citation that cannot be resolved to the checked local knowledge;
- a finding supported only by Review Memory, review queue or Step 7;
- a manuscript fingerprint change before reporting.
