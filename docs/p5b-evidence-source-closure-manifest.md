# P5-B Evidence Source Access Core Closure Manifest

- status: `closed`
- reconciled_at: `2026-08-02T06:40:22+08:00`
- implementation_commit: `64c6a1528bb212daae3e5d903229277321328f17`
- implementation_tree: `4462af44a4312531492ae4f19a98a84a7e96d68a`
- branch: `feature/p5b-evidence-source`
- application_service_interface: `1.8`
- validation_receipt: `docs/p5b-evidence-source-validation-receipt.md`
- next_gate: `p5b_core_remote_review_and_app_plan`
- cleanup_status: `generated validation workspaces retained until P11 and overall completion`

## Delivered Boundary

- a non-persistent, backend-only `EvidenceSourceHandle` bound to workspace, Evidence,
  Primary revision, exact source fingerprint/ref, PDF page and locator;
- exact active or historical source manifestation resolution without rebinding Evidence to
  a different current source;
- per-open canonical/source lineage revalidation and opened-descriptor SHA-256 checking;
- safe regular-file identity, `512 MiB` size budget and PDF-header enforcement;
- a redacted App descriptor plus an already-open Core stream/path value for a trusted App
  streaming or explicit local-reader adapter;
- Application Service interface advancement from `1.7` to `1.8` and an explicit
  `reading_evidence_source_access` capability fact;
- complete source, build, installed-wheel, PDF-extra and privacy validation.

## Authority And Compatibility

Core remains the sole authority for Evidence ownership, historical revision binding,
source provenance and live byte validation. The browser receives no path or source-ref
authority. The backend path exists only on the validated opened value and creates no
scientific or operational write.

No schema, ID namespace, workspace directory, transaction, operational record or writer
authority changed. Existing CLI and P5-A reading behavior remain compatible; the new
facade is additive.

## Deferred

- App session-bound opaque handles, HTTP Range streaming and public error mapping;
- PDF.js page rendering, quote positioning and desktop/mobile reading interaction;
- UPDF/system-reader launch adapter;
- OCR, annotation ingestion, Knowledge Query and Research Synthesis;
- Direction, Field Map, discovery UI, Exchange and Obsidian rendering;
- private-workspace integration, migration and legacy cutover;
- generated-workspace cleanup before P11 and overall completion.
