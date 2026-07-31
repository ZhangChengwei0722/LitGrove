# P4-B Primary Semantic Bundle Validation Receipt

- status: `passed`
- validated_at: `2026-08-01T01:01:45+08:00`
- implementation_commit: `d85c1955fd65b0de9c4bb77e772ce82e33dec35c`
- branch: `feature/p4b-primary-semantic-bundle`
- baseline: `main@e5399a1`
- package: `research-kb-core==0.1.0`
- application_service_interface: `1.3`
- agent_task_registry: `p4b-v1`
- wheel_sha256: `087d41309253e7ba0f514cff0a99b707f2041969c7cfc14891b064d7974ceeed`
- sdist_sha256: `25fabbade3de9dc1c3226738a038ba76d215670b4cff08c48754d8cb9997a949`
- fixture_scope: `synthetic sources and generated workspaces only`

## Validation Matrix

| Check | Result |
|---|---|
| final focused Primary/history/view regression | `27 passed` |
| final complete Windows suite | `857 passed, 4 expected POSIX skips` |
| `compileall` | passed |
| source build of sdist and wheel | passed |
| isolated installed-wheel P4-B end-to-end smoke | passed |
| package version | `research-kb 0.1.0` |
| privacy scan | `7 expected, 0 unexpected` |
| private-path/secret review | only the intentional invalid privacy fixture matched |
| `git diff --check` | passed |

An earlier final-suite attempt found one stale-diagnostic message assertion that still
expected the pre-revision wording. The test was corrected to the accepted behavior in
which a Question Mapping becomes stale when a linked record is newer or no longer the
active Primary revision. The complete suite was then rerun from zero and passed.

## End-To-End Coverage

- creates an independent Primary semantic Pipeline Job only from a completed deterministic
  Primary intake gate;
- assesses five use-specific Source Adequacy operations and blocks figure/SI Evidence
  without converting source failure into scientific review queue;
- prepares a bounded external handoff, validates task-local aliases and exact
  quote/page/locator provenance, and writes only non-canonical staging before approval;
- atomically approves one complete Paper Card/Evidence/review-boundary revision;
- recovers bundle, Job and Task receipts across injected crash points;
- creates append-only correction revisions and retains historical IDs for audit while
  excluding inactive children from factual reads;
- rejects stale source, Parse, adequacy, Task input and bundle-head state;
- rejects legacy/P4-B mixed Primary authority and incomplete Task approval receipts.

## Scope Notes

No private scientific record or workspace, real PDF, embedded Agent runtime or external
model was accessed. Review semantic processing, App Agent Task UI, Field Map, Question
proposals and Research Synthesis drafting remain outside this receipt.
