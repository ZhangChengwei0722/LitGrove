# P3-A Pipeline Job Kernel Closure Manifest

- status: `implementation_validated`
- closed_at: `2026-07-30T04:25:25+08:00`
- implementation_commit: `09351c45515c3321074eaecc4a5b6bb934a3c9da`
- branch: `feature/p3a-pipeline-job-kernel`
- baseline: `origin/main@b46277e41fd55b47a2a4a6d669f95be64cb93fce`
- package: `research-kb-core==0.1.0`
- wheel_sha256: `baaabbf4f0ea76d725efb9c1f73be7aab14aa4833bc25244b03a35078ba677d7`
- sdist_sha256: `b702ccb7a411234381a651387f374cadd501e23865cf17e6e26b12fb6789f14d`
- layout_contract: `m3c-2a` unchanged
- cleanup_status: `cleanup_deferred_until_user_returns`

## Delivered Boundary

- optional `process/jobs.jsonl` append-only Pipeline Job state revisions;
- stable Job IDs, unique state IDs, predecessor state ID/digest and current-head projection;
- transition/wait compatibility, expected-state CAS, exact-rerun idempotency and immutable terminal revisions;
- cooperative cancellation and explicit recovery transitions without reverting committed outputs;
- optional Core-owned `job_id` correlation in process events and transaction journals;
- recovery before and after Job-store replacement through the existing transaction kernel;
- optional `guardian/finding_dispositions.jsonl` records bound to immutable report finding index and digest;
- Guardian checks for Job chain, correlated success events and unresolved transaction state;
- current-Job-only Catalog projection with stable pagination and bounded detail;
- thin Job and Guardian-disposition CLI commands plus public capability facts.

Existing workspaces without the two optional stores remain valid. The layout contract was
not upgraded, no migration or backfill was introduced, and terminal Job revisions are
their own immutable receipts.

## Fixed Invariants

- Job revision one is `created`, has no predecessor or outputs, and starts with retry
  count zero.
- Later revisions retain route, depth, authority, inputs, idempotency identity and
  creation time; prior committed output refs cannot disappear.
- A terminal revision has no successor. `waiting_agent` remains rejected by the P3
  service until P4 Agent Task support exists.
- A success Job event resolves to exactly one stored Job state. A failed create may keep
  its Core-owned Job correlation without inventing a canonical Job state.
- Guardian disposition records append a new decision; they never rewrite the original
  Guardian report or finding.
- Catalog indexes only the current Job head. Full history remains in canonical JSONL and
  is available through Job detail.

## Validation

- final Windows suite: `721 passed, 4 expected POSIX skips`;
- focused P3-A service, recovery, Guardian, CLI, schema and Catalog suites: passed;
- package build from final source: passed;
- base installed-wheel smoke: passed;
- PDF-extra installed-wheel smoke: passed;
- package version: `research-kb 0.1.0`;
- privacy scan: `7 expected, 0 unexpected`;
- `git diff --check`: passed before implementation commit;
- source assets, Q001, private workspaces and real PDFs: not accessed.

## Explicitly Not Delivered

- source asset manifestations, Registry identity correction and intake writes: P3-B;
- watched-inbox scan and create-only copy/upload flow: P3-B;
- Source Adequacy profiles and deterministic intake/Parse orchestration: P3-C;
- localhost App controls and integrated browser acceptance: P3-D;
- Agent Task, Evidence, Review Unit, scientific `review_queue`, Research Synthesis and
  any Q001 migration or write.

P3-B may begin only from its own bounded implementation plan and from the merged,
post-merge-validated P3-A head.
