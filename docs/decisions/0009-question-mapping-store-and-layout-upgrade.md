# ADR 0009: Question Mapping Store And Layout Upgrade

- status: accepted_for_m2b_1

## Decision

Shared Core persists one domain-neutral Question Mapping record per active Research Question in `questions/mappings.jsonl`. The existing monolithic `question-mapping` public schema remains the canonical organizational contract. A paper may participate in multiple questions through different Card Units, while one question may contain at most one link to a given paper.

Mutation continues through `record promote`. A dedicated `QuestionMappingService` handles multi-paper normalization before `RecordService` would resolve a paper-owned target. The Agent supplies question semantics, selected Card Units, link roles, rationales, and optional question-specific review queue boundaries. Core allocates question/link IDs, binds the domain profile, derives the exact evidence union from selected units, preserves selected-unit boundaries, sorts records and links, and performs atomic promotion through the existing transaction kernel.

Append accepts only user-supplied or explicitly user-approved questions. Replace uses `existing_question`, preserves the question ID and every existing paper link ID, and cannot remove links in M2B-1. Unapproved Agent-generated questions remain transient task report candidates.

## Layout Upgrade

Layout `m2b-1` adds the managed `questions/` directory. Workspace marker schema reads both `m2a-1` and `m2b-1`, but runtime accepts only the current exact marker. An eligible predecessor receives `RKBC-027` until `workspace init` performs the locked upgrade.

Upgrade eligibility requires an exact predecessor identity, valid M2A layout and structured state, complete transaction history, and either no `questions/` directory or one safe empty transitional directory. Apply creates the directory and atomically replaces the marker. It creates no empty mapping store, process event, journal, report, or scientific record.

## Freshness And Views

Guardian compares each mapping timestamp with linked Paper Cards, evidence, and review queue records. A newer upstream record produces warning `RKBC-014`; Guardian does not rewrite the mapping. `question list` and `question show` provide deterministic read access for later Skill orchestration.

Question Layer Markdown, evidence matrices, relation views, gap maps, contradictions, automatic question matching, Step 7 runtime, and link deletion remain outside M2B-1.

## Rejected Alternatives

Splitting Question and Question Link into separate canonical stores would add lifecycle and transaction complexity before deletion semantics exist. Accepting caller-supplied evidence projections would permit drift from Paper Card Units. Automatically persisting Agent-proposed questions would bypass user control. Creating an empty JSONL during workspace upgrade would turn operational initialization into canonical-state mutation.
