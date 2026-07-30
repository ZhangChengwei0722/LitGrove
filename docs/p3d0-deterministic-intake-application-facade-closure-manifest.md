# P3-D0 Deterministic Intake Application Facade Closure Manifest

- status: `validated_pre_merge`
- validated_at: `2026-07-31T02:05:02+08:00`
- implementation_commit: `bb439ef9fce565de7bde50c28ac3e3275a39618f`
- closure_commit: `pending`
- review_pr: `pending`
- merge_commit: `pending`
- branch: `feature/p3d0-intake-facade`
- baseline: `main@0c961b2143686b19ffbb07a9ac5e20c70f5ae2f2`
- package: `research-kb-core==0.1.0`
- application_service_interface: `1.1`
- catalog_contract: `1.0` unchanged
- layout_contract: `m3c-2a` unchanged
- wheel_sha256: `3025ad611c98189ca696347359fcef581756180324dec5870d1fed818a953c51`
- sdist_sha256: `b96f0029fa1739b9432a73013a8a3b998a633cfa1199e39147dab94a17386aae`
- cleanup_status: `cleanup_deferred_until_p11_and_overall_goal_complete`

## Delivered Boundary

- public `DeterministicIntakeApplicationService` over an opaque `WorkspaceSession`;
- bounded App projections for inbox scan, upload/watched start, resume, cancel, Job list,
  Job detail and authoritative limits;
- closed upload and watched-inbox authority registries with no browser filesystem-path
  input or caller-expanded operation set;
- trusted backend-computed upload size and SHA-256 binding through the existing bounded,
  create-only source stream path;
- deterministic composition of Source Asset intake, Registry add, source association,
  Parse, Source Adequacy and the primary/review semantic-gate trunk;
- exact idempotency replay and receipt-based recovery after source, Registry or association
  commit without duplicate Job, paper or Source Asset creation;
- source intake closure requiring one revision-one main asset and one matching correlated
  success event containing both source asset and state IDs;
- constrained `running -> running` progress for committed deterministic substeps, with the
  same invariant enforced on stored Pipeline Job chains;
- monotonic intake nodes during crash recovery and source-change re-entry through the
  deterministic source check;
- capability advertisement and base/PDF-extra installed-wheel facade execution.

P3-D0 adds no CLI workflow command, localhost server, browser UI, App workflow database,
canonical scientific schema or semantic record writer.

## Fixed Invariants

- Only Core reads `WorkspaceSession._layout`; App code receives no path or canonical-store
  authority through the session.
- Upload and watched-inbox starts have distinct exact authority sets. Watched selection
  requires both `select_inbox_candidate` and `register_by_reference`.
- `running -> running` requires a changed node, a strict superset of committed output refs,
  unchanged retry count, null wait reason and null recovery action.
- A later deterministic intake node cannot be rewritten backwards during resume.
- Registry recovery is anchored to the revision-one source receipt. A changed current
  manifestation re-enters `source_changed` handling instead of becoming an identity
  conflict or silently replacing the original fingerprint.
- Source, Registry and association receipts must be unique and mutually consistent.
  Missing, duplicate or mismatched receipts fail closed for Guardian/recovery inspection.
- Public projections omit source refs, paths, fingerprints, authority snapshots,
  idempotency keys, raw parsed text and user-authored operational reason text.
- The finite `mixed_document` route reason may be projected because it is a registered
  route enum, not user free text.
- The facade creates no Paper Card, Evidence, Review Memory, scientific review queue,
  Agent Task, semantic staging or Research Synthesis record.

## Validation

- final Windows suite: `834 passed, 4 expected POSIX skips`;
- focused facade, Pipeline Job and capability regression: `40 passed` before the final
  source-change test, followed by `16 passed` for the final facade suite;
- `compileall`: passed;
- package build from final source: passed;
- base installed-wheel smoke: passed and exercised `limits()` through a real session;
- PDF-extra installed-wheel smoke: passed and completed one synthetic facade upload to
  `primary_semantic_gate` through `pdfplumber-text-flow`;
- package version: `research-kb 0.1.0`;
- privacy scan: `7 expected, 0 unexpected`;
- `git diff --check`: passed;
- synthetic source assets only; Q001, private workspaces, real PDFs and protected sources:
  not accessed.

## Review Notes

- Tightening deterministic progress exposed and fixed a prior crash-resume node rollback
  after source association.
- Installed-wheel smoke exposed and corrected the synthetic fixture requirement that an
  existing `local_inbox` be addressable through exactly one declared source root. Core
  correctly failed closed in both invalid fixture states.
- The 840-line facade is comparable to existing focused Catalog and Source Asset services.
  Its normalization, receipt reconciliation and redacted projection helpers remain one
  application-use-case boundary; splitting them now would distribute recovery invariants
  without reducing public complexity.

## Durable Reconciliation

- repository audiences are synchronized through `README.md`, `docs/architecture.md`,
  `docs/workflow.md`, `docs/contributor-guide.md` and this manifest;
- external final design, overall plan, product roadmap and App compatibility state remain
  pending until remote merge and post-merge validation;
- `neat-freak` milestone reconciliation remains pending until the merge commit is durable.

## Explicitly Not Delivered

- secure localhost backend, multipart spool ownership, operation coordinator and Catalog
  rebuild scheduling: P3-D1;
- processing UI and responsive interaction states: P3-D2;
- integrated browser acceptance and release closure: P3-D3;
- Agent Task, App preview/approval, Paper Card/Evidence/Review Memory staging and semantic
  commit: P4;
- Direction, Field Map, Question screening, Research Synthesis, Exchange, backup,
  migration or legacy cutover.

P3-D1 may begin only after this branch is remotely reviewed, merged and post-merge
validated against the exact reviewed Core wheel.
