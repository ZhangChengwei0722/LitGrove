---
view_type: "step7_reading_view"
interface_version: "1.0"
question_id: "question_a0000001-0000-4000-8000-000000000001"
canonical: false
generated_view: true
editable_source: false
candidate_count: 4
stale_count: 0
---

# Which synthetic response patterns agree across studies?

- Scope: The fabricated fixture records only.
- Mapping Status: `ai_checked`
- Candidates: 4
- Stale Candidates: 0

## Synthesis

### Synthetic synthesis candidate (`synthesis_a0000001-0000-4000-8000-000000000001`)

- Candidate Status: `keep`
- Freshness: `current`
- Analysis Operator: `aggregate`
- Trace Status: `traceable`
- Candidate Only: `not_fact: true`

#### Scientific Content
- Claim: Both synthetic studies report a response in the same direction.
- Scope: The two fabricated settings represented in the fixture.
- Agreement Pattern: Direction agrees while magnitude is not compared.
- Conflict Pattern: No direct conflict is represented.
- Boundary Statement: The fixture does not support universal generalization.

#### Paper Card Base
- `paper_a0000001-0000-4000-8000-000000000001`: `unit_a0000001-0000-4000-8000-000000000001`
- `paper_a0000002-0000-4000-8000-000000000002`: `unit_a0000006-0000-4000-8000-000000000006`

#### Canonical Evidence Base
- `evidence_a0000001-0000-4000-8000-000000000001`
- `evidence_a0000003-0000-4000-8000-000000000003`

#### Review Queue Boundaries (Not Evidence)
None.

#### Missing Evidence
- Independent fabricated replication

#### Assumptions
- The synthetic records are comparable on the stated dimension

#### Risk
- The fixture does not represent external validity

#### Testability
Add one fabricated discriminating observation.

#### Next Action
Retain as a contract-validation candidate.

## Review Angles

### Synthetic review angle candidate (`angle_a0000001-0000-4000-8000-000000000001`)

- Candidate Status: `keep`
- Freshness: `current`
- Analysis Operator: `compare`
- Trace Status: `traceable`
- Candidate Only: `not_fact: true`

#### Scientific Content
- Thesis: Organize the synthetic studies by response comparability and control completeness.
- Organizing Axes:
  - response comparability
  - control completeness
- Included Clusters:
  - compatible response direction
- Excluded Scope:
  - settings not represented in the fixture
- Why This Angle Adds Value: It separates observed agreement from unsupported generalization.

#### Paper Card Base
- `paper_a0000001-0000-4000-8000-000000000001`: `unit_a0000001-0000-4000-8000-000000000001`
- `paper_a0000002-0000-4000-8000-000000000002`: `unit_a0000006-0000-4000-8000-000000000006`

#### Canonical Evidence Base
- `evidence_a0000001-0000-4000-8000-000000000001`
- `evidence_a0000003-0000-4000-8000-000000000003`

#### Review Queue Boundaries (Not Evidence)
None.

#### Missing Evidence
- Independent fabricated replication

#### Assumptions
- The synthetic records are comparable on the stated dimension

#### Risk
- The fixture does not represent external validity

#### Testability
Add one fabricated discriminating observation.

#### Next Action
Retain as a contract-validation candidate.

## Insights

### Synthetic insight candidate (`insight_a0000001-0000-4000-8000-000000000001`)

- Candidate Status: `keep`
- Freshness: `current`
- Analysis Operator: `experiment_design`
- Trace Status: `traceable`
- Candidate Only: `not_fact: true`

#### Scientific Content
- Insight Type: experimental\_idea
- Hypothesis Or Idea: Adding the missing synthetic control may distinguish two explanations.
- Rationale: The current fixture records the control gap without resolving it.
- Falsification Condition: Both explanations remain indistinguishable after the added control.
- Minimum Test: Run the fabricated comparison with one added control arm.

#### Paper Card Base
- `paper_a0000001-0000-4000-8000-000000000001`: `unit_a0000002-0000-4000-8000-000000000002`

#### Canonical Evidence Base
- `evidence_a0000002-0000-4000-8000-000000000002`

#### Review Queue Boundaries (Not Evidence)
None.

#### Missing Evidence
- Independent fabricated replication

#### Assumptions
- The synthetic records are comparable on the stated dimension

#### Risk
- The fixture does not represent external validity

#### Testability
Add one fabricated discriminating observation.

#### Next Action
Retain as a contract-validation candidate.

## Cross-Views

### Synthetic cross view candidate (`crossview_a0000001-0000-4000-8000-000000000001`)

- Candidate Status: `keep`
- Freshness: `current`
- Analysis Operator: `contrast`
- Trace Status: `traceable`
- Candidate Only: `not_fact: true`

#### Scientific Content
- Source Views:
  - synthesis\_a0000001-0000-4000-8000-000000000001
  - angle\_a0000001-0000-4000-8000-000000000001
- Relation Type: complements
- Why Interesting: Agreement and study organization expose different aspects of the same fixture.
- Shared Dimension: response direction
- Non-Equivalence Warning: The fabricated methods and settings are not identical.

#### Paper Card Base
- `paper_a0000001-0000-4000-8000-000000000001`: `unit_a0000001-0000-4000-8000-000000000001`
- `paper_a0000002-0000-4000-8000-000000000002`: `unit_a0000006-0000-4000-8000-000000000006`

#### Canonical Evidence Base
- `evidence_a0000001-0000-4000-8000-000000000001`
- `evidence_a0000003-0000-4000-8000-000000000003`

#### Review Queue Boundaries (Not Evidence)
None.

#### Missing Evidence
- Independent fabricated replication

#### Assumptions
- The synthetic records are comparable on the stated dimension

#### Risk
- The fixture does not represent external validity

#### Testability
Add one fabricated discriminating observation.

#### Next Action
Retain as a contract-validation candidate.
