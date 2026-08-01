# P5-A Reading Context Core Closure Manifest

- status: `closed`
- reconciled_at: `2026-08-02T03:57:15+08:00`
- implementation_commit: `aeefc3eaf3a4e006e0472bd45e68933706bc3719`
- implementation_tree: `d9a99324fc77b6936636ef5870da657e6fdef11d`
- branch: `feature/p5a-reading-context`
- application_service_interface: `1.7`
- validation_receipt: `docs/p5a-reading-context-validation-receipt.md`
- next_gate: `p5a_core_remote_review_and_app_plan`
- cleanup_status: `generated validation workspaces retained until P11 and overall completion`

## Delivered Boundary

- one zero-write `ReadingApplicationService` behind the opaque workspace session;
- complete Primary Paper Card and Review Memory reading projections;
- deterministic two-to-four-paper ordered reading input;
- exact Primary-revision Evidence trace descriptors;
- source, parse, Source Adequacy and Question context projections;
- Application Service interface advancement from `1.6` to `1.7`;
- complete source, build, installed-wheel, PDF-extra and privacy validation.

## Authority And Compatibility

The service adds an App-facing read projection only. Core remains the sole authority for
workspace validation, provenance resolution and currentness. Committed semantic content
remains visible when its source is unavailable, but no stale or unresolved trace is
presented as current factual support. Historical revisions are not rewritten or rebound to
the active parse.

No schema, ID namespace, workspace directory, transaction, operational record or writer
authority changed. Existing CLI behavior remains compatible; the new facade is additive.

## Deferred

- App reading workspace UI and multi-paper presentation;
- trusted PDF source handles, PDF.js and UPDF handoff;
- report-only Knowledge Query Agent Tasks and Research Synthesis;
- Direction, Field Map, discovery UI, Exchange and Obsidian rendering;
- private-workspace integration, migration and legacy cutover;
- generated-workspace cleanup before P11 and overall completion.
