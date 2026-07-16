---
view_type: "question_reading_view"
view_contract_version: "1.0"
question_id: "question_a0000001-0000-4000-8000-000000000001"
domain_profile_id: "domain-alpha"
mapping_status: "ai_checked"
freshness_status: "current"
mapping_updated_at: "2026-01-01T00:00:00Z"
source_snapshot_sha256: "ec23812a3503081462a3fb8724bcade4a74825bfced3d2fc3d07511398f8d9e2"
canonical: false
generated_view: true
editable_source: false
---

# Which synthetic response patterns agree across studies?

## Question Scope

The fabricated fixture records only\.

## Mapping State

- Question ID: `question_a0000001-0000-4000-8000-000000000001`
- Domain Profile ID: `domain-alpha`
- Mapping Status: `ai_checked`
- Freshness Status: `current`
- Linked Papers: 2
- Selected Card Units: 2
- Canonical Evidence: 2
- Review Queue Boundaries: 1
- Mapping Updated At: `2026-01-01T00:00:00Z`

## Linked Papers And Selected Card Units

### Fabricated Alpha Study 1 (`paper_a0000001-0000-4000-8000-000000000001`)

- Question Link ID: `qlink_a0000001-0000-4000-8000-000000000001`
- Authors: Synthetic Author
- Year: 2026
- DOI: No DOI
- Screening Status: `candidate`
- Role In Question: `comparison`
- Relevance Rationale: The synthetic unit addresses the fixture question\.

#### Research Problem (`research_problem`)

##### Card Unit `unit_a0000001-0000-4000-8000-000000000001`

- Statement: The study asks whether the synthetic intervention changes the response\.
- Statement Type: `reported_result`
- Grounding Status: `grounded`
- Confidence: `medium`
- Source Page: PDF Page: 1; Section: Synthetic section
- Evidence IDs: `evidence_a0000001-0000-4000-8000-000000000001`
- Boundary Refs: None.

### Fabricated Alpha Study 2 (`paper_a0000002-0000-4000-8000-000000000002`)

- Question Link ID: `qlink_a0000002-0000-4000-8000-000000000002`
- Authors: Synthetic Author
- Year: 2026
- DOI: No DOI
- Screening Status: `candidate`
- Role In Question: `comparison`
- Relevance Rationale: The synthetic unit addresses the fixture question\.

#### Conclusions Applications (`conclusions_applications`)

##### Card Unit `unit_a0000006-0000-4000-8000-000000000006`

- Statement: The second study reports a compatible response direction\.
- Statement Type: `reported_result`
- Grounding Status: `grounded`
- Confidence: `medium`
- Source Page: PDF Page: 1; Section: Synthetic section
- Evidence IDs: `evidence_a0000003-0000-4000-8000-000000000003`
- Boundary Refs: None.

## Canonical Evidence Trace

### Fabricated Alpha Study 1 (`paper_a0000001-0000-4000-8000-000000000001`)

#### Evidence `evidence_a0000001-0000-4000-8000-000000000001`

- Paper ID: `paper_a0000001-0000-4000-8000-000000000001`
- Claim: The synthetic intervention changed the primary response\.
- Evidence Type: `reported_result`
- Source Type: `primary`
- Quote:
> Primary response was higher in the fabricated comparison\.
- Source Page: PDF Page: 1; Section: Synthetic results
- Locator: `page:1:block:1`
- Support Scope: The fabricated comparison and stated response only\.
- What It Does Not Support:
  - Universal generalization
  - Unmeasured mechanisms
- Review Status: `ai_checked`
- Automation Status: `passed_auto_checks`

### Fabricated Alpha Study 2 (`paper_a0000002-0000-4000-8000-000000000002`)

#### Evidence `evidence_a0000003-0000-4000-8000-000000000003`

- Paper ID: `paper_a0000002-0000-4000-8000-000000000002`
- Claim: The second synthetic study reported a compatible response pattern\.
- Evidence Type: `reported_result`
- Source Type: `primary`
- Quote:
> The fabricated response followed the same direction\.
- Source Page: PDF Page: 1; Section: Synthetic results
- Locator: `page:1:block:1`
- Support Scope: The fabricated comparison and stated response only\.
- What It Does Not Support:
  - Universal generalization
  - Unmeasured mechanisms
- Review Status: `ai_checked`
- Automation Status: `passed_auto_checks`

## Review Queue Boundaries

These records are risk and unresolved-context boundaries. They are not evidence.

### Fabricated Alpha Study 1 (`paper_a0000001-0000-4000-8000-000000000001`)

#### Boundary `queue_a0000001-0000-4000-8000-000000000001`

- Paper ID: `paper_a0000001-0000-4000-8000-000000000001`
- Issue Type: `overclaim`
- Claim Candidate: The response applies to every setting\.
- Reason: The synthetic record covers one setting only\.
- Source Page: PDF Page: 1; Section: Synthetic discussion
- Locator: `page:1:block:2`
- Resolution Status: `needs_resolution`
- Review Status: `ai_checked`
- Automation Status: `passed_auto_checks`
- Not Evidence: `true`

## Freshness Diagnostics

None.
