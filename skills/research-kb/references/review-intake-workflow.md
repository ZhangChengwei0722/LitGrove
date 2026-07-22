# Local Review Intake Workflow

Use this route only for a supported review subtype. Review Memory is a reusable reading decision cache, not a generic summary and not canonical Evidence.

## 1. Route Gate

Reuse the common capability, workspace, intake, Registry and parse steps from the local intake workflow. Before mutation, call:

```text
paper status
paper context
review context
```

New review parses use the same explicit `pdfplumber-text-flow` profile and the same reading-order stop rule as primary research. A review with unresolved column or spacing extraction remains stopped before Review Memory promotion.

Classify in task memory as one of:

```text
narrative_review
systematic_review
scoping_review
meta_analysis
perspective_or_commentary
```

Persist only when the user supplied the subtype or the Agent has high confidence from parsed text. Stop for medium/low confidence, mixed primary/review articles, protocols, methods-only documents, editorials outside the supported perspective route, or unknown types.

If the paper already has a Paper Card or canonical Evidence, stop with `route_conflict`. Do not convert, delete or overwrite the primary route.

## 2. Read For Reuse

Do not summarize a review for completeness. Retain content only when it changes later work or prevents repeated reading, such as:

- a field axis or mechanism frame;
- a method, metric or assay guardrail;
- an overclaim warning or concrete gap;
- a frontier or question seed candidate;
- a primary-paper lead;
- a synthesis statement that is useful as background but still requires primary grounding.

Use one read status:

- `skimmed`: title, abstract, headings, figures/tables and conclusion only;
- `targeted_read`: selected relevant sections read closely;
- `deep_read`: main text read sufficiently to build section map, reusable Units and leads.

Record unread and weakly read sections honestly. Empty sections are preferable to filler.

## 3. Fixed Memory Structure

Include all seven sections in this order:

```text
review_objective_scope
review_question_search_boundaries
taxonomy_field_structure
major_synthesis
methods_metrics_guardrails
gaps_frontiers
primary_leads_reuse
```

Every retained Unit must have at least one concrete `workflow_impact`. Fill only the targets it actually changes. Every Unit remains:

```text
background_only: true
can_enter_canonical_evidence: false
not_fact: true
```

Core injects these constants and all Review Memory/Unit IDs. Do not submit them on append.

## 4. Source Notes

Every Unit requires same-review page and section provenance.

For a paraphrase:

- set `note_type: paraphrase`;
- use concise text no longer than 1,000 characters;
- set `locator: null`;
- retain `pdf_page`, `printed_page`, section and optional figure/table label.

For a quote excerpt:

- use it only when exact wording materially helps reopening or boundary checking;
- keep it at most 500 characters;
- copy an exact contiguous slice from `parse show`;
- use `page:<n>:char:<zero_based_start>-<exclusive_end>`.

This quote is Review Memory provenance only. It never becomes an Evidence quote.

## 5. Memory Value And Leads

Use `memory_value.status: reusable` when any reusable Unit remains. A low-value, redundant, outdated or outside-scope review must have zero Units and a concrete reason; preserve bounded rejected material in `non_reusable_notes` so the source is not reread without reason.

A `primary_paper_lead` is a bibliographic hint, not proof. Record why to follow it and a bounded priority reason. `review context` computes only a transient exact local DOI match; no match does not mean the paper is unavailable.

## 6. Promotion And Rerun

Promote through `record promote` only, then recover IDs through `review context`:

```text
no memory + current source/parse -> append one memory
current memory + ordinary rerun -> reuse; no write
current AI-owned memory + explicit deepen/refresh -> reread and replace
stale parse -> stop and reread current parse before explicit refresh
stale source, route conflict or uncertain near-duplicate -> stop and report
```

On replace, submit only Unit IDs returned by the current memory; omit the ID for each new Unit. Never replace `human_checked` or `verified` content as Agent.

Run Guardian read-only. A stale Review Memory is a warning and is never rebound automatically. Broken current page/locator provenance is an integrity failure.

## 7. Deferred Downstream Work

Do not create Field Map, frontier, Question Mapping or Step 7 records from Review Units in this route. Do not turn review content into canonical Evidence, rank platforms, verify cited primary papers, search externally or acquire files. Return possible downstream ideas only in the private task report.
