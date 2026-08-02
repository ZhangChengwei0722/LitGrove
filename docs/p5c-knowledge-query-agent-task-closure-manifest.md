# P5-C Knowledge Query Agent Task Core Closure Manifest

- status: `closed`
- reconciled_at: `2026-08-02T12:24:54+08:00`
- implementation_commit: `9d5ac6807c741c5f3f2de916b07a0c9f4b566a34`
- implementation_tree: `cb7f5aabd601b54ea83f7389b8d9be95645e06de`
- branch: `feature/p5c-knowledge-query-tasks`
- application_service_interface: `1.9`
- agent_task_registry: `p5c-v1`
- validation_receipt: `docs/p5c-knowledge-query-agent-task-validation-receipt.md`
- next_gate: `p5c_core_remote_review_merge_then_app_implementation`
- cleanup_status: `generated validation workspaces retained until P11 and overall completion`

## Delivered Boundary

- a report-only Knowledge Query Task covering single-paper explanation, seven-section
  overview, methods, selected-paper comparison, trend/problem discussion and evidence
  finding;
- a deterministic admissibility snapshot over active Registry identity, source
  currentness, current Primary Card/Evidence revisions and optional Review background;
- exact support and background allowlists with stale-submit and stale-accept rejection;
- external Codex CLI or Claude Code CLI handoff through one host-neutral, resolved JSON
  result contract and untrusted-data prompt boundary;
- App-previewable answer blocks plus revision, rejection and report-acceptance lifecycle;
- Guardian and schema invariants proving accepted reports cannot claim Pipeline Job or
  canonical scientific write authority;
- Application Service interface advancement from `1.8` to `1.9`, registry advancement
  to `p5c-v1`, and `knowledge_query_agent_tasks: true` capability reporting.

## Authority And Compatibility

Core owns selectors, admissibility, exact basis digests, Task state, result validation and
Guardian checks. The external Agent owns answer semantics but receives no filesystem,
network, credential, lease extension or persistence authority. The App remains the user
preview and acceptance boundary.

Query acceptance is an operational write only. The answer remains a
`current_task_report`; it is not a canonical scientific record and cannot silently create
or update Question Mapping or Research Synthesis. Existing Primary, Review, reading,
Evidence-source and deterministic-intake interfaces remain compatible.

## Deferred

- App API and the user-facing Knowledge Query work surface;
- persisted Research Synthesis maintenance from approved factual inputs;
- Direction, Field Map, discovery UI, Exchange and Obsidian rendering;
- embedded Agent execution, credentials, migration and legacy cutover;
- private-workspace integration, real-paper processing and cleanup before P11 and overall
  completion.
